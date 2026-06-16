from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger as log

from . import config, db
from .scanner import API_STATUS, check_network, discover_weather_cities, get_gfs_forecast, get_weather_markets_with_retry, make_demo_weather_market, reset_scan_diagnostics

LAST_SIGNALS: list[dict] = []
LAST_SCAN_SUMMARY: dict = {}
LAST_SKIP_REASONS: dict[str, int] = {}


def norm_cdf(x: float, mean: float, std: float) -> float:
    if math.isinf(x):
        return 0.0 if x < 0 else 1.0
    return 0.5 * (1 + math.erf((x - mean) / (std * math.sqrt(2))))


def bucket_probability(bucket: dict, forecast: float, std: float) -> float:
    return max(0.0, min(1.0, norm_cdf(bucket["high"], forecast, std) - norm_cdf(bucket["low"], forecast, std)))


def normalize_probabilities(items: list[tuple[dict, float]]) -> list[tuple[dict, float]]:
    total = sum(max(0.0, probability) for _, probability in items)
    if total <= 0:
        return [(bucket, 0.0) for bucket, _ in items]
    return [(bucket, max(0.0, probability) / total) for bucket, probability in items]


def _hours_to_resolution(resolution_dt: datetime) -> float:
    return max(0.0, (resolution_dt - datetime.now(UTC)).total_seconds() / 3600)


def _ev(true_probability: float, price: float) -> float:
    return true_probability * (1 - price) - (1 - true_probability) * price


def _skip(reason: str) -> None:
    LAST_SKIP_REASONS[reason] = LAST_SKIP_REASONS.get(reason, 0) + 1


def _city_timezone(city: dict) -> tzinfo:
    name = city.get("timezone") or "UTC"
    try:
        return ZoneInfo(str(name))
    except ZoneInfoNotFoundError:
        return timezone.utc


def _market_time_ok(city: dict, market: dict) -> tuple[bool, float, str]:
    resolution_dt = market["resolution_dt"]
    if resolution_dt.tzinfo is None:
        resolution_dt = resolution_dt.replace(tzinfo=UTC)
    tz = _city_timezone(city)
    now_local = datetime.now(UTC).astimezone(tz)
    resolution_local = resolution_dt.astimezone(tz)
    hours = max(0.0, (resolution_local - now_local).total_seconds() / 3600)
    if hours <= 0:
        return False, hours, "resolved_or_expired"
    if hours < config.MIN_HOURS_TO_RESOLUTION:
        return False, hours, "too_close_to_resolution"
    if now_local.date() >= resolution_local.date() and now_local.hour >= config.KNOWN_OUTCOME_LOCAL_HOUR:
        return False, hours, "outcome_mostly_known"
    return True, hours, "ok"


def _dynamic_std(base_std: float, hours_to_resolution: float) -> float:
    horizon = max(0.0, min(24.0, hours_to_resolution))
    multiplier = 0.75 + (horizon / 24.0) * 0.50
    return max(base_std * multiplier, base_std * 0.65)


def _market_group_key(market: dict) -> str:
    slug = str(market.get("raw", {}).get("slug") or market.get("id") or market.get("question") or "")
    slug = re.sub(r"-(?:\d+(?:-\d+)?(?:f|c)(?:or(?:higher|below))?)$", "", slug)
    if slug:
        return slug
    question = str(market.get("question") or "").lower()
    return re.sub(r"\bbe\s+.+?\s+on\b", "be <bucket> on", question)


def _signal_from_side(city: dict, market: dict, bucket: dict, forecast: float, normalized_prob: float, hours: float, std: float) -> dict | None:
    yes_price = float(bucket.get("yes_price") or 0)
    no_price = float(bucket.get("no_price") or max(0, 1 - yes_price))
    yes_ev = _ev(normalized_prob, yes_price)
    no_prob = 1 - normalized_prob
    no_ev = _ev(no_prob, no_price)
    if yes_ev >= no_ev and yes_ev > 0:
        signal_type, side, side_prob, ev, side_price = config.SignalType.EDGE_YES, "yes", normalized_prob, yes_ev, yes_price
    elif no_ev > 0:
        signal_type, side, side_prob, ev, side_price = config.SignalType.EDGE_NO, "no", no_prob, no_ev, no_price
    else:
        _skip("no_positive_ev_side")
        return None
    if ev < config.MIN_EV_THRESHOLD:
        _skip("ev_below_threshold")
        return None
    entry_count = db.get_entry_count(market["id"], bucket["label"])
    if db.has_open_position(market["id"], side, bucket["label"]):
        _skip("position_already_open")
        return None
    if not _valid_common(bucket, side_price, hours, entry_count, side):
        return None
    signal = _base_signal(signal_type, city, market, bucket, forecast, side_prob, ev, 0.0, side_price)
    signal["hours_to_resolution"] = hours
    signal["forecast_std_dev"] = std
    signal["raw_bucket_probability"] = bucket.get("raw_model_prob", normalized_prob)
    signal["normalized_bucket_probability"] = normalized_prob
    signal["market_group_key"] = _market_group_key(market)
    signal["city_date_key"] = f"{city['name']}:{market['resolution_dt'].date().isoformat()}"
    return signal


def _base_signal(signal_type: config.SignalType, city: dict, market: dict, bucket: dict, forecast: float, model_prob: float, edge: float, incentive: float, side_price: float) -> dict:
    return {
        "type": signal_type,
        "city": city["name"],
        "market_id": market["id"],
        "market_question": market["question"],
        "resolution_dt": market["resolution_dt"],
        "forecast_temp": forecast,
        "unit": city["unit"],
        "bucket": bucket,
        "model_prob": model_prob,
        "effective_edge": edge,
        "ev": edge,
        "market_price": side_price,
        "incentive_adj": incentive,
        "entry_count_today": db.get_entry_count(market["id"], bucket["label"]),
        "ladder_signals": [],
    }


def _valid_common(bucket: dict, price: float, hours: float, entry_count: int, side: str = "yes") -> bool:
    depth_key = "ask_depth_shares" if side == "yes" else "no_ask_depth_shares"
    if price <= 0:
        _skip("missing_price")
        return False
    if price > config.MAX_ENTRY_PRICE:
        _skip("entry_price_too_high")
        return False
    if hours < config.MIN_HOURS_TO_RESOLUTION:
        _skip("too_close_to_resolution")
        return False
    if bucket.get(depth_key, 0) < 10:
        _skip("low_liquidity")
        return False
    if entry_count >= config.MAX_ENTRIES_PER_BUCKET:
        _skip("bucket_already_traded_today")
        return False
    return True


def _maker_exists(market_id: str, bucket_label: str) -> bool:
    return any(o for o in db.get_maker_orders("pending") if o["market_id"] == market_id and o["bucket_label"] == bucket_label)


def _cities_for_scan() -> list[dict]:
    cities = {city["name"].lower(): city for city in config.CITIES}
    for city in discover_weather_cities():
        cities[city["name"].lower()] = city
    return list(cities.values())


def _forecast_for_market(forecast_data: dict, market: dict) -> float:
    text = f"{market.get('question', '')} {market.get('raw', {}).get('slug', '')}".lower()
    if "lowest temperature" in text or "low temperature" in text:
        return float(forecast_data.get("tomorrow_min", forecast_data["tomorrow"]))
    return float(forecast_data["tomorrow"])


def find_all_signals() -> list[dict]:
    db.init_db()
    reset_scan_diagnostics()
    LAST_SKIP_REASONS.clear()
    signals = []
    forecast_ok = 0
    forecast_failed = 0
    markets_seen = 0
    buckets_seen = 0
    demo_skipped_existing_entry = 0
    dns = check_network()
    failed_hosts = {host: status for host, status in dns.items() if str(status).startswith("DNS_FAIL")}
    if failed_hosts:
        global LAST_SIGNALS
        global LAST_SCAN_SUMMARY
        LAST_SIGNALS = []
        LAST_SCAN_SUMMARY = {
            "signals": 0,
            "forecast_ok": 0,
            "forecast_failed": len(config.CITIES),
            "markets_seen": 0,
            "buckets_seen": 0,
            "gamma_markets_total": 0,
            "weather_candidates": 0,
            "parsed_buckets": 0,
            "last_network_error": f"DNS failed for: {', '.join(failed_hosts)}",
            "dns": dns,
        }
        log.error(f"Network/DNS preflight failed: {failed_hosts}")
        return []
    cities = _cities_for_scan()
    planned_position_keys: set[tuple[str, str]] = set()
    planned_bucket_keys: set[tuple[str, str]] = set()
    for city in cities:
        try:
            forecast_data = get_gfs_forecast(city)
            forecast_ok += 1
            base_std = config.FORECAST_STD_DEV_F if city["unit"] == "F" else config.FORECAST_STD_DEV_C
            markets = get_weather_markets_with_retry(city)
            if not markets and config.TRADING_MODE == "paper" and config.PAPER_DEMO_MODE and city["name"] == "New York":
                forecast = _forecast_for_market(forecast_data, {"question": "highest temperature", "raw": {}})
                markets = [make_demo_weather_market(city, forecast)]
        except Exception as exc:
            forecast_failed += 1
            log.warning(f"Skipping {city['name']}: {exc}")
            continue
        markets_seen += len(markets)
        market_groups: dict[str, list[tuple[dict, dict, float, float, float]]] = {}
        for market in markets:
            forecast = _forecast_for_market(forecast_data, market)
            time_ok, hours, time_reason = _market_time_ok(city, market)
            if not time_ok:
                _skip(time_reason)
                continue
            std = _dynamic_std(base_std, hours)
            buckets_seen += len(market["buckets"])
            for bucket in market["buckets"]:
                model_prob = bucket_probability(bucket, forecast, std)
                bucket["raw_model_prob"] = model_prob
                market_groups.setdefault(_market_group_key(market), []).append((market, bucket, model_prob, forecast, hours))
        for group_key, group_items in market_groups.items():
            normalized = normalize_probabilities([(bucket, prob) for _, bucket, prob, _, _ in group_items])
            group_signals = []
            for (market, bucket, _prob, forecast, hours), (_bucket, normalized_prob) in zip(group_items, normalized, strict=False):
                std = _dynamic_std(base_std, hours)
                signal = _signal_from_side(city, market, bucket, forecast, normalized_prob, hours, std)
                if signal:
                    group_signals.append(signal)
            group_signals.sort(key=lambda item: item["ev"], reverse=True)
            for signal in group_signals[: config.MAX_TRADES_PER_MARKET]:
                side = "yes" if signal["type"] == config.SignalType.EDGE_YES else "no"
                bucket = signal["bucket"]
                market_id = signal["market_id"]
                bucket_key = (market_id, bucket["label"])
                position_key = (market_id, side)
                market_side_key = (group_key, side)
                if bucket_key in planned_bucket_keys:
                    _skip("duplicate_bucket_in_scan")
                    continue
                if position_key in planned_position_keys:
                    _skip("duplicate_position_in_scan")
                    continue
                if market_side_key in planned_position_keys:
                    _skip("duplicate_market_side_in_scan")
                    continue
                signals.append(signal)
                planned_position_keys.add(position_key)
                planned_position_keys.add(market_side_key)
                planned_bucket_keys.add(bucket_key)

    for signal in signals:
        signal["ladder_signals"] = [
            {"bucket_label": other["bucket"]["label"], "effective_edge": other["effective_edge"]}
            for other in signals
            if other is not signal
            and other["city"] == signal["city"]
            and other["market_id"] == signal["market_id"]
            and other["type"] == signal["type"]
            and other["effective_edge"] >= 0.08
        ][:3]
    signals.sort(key=lambda item: item.get("ev", item.get("effective_edge", 0)), reverse=True)
    raw_signal_count = len(signals)
    selected = []
    selected_city_dates: set[str] = set()
    selected_city_counts: dict[str, int] = {}
    selected_city_exposure: dict[str, float] = {city["name"]: db.get_open_city_exposure(city["name"]) for city in cities}
    for signal in signals:
        city = signal["city"]
        city_date_key = signal.get("city_date_key", city)
        if city_date_key in selected_city_dates:
            _skip("city_date_trade_cap")
            continue
        if selected_city_counts.get(city, 0) >= config.MAX_TRADES_PER_CITY:
            _skip("city_trade_cap")
            continue
        if selected_city_exposure.get(city, 0.0) >= config.MAX_CITY_EXPOSURE_USD:
            _skip("city_exposure_cap")
            continue
        selected.append(signal)
        selected_city_dates.add(city_date_key)
        selected_city_counts[city] = selected_city_counts.get(city, 0) + 1
        selected_city_exposure[city] = selected_city_exposure.get(city, 0.0) + config.MAX_TRADE_SIZE_USD
        if len(selected) >= config.MAX_TRADES_PER_SCAN:
            break
    signals = selected
    LAST_SIGNALS = signals
    LAST_SCAN_SUMMARY = {
        "signals": len(signals),
        "raw_signals_before_cap": raw_signal_count,
        "max_trades_per_scan": config.MAX_TRADES_PER_SCAN,
        "skip_reasons": dict(sorted(LAST_SKIP_REASONS.items())),
        "cities_scanned": [city["name"] for city in cities],
        "dynamic_cities": API_STATUS.get("last_discovered_cities", []),
        "forecast_ok": forecast_ok,
        "forecast_failed": forecast_failed,
        "markets_seen": markets_seen,
        "buckets_seen": buckets_seen,
        "demo_skipped_existing_entry": demo_skipped_existing_entry,
        "gamma_markets_total": API_STATUS.get("last_market_count", 0),
        "temperature_page_slugs": API_STATUS.get("last_temperature_page_slugs", 0),
        "generated_temperature_slugs": API_STATUS.get("last_generated_temperature_slugs", 0),
        "temperature_markets_loaded": API_STATUS.get("last_temperature_page_markets", 0),
        "weather_keyset_markets": API_STATUS.get("last_weather_keyset_markets", 0),
        "weather_candidates": API_STATUS.get("last_weather_candidates", 0),
        "parsed_buckets": API_STATUS.get("last_bucket_count", 0),
        "last_network_error": API_STATUS.get("last_network_error"),
        "paper_demo_mode": config.PAPER_DEMO_MODE,
        "dns": dns if forecast_failed == len(config.CITIES) else {},
    }
    if not signals:
        log.info(f"No signals. Scan summary: {LAST_SCAN_SUMMARY}")
    elif LAST_SKIP_REASONS:
        log.info(f"Scan skip summary: {dict(sorted(LAST_SKIP_REASONS.items()))}")
    return signals

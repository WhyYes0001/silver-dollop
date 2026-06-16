from __future__ import annotations

import json
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from loguru import logger as log

from . import config

FORECAST_CACHE: dict[str, tuple[float, dict]] = {}
MARKET_CACHE: tuple[float, list[dict]] | None = None
GEOCODE_CACHE: dict[str, dict | None] = {}

WEATHER_WORDS = ("temperature", "highest temperature", "lowest temperature", "high temperature", "low temperature", "weather")
TEMP_WORDS = ("temperature", "highest temperature", "lowest temperature", "high temperature", "low temperature", "degrees", "fahrenheit", "celsius")
API_STATUS: dict[str, Any] = {
    "last_network_error": None,
    "last_market_count": 0,
    "last_temperature_page_markets": 0,
    "last_temperature_page_slugs": 0,
    "last_generated_temperature_slugs": 0,
    "last_weather_keyset_markets": 0,
    "last_weather_candidates": 0,
    "last_bucket_count": 0,
    "last_discovered_cities": [],
    "last_scan_at": None,
}


class MarketDataError(RuntimeError):
    pass


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC) + timedelta(days=1)
    value = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC) + timedelta(days=1)


def _request_json(url: str, *, params: dict | None = None, timeout: int = 20) -> Any:
    try:
        response = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": "weather-paper-bot/1.0"})
        response.raise_for_status()
        API_STATUS["last_network_error"] = None
        return response.json()
    except requests.exceptions.RequestException as exc:
        API_STATUS["last_network_error"] = str(exc)
        raise


def check_network() -> dict[str, str]:
    result = {}
    for host in ("api.open-meteo.com", "geocoding-api.open-meteo.com", "gamma-api.polymarket.com", "clob.polymarket.com"):
        try:
            result[host] = socket.gethostbyname(host)
        except OSError as exc:
            result[host] = f"DNS_FAIL: {exc}"
    return result


def get_gfs_forecast(city: dict, days: int = 2) -> dict:
    key = f"{city['name']}:{days}"
    cached = FORECAST_CACHE.get(key)
    if cached and time.time() - cached[0] < 1800:
        return cached[1]
    unit = "fahrenheit" if city["unit"] == "F" else "celsius"
    params = {
        "latitude": city["lat"],
        "longitude": city["lon"],
        "models": "gfs_seamless",
        "daily": "temperature_2m_max,temperature_2m_min",
        "temperature_unit": unit,
        "forecast_days": max(days + 1, 3),
        "timezone": "UTC",
    }
    data = _request_json("https://api.open-meteo.com/v1/forecast", params=params, timeout=20)
    daily = data.get("daily", {})
    max_temps = daily.get("temperature_2m_max", [])
    min_temps = daily.get("temperature_2m_min", [])
    if len(max_temps) < 3 or len(min_temps) < 3:
        raise MarketDataError(f"Open-Meteo returned too few forecast days for {city['name']}")
    result = {
        "tomorrow": float(max_temps[1]),
        "tomorrow_min": float(min_temps[1]),
        "day_after": float(max_temps[2]),
        "day_after_min": float(min_temps[2]),
        "fetched_at": datetime.now(UTC),
    }
    FORECAST_CACHE[key] = (time.time(), result)
    return result


def _loads(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def parse_temp_range(label: str) -> tuple[float, float]:
    text = label.lower().replace("deg", "").replace("degrees", "").replace("\u00b0", "")
    range_match = re.search(r"(-?\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*(-?\d+(?:\.\d+)?)", text)
    if range_match:
        first = float(range_match.group(1))
        second = float(range_match.group(2))
        return min(first, second), max(first, second)
    nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]
    if not nums:
        return float("-inf"), float("inf")
    if ("between" in text or "from" in text) and len(nums) >= 2:
        return min(nums[0], nums[1]), max(nums[0], nums[1])
    if "below" in text or "under" in text or "less than" in text or "lower" in text:
        return float("-inf"), nums[0]
    if "above" in text or "over" in text or "greater than" in text or "higher" in text:
        return nums[0], float("inf")
    return nums[0] - 0.5, nums[0] + 0.5


def _levels(levels: list | None, reverse: bool = False) -> tuple[float, float]:
    total = 0.0
    best = 0.0
    if not levels:
        return best, total
    for level in levels:
        price = float(level.get("price", level[0] if isinstance(level, list) else 0) or 0)
        size = float(level.get("size", level.get("shares", level[1] if isinstance(level, list) and len(level) > 1 else 0)) or 0)
        total += size
        if best == 0:
            best = price
        elif reverse:
            best = max(best, price)
        else:
            best = min(best, price)
    return best, total


def get_orderbook_depth(token_id: str) -> dict:
    if not token_id:
        return {"best_bid": 0.0, "best_ask": 0.0, "mid": 0.0, "bid_depth_shares": 0.0, "ask_depth_shares": 0.0}
    if token_id.startswith("demo_"):
        return {"best_bid": 0.04, "best_ask": 0.05, "mid": 0.045, "bid_depth_shares": 1000.0, "ask_depth_shares": 1000.0}
    data = _request_json("https://clob.polymarket.com/book", params={"token_id": token_id}, timeout=15)
    best_bid, bid_depth = _levels(data.get("bids"), reverse=True)
    best_ask, ask_depth = _levels(data.get("asks"), reverse=False)
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else best_ask or best_bid
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "bid_depth_shares": bid_depth,
        "ask_depth_shares": ask_depth,
    }


def _outcomes(market: dict) -> list[str]:
    raw = _loads(market.get("outcomes") or [])
    if isinstance(raw, list):
        return [str(x.get("name", x)) if isinstance(x, dict) else str(x) for x in raw]
    return []


def _prices(market: dict) -> list[float]:
    raw = _loads(market.get("outcomePrices") or [])
    if not isinstance(raw, list):
        return []
    prices = []
    for item in raw:
        try:
            prices.append(float(item))
        except (TypeError, ValueError):
            prices.append(0.0)
    return prices


def _tokens(market: dict) -> list[str]:
    raw = _loads(market.get("clobTokenIds") or market.get("clob_token_ids") or [])
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def _market_identity_text(market: dict) -> str:
    pieces = [market.get("question", ""), market.get("title", ""), market.get("slug", "")]
    event = market.get("event") or {}
    if isinstance(event, dict):
        pieces.extend([event.get("title", ""), event.get("slug", "")])
    return " ".join(str(p) for p in pieces if p).lower()


def _fetch_gamma_markets() -> list[dict]:
    global MARKET_CACHE
    if MARKET_CACHE and time.time() - MARKET_CACHE[0] < 300:
        return MARKET_CACHE[1]

    markets: list[dict] = []
    seen: set[str] = set()

    for market in [*_fetch_temperature_page_markets(), *_fetch_weather_keyset_markets()]:
        key = str(market.get("id") or market.get("conditionId") or market.get("slug"))
        if key and key not in seen:
            markets.append(market)
            seen.add(key)
    present_city_slugs = {
        city_slug
        for market in markets
        if (city_slug := _city_slug_from_market(market))
    }
    for market in _fetch_generated_temperature_markets(present_city_slugs):
        key = str(market.get("id") or market.get("conditionId") or market.get("slug"))
        if key and key not in seen:
            markets.append(market)
            seen.add(key)
    if markets:
        API_STATUS["last_market_count"] = len(markets)
        MARKET_CACHE = (time.time(), markets)
        return markets
    for endpoint in ("markets", "events"):
        fetched = 0
        while fetched < config.GAMMA_MAX_MARKETS:
            limit = min(100, config.GAMMA_MAX_MARKETS - fetched)
            data = _request_json(
                f"https://gamma-api.polymarket.com/{endpoint}",
                params={"active": "true", "closed": "false", "limit": limit, "offset": fetched},
                timeout=25,
            )
            items = data.get("data", data) if isinstance(data, dict) else data
            if not isinstance(items, list) or not items:
                break
            for item in items:
                nested = item.get("markets") if endpoint == "events" and isinstance(item, dict) else None
                candidates = nested if isinstance(nested, list) else [item]
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    market = {**candidate, "event": item} if isinstance(nested, list) else candidate
                    key = str(market.get("id") or market.get("conditionId") or market.get("slug"))
                    if key and key not in seen:
                        markets.append(market)
                        seen.add(key)
            fetched += len(items)
            if len(items) < limit:
                break

    API_STATUS["last_market_count"] = len(markets)
    MARKET_CACHE = (time.time(), markets)
    return markets


def _fetch_temperature_page_markets() -> list[dict]:
    if config.TEMPERATURE_PAGE_MAX_MARKETS <= 0:
        return []
    try:
        html = requests.get(
            "https://polymarket.com/predictions/temperature",
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 weather-paper-bot/1.0"},
        ).text
    except requests.exceptions.RequestException as exc:
        API_STATUS["last_network_error"] = str(exc)
        log.warning(f"Could not fetch Polymarket temperature page: {exc}")
        return []

    slugs: list[str] = []
    seen_slugs: set[str] = set()
    pattern = re.compile(r'((?:highest|lowest)-temperature-in-[a-z0-9-]+)')
    bucket_suffix = re.compile(r"(?:\d+(?:-\d+)?(?:f|c)(?:or(?:higher|below))?)$")
    for slug in pattern.findall(html):
        if not bucket_suffix.search(slug):
            continue
        if slug not in seen_slugs:
            slugs.append(slug)
            seen_slugs.add(slug)
        if len(slugs) >= config.TEMPERATURE_PAGE_MAX_MARKETS:
            break
    page_slug_count = len(slugs)

    hydrated = _hydrate_temperature_slugs(slugs)

    API_STATUS["last_temperature_page_markets"] = len(hydrated)
    API_STATUS["last_temperature_page_slugs"] = page_slug_count
    return hydrated


def _fetch_weather_keyset_markets() -> list[dict]:
    markets: list[dict] = []
    cursor = None
    seen_pages: set[tuple[str, ...]] = set()
    seen_markets: set[str] = set()
    for _ in range(max(0, config.WEATHER_KEYSET_PAGES)):
        params = {"tag_slug": "weather", "closed": "false", "limit": "100"}
        if cursor:
            params["cursor"] = cursor
        try:
            data = _request_json("https://gamma-api.polymarket.com/events/keyset", params=params, timeout=20)
        except Exception as exc:
            log.debug(f"Could not fetch weather keyset page: {exc}")
            break
        events = data.get("events") if isinstance(data, dict) else []
        if not isinstance(events, list) or not events:
            break
        page_key = tuple(str(event.get("slug") or event.get("id")) for event in events if isinstance(event, dict))
        if page_key in seen_pages:
            break
        seen_pages.add(page_key)
        for event in events:
            nested = event.get("markets") if isinstance(event, dict) else None
            if not isinstance(nested, list):
                continue
            for candidate in nested:
                if not isinstance(candidate, dict):
                    continue
                market = {**candidate, "event": event}
                key = str(market.get("id") or market.get("conditionId") or market.get("slug"))
                if key in seen_markets:
                    continue
                if market.get("active") and not market.get("closed") and _is_weather_market(market):
                    seen_markets.add(key)
                    markets.append(market)
        cursor = data.get("next_cursor") if isinstance(data, dict) else None
        if not cursor:
            break
    API_STATUS["last_weather_keyset_markets"] = len(markets)
    return markets


def _city_slug_from_market(market: dict) -> str | None:
    slug = str(market.get("slug") or "")
    match = re.search(r"(?:highest|lowest)-temperature-in-(.+?)-on-", slug)
    return match.group(1) if match else None


def _fetch_generated_temperature_markets(existing_city_slugs: set[str]) -> list[dict]:
    slugs = _generated_temperature_slugs()
    API_STATUS["last_generated_temperature_slugs"] = len(slugs)
    return _hydrate_temperature_slugs(slugs)


def _hydrate_temperature_slugs(slugs: list[str]) -> list[dict]:
    def hydrate(slug: str) -> dict | None:
        try:
            response = requests.get(
                f"https://gamma-api.polymarket.com/markets/slug/{slug}",
                timeout=config.TEMPERATURE_MARKET_HYDRATE_TIMEOUT,
                headers={"User-Agent": "weather-paper-bot/1.0"},
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            market = response.json()
            API_STATUS["last_network_error"] = None
        except requests.exceptions.RequestException as exc:
            if getattr(getattr(exc, "response", None), "status_code", None) != 404:
                log.debug(f"Could not hydrate temperature market {slug}: {exc}")
            return None
        if isinstance(market, dict) and market.get("active") and not market.get("closed"):
            return market
        return None

    hydrated = []
    workers = max(1, config.TEMPERATURE_MARKET_HYDRATE_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(hydrate, slug) for slug in slugs]
        for future in as_completed(futures):
            market = future.result()
            if market:
                hydrated.append(market)
    return hydrated


def _generated_temperature_slugs(existing_city_slugs: set[str] | None = None) -> list[str]:
    city_buckets = {
        "seoul": ["25c", "26c", "28c", "29c", "30c", "32corhigher"],
        "beijing": ["25corbelow", "27c", "28c", "29c", "30c"],
        "paris": ["27c", "28c", "29c", "30c", "31c", "32c"],
        "wellington": ["10c", "11c", "12c", "13c", "14c"],
        "london": ["20c", "21c", "22c", "23c", "24c", "25c", "24corhigher", "15c", "16c"],
        "taipei": ["27c", "28c", "29c", "30c", "32c", "34corhigher", "31corhigher"],
        "hong-kong": ["27c", "28c", "29c", "30c", "31c", "22corbelow", "23c", "24c", "25c", "26c"],
        "chengdu": ["27corbelow", "28c", "29c", "30c"],
        "shenzhen": ["27c", "28c", "30c", "31c", "32corhigher"],
        "tokyo": ["20c", "21c", "22c", "23c", "24c", "17c", "18c"],
        "shanghai": ["22c", "23c", "24c", "25c", "26c"],
        "chongqing": ["25corbelow", "26c", "28corbelow", "29c", "30c", "31c", "32c"],
        "munich": ["20c", "21c", "22c", "23c", "24c", "26c", "27c"],
        "helsinki": ["14c", "15c", "16c", "17c", "18c", "19c", "20c"],
        "lucknow": ["37c", "38c", "39c", "40c", "41c"],
        "madrid": ["30c", "32c", "33c", "34c", "35c"],
        "seattle": ["68-69f", "70-71f", "72-73f", "74-75f"],
        "kuala-lumpur": ["30c", "31c", "32c", "33c", "34c"],
        "milan": ["27c", "28c", "29c", "30c"],
        "karachi": ["32c", "33c", "34c", "35c"],
        "warsaw": ["18c", "19c", "20c", "21c"],
        "jeddah": ["33c", "34c", "36c", "37c", "38c", "38corhigher"],
        "amsterdam": ["15c", "16c", "17c", "18c", "19c", "23c"],
        "miami": ["88-89f", "90-91f", "92-93f", "94-95f", "78-79f"],
        "singapore": ["29c", "30c", "31c", "32c", "33c"],
        "atlanta": ["74-75f", "76-77f", "78-79f"],
        "wuhan": ["31c", "32c", "33c", "34c", "35c"],
        "guangzhou": ["29c", "30c", "31c", "32c"],
        "ankara": ["23c", "24c", "25c", "26c", "27c", "28c"],
        "istanbul": ["24c", "25c", "26c", "27c"],
        "tel-aviv": ["28c", "29c", "30c", "31c"],
        "san-francisco": ["68-69f", "70-71f", "72-73f"],
        "busan": ["27c", "28c", "29c", "30c", "31c"],
        "los-angeles": ["68-69f", "70-71f", "72-73f"],
        "new-york": ["78-79f", "80-81f", "60-61f"],
        "manila": ["32c", "33c", "34c", "35c"],
        "buenos-aires": ["16c", "17c", "18c", "19c"],
        "austin": ["76-77f", "78-79f", "80-81f", "84-85f", "88-89f"],
        "qingdao": ["26c", "27c", "28c", "29c"],
        "denver": ["88-89f", "90-91f", "92-93f"],
        "moscow": ["16c", "17c", "18c", "19c"],
        "cape-town": ["18c", "19c", "20c"],
        "dallas": ["86-87f", "88-89f", "90-91f"],
        "houston": ["82-83f", "84-85f", "86-87f"],
        "chicago": ["72-73f", "74-75f", "76-77f"],
        "sao-paulo": ["15c", "16c", "17c", "18c", "19c"],
        "panama-city": ["30c", "31c", "32c"],
        "toronto": ["21c", "22c", "23c", "24c"],
        "mexico-city": ["24c", "25c", "26c", "27c"],
    }
    low_buckets = {
        "paris": ["18c", "19c"],
        "london": ["15c", "16c"],
        "hong-kong": ["25c", "26c"],
        "tokyo": ["17c", "18c"],
        "shanghai": ["22c", "23c"],
        "miami": ["78-79f", "80-81f"],
        "new-york": ["60-61f", "62-63f"],
    }
    now = datetime.now(UTC)
    days = max(1, config.TEMPERATURE_GENERATED_DAYS)
    dates = [f"{(now + timedelta(days=o)).strftime('%B').lower()}-{(now + timedelta(days=o)).day}-{(now + timedelta(days=o)).year}" for o in range(days)]

    slugs = []
    for date_slug in dates:
        for city, buckets in city_buckets.items():
            for bucket in buckets:
                slugs.append(f"highest-temperature-in-{city}-on-{date_slug}-{bucket}")
            for bucket in low_buckets.get(city, []):
                slugs.append(f"lowest-temperature-in-{city}-on-{date_slug}-{bucket}")
    return slugs


def _is_weather_market(market: dict) -> bool:
    text = _market_identity_text(market)
    has_weather_word = any(word in text for word in WEATHER_WORDS)
    has_temp_word = any(word in text for word in TEMP_WORDS)
    has_temp_unit = bool(re.search(r"\b-?\d+(?:\.\d+)?\s*(?:f|c)\b", text))
    return has_weather_word and (has_temp_word or has_temp_unit)


def _is_city_market(city: dict, market: dict) -> bool:
    text = _market_identity_text(market)
    city_terms = {city["name"].lower(), city.get("polymarket_tag", "").replace("-", " ")}
    station = city.get("station_note", "").lower()
    if station:
        city_terms.add(station)
    return any(term and term in text for term in city_terms) and _is_weather_market(market)


def _clean_city_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" ?.,:;-")
    value = re.sub(r"\b(?:high|temperature|weather|forecast|today|tomorrow|on|by|be|reach|hit|exceed).*$", "", value, flags=re.I).strip(" ?.,:;-")
    return value


def extract_city_name_from_market(market: dict) -> str | None:
    text = str(market.get("question") or market.get("title") or "")
    patterns = [
        r"\b(?:temperature|highest temperature|lowest temperature|high temperature|low temperature|weather)\s+in\s+([A-Z][A-Za-z .'-]{2,60}?)(?:\s+(?:be|on|by|reach|hit|exceed|go|at|today|tomorrow)|[?])",
        r"\bin\s+([A-Z][A-Za-z .'-]{2,60}?)(?:\s+(?:be|on|by|reach|hit|exceed|go|at|today|tomorrow)|[?])",
        r"^Will\s+([A-Z][A-Za-z .'-]{2,60}?)(?:'s)?\s+(?:high|low|temperature|weather)\b",
        r"^([A-Z][A-Za-z .'-]{2,60}?)(?:'s)?\s+(?:high|low|temperature|weather)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        city = _clean_city_name(match.group(1))
        if 2 <= len(city) <= 60 and not re.search(r"\b(will|the|a|an|this|market)\b", city, re.I):
            return city

    slug = str(market.get("slug") or "").replace("-", " ")
    slug_match = re.search(r"(?:temperature|weather|high|low).*?\bin\s+([a-z][a-z ]{2,60})(?:\s+(?:on|by|above|below|between)|$)", slug)
    if slug_match:
        return _clean_city_name(slug_match.group(1)).title()
    return None


def _geocode_city(name: str) -> dict | None:
    key = name.lower()
    if key in GEOCODE_CACHE:
        return GEOCODE_CACHE[key]
    data = _request_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": name, "count": 1, "language": "en", "format": "json"},
        timeout=15,
    )
    results = data.get("results") or []
    if not results:
        GEOCODE_CACHE[key] = None
        return None
    item = results[0]
    city_name = str(item.get("name") or name)
    country_code = str(item.get("country_code") or "").upper()
    city = {
        "name": city_name,
        "lat": float(item["latitude"]),
        "lon": float(item["longitude"]),
        "unit": "F" if country_code in {"US", "BS", "BZ", "KY", "PW"} else "C",
        "polymarket_tag": re.sub(r"[^a-z0-9]+", "-", city_name.lower()).strip("-"),
        "station_note": str(item.get("admin1") or item.get("country") or "dynamic"),
        "timezone": str(item.get("timezone") or "UTC"),
        "source": "dynamic",
    }
    GEOCODE_CACHE[key] = city
    return city


def discover_weather_cities() -> list[dict]:
    if not config.DYNAMIC_CITY_DISCOVERY:
        return []
    discovered: dict[str, dict] = {}
    for market in _fetch_gamma_markets():
        if not _is_weather_market(market):
            continue
        name = extract_city_name_from_market(market)
        if not name:
            continue
        city = _geocode_city(name)
        if city:
            discovered[city["name"].lower()] = city
    API_STATUS["last_discovered_cities"] = [city["name"] for city in discovered.values()]
    return list(discovered.values())


def _binary_bucket_from_market(city: dict, market: dict) -> dict | None:
    outcomes = [x.lower() for x in _outcomes(market)]
    if outcomes[:2] != ["yes", "no"]:
        return None

    question = str(market.get("question") or market.get("title") or "")
    text = _market_identity_text(market)
    if not any(word in text for word in TEMP_WORDS) and not re.search(r"\b-?\d+(?:\.\d+)?\s*(?:f|c)\b", text):
        return None

    prices = _prices(market)
    tokens = _tokens(market)
    yes_token = tokens[0] if len(tokens) > 0 else ""
    no_token = tokens[1] if len(tokens) > 1 else ""
    yes_price = prices[0] if len(prices) > 0 else 0.0
    no_price = prices[1] if len(prices) > 1 else max(0.0, 1.0 - yes_price)

    if config.FETCH_ORDERBOOK_DEPTH:
        yes_depth = get_orderbook_depth(yes_token) if yes_token else {}
        no_depth = get_orderbook_depth(no_token) if no_token else {}
        yes_price = float(yes_depth.get("best_ask") or yes_price or 0)
        no_price = float(no_depth.get("best_ask") or no_price or max(0.0, 1.0 - yes_price))
        yes_best_bid = float(yes_depth.get("best_bid") or market.get("bestBid") or 0)
        yes_mid = float(yes_depth.get("mid") or yes_price)
        yes_bid_depth = float(yes_depth.get("bid_depth_shares") or 0)
        yes_ask_depth = float(yes_depth.get("ask_depth_shares") or 0)
        no_best_bid = float(no_depth.get("best_bid") or 0)
        no_ask_depth = float(no_depth.get("ask_depth_shares") or 0)
    else:
        yes_price = float(market.get("bestAsk") or yes_price or 0)
        yes_best_bid = float(market.get("bestBid") or 0)
        no_price = float(no_price or max(0.0, 1.0 - yes_best_bid if yes_best_bid else 1.0 - yes_price))
        yes_mid = (yes_price + yes_best_bid) / 2 if yes_price and yes_best_bid else yes_price
        liquidity = float(market.get("liquidityNum") or market.get("liquidity") or 0)
        yes_ask_depth = max(10.0, liquidity / max(yes_price, 0.01)) if liquidity else 100.0
        yes_bid_depth = max(10.0, liquidity / max(yes_best_bid or yes_price or 0.01, 0.01)) if liquidity else 100.0
        no_best_bid = max(0.0, 1.0 - yes_price) if yes_price else 0.0
        no_ask_depth = max(10.0, liquidity / max(no_price, 0.01)) if liquidity else 100.0

    low, high = parse_temp_range(question)
    return {
        "label": question,
        "low": low,
        "high": high,
        "token_id_yes": yes_token,
        "token_id_no": no_token,
        "yes_price": yes_price,
        "no_price": no_price,
        "best_bid": yes_best_bid,
        "best_ask": yes_price,
        "mid": yes_mid,
        "bid_depth_shares": yes_bid_depth,
        "ask_depth_shares": yes_ask_depth,
        "no_best_bid": no_best_bid,
        "no_best_ask": no_price,
        "no_ask_depth_shares": no_ask_depth,
    }


def get_weather_markets(city: dict) -> list[dict]:
    API_STATUS["last_scan_at"] = datetime.now(UTC).isoformat()
    all_markets = _fetch_gamma_markets()
    city_markets = [m for m in all_markets if _is_city_market(city, m)]
    API_STATUS["last_weather_candidates"] += len(city_markets)

    parsed = []
    for market in city_markets:
        bucket = _binary_bucket_from_market(city, market)
        if not bucket:
            continue
        parsed.append(
            {
                "id": str(market.get("id") or market.get("conditionId") or market.get("slug")),
                "question": str(market.get("question") or market.get("title") or ""),
                "resolution_dt": _parse_dt(market.get("endDate") or market.get("end_date")),
                "buckets": [bucket],
                "raw": market,
            }
        )

    API_STATUS["last_bucket_count"] += sum(len(m["buckets"]) for m in parsed)
    return parsed


def make_demo_weather_market(city: dict, forecast: float) -> dict:
    low = round(forecast - 1, 1)
    high = round(forecast + 1, 1)
    label = f"Demo: Will {city['name']} high temperature be between {low} and {high}{city['unit']}?"
    bucket = {
        "label": label,
        "low": low,
        "high": high,
        "token_id_yes": f"demo_yes_{city['polymarket_tag']}",
        "token_id_no": f"demo_no_{city['polymarket_tag']}",
        "yes_price": 0.05,
        "no_price": 0.95,
        "best_bid": 0.04,
        "best_ask": 0.05,
        "mid": 0.045,
        "bid_depth_shares": 1000.0,
        "ask_depth_shares": 1000.0,
        "no_best_bid": 0.94,
        "no_best_ask": 0.95,
        "no_ask_depth_shares": 1000.0,
        "is_demo": True,
    }
    return {
        "id": f"demo_weather_{city['polymarket_tag']}",
        "question": label,
        "resolution_dt": datetime.now(UTC) + timedelta(hours=24),
        "buckets": [bucket],
        "raw": {"demo": True},
        "is_demo": True,
    }


def reset_scan_diagnostics() -> None:
    API_STATUS.update(
        {
            "last_network_error": None,
            "last_market_count": 0,
            "last_temperature_page_markets": 0,
            "last_temperature_page_slugs": 0,
            "last_generated_temperature_slugs": 0,
            "last_weather_keyset_markets": 0,
            "last_weather_candidates": 0,
            "last_bucket_count": 0,
            "last_discovered_cities": [],
            "last_scan_at": datetime.now(UTC).isoformat(),
        }
    )


def get_weather_markets_with_retry(city: dict) -> list[dict]:
    try:
        return get_weather_markets(city)
    except Exception as exc:
        log.warning(f"Polymarket fetch failed for {city['name']}: {exc}; retrying once")
        time.sleep(5)
        return get_weather_markets(city)

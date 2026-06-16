from __future__ import annotations

from . import config, db


def ev_tier_size(ev: float) -> float:
    """Map edge quality to a small fixed stake before safety caps."""
    if ev >= 0.50:
        return config.EV_SIZE_LARGE_USD
    if ev >= 0.25:
        return config.EV_SIZE_MEDIUM_USD
    if ev >= config.MIN_EV_THRESHOLD:
        return config.EV_SIZE_SMALL_USD
    return 0.0


def calc_order_size_details(signal: dict) -> tuple[float, str]:
    db.init_db()
    if config.TRADING_MODE == "paper":
        bankroll = db.get_paper_balance()
    else:
        bankroll = config.MAX_DAILY_SPEND_USD
    remaining_daily_budget = max(0.0, config.MAX_DAILY_SPEND_USD - db.get_today_spend(config.TRADING_MODE))
    ev = max(0.0, float(signal.get("ev", signal.get("effective_edge", 0))))
    risk_cap = bankroll * config.MAX_RISK_PER_TRADE_PCT
    ev_size = ev_tier_size(ev)
    size = min(ev_size, risk_cap, config.MAX_TRADE_SIZE_USD, remaining_daily_budget)
    if remaining_daily_budget < config.MIN_TRADE_SIZE_USD:
        return 0.0, f"daily spend cap leaves only ${remaining_daily_budget:.2f}; minimum trade is ${config.MIN_TRADE_SIZE_USD:.2f}"
    if bankroll < config.MIN_TRADE_SIZE_USD:
        return 0.0, f"bankroll is only ${bankroll:.2f}; minimum trade is ${config.MIN_TRADE_SIZE_USD:.2f}"
    if ev <= 0:
        return 0.0, "signal EV is not positive"
    if ev < config.MIN_EV_THRESHOLD:
        return 0.0, f"EV {ev:.2f} is below threshold {config.MIN_EV_THRESHOLD:.2f}"
    if risk_cap < config.MIN_TRADE_SIZE_USD:
        return 0.0, f"risk cap is only ${risk_cap:.2f}; raise bankroll or MAX_RISK_PER_TRADE_PCT"
    if ev_size < config.MIN_TRADE_SIZE_USD:
        return 0.0, f"EV tier sizing is only ${ev_size:.2f}; lower MIN_TRADE_SIZE_USD or raise EV"
    if size < config.MIN_TRADE_SIZE_USD:
        return 0.0, f"computed size is only ${size:.2f}; minimum trade is ${config.MIN_TRADE_SIZE_USD:.2f}"
    return round(size, 2), "ok"


def calc_order_size(signal: dict) -> float:
    size, _ = calc_order_size_details(signal)
    return size


def execute_signal(signal: dict) -> list[dict]:
    side = "yes" if signal["type"] == config.SignalType.EDGE_YES else "no" if signal["type"] == config.SignalType.EDGE_NO else ""
    if side and db.has_open_position(signal["market_id"], side, signal["bucket"]["label"]):
        return []
    if config.TRADING_MODE == "paper":
        from . import paper_trader

        return paper_trader.execute(signal)
    if config.TRADING_MODE == "live":
        from . import live_trader

        return live_trader.execute(signal)
    raise ValueError(f"Unknown TRADING_MODE {config.TRADING_MODE}")

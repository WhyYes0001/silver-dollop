from __future__ import annotations

import uuid

from . import config, db
from .incentive_tracker import estimate_maker_rebate, log_incentive
from .order_engine import calc_order_size


def _record(signal: dict, side: str, token_id: str, fill_price: float, usd: float, arb_pair_id: str | None = None) -> dict:
    shares = usd / fill_price if fill_price > 0 else 0
    db.update_paper_balance(-usd)
    trade_id = db.log_trade(
        {
            "mode": "paper",
            "signal_type": signal["type"].value,
            "city": signal["city"],
            "market_id": signal["market_id"],
            "market_question": signal["market_question"],
            "bucket_label": signal["bucket"]["label"],
            "token_id": token_id,
            "side": side,
            "entry_price": fill_price,
            "shares": shares,
            "usd_spent": usd,
            "forecast_temp": signal["forecast_temp"],
            "model_prob": signal["model_prob"],
            "effective_edge": signal["effective_edge"],
            "arb_pair_id": arb_pair_id,
        }
    )
    rebate = estimate_maker_rebate(shares, fill_price)
    log_incentive("estimated", rebate, signal["market_id"], f"paper-{trade_id}")
    return {
        "trade_id": trade_id,
        "order_id": None,
        "mode": "paper",
        "side": side,
        "fill_price": fill_price,
        "shares": shares,
        "usd_spent": usd,
        "status": "filled",
        "rebate_estimate": rebate,
    }


def _buy_price(signal: dict, side: str) -> float:
    bucket = signal["bucket"]
    if side == "yes":
        return min(1.0, float(bucket.get("best_ask") or bucket.get("yes_price") or 0) + 0.01)
    return min(1.0, float(bucket.get("no_price") or (1 - float(bucket.get("best_bid") or 0))) + 0.01)


def execute(signal: dict) -> list[dict]:
    usd = calc_order_size(signal)
    if usd <= 0:
        return []
    bucket = signal["bucket"]
    if signal["type"] == config.SignalType.EDGE_YES:
        return [_record(signal, "yes", bucket["token_id_yes"], _buy_price(signal, "yes"), usd)]
    if signal["type"] == config.SignalType.EDGE_NO:
        return [_record(signal, "no", bucket["token_id_no"], _buy_price(signal, "no"), usd)]
    if signal["type"] == config.SignalType.ARBITRAGE:
        pair_id = str(uuid.uuid4())
        half = usd / 2
        return [
            _record(signal, "yes", bucket["token_id_yes"], _buy_price(signal, "yes"), half, pair_id),
            _record(signal, "no", bucket["token_id_no"], _buy_price(signal, "no"), half, pair_id),
        ]
    if signal["type"] == config.SignalType.MAKER:
        shares = usd / max(signal.get("maker_yes_price") or bucket.get("mid") or 0.01, 0.01)
        orders = []
        for side, price, token_id in (
            ("yes", signal.get("maker_yes_price", bucket.get("mid", 0.01)), bucket["token_id_yes"]),
            ("no", signal.get("maker_no_price", 1 - bucket.get("mid", 0.99)), bucket["token_id_no"]),
        ):
            maker_id = db.log_maker_order(
                {
                    "market_id": signal["market_id"],
                    "bucket_label": bucket["label"],
                    "token_id": token_id,
                    "side": side,
                    "quoted_price": price,
                    "shares": shares,
                }
            )
            orders.append({"maker_order_id": maker_id, "mode": "paper", "side": side, "fill_price": price, "shares": shares, "usd_spent": usd, "status": "pending"})
        return orders
    return []

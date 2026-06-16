from __future__ import annotations

import time
import uuid

from loguru import logger as log

from . import config, db
from .order_engine import calc_order_size


def _client():
    if not config.POLYGON_PRIVATE_KEY:
        raise RuntimeError("POLYGON_PRIVATE_KEY is required for live mode")
    from py_clob_client.client import ClobClient

    return ClobClient(host="https://clob.polymarket.com", key=config.POLYGON_PRIVATE_KEY, chain_id=137, signature_type=0)


def _place_limit(client, token_id: str, side: str, price: float, size: float):
    from py_clob_client.clob_types import OrderArgs, OrderType

    args = OrderArgs(price=price, size=size, side="BUY", token_id=token_id)
    signed = client.create_order(args)
    return client.post_order(signed, OrderType.GTC)


def _record(signal: dict, side: str, token_id: str, price: float, usd: float, order_id: str | None, arb_pair_id: str | None = None) -> dict:
    shares = usd / price if price > 0 else 0
    trade_id = db.log_trade(
        {
            "mode": "live",
            "signal_type": signal["type"].value,
            "city": signal["city"],
            "market_id": signal["market_id"],
            "market_question": signal["market_question"],
            "bucket_label": signal["bucket"]["label"],
            "token_id": token_id,
            "side": side,
            "entry_price": price,
            "shares": shares,
            "usd_spent": usd,
            "order_id": order_id,
            "forecast_temp": signal["forecast_temp"],
            "model_prob": signal["model_prob"],
            "effective_edge": signal["effective_edge"],
            "arb_pair_id": arb_pair_id,
        }
    )
    return {"trade_id": trade_id, "order_id": order_id, "mode": "live", "side": side, "fill_price": price, "shares": shares, "usd_spent": usd, "status": "submitted"}


def execute(signal: dict) -> list[dict]:
    client = _client()
    usd = calc_order_size(signal)
    bucket = signal["bucket"]
    orders = []
    try:
        if signal["type"] in {config.SignalType.EDGE_YES, config.SignalType.EDGE_NO}:
            side = "yes" if signal["type"] == config.SignalType.EDGE_YES else "no"
            token_id = bucket["token_id_yes"] if side == "yes" else bucket["token_id_no"]
            price = bucket.get("mid") if config.MAKER_ORDER_MODE else min(1.0, bucket.get("best_ask", bucket.get("yes_price", 0)) + 0.01)
            if side == "no" and not config.MAKER_ORDER_MODE:
                price = min(1.0, bucket.get("no_price", 0) + 0.01)
            size = usd / max(price, 0.01)
            response = _place_limit(client, token_id, side, price, size)
            time.sleep(5)
            order_id = str(response.get("orderID") or response.get("id") or "")
            return [_record(signal, side, token_id, price, usd, order_id)]

        if signal["type"] == config.SignalType.ARBITRAGE:
            pair_id = str(uuid.uuid4())
            half = usd / 2
            for side, token_id, price in (
                ("yes", bucket["token_id_yes"], min(1.0, bucket.get("best_ask", bucket.get("yes_price", 0)) + 0.01)),
                ("no", bucket["token_id_no"], min(1.0, bucket.get("no_price", 0) + 0.01)),
            ):
                response = _place_limit(client, token_id, side, price, half / max(price, 0.01))
                time.sleep(5)
                orders.append(_record(signal, side, token_id, price, half, str(response.get("orderID") or response.get("id") or ""), pair_id))
            return orders

        if signal["type"] == config.SignalType.MAKER:
            for side, token_id, price in (
                ("yes", bucket["token_id_yes"], signal.get("maker_yes_price", bucket.get("mid", 0.01))),
                ("no", bucket["token_id_no"], signal.get("maker_no_price", 1 - bucket.get("mid", 0.99))),
            ):
                response = _place_limit(client, token_id, side, price, usd / max(price, 0.01))
                maker_id = db.log_maker_order({"market_id": signal["market_id"], "bucket_label": bucket["label"], "token_id": token_id, "side": side, "quoted_price": price, "shares": usd / max(price, 0.01)})
                orders.append({"maker_order_id": maker_id, "order_id": str(response.get("orderID") or response.get("id") or ""), "mode": "live", "side": side, "fill_price": price, "status": "submitted"})
            return orders
    except Exception:
        log.exception("Live order placement failed")
        raise
    return []

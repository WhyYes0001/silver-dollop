from __future__ import annotations

from . import config, db


def estimate_maker_rebate(shares: float, price: float) -> float:
    return shares * config.INCENTIVE_REBATE_EST


def log_incentive(type: str, amount: float, market_id: str, order_id: str | None = None) -> None:
    db.log_incentive(type, amount, market_id, order_id)


def get_incentive_summary() -> dict:
    return db.get_incentive_summary()

from __future__ import annotations

from loguru import logger as log

from . import config, db


def check_kill_switch() -> bool:
    return config.KILL_SWITCH_PATH.exists()


def can_trade(amount_usd: float, mode: str = config.TRADING_MODE) -> tuple[bool, str]:
    db.init_db()
    if amount_usd <= 0:
        return False, "order size is zero"
    if check_kill_switch():
        return False, "kill switch is active"
    if amount_usd > config.MAX_TRADE_SIZE_USD:
        return False, "amount exceeds max trade size"
    if db.get_today_spend(mode) + amount_usd > config.MAX_DAILY_SPEND_USD:
        return False, "daily spend cap reached"
    if db.get_today_loss(mode) > config.MAX_DAILY_LOSS_USD:
        return False, "daily loss cap reached"
    if len(db.get_open_positions()) >= config.MAX_OPEN_POSITIONS:
        return False, "max open positions reached"
    if mode == "paper" and db.get_paper_balance() < amount_usd:
        return False, "paper balance too low"
    return True, "ok"


def record_mode_switch(from_mode: str, to_mode: str) -> None:
    log.warning(f"TRADING_MODE changed from {from_mode} to {to_mode}. Live mode can only be enabled via .env and restart.")

from __future__ import annotations

import asyncio
import sys

import schedule
from loguru import logger as log

from . import config, db, position_manager
from .notifier import build_application, send_daily_report, send_error_alert, send_exit_notification, send_merge_notification, send_scan_trade_summary, send_trade_notification
from .order_engine import calc_order_size_details, execute_signal
from .safety import can_trade, check_kill_switch
from .signal_engine import find_all_signals
import time

def main():
    print("Bot running...")
    # your existing code

if __name__ == "__main__":
    while True:
        try:
            main()
        except Exception as e:
            print("ERROR:", e)
        time.sleep(10)

def setup_logging() -> None:
    log.remove()
    log.add(sys.stderr, level="INFO")
    log.add("logs/bot.log", level="DEBUG", rotation="10 MB", retention=10)


async def run_scan() -> None:
    try:
        if check_kill_switch():
            log.info("Kill switch active; skipping scan")
            return
        signals = find_all_signals()
        log.info(f"Found {len(signals)} signals")
        blocked: dict[str, int] = {}
        placed = 0
        notified = 0
        for signal in signals:
            size, size_reason = calc_order_size_details(signal)
            ok, reason = can_trade(size)
            if not ok:
                detail = size_reason if reason == "order size is zero" else reason
                blocked[detail] = blocked.get(detail, 0) + 1
                if reason in {"daily spend cap reached", "max open positions reached", "paper balance too low"} or detail.startswith("daily spend cap leaves"):
                    log.info(f"Stopping scan early: {detail}")
                    break
                continue
            orders = execute_signal(signal)
            if orders:
                placed += len(orders)
                if notified < config.TELEGRAM_TRADE_NOTIFICATIONS_PER_SCAN:
                    await send_trade_notification(signal, orders, config.TRADING_MODE)
                    notified += 1
        if blocked:
            summary = "; ".join(f"{reason} ({count})" for reason, count in blocked.items())
            log.info(f"Blocked trade summary: {summary}")
        else:
            summary = ""
        if placed:
            log.info(f"Placed {placed} orders")
        await send_scan_trade_summary(placed, notified, summary)
        db.update_daily_summary(len(signals))
    except Exception as exc:
        await send_error_alert(str(exc))
        log.exception(f"Scan failed: {exc}")


async def check_positions() -> None:
    try:
        exits = position_manager.check_all_positions()
        for exit_result in exits:
            await send_exit_notification(exit_result["position"], exit_result)
            if exit_result.get("is_merge"):
                await send_merge_notification(exit_result["arb_pair"], exit_result)
    except Exception as exc:
        await send_error_alert(f"Position check failed: {exc}")
        log.exception(f"Position check failed: {exc}")


def _schedule_async(coro):
    def runner():
        asyncio.create_task(coro())

    return runner


async def scheduler_loop() -> None:
    schedule.every().day.at("00:30").do(_schedule_async(run_scan))
    schedule.every().day.at("06:30").do(_schedule_async(run_scan))
    schedule.every().day.at("12:30").do(_schedule_async(run_scan))
    schedule.every().day.at("18:30").do(_schedule_async(run_scan))
    if config.SCAN_INTERVAL_MINUTES > 0:
        schedule.every(config.SCAN_INTERVAL_MINUTES).minutes.do(_schedule_async(run_scan))
    schedule.every().day.at("09:00").do(_schedule_async(lambda: send_daily_report(config.TRADING_MODE)))
    schedule.every(30).minutes.do(_schedule_async(check_positions))
    while True:
        schedule.run_pending()
        await asyncio.sleep(1)


async def main() -> None:
    setup_logging()
    db.init_db()
    log.info(f"RUNNING IN {config.TRADING_MODE.upper()} MODE")
    app = build_application()
    tasks = []
    app_initialized = False
    try:
        if app and config.TRADING_MODE == "paper":
            try:
                await app.bot.send_message(
                    chat_id=config.TELEGRAM_CHAT_ID,
                    text="Bot started in PAPER MODE - no real trades will be placed",
                    read_timeout=20,
                    write_timeout=20,
                    connect_timeout=20,
                    pool_timeout=20,
                )
            except Exception as exc:
                log.warning(f"Telegram startup notification skipped: {exc}")
        tasks.append(asyncio.create_task(scheduler_loop()))
        if config.RUN_SCAN_ON_STARTUP:
            tasks.append(asyncio.create_task(run_scan()))
        if app:
            await app.initialize()
            app_initialized = True
            await app.start()
            await app.updater.start_polling()
            tasks.append(asyncio.create_task(asyncio.Event().wait()))
        else:
            log.warning("Telegram bot token not configured; scheduler will run without command handling")
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        log.info("Shutdown requested")
        raise
    finally:
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if app:
            updater = getattr(app, "updater", None)
            if updater and updater.running:
                await updater.stop()
            if app.running:
                await app.stop()
            if app_initialized:
                await app.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped")

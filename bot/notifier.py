from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from loguru import logger as log
from telegram import Update
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes

from . import config, db, signal_engine
from .scanner import check_network, discover_weather_cities
from .safety import check_kill_switch

APPLICATION: Application | None = None


async def _send(text: str) -> None:
    if not APPLICATION or not config.TELEGRAM_CHAT_ID:
        return
    try:
        await APPLICATION.bot.send_message(
            chat_id=config.TELEGRAM_CHAT_ID,
            text=text[:3900],
            read_timeout=20,
            write_timeout=20,
            connect_timeout=20,
            pool_timeout=20,
        )
    except RetryAfter as exc:
        log.warning(f"Telegram rate limited notification for {exc.retry_after}s; skipping message")
    except (TimedOut, NetworkError) as exc:
        log.warning(f"Telegram notification skipped: {exc}")
    except TelegramError as exc:
        log.warning(f"Telegram notification failed: {exc}")


def _type_value(signal: dict) -> str:
    value = signal.get("type")
    return value.value if hasattr(value, "value") else str(value)


async def send_trade_notification(signal: dict, orders: list[dict], mode: str) -> None:
    first = orders[0]
    bucket = signal["bucket"]
    msg = (
        f"{'PAPER TRADE' if mode == 'paper' else 'AUTO-TRADE EXECUTED'}\n"
        f"[MODE: {mode.upper()}]\n\n"
        f"{_type_value(signal)} - {signal['city']}\n"
        f"{signal['market_question']}\n\n"
        f"Bucket: {bucket['label']}\n"
        f"Market price: {signal.get('market_price', bucket.get('yes_price', 0)):.2f} | Model: {signal['model_prob']:.0%}\n"
        f"EV: {signal.get('ev', signal['effective_edge']):+.2f}\n"
        f"Size: ${sum(o.get('usd_spent', 0) for o in orders):.2f} @ {first.get('fill_price', 0):.2f}\n\n"
        f"GFS forecast: {signal['forecast_temp']:.1f}{signal['unit']}\n"
        f"Resolves: {signal['resolution_dt']}\n"
        f"Daily spend: ${db.get_today_spend(mode):.2f} / ${config.MAX_DAILY_SPEND_USD:.2f}"
    )
    await _send(msg)


async def send_exit_notification(position: dict, exit_result: dict) -> None:
    await _send(
        f"POSITION CLOSED [{exit_result.get('mode', config.TRADING_MODE).upper()}]\n\n"
        f"{exit_result['exit_reason']}: {position['bucket_label']} ({position.get('city', '')})\n"
        f"Entry: {position['entry_price']:.2f} -> Exit: {exit_result.get('exit_price', 0):.2f}\n"
        f"P&L: ${exit_result.get('pnl_usd', 0):+.2f}"
    )


async def send_merge_notification(arb_pair: dict, merge_result: dict) -> None:
    yes = arb_pair["yes"]
    no = arb_pair["no"]
    await _send(
        f"MERGE EXECUTED [{yes['mode'].upper()}]\n\n"
        f"{yes.get('city', '')} - {yes['bucket_label']}\n"
        f"YES {yes['shares']:.0f} @ {yes['entry_price']:.2f}\n"
        f"NO {no['shares']:.0f} @ {no['entry_price']:.2f}\n"
        f"Locked profit: ${merge_result.get('pnl_usd', 0):+.2f}"
    )


async def send_daily_report(mode: str = config.TRADING_MODE) -> None:
    pnl = db.get_pnl_summary(mode)
    incentives = db.get_incentive_summary()
    inc_today = sum(v["today"] for v in incentives.values())
    await _send(
        f"DAILY REPORT - {date.today().isoformat()} [{mode.upper()}]\n\n"
        f"Capital deployed: ${db.get_today_spend(mode):.2f}\n"
        f"Realized P&L: ${pnl['today_realized']:+.2f}\n"
        f"Unrealized P&L: ${pnl['unrealized']:+.2f}\n"
        f"Incentives today: ${inc_today:.2f}\n"
        f"Net P&L incl. incentives: ${pnl['today_realized'] + inc_today:+.2f}\n\n"
        f"Open value: ${pnl['open_value']:.2f}\n"
        f"All-time realized P&L: ${pnl['all_time_realized']:+.2f}\n"
        f"Mode: {mode.upper()}"
    )


async def send_error_alert(message: str) -> None:
    await _send(f"ERROR: {message}")


async def send_scan_trade_summary(placed: int, notified: int, blocked_summary: str = "") -> None:
    if placed <= notified and not blocked_summary:
        return
    lines = [f"SCAN SUMMARY [{config.TRADING_MODE.upper()}]", f"Orders placed: {placed}"]
    if placed > notified:
        lines.append(f"Trade notifications shown: {notified}; suppressed: {placed - notified}")
    if blocked_summary:
        lines.append(f"Blocked: {blocked_summary}")
    await _send("\n".join(lines))


async def _reply(update: Update, text: str) -> None:
    if update.message:
        try:
            await update.message.reply_text(text[:3900])
        except (TimedOut, NetworkError) as exc:
            log.warning(f"Telegram reply skipped: {exc}")
        except TelegramError as exc:
            log.warning(f"Telegram reply failed: {exc}")


async def _reply_lines(update: Update, header: str, lines: list[str]) -> None:
    if not lines:
        await _reply(update, header)
        return
    chunk = header
    for line in lines:
        next_chunk = f"{chunk}\n{line}" if chunk else line
        if len(next_chunk) > 3800:
            await _reply(update, chunk)
            chunk = line
        else:
            chunk = next_chunk
    if chunk:
        await _reply(update, chunk)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    pnl = db.get_pnl_summary(config.TRADING_MODE)
    await _reply(update, f"Mode: {config.TRADING_MODE.upper()}\nKill switch: {check_kill_switch()}\nOpen positions: {len(db.get_open_positions())}\nToday P&L: ${pnl['today_realized']:+.2f}")


async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.get_open_positions()
    if not rows:
        await _reply(update, "No open positions.")
        return
    lines = [
        f"#{p['trade_id']} {p['side']} {p.get('city', '')} entry {p['entry_price']:.3f} current {p['current_price']:.3f} | {p['bucket_label']}"
        for p in rows
    ]
    await _reply_lines(update, f"Open positions: {len(rows)}", lines)


async def pnl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    both = db.get_pnl_summary(None)
    lines = []
    for mode, item in both.items():
        lines.append(f"{mode.upper()}: today ${item['today_realized']:+.2f}, week ${item['week_realized']:+.2f}, all-time ${item['all_time_realized']:+.2f}, unrealized ${item['unrealized']:+.2f}")
    await _reply(update, "\n".join(lines))


async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not signal_engine.LAST_SIGNALS:
        summary = signal_engine.LAST_SCAN_SUMMARY or {}
        await _reply(update, "No signals from the latest scan.\n\n" + "\n".join(f"{k}: {v}" for k, v in summary.items()))
        return
    await _reply(update, "\n".join(f"{_type_value(s)} {s['city']} {s['bucket']['label']} EV {s.get('ev', s['effective_edge']):+.2f}" for s in signal_engine.LAST_SIGNALS[:25]))


async def arb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arbs = [s for s in signal_engine.LAST_SIGNALS if s["type"] == config.SignalType.ARBITRAGE]
    await _reply(update, "\n".join(f"{s['city']} {s['bucket']['label']} profit {s['effective_edge']:.2f}" for s in arbs[:25]) or "No arbitrage signals.")


async def maker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    orders = db.get_maker_orders()
    await _reply(update, "\n".join(f"#{o['id']} {o['status']} {o['side']} {o['bucket_label']} @ {o['quoted_price']:.2f}" for o in orders[:25]) or "No maker orders.")


async def paper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, f"Paper balance: ${db.get_paper_balance():.2f}\n{db.get_pnl_summary('paper')}")


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config.KILL_SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.KILL_SWITCH_PATH.write_text("paused", encoding="utf-8")
    await _reply(update, "Trading paused.")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if config.KILL_SWITCH_PATH.exists():
        config.KILL_SWITCH_PATH.unlink()
    await _reply(update, "Trading resumed.")


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = db.get_trade_history()
    await _reply(update, "\n".join(f"#{r['id']} {r['signal_type']} {r['side']} {r['status']} pnl ${r['pnl_usd']:+.2f}" for r in rows) or "No trades.")


async def show_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, "\n".join(f"{k}={v}" for k, v in config.redacted_config().items()))


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    summary = signal_engine.LAST_SCAN_SUMMARY or {}
    dns = check_network()
    lines = ["DNS/API:"]
    lines.extend(f"{host}: {status}" for host, status in dns.items())
    lines.append("")
    lines.append("Last scan:")
    if summary:
        lines.extend(f"{k}: {v}" for k, v in summary.items() if k != "dns")
    else:
        lines.append("No scan has completed in this process yet.")
    await _reply(update, "\n".join(lines))


async def cities(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    found = discover_weather_cities()
    if not found:
        await _reply(update, "No active weather-market cities discovered right now.")
        return
    await _reply(update, "\n".join(f"{city['name']} ({city['unit']}) - {city.get('station_note', 'dynamic')}" for city in found))


async def scan_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, "Starting scan now...")
    from .main import run_scan

    await run_scan()
    summary = signal_engine.LAST_SCAN_SUMMARY or {}
    await _reply(update, "Scan finished.\n" + "\n".join(f"{k}: {v}" for k, v in summary.items() if k != "dns"))


def build_application() -> Application | None:
    global APPLICATION
    if not config.TELEGRAM_BOT_TOKEN:
        return None
    APPLICATION = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    for name, handler in {
        "status": status,
        "positions": positions,
        "postions": positions,
        "pnl": pnl,
        "signals": signals,
        "arb": arb,
        "maker": maker,
        "paper": paper,
        "pause": pause,
        "resume": resume,
        "history": history,
        "config": show_config,
        "debug": debug,
        "cities": cities,
        "scan": scan_now,
    }.items():
        APPLICATION.add_handler(CommandHandler(name, handler))
    return APPLICATION

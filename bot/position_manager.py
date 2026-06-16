from __future__ import annotations

from collections import defaultdict

import requests
from loguru import logger as log

from . import config, db
from .scanner import get_orderbook_depth


def execute_exit(position: dict, reason: str) -> dict:
    depth = get_orderbook_depth(position["token_id"])
    best_bid = float(depth.get("best_bid") or 0)
    fallback = float(position.get("current_price") or position.get("entry_price") or 0)
    exit_price = max(0.0, (best_bid or fallback) - 0.01)
    closed = db.close_position(position["trade_id"], exit_price, reason)
    return {"position": position, "pnl_usd": closed["pnl_usd"], "exit_price": exit_price, "exit_reason": reason, "mode": position["mode"]}


def _merge_positions(positions: list[dict]) -> list[dict]:
    exits = []
    grouped = defaultdict(dict)
    for pos in positions:
        key = (pos["market_id"], pos["bucket_label"], pos.get("arb_pair_id"))
        grouped[key][pos["side"]] = pos
    for _, sides in grouped.items():
        yes = sides.get("yes")
        no = sides.get("no")
        if not yes or not no:
            continue
        merged_shares = min(yes["shares"], no["shares"])
        profit_per_pair = 1.0 - yes["entry_price"] - no["entry_price"]
        if merged_shares <= 0 or profit_per_pair <= 0:
            continue
        for pos in (yes, no):
            db.close_position(pos["trade_id"], 1.0 if pos["shares"] == merged_shares else pos["entry_price"], "merged", "merged")
        exits.append(
            {
                "position": yes,
                "is_merge": True,
                "arb_pair": {"yes": yes, "no": no},
                "pnl_usd": merged_shares * profit_per_pair,
                "exit_reason": "merged",
                "mode": yes["mode"],
            }
        )
    return exits


def check_all_positions() -> list[dict]:
    positions = db.get_open_positions()
    exits = _merge_positions(positions)
    skipped_no_bid = 0
    skipped_not_found = 0
    skipped_errors: dict[str, int] = {}
    merged_trade_ids = {e["arb_pair"]["yes"]["trade_id"] for e in exits if e.get("is_merge")}
    merged_trade_ids |= {e["arb_pair"]["no"]["trade_id"] for e in exits if e.get("is_merge")}
    for position in positions:
        if position["trade_id"] in merged_trade_ids:
            continue
        try:
            depth = get_orderbook_depth(position["token_id"])
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                skipped_not_found += 1
            else:
                key = str(exc).split(" for url:", 1)[0]
                skipped_errors[key] = skipped_errors.get(key, 0) + 1
            continue
        except Exception as exc:
            key = str(exc).split(" for url:", 1)[0]
            skipped_errors[key] = skipped_errors.get(key, 0) + 1
            continue
        best_bid = float(depth.get("best_bid") or 0)
        if best_bid <= 0:
            skipped_no_bid += 1
            continue
        current = best_bid
        db.update_position(position["trade_id"], current)
        model_prob = float(position.get("model_prob") or 0)
        current_edge = model_prob - current if position.get("side") == "yes" else model_prob - current
        if model_prob and abs(model_prob - current) <= config.PRICE_CONVERGENCE_THRESHOLD:
            exits.append(execute_exit(position, "price_converged_to_model"))
        elif model_prob and current_edge <= config.EDGE_EXIT_THRESHOLD:
            exits.append(execute_exit(position, "edge_disappeared"))
        elif current >= position["entry_price"] * config.TAKE_PROFIT_MULTIPLIER:
            exits.append(execute_exit(position, "take_profit"))
        elif position["entry_price"] >= config.STOP_LOSS_MIN_ENTRY_PRICE and current <= position["entry_price"] * config.STOP_LOSS_MULTIPLIER:
            exits.append(execute_exit(position, "stop_loss"))
        elif position.get("hours_to_resolution") is not None and position["hours_to_resolution"] <= 0.5:
            exits.append(execute_exit(position, "time_exit"))
    skipped_parts = []
    if skipped_not_found:
        skipped_parts.append(f"missing CLOB book: {skipped_not_found}")
    if skipped_no_bid:
        skipped_parts.append(f"no bid: {skipped_no_bid}")
    skipped_parts.extend(f"{reason}: {count}" for reason, count in skipped_errors.items())
    if skipped_parts:
        log.info(f"Position check skipped updates ({'; '.join(skipped_parts)})")
    return exits

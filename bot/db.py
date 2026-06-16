from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import config


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                mode TEXT NOT NULL,
                signal_type TEXT,
                city TEXT,
                market_id TEXT,
                market_question TEXT,
                bucket_label TEXT,
                token_id TEXT,
                side TEXT,
                entry_price REAL,
                shares REAL,
                usd_spent REAL,
                order_id TEXT,
                forecast_temp REAL,
                model_prob REAL,
                effective_edge REAL,
                is_ladder INTEGER DEFAULT 0,
                status TEXT DEFAULT 'open',
                exit_price REAL,
                exit_timestamp TEXT,
                pnl_usd REAL DEFAULT 0,
                exit_reason TEXT,
                arb_pair_id TEXT
            );
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER UNIQUE,
                market_id TEXT,
                bucket_label TEXT,
                token_id TEXT,
                side TEXT,
                entry_price REAL,
                current_price REAL,
                model_prob REAL,
                entry_ev REAL,
                shares REAL,
                unrealized_pnl REAL DEFAULT 0,
                last_checked_at TEXT,
                hours_to_resolution REAL
            );
            CREATE TABLE IF NOT EXISTS paper_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                date TEXT,
                starting_balance REAL,
                current_balance REAL,
                total_deployed REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                estimated_incentives REAL DEFAULT 0,
                open_positions_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS incentives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                type TEXT,
                amount_usd REAL,
                market_id TEXT,
                order_id TEXT,
                mode TEXT
            );
            CREATE TABLE IF NOT EXISTS maker_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                placed_at TEXT,
                market_id TEXT,
                bucket_label TEXT,
                token_id TEXT,
                side TEXT,
                quoted_price REAL,
                shares REAL,
                status TEXT,
                fill_price REAL,
                filled_at TEXT,
                rebate_earned REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS daily_summary (
                date TEXT,
                mode TEXT,
                total_spent REAL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                unrealized_pnl REAL DEFAULT 0,
                incentives_earned REAL DEFAULT 0,
                incentives_estimated REAL DEFAULT 0,
                trades_placed INTEGER DEFAULT 0,
                merges_executed INTEGER DEFAULT 0,
                redeems_executed INTEGER DEFAULT 0,
                arb_trades INTEGER DEFAULT 0,
                edge_yes_trades INTEGER DEFAULT 0,
                edge_no_trades INTEGER DEFAULT 0,
                maker_orders INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                signals_found INTEGER DEFAULT 0,
                PRIMARY KEY (date, mode)
            );
            """
        )
        for table, column, ddl in (
            ("positions", "model_prob", "ALTER TABLE positions ADD COLUMN model_prob REAL"),
            ("positions", "entry_ev", "ALTER TABLE positions ADD COLUMN entry_ev REAL"),
        ):
            cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if column not in cols:
                conn.execute(ddl)
        conn.execute(
            """
            INSERT OR IGNORE INTO paper_state
            (id, date, starting_balance, current_balance)
            VALUES (1, ?, ?, ?)
            """,
            (date.today().isoformat(), config.PAPER_BALANCE_USD, config.PAPER_BALANCE_USD),
        )
        row = conn.execute("SELECT starting_balance FROM paper_state WHERE id = 1").fetchone()
        if row and float(row["starting_balance"] or 0) < config.PAPER_BALANCE_USD:
            top_up = config.PAPER_BALANCE_USD - float(row["starting_balance"] or 0)
            conn.execute(
                """
                UPDATE paper_state
                SET starting_balance = ?, current_balance = current_balance + ?
                WHERE id = 1
                """,
                (config.PAPER_BALANCE_USD, top_up),
            )


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _one(sql: str, params: tuple = ()) -> dict | None:
    rows = _rows(sql, params)
    return rows[0] if rows else None


def log_trade(trade_dict: dict[str, Any]) -> int:
    init_db()
    payload = {
        "timestamp": utcnow(),
        "mode": config.TRADING_MODE,
        "status": "open",
        **trade_dict,
    }
    cols = ", ".join(payload.keys())
    marks = ", ".join("?" for _ in payload)
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO trades ({cols}) VALUES ({marks})", tuple(payload.values()))
        trade_id = int(cur.lastrowid)
        if payload.get("status") == "open" and payload.get("token_id"):
            conn.execute(
                """
                INSERT OR REPLACE INTO positions
                (trade_id, market_id, bucket_label, token_id, side, entry_price, current_price, model_prob, entry_ev, shares, last_checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    payload.get("market_id"),
                    payload.get("bucket_label"),
                    payload.get("token_id"),
                    payload.get("side"),
                    payload.get("entry_price"),
                    payload.get("entry_price"),
                    payload.get("model_prob"),
                    payload.get("effective_edge"),
                    payload.get("shares"),
                    utcnow(),
                ),
            )
        return trade_id


def update_position(trade_id: int, current_price: float, hours_to_resolution: float | None = None) -> None:
    row = _one("SELECT entry_price, shares FROM positions WHERE trade_id = ?", (trade_id,))
    if not row:
        return
    pnl = (current_price - row["entry_price"]) * row["shares"]
    with connect() as conn:
        conn.execute(
            """
            UPDATE positions SET current_price = ?, unrealized_pnl = ?, last_checked_at = ?,
            hours_to_resolution = COALESCE(?, hours_to_resolution) WHERE trade_id = ?
            """,
            (current_price, pnl, utcnow(), hours_to_resolution, trade_id),
        )


def close_position(trade_id: int, exit_price: float, exit_reason: str, status: str = "closed") -> dict:
    trade = _one("SELECT * FROM trades WHERE id = ?", (trade_id,))
    if not trade:
        raise ValueError(f"Unknown trade_id {trade_id}")
    pnl = (exit_price - (trade["entry_price"] or 0)) * (trade["shares"] or 0)
    with connect() as conn:
        conn.execute(
            """
            UPDATE trades SET status = ?, exit_price = ?, exit_timestamp = ?, pnl_usd = ?,
            exit_reason = ? WHERE id = ?
            """,
            (status, exit_price, utcnow(), pnl, exit_reason, trade_id),
        )
        conn.execute("DELETE FROM positions WHERE trade_id = ?", (trade_id,))
        if trade["mode"] == "paper":
            conn.execute(
                """
                UPDATE paper_state SET current_balance = current_balance + ?,
                realized_pnl = realized_pnl + ?, open_positions_count =
                (SELECT COUNT(*) FROM positions)
                WHERE id = 1
                """,
                (exit_price * (trade["shares"] or 0), pnl),
            )
    return {"trade": trade, "pnl_usd": pnl, "exit_price": exit_price, "exit_reason": exit_reason}


def get_open_positions() -> list[dict]:
    return _rows(
        """
        SELECT p.*, t.city, t.market_question, t.forecast_temp, t.model_prob, t.effective_edge,
               t.mode, t.arb_pair_id
        FROM positions p JOIN trades t ON t.id = p.trade_id
        ORDER BY p.id
        """
    )


def has_open_position(market_id: str, side: str, bucket_label: str | None = None) -> bool:
    if bucket_label is None:
        row = _one("SELECT 1 FROM positions WHERE market_id = ? AND side = ? LIMIT 1", (market_id, side))
    else:
        row = _one(
            "SELECT 1 FROM positions WHERE market_id = ? AND side = ? AND bucket_label = ? LIMIT 1",
            (market_id, side, bucket_label),
        )
    return row is not None


def get_open_city_exposure(city: str, mode: str = config.TRADING_MODE) -> float:
    row = _one(
        """
        SELECT COALESCE(SUM(t.usd_spent), 0) AS exposure
        FROM trades t
        JOIN positions p ON p.trade_id = t.id
        WHERE t.city = ? AND t.mode = ?
        """,
        (city, mode),
    )
    return float(row["exposure"] if row else 0.0)


def get_today_spend(mode: str = config.TRADING_MODE) -> float:
    row = _one(
        "SELECT COALESCE(SUM(usd_spent), 0) AS n FROM trades WHERE mode = ? AND date(timestamp) = date('now')",
        (mode,),
    )
    return float(row["n"] if row else 0)


def get_today_loss(mode: str = config.TRADING_MODE) -> float:
    row = _one(
        """
        SELECT COALESCE(SUM(CASE WHEN pnl_usd < 0 THEN -pnl_usd ELSE 0 END), 0) AS n
        FROM trades WHERE mode = ? AND date(timestamp) = date('now')
        """,
        (mode,),
    )
    return float(row["n"] if row else 0)


def get_entry_count(market_id: str, bucket_label: str, entry_date: date | None = None) -> int:
    day = (entry_date or date.today()).isoformat()
    row = _one(
        """
        SELECT COUNT(*) AS n FROM trades
        WHERE market_id = ? AND bucket_label = ? AND date(timestamp) = ?
        """,
        (market_id, bucket_label, day),
    )
    return int(row["n"] if row else 0)


def get_paper_balance() -> float:
    init_db()
    row = _one("SELECT current_balance FROM paper_state WHERE id = 1")
    return float(row["current_balance"] if row else config.PAPER_BALANCE_USD)


def update_paper_balance(delta: float) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            UPDATE paper_state SET current_balance = current_balance + ?,
            total_deployed = total_deployed + CASE WHEN ? < 0 THEN -? ELSE 0 END,
            open_positions_count = (SELECT COUNT(*) FROM positions)
            WHERE id = 1
            """,
            (delta, delta, delta),
        )


def log_incentive(type: str, amount: float, market_id: str, order_id: str | None = None, mode: str = config.TRADING_MODE) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO incentives (timestamp, type, amount_usd, market_id, order_id, mode) VALUES (?, ?, ?, ?, ?, ?)",
            (utcnow(), type, amount, market_id, order_id, mode),
        )
        if mode == "paper":
            conn.execute("UPDATE paper_state SET estimated_incentives = estimated_incentives + ? WHERE id = 1", (amount,))


def get_pnl_summary(mode: str | None = config.TRADING_MODE) -> dict:
    init_db()
    modes = [mode] if mode else ["paper", "live"]
    out = {}
    for item in modes:
        today = _one(
            "SELECT COALESCE(SUM(pnl_usd),0) pnl, COUNT(*) trades FROM trades WHERE mode=? AND date(timestamp)=date('now')",
            (item,),
        )
        week = _one(
            "SELECT COALESCE(SUM(pnl_usd),0) pnl FROM trades WHERE mode=? AND timestamp >= ?",
            (item, (datetime.now(UTC) - timedelta(days=7)).isoformat()),
        )
        all_time = _one("SELECT COALESCE(SUM(pnl_usd),0) pnl FROM trades WHERE mode=?", (item,))
        open_value = _one("SELECT COALESCE(SUM(current_price * shares),0) v, COALESCE(SUM(unrealized_pnl),0) u FROM positions")
        out[item] = {
            "today_realized": float(today["pnl"]),
            "week_realized": float(week["pnl"]),
            "all_time_realized": float(all_time["pnl"]),
            "trades_today": int(today["trades"]),
            "open_value": float(open_value["v"]),
            "unrealized": float(open_value["u"]),
        }
    return out if mode is None else out[mode]


def get_incentive_summary() -> dict:
    rows = _rows(
        """
        SELECT type,
               SUM(CASE WHEN date(timestamp)=date('now') THEN amount_usd ELSE 0 END) today,
               SUM(amount_usd) all_time
        FROM incentives GROUP BY type
        """
    )
    return {row["type"]: {"today": float(row["today"] or 0), "all_time": float(row["all_time"] or 0)} for row in rows}


def log_maker_order(order_dict: dict[str, Any]) -> int:
    init_db()
    payload = {"placed_at": utcnow(), "status": "pending", **order_dict}
    cols = ", ".join(payload.keys())
    marks = ", ".join("?" for _ in payload)
    with connect() as conn:
        cur = conn.execute(f"INSERT INTO maker_orders ({cols}) VALUES ({marks})", tuple(payload.values()))
        return int(cur.lastrowid)


def update_maker_order(order_id: int, status: str, fill_price: float | None = None, rebate: float | None = None) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE maker_orders SET status = ?, fill_price = COALESCE(?, fill_price),
            filled_at = CASE WHEN ? = 'filled' THEN ? ELSE filled_at END,
            rebate_earned = COALESCE(?, rebate_earned) WHERE id = ?
            """,
            (status, fill_price, status, utcnow(), rebate, order_id),
        )


def get_maker_orders(status: str | None = None) -> list[dict]:
    if status:
        return _rows("SELECT * FROM maker_orders WHERE status = ? ORDER BY id DESC LIMIT 50", (status,))
    return _rows("SELECT * FROM maker_orders ORDER BY id DESC LIMIT 50")


def get_trade_history(limit: int = 20) -> list[dict]:
    return _rows("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))


def update_daily_summary(signals_found: int = 0) -> None:
    init_db()
    mode = config.TRADING_MODE
    summary = get_pnl_summary(mode)
    incentives = get_incentive_summary()
    today_inc = sum(v["today"] for v in incentives.values())
    counts = _one(
        """
        SELECT COUNT(*) trades,
        SUM(CASE WHEN signal_type='arbitrage' THEN 1 ELSE 0 END) arb,
        SUM(CASE WHEN signal_type='edge_yes' THEN 1 ELSE 0 END) edge_yes,
        SUM(CASE WHEN signal_type='edge_no' THEN 1 ELSE 0 END) edge_no,
        SUM(CASE WHEN signal_type='maker' THEN 1 ELSE 0 END) maker
        FROM trades WHERE mode=? AND date(timestamp)=date('now')
        """,
        (mode,),
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_summary
            (date, mode, total_spent, realized_pnl, unrealized_pnl, incentives_estimated,
             trades_placed, arb_trades, edge_yes_trades, edge_no_trades, maker_orders, signals_found)
            VALUES (date('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mode,
                get_today_spend(mode),
                summary["today_realized"],
                summary["unrealized"],
                today_inc,
                counts["trades"] or 0,
                counts["arb"] or 0,
                counts["edge_yes"] or 0,
                counts["edge_no"] or 0,
                counts["maker"] or 0,
                signals_found,
            ),
        )

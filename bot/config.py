from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> None:
        return None

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = os.getenv("DB_PATH", str(ROOT_DIR / "trading_bot.db"))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


TRADING_MODE = os.getenv("TRADING_MODE", "paper").strip().lower()
if TRADING_MODE not in {"paper", "live"}:
    raise ValueError("TRADING_MODE must be 'paper' or 'live'")

PAPER_BALANCE_USD = _float("PAPER_BALANCE_USD", 1000.0)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

POLYGON_PRIVATE_KEY = os.getenv("POLYGON_PRIVATE_KEY", "")
POLYGON_WALLET_ADDRESS = os.getenv("POLYGON_WALLET_ADDRESS", "")
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_SECRET = os.getenv("POLYMARKET_SECRET", "")
POLYMARKET_PASSPHRASE = os.getenv("POLYMARKET_PASSPHRASE", "")

MAX_TRADE_SIZE_USD = _float("MAX_TRADE_SIZE_USD", 1.00)
MAX_DAILY_SPEND_USD = _float("MAX_DAILY_SPEND_USD", 250.0)
MAX_DAILY_LOSS_USD = _float("MAX_DAILY_LOSS_USD", 15.0)
MAX_OPEN_POSITIONS = _int("MAX_OPEN_POSITIONS", 200)
MAX_ENTRIES_PER_BUCKET = _int("MAX_ENTRIES_PER_BUCKET", 1)
MIN_EDGE_THRESHOLD = _float("MIN_EDGE_THRESHOLD", 0.12)
MIN_EV_THRESHOLD = _float("MIN_EV_THRESHOLD", 0.15)
MAX_ENTRY_PRICE = _float("MAX_ENTRY_PRICE", 0.08)
MIN_HOURS_TO_RESOLUTION = _float("MIN_HOURS_TO_RESOLUTION", 6.0)
MAX_TRADES_PER_SCAN = _int("MAX_TRADES_PER_SCAN", 30)
MAX_TRADES_PER_MARKET = _int("MAX_TRADES_PER_MARKET", 2)
MAX_TRADES_PER_CITY = _int("MAX_TRADES_PER_CITY", 1)
MAX_CITY_EXPOSURE_USD = _float("MAX_CITY_EXPOSURE_USD", 1.0)
KNOWN_OUTCOME_LOCAL_HOUR = _int("KNOWN_OUTCOME_LOCAL_HOUR", 18)
ARBI_THRESHOLD = _float("ARBI_THRESHOLD", 0.97)
TAKE_PROFIT_MULTIPLIER = _float("TAKE_PROFIT_MULTIPLIER", 2.5)
STOP_LOSS_MULTIPLIER = _float("STOP_LOSS_MULTIPLIER", 0.3)
STOP_LOSS_MIN_ENTRY_PRICE = _float("STOP_LOSS_MIN_ENTRY_PRICE", 0.05)
MAKER_ORDER_MODE = _bool("MAKER_ORDER_MODE", True)
RUN_SCAN_ON_STARTUP = _bool("RUN_SCAN_ON_STARTUP", True)
SCAN_INTERVAL_MINUTES = _int("SCAN_INTERVAL_MINUTES", 10)
PAPER_DEMO_MODE = _bool("PAPER_DEMO_MODE", False)
DYNAMIC_CITY_DISCOVERY = _bool("DYNAMIC_CITY_DISCOVERY", True)
GAMMA_MAX_MARKETS = _int("GAMMA_MAX_MARKETS", 2000)
TEMPERATURE_PAGE_MAX_MARKETS = _int("TEMPERATURE_PAGE_MAX_MARKETS", 1000)
TEMPERATURE_MARKET_HYDRATE_WORKERS = _int("TEMPERATURE_MARKET_HYDRATE_WORKERS", 32)
TEMPERATURE_MARKET_HYDRATE_TIMEOUT = _int("TEMPERATURE_MARKET_HYDRATE_TIMEOUT", 4)
TEMPERATURE_GENERATED_DAYS = _int("TEMPERATURE_GENERATED_DAYS", 2)
WEATHER_KEYSET_PAGES = _int("WEATHER_KEYSET_PAGES", 10)
FETCH_ORDERBOOK_DEPTH = _bool("FETCH_ORDERBOOK_DEPTH", False)
INCENTIVE_REBATE_EST = _float("INCENTIVE_REBATE_EST", 0.0015)
EV_KELLY_FRACTION = _float("EV_KELLY_FRACTION", 0.05)
MAX_RISK_PER_TRADE_PCT = _float("MAX_RISK_PER_TRADE_PCT", 0.005)
MIN_TRADE_SIZE_USD = _float("MIN_TRADE_SIZE_USD", 0.10)
EV_SIZE_SMALL_USD = _float("EV_SIZE_SMALL_USD", 0.25)
EV_SIZE_MEDIUM_USD = _float("EV_SIZE_MEDIUM_USD", 0.50)
EV_SIZE_LARGE_USD = _float("EV_SIZE_LARGE_USD", 1.00)
PRICE_CONVERGENCE_THRESHOLD = _float("PRICE_CONVERGENCE_THRESHOLD", 0.01)
EDGE_EXIT_THRESHOLD = _float("EDGE_EXIT_THRESHOLD", 0.005)
FORECAST_STD_DEV_F = _float("FORECAST_STD_DEV_F", 2.5)
FORECAST_STD_DEV_C = _float("FORECAST_STD_DEV_C", 1.5)
TELEGRAM_TRADE_NOTIFICATIONS_PER_SCAN = _int("TELEGRAM_TRADE_NOTIFICATIONS_PER_SCAN", 5)

KILL_SWITCH_PATH = Path(os.getenv("KILL_SWITCH_PATH", "/tmp/killswitch"))

CITIES = [
    {"name": "New York", "lat": 40.7769, "lon": -73.8740, "unit": "F", "polymarket_tag": "new-york", "station_note": "KLGA", "timezone": "America/New_York"},
    {"name": "Chicago", "lat": 41.9742, "lon": -87.9073, "unit": "F", "polymarket_tag": "chicago", "station_note": "KORD", "timezone": "America/Chicago"},
    {"name": "Miami", "lat": 25.7959, "lon": -80.2870, "unit": "F", "polymarket_tag": "miami", "station_note": "KMIA", "timezone": "America/New_York"},
    {"name": "Seattle", "lat": 47.4502, "lon": -122.3088, "unit": "F", "polymarket_tag": "seattle", "station_note": "KSEA", "timezone": "America/Los_Angeles"},
    {"name": "London", "lat": 51.5053, "lon": 0.0553, "unit": "C", "polymarket_tag": "london", "station_note": "EGLC", "timezone": "Europe/London"},
    {"name": "Buenos Aires", "lat": -34.8222, "lon": -58.5358, "unit": "C", "polymarket_tag": "buenos-aires", "station_note": "SAEZ", "timezone": "America/Argentina/Buenos_Aires"},
    {"name": "Tokyo", "lat": 35.5494, "lon": 139.7798, "unit": "C", "polymarket_tag": "tokyo", "station_note": "RJTT", "timezone": "Asia/Tokyo"},
    {"name": "Moscow", "lat": 55.4088, "lon": 37.9063, "unit": "C", "polymarket_tag": "moscow", "station_note": "UUDD", "timezone": "Europe/Moscow"},
    {"name": "Hong Kong", "lat": 22.3080, "lon": 113.9185, "unit": "C", "polymarket_tag": "hong-kong", "station_note": "VHHH", "timezone": "Asia/Hong_Kong"},
]


class SignalType(Enum):
    EDGE_YES = "edge_yes"
    EDGE_NO = "edge_no"
    ARBITRAGE = "arbitrage"
    MAKER = "maker"


def redacted_config() -> dict:
    return {
        "TRADING_MODE": TRADING_MODE,
        "PAPER_BALANCE_USD": PAPER_BALANCE_USD,
        "MAX_TRADE_SIZE_USD": MAX_TRADE_SIZE_USD,
        "MAX_DAILY_SPEND_USD": MAX_DAILY_SPEND_USD,
        "MAX_DAILY_LOSS_USD": MAX_DAILY_LOSS_USD,
        "MAX_OPEN_POSITIONS": MAX_OPEN_POSITIONS,
        "MAX_TRADES_PER_SCAN": MAX_TRADES_PER_SCAN,
        "MAX_TRADES_PER_MARKET": MAX_TRADES_PER_MARKET,
        "MAX_TRADES_PER_CITY": MAX_TRADES_PER_CITY,
        "MAX_CITY_EXPOSURE_USD": MAX_CITY_EXPOSURE_USD,
        "KNOWN_OUTCOME_LOCAL_HOUR": KNOWN_OUTCOME_LOCAL_HOUR,
        "MAX_ENTRY_PRICE": MAX_ENTRY_PRICE,
        "STOP_LOSS_MIN_ENTRY_PRICE": STOP_LOSS_MIN_ENTRY_PRICE,
        "MIN_EDGE_THRESHOLD": MIN_EDGE_THRESHOLD,
        "MIN_EV_THRESHOLD": MIN_EV_THRESHOLD,
        "EV_SIZE_SMALL_USD": EV_SIZE_SMALL_USD,
        "EV_SIZE_MEDIUM_USD": EV_SIZE_MEDIUM_USD,
        "EV_SIZE_LARGE_USD": EV_SIZE_LARGE_USD,
        "RUN_SCAN_ON_STARTUP": RUN_SCAN_ON_STARTUP,
        "SCAN_INTERVAL_MINUTES": SCAN_INTERVAL_MINUTES,
        "PAPER_DEMO_MODE": PAPER_DEMO_MODE,
        "DYNAMIC_CITY_DISCOVERY": DYNAMIC_CITY_DISCOVERY,
        "GAMMA_MAX_MARKETS": GAMMA_MAX_MARKETS,
        "TEMPERATURE_PAGE_MAX_MARKETS": TEMPERATURE_PAGE_MAX_MARKETS,
        "TEMPERATURE_MARKET_HYDRATE_WORKERS": TEMPERATURE_MARKET_HYDRATE_WORKERS,
        "TEMPERATURE_MARKET_HYDRATE_TIMEOUT": TEMPERATURE_MARKET_HYDRATE_TIMEOUT,
        "TEMPERATURE_GENERATED_DAYS": TEMPERATURE_GENERATED_DAYS,
        "WEATHER_KEYSET_PAGES": WEATHER_KEYSET_PAGES,
        "FETCH_ORDERBOOK_DEPTH": FETCH_ORDERBOOK_DEPTH,
        "EV_KELLY_FRACTION": EV_KELLY_FRACTION,
        "MAX_RISK_PER_TRADE_PCT": MAX_RISK_PER_TRADE_PCT,
        "TELEGRAM_TRADE_NOTIFICATIONS_PER_SCAN": TELEGRAM_TRADE_NOTIFICATIONS_PER_SCAN,
        "TELEGRAM_BOT_TOKEN": "***" if TELEGRAM_BOT_TOKEN else "",
        "POLYGON_PRIVATE_KEY": "***" if POLYGON_PRIVATE_KEY else "",
        "POLYMARKET_API_KEY": "***" if POLYMARKET_API_KEY else "",
    }

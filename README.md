# Polymarket Weather Trading Bot

A Python bot for scanning Polymarket weather markets, comparing market prices against GFS weather forecasts, and trading signals in either paper mode or live mode.

The bot has two operating modes:

- `paper`: simulated trading with a virtual balance. This is the default and safest mode.
- `live`: real Polymarket order submission through `py-clob-client`.

Paper mode and live mode use the same scanner, signal engine, safety checks, position manager, and database schema. The goal is to let you test the exact strategy logic before risking real capital.

Important: run paper mode for at least 7 days before switching to live mode.

## What The Bot Does

The bot watches weather-related Polymarket markets and looks for four kinds of opportunities:

- `EDGE_YES`: buy a cheap YES token when the forecast model says the bucket is more likely than the market price implies.
- `EDGE_NO`: buy a cheap NO token when the forecast model says the bucket is unlikely.
- `ARBITRAGE`: buy both YES and NO when the combined cost is below the configured arbitrage threshold.
- `MAKER`: place resting limit orders to simulate or collect maker incentives.

It also manages open positions:

- Take profit when price rises enough.
- Stop loss when price falls too far.
- Merge YES/NO pairs where possible.
- Track realized P&L, unrealized P&L, paper balance, maker orders, and estimated incentives.
- Respond to Telegram commands while the scheduler is running.

## Project Layout

```text
bot/
  main.py              Scheduler and Telegram bot runtime
  config.py            Environment variables, constants, city list, signal enum
  scanner.py           GFS forecast fetching and Polymarket market/orderbook fetching
  signal_engine.py     Signal detection logic
  order_engine.py      Paper/live routing and order sizing
  paper_trader.py      Simulated fills and paper trade logging
  live_trader.py       Live Polymarket order adapter
  position_manager.py  Take-profit, stop-loss, merge, and exit checks
  incentive_tracker.py Incentive estimates and logging
  notifier.py          Telegram notifications and commands
  db.py                SQLite schema and persistence helpers
  safety.py            Daily caps, balance checks, and kill switch
  requirements.txt     Python dependencies
```

Runtime files:

- `.env`: your private local config. Do not commit this.
- `trading_bot.db`: local SQLite database created on first run.
- `logs/bot.log`: rotating log file.

## Requirements

Install these first:

- Python 3.11 or newer
- PowerShell, Terminal, or another shell
- A Telegram bot token and chat ID if you want notifications and commands
- Polymarket wallet/API credentials only if you plan to use live mode

The code is designed so paper mode does not need Polymarket trading keys.

## 1. Paper Mode Quick-Start

Use this path first. It gets the bot running safely with simulated trades.

### Step 1: Create a virtual environment

From the project directory:

```powershell
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation scripts, run:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the environment again.

### Step 2: Install dependencies

```powershell
pip install -r bot\requirements.txt
```

### Step 3: Create your `.env`

```powershell
Copy-Item .env.example .env
```

Open `.env` and set:

```dotenv
TRADING_MODE=paper
PAPER_BALANCE_USD=100
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

For paper mode, you can leave all Polymarket private keys blank.

### Step 4: Start the bot

```powershell
python -m bot.main
```

You should see a log line like:

```text
RUNNING IN PAPER MODE
```

If Telegram is configured, the bot sends:

```text
Bot started in PAPER MODE - no real trades will be placed
```

### Step 5: Confirm Telegram commands

In Telegram, send:

```text
/status
```

You should get a response showing:

- Current mode
- Kill switch state
- Number of open positions
- Today P&L

Then try:

```text
/config
```

Secrets should be redacted.

## Telegram Setup

### Create a bot token

1. Open Telegram.
2. Search for `@BotFather`.
3. Send `/newbot`.
4. Follow the prompts.
5. Copy the token into `.env` as `TELEGRAM_BOT_TOKEN`.

### Get your chat ID

One simple method:

1. Send any message to your new bot.
2. Visit this URL in your browser, replacing `TOKEN` with your bot token:

```text
https://api.telegram.org/botTOKEN/getUpdates
```

3. Find the `chat.id` value.
4. Put it in `.env`:

```dotenv
TELEGRAM_CHAT_ID=123456789
```

Restart the bot after changing `.env`.

## Configuration Guide

These are the main settings in `.env`.

```dotenv
TRADING_MODE=paper
PAPER_BALANCE_USD=1000
MAX_TRADE_SIZE_USD=1.00
MAX_DAILY_SPEND_USD=250
MAX_DAILY_LOSS_USD=15
MAX_OPEN_POSITIONS=200
MAX_ENTRIES_PER_BUCKET=1
MIN_EDGE_THRESHOLD=0.12
MIN_EV_THRESHOLD=0.15
MAX_ENTRY_PRICE=0.08
MIN_HOURS_TO_RESOLUTION=6
MAX_TRADES_PER_SCAN=30
MAX_TRADES_PER_MARKET=2
MAX_TRADES_PER_CITY=1
MAX_CITY_EXPOSURE_USD=1
KNOWN_OUTCOME_LOCAL_HOUR=18
ARBI_THRESHOLD=0.97
TAKE_PROFIT_MULTIPLIER=2.5
STOP_LOSS_MULTIPLIER=0.3
STOP_LOSS_MIN_ENTRY_PRICE=0.05
MAKER_ORDER_MODE=true
RUN_SCAN_ON_STARTUP=true
PAPER_DEMO_MODE=false
DYNAMIC_CITY_DISCOVERY=true
GAMMA_MAX_MARKETS=2000
TEMPERATURE_PAGE_MAX_MARKETS=1000
TEMPERATURE_MARKET_HYDRATE_WORKERS=32
TEMPERATURE_MARKET_HYDRATE_TIMEOUT=4
TEMPERATURE_GENERATED_DAYS=2
WEATHER_KEYSET_PAGES=10
FETCH_ORDERBOOK_DEPTH=false
INCENTIVE_REBATE_EST=0.0015
EV_KELLY_FRACTION=0.05
MAX_RISK_PER_TRADE_PCT=0.005
MIN_TRADE_SIZE_USD=0.10
EV_SIZE_SMALL_USD=0.25
EV_SIZE_MEDIUM_USD=0.50
EV_SIZE_LARGE_USD=1.00
PRICE_CONVERGENCE_THRESHOLD=0.01
EDGE_EXIT_THRESHOLD=0.005
FORECAST_STD_DEV_F=2.5
FORECAST_STD_DEV_C=1.5
TELEGRAM_TRADE_NOTIFICATIONS_PER_SCAN=5
```

Recommended paper settings for first run:

- Keep `TRADING_MODE=paper`.
- Keep `MAX_TRADE_SIZE_USD` small, such as `0.25` to `1.00`.
- Keep `MAX_DAILY_SPEND_USD` small until you trust the signals.
- Do not loosen `MIN_EV_THRESHOLD=0.15` or `MAX_ENTRY_PRICE=0.08` until you understand how many signals the bot generates.

### Paper demo mode

Sometimes Polymarket has no active weather/temperature markets for the configured cities. In that case the correct live-safe result is zero signals.

To test the paper trading pipeline anyway, enable:

```dotenv
PAPER_DEMO_MODE=true
```

When enabled, paper mode injects one clearly labeled synthetic New York weather market only if no real matching weather market exists. This is for testing the scanner, signal engine, order engine, database, and Telegram notifications. Live mode never uses the demo market.

After one demo entry is placed for the day, the bot will not repeat the same synthetic entry.

## How The Strategy Works

The bot is a weather-only expected-value trader. It does not trade politics, sports, crypto, celebrity, or other non-weather markets.

Core EV formula:

```text
EV = p * (1 - price) - (1 - p) * price
```

For a binary token this simplifies to:

```text
EV = p - price
```

The bot estimates `p`, compares it with the current token price, and only enters when `EV >= MIN_EV_THRESHOLD`.

### Forecast data

The scanner calls Open-Meteo with the `gfs_seamless` model and reads the daily maximum temperature forecast for each configured city.

Cities included:

- New York
- Chicago
- Miami
- Seattle
- London
- Buenos Aires
- Tokyo
- Moscow
- Hong Kong

### Market data

The scanner first reads Polymarket's Temperature page, extracts active daily high-temperature markets, hydrates those market IDs through Gamma, and discovers the cities that are actually tradable. It then geocodes those cities with Open-Meteo and scans them alongside the fallback city list.

It parses bucket labels such as:

- `between 65F and 69F`
- `65-69F`
- `65 to 69`
- `above 90`
- `below 32`

For each token, the bot uses current CLOB orderbook prices, especially best ask for entries.

### Signal calculation

For each bucket, the signal engine estimates probability using a normal distribution around the forecast temperature. It then compares:

```text
estimated probability vs market price
```

Signals must pass limits for:

- Minimum EV threshold
- Purchased token price at or below `MAX_ENTRY_PRICE`, default 8 cents
- Minimum hours to resolution, currently 6 hours in the market city's timezone
- Local time before the outcome is mostly known, defaulting to before 18:00 local time
- Available depth
- Maximum entries per bucket
- No existing open position for the same market and side
- Maximum city exposure, default $1
- One trade per city/date, so the bot does not stack correlated temperature buckets
- Daily spend and loss limits

Directional rules:

- Buy YES when `p_yes - yes_price >= MIN_EV_THRESHOLD`.
- Buy NO when `p_no - no_price >= MIN_EV_THRESHOLD`.

Position sizing:

```text
size = bankroll * EV * EV_KELLY_FRACTION
```

The result is capped by:

- `MAX_RISK_PER_TRADE_PCT`
- `MAX_TRADE_SIZE_USD`
- remaining daily budget

After all candidate signals are scored, the bot sorts them by EV and only keeps the top `MAX_TRADES_PER_SCAN`. This prevents the wide scanner from opening hundreds of weak positions.

Exit logic closes positions when price converges to the model estimate, when edge disappears, when stop-loss/take-profit rules trigger, or when time risk becomes too high.

## 2. Live Mode Setup

Only switch to live mode after paper testing.

Before live mode, confirm:

- Paper mode has run for at least 7 days.
- Telegram `/pause` and `/resume` work.
- `/pnl` and `/history` show expected records.
- You understand your `MAX_TRADE_SIZE_USD`, `MAX_DAILY_SPEND_USD`, and `MAX_DAILY_LOSS_USD`.
- Your wallet and Polymarket API credentials are correct.

### Live `.env` values

Stop the bot, edit `.env`, and set:

```dotenv
TRADING_MODE=live
POLYGON_PRIVATE_KEY=your_polygon_private_key
POLYGON_WALLET_ADDRESS=your_wallet_address
POLYMARKET_API_KEY=your_polymarket_api_key
POLYMARKET_SECRET=your_polymarket_secret
POLYMARKET_PASSPHRASE=your_polymarket_passphrase
```

Then restart:

```powershell
python -m bot.main
```

You should see:

```text
RUNNING IN LIVE MODE
```

There is intentionally no Telegram `/live` command. Switching to live requires editing `.env` and restarting the process. This prevents accidental or malicious escalation from paper trading to real trading.

## Safety Controls

The bot has several safety layers:

- `TRADING_MODE` controls paper vs live routing.
- `MAX_TRADE_SIZE_USD` caps a single order.
- `MAX_DAILY_SPEND_USD` caps daily deployed capital.
- `MAX_DAILY_LOSS_USD` stops trading after configured losses.
- `MAX_ENTRY_PRICE` avoids buying expensive long-shot tokens.
- `/pause` creates a kill switch file.
- `/resume` removes the kill switch file.
- Live mode cannot be enabled from Telegram.

The kill switch path defaults to:

```text
/tmp/killswitch
```

On Windows, you can override it in `.env`:

```dotenv
KILL_SWITCH_PATH=C:\Users\yourname\killswitch
```

## Telegram Commands

All commands work in paper and live mode.

```text
/status
```

Shows mode, kill switch state, open positions, and today P&L.

```text
/positions
```

Lists open positions with entry and current prices.

```text
/pnl
```

Shows paper and live P&L side by side if both have records.

```text
/signals
```

Shows signals found during the latest scan.

```text
/scan
```

Runs a scan immediately instead of waiting for the scheduled scan time.

```text
/debug
```

Shows DNS/API status and the latest scan summary. Use this first when `/signals` says there are no signals.

```text
/cities
```

Shows active weather-market cities discovered from Polymarket. The bot geocodes these dynamically and includes them in scans.

```text
/arb
```

Shows latest arbitrage signals.

```text
/maker
```

Shows maker orders and their status.

```text
/paper
```

Shows paper balance and paper-mode summary.

```text
/pause
```

Stops new trading by creating the kill switch.

```text
/resume
```

Allows trading again by removing the kill switch.

```text
/history
```

Shows recent trades.

```text
/config
```

Shows current configuration with private keys redacted.

## 3. Reading The Daily Reports

The bot sends a daily report at the scheduled report time.

Key fields:

- `Capital deployed`: money used for trades today.
- `Realized P&L`: profit or loss from closed positions.
- `Unrealized P&L`: estimated profit or loss from open positions.
- `Incentives today`: estimated maker/liquidity incentive value.
- `Net P&L incl. incentives`: realized P&L plus incentives.
- `Open value`: estimated value of open positions.
- `All-time realized P&L`: total closed-position P&L.
- `Mode`: `PAPER` or `LIVE`.

Interpretation tips:

- Paper P&L is useful for validating logic, not proving future live performance.
- Incentives in paper mode are estimates.
- Unrealized P&L depends on current orderbook prices and can move quickly.
- A high number of signals may mean thresholds are too loose.
- Very few signals may mean thresholds are too strict or the market has limited liquidity.

## Database

The bot creates `trading_bot.db` automatically.

Main tables:

- `trades`: every paper or live trade.
- `positions`: currently open positions.
- `paper_state`: virtual balance and paper summary.
- `incentives`: estimated or actual incentive records.
- `maker_orders`: maker order tracking.
- `daily_summary`: daily aggregate stats.

Paper and live trades are stored together but separated by the `mode` field. This allows `/pnl` to compare both.

To inspect the database manually, use any SQLite browser or CLI:

```powershell
sqlite3 trading_bot.db
```

Example query:

```sql
SELECT mode, signal_type, side, entry_price, shares, status, pnl_usd
FROM trades
ORDER BY id DESC
LIMIT 20;
```

## Scheduler

The scanner runs four times per day:

```text
00:30 UTC
06:30 UTC
12:30 UTC
18:30 UTC
```

The position manager runs every 30 minutes.

The daily report runs once per day.

The Telegram bot runs concurrently, so commands such as `/pause` and `/status` should respond between scans.

## 4. Railway.app Deployment

Railway is useful if you want the bot running continuously.

### Step 1: Push the project to a Git repo

Railway deploys most easily from GitHub.

Make sure `.env` and database files are not committed. This repo includes `.gitignore` entries for:

```text
.env
*.db
logs/
.venv/
```

### Step 2: Create a Railway project

1. Go to Railway.
2. Create a new project.
3. Deploy from your GitHub repo.
4. Select this project.

### Step 3: Add environment variables

In Railway Variables, add the same values from your local `.env`.

For paper mode:

```dotenv
TRADING_MODE=paper
PAPER_BALANCE_USD=100
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

For live mode, add the Polymarket credentials too.

### Step 4: Set the start command

Use:

```bash
python -m bot.main
```

### Step 5: Add persistent storage

If you want `trading_bot.db` to survive redeploys, attach persistent storage and point `DB_PATH` at that mounted location.

Example:

```dotenv
DB_PATH=/data/trading_bot.db
```

Without persistent storage, Railway may lose the SQLite database between deploys.

## Troubleshooting

### `ModuleNotFoundError`

Install dependencies:

```powershell
pip install -r bot\requirements.txt
```

Make sure your virtual environment is activated.

### Bot starts but Telegram does not respond

Check:

- `TELEGRAM_BOT_TOKEN` is correct.
- `TELEGRAM_CHAT_ID` is correct.
- You messaged the bot first.
- The bot process is still running.

### No signals found

This can be normal. Check:

- Run `/debug` first. If DNS fails for `gamma-api.polymarket.com` or `clob.polymarket.com`, the bot cannot fetch Polymarket markets or orderbooks.
- Run `/cities` to see whether Polymarket currently has active weather markets and which cities the bot discovered.
- If `/debug` shows `forecast_ok: 9`, `last_network_error: None`, and `weather_candidates: 0`, the APIs are reachable but there are currently no active matching weather markets.
- Set `PAPER_DEMO_MODE=true` only if you want to test paper execution with a synthetic demo market.
- Weather markets may not currently match the configured cities.
- The market may have low liquidity.
- `MIN_EV_THRESHOLD` may be too high.
- `MAX_ENTRY_PRICE` may be too low.
- The market may be too close to resolution.

### DNS failure for Polymarket or Open-Meteo

If the log says `Failed to resolve` or `/debug` shows `DNS_FAIL`, this is a network/DNS problem on the machine running the bot. The bot cannot paper trade real market data until these domains resolve:

```text
api.open-meteo.com
gamma-api.polymarket.com
clob.polymarket.com
```

On Windows, test DNS:

```powershell
Resolve-DnsName api.open-meteo.com
Resolve-DnsName gamma-api.polymarket.com
Resolve-DnsName clob.polymarket.com
```

Common fixes:

- Switch DNS to Cloudflare `1.1.1.1` or Google `8.8.8.8`.
- Disable VPN/proxy filtering temporarily.
- Check firewall, antivirus, or router DNS filtering.
- Restart the terminal after changing network settings.

### Paper balance is too low

Increase `PAPER_BALANCE_USD` before the first DB initialization, or delete `trading_bot.db` if you are intentionally resetting paper history.

### Live mode fails immediately

Check:

- `TRADING_MODE=live`
- `POLYGON_PRIVATE_KEY` is set
- Your wallet has required funds/collateral
- Polymarket API credentials are valid
- `py-clob-client` installed successfully

### I want to reset everything

Stop the bot, then remove the database:

```powershell
Remove-Item trading_bot.db
```

Start the bot again. A fresh database will be created.

## Practical First-Week Checklist

Day 1:

- Run in paper mode.
- Confirm Telegram commands.
- Keep trade sizes small.
- Watch logs for API failures.

Days 2-3:

- Review `/signals`, `/history`, and `/pnl`.
- Look for repeated bad entries or too many maker orders.
- Keep notes on signal quality.

Days 4-7:

- Check daily reports.
- Compare realized and unrealized P&L.
- Confirm kill switch behavior.
- Decide whether config thresholds are too aggressive.

After day 7:

- Only consider live mode if paper behavior is stable and understandable.
- Start live mode with very small caps.
- Keep `/pause` ready.

## Important Risk Notes

This bot can lose money in live mode. Forecasts can be wrong, markets can move quickly, fills can be partial, liquidity can disappear, and API behavior can change. Paper mode is a simulation and does not guarantee live results.

Never put private keys directly into code. Use `.env` or your deployment provider's secret manager.

Start small, verify everything, and treat live mode as real money from the first order.

## Full Bot Description

This project is an automated weather-market trading bot for Polymarket. Its job is to find active temperature markets, estimate the fair probability of each outcome using weather forecasts, compare that fair probability with the market price, and only enter trades when the expected value is positive enough to justify the risk.

The bot is designed to trade weather markets only. It does not intentionally trade politics, sports, crypto, entertainment, or general news markets. The scanner focuses on Polymarket temperature contracts such as highest temperature, lowest temperature, city daily highs, city daily lows, and bucketed outcomes like `72-73F`, `31C`, `27C or lower`, or `32C or higher`.

At startup and on every scheduled scan, the bot discovers currently tradable weather markets from multiple sources. It reads Polymarket's temperature page, queries Gamma weather event data, hydrates individual market slugs, and uses a targeted fallback list for common weather cities that Polymarket sometimes hides from normal pagination. This is why the bot can find markets for changing cities instead of being limited to a fixed list like New York, Chicago, Miami, London, Tokyo, or Hong Kong.

For each discovered city, the bot geocodes the location with Open-Meteo and fetches GFS forecast data. It reads both daily high and daily low temperature forecasts so it can handle highest-temperature and lowest-temperature markets separately. The bot then parses each Polymarket bucket into a numeric range. For example, `72-73F` becomes a temperature interval, `31C or higher` becomes an open-ended upper bucket, and `27C or lower` becomes an open-ended lower bucket.

The signal engine estimates the probability that the forecast lands inside each bucket. It models uncertainty around the weather forecast using a normal distribution. The standard deviation is configurable with `FORECAST_STD_DEV_F` and `FORECAST_STD_DEV_C`. A wider standard deviation makes the bot less confident and spreads probability across more buckets. A narrower standard deviation makes the bot more aggressive around the forecast value.

The core strategy is expected value trading:

```text
EV = p * (1 - price) - (1 - p) * price
```

For a binary Polymarket token, that simplifies to:

```text
EV = p - price
```

Where `p` is the bot's estimated true probability and `price` is the current market price. If the YES token is cheap compared with the estimated probability, the bot creates an `EDGE_YES` signal. If the NO token is cheap compared with the estimated probability that the bucket will not happen, the bot creates an `EDGE_NO` signal.

The default rule is:

```text
Only trade when EV >= MIN_EV_THRESHOLD
```

If `MIN_EV_THRESHOLD=0.15`, the bot needs at least a 15 percentage point edge before entering. This prevents it from taking every tiny difference between model and market. The goal is not to predict every market correctly. The goal is to repeatedly enter trades where the price appears better than the estimated probability.

Order size is intentionally small. The sizing formula starts from:

```text
size = bankroll * EV * EV_KELLY_FRACTION
```

Then the result is capped by `MAX_TRADE_SIZE_USD`, `MAX_RISK_PER_TRADE_PCT`, remaining daily spend, and available paper/live balance. For high-frequency paper testing, small trade settings are recommended:

```dotenv
MAX_TRADE_SIZE_USD=1.00
MIN_TRADE_SIZE_USD=0.10
MAX_DAILY_SPEND_USD=250
MAX_OPEN_POSITIONS=200
MAX_TRADES_PER_SCAN=30
MAX_TRADES_PER_MARKET=2
MAX_TRADES_PER_CITY=1
MAX_CITY_EXPOSURE_USD=1
EV_KELLY_FRACTION=0.05
MAX_RISK_PER_TRADE_PCT=0.005
EV_SIZE_SMALL_USD=0.25
EV_SIZE_MEDIUM_USD=0.50
EV_SIZE_LARGE_USD=1.00
```

These settings make each simulated position small while still forcing the bot to choose only its best ideas. If the bot says an order size is zero, it usually means one of the caps has been reached: daily spend, minimum trade size, paper balance, max open positions, city exposure, or risk-per-trade. The scan loop now stops early when a hard cap is reached so the logs do not repeat the same blocked reason for every remaining signal.

Paper mode simulates fills and records them in SQLite. It updates paper balance, open positions, trade history, estimated incentives, and daily summaries. Paper mode is meant for testing the full workflow: market discovery, probability estimation, signal generation, sizing, safety checks, notifications, position exits, and reporting. It does not guarantee that live mode will fill at the same prices.

Live mode routes orders through the Polymarket CLOB adapter. Live mode uses the same scanner, signal engine, sizing rules, and safety checks, but real orders can fail, partially fill, slip, or be rejected. Live mode should only be used after paper mode behaves correctly for multiple days and after all private keys and API credentials are configured carefully.

The bot manages risk with several layers:

- `MAX_TRADE_SIZE_USD` limits any one trade.
- `MAX_DAILY_SPEND_USD` limits total deployed capital per day.
- `MAX_DAILY_LOSS_USD` stops trading after losses.
- `MAX_OPEN_POSITIONS` limits how many positions can be open at once.
- `MAX_ENTRIES_PER_BUCKET` prevents repeatedly buying the same exact bucket.
- `MIN_HOURS_TO_RESOLUTION` avoids markets too close to settlement.
- `MIN_EV_THRESHOLD` filters weak edges.
- The kill switch pauses new trades immediately.

Open positions are checked periodically. The position manager can close positions when the edge disappears, when price converges toward the model probability, when take-profit or stop-loss rules trigger, or when a paired YES/NO position can be merged. In paper mode these exits are simulated. In live mode they require working market access and valid credentials.

Telegram is used as the control panel. You can check `/status`, inspect `/positions`, view `/signals`, force a `/scan`, read `/debug`, pause trading with `/pause`, resume with `/resume`, and view recent `/history`. The `/debug` command is especially useful because it shows whether the bot found weather markets, how many cities were scanned, whether DNS/API calls worked, and why signals may have been skipped.

In short, the bot is a disciplined EV engine for weather contracts. It continuously looks for active Polymarket temperature markets, builds a probability estimate from forecast data, buys only when the model price is better than the market price, keeps each trade small, and records every action so paper performance can be reviewed before live money is used.

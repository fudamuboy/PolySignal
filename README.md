# Polymarket Trading Bot

A structured, modular, and safe Polymarket trading bot built in Python.

## Features
- **Official API Integration**: Uses `py-clob-client` for interacting with Polymarket CLOB.
- **Modular Architecture**: Separate modules for data fetching, filtering, signal generation, risk management, and execution.
- **Risk Management**: Strict rules for capital allocation, daily losses, and open positions.
- **Paper Trading Mode**: Safely test strategies without real capital.
- **Comprehensive Logging**: Detailed logs for every action.

## Installation
1. Clone the repository.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your credentials.

## Usage
Run the bot:
```bash
python -m app.main
```

## Project Structure
- `app/`: Core logic (config, data fetcher, filter, risk, execution, main loop).
- `strategies/`: Trading strategies.
- `storage/`: Database and logs.
- `tests/`: Automated tests.

## Security
- Never share your private key or API credentials.
- The bot uses environment variables for security.
- Always start with `PAPER_TRADING=True`.
# PolySignal

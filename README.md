# ETCUSD Delta Futures Bot

This bot monitors Delta Exchange India's ETCUSD perpetual futures on completed
5-minute candles. Its short-only example signal is a close crossing below
EMA(20) while RSI(14) is below 40. It uses a 1 USD maximum position notional,
1x leverage, an exchange-side 1% stop loss, and a 2% take-profit condition.

The take-profit is monitored by this process and closed with a reduce-only
market order. The stop loss is submitted to Delta as a reduce-only stop-market
order. Keep this process running; a take-profit will not be managed while it is
offline. The exchange-side stop remains active if the process exits, but check
your Delta account manually after any restart or API error.

## Setup

1. Use a Delta API key with only the permissions needed by the bot. Start with
   the corresponding Demo/testnet account and its API key.
2. Copy `.env.example` to `.env` and add the testnet API credentials.
3. Install dependencies with `python -m pip install -r requirements.txt`.
4. Run `python main.py`. With the defaults, it reports signals and does not
   place orders.
5. To place testnet orders, set `ENABLE_TRADING=true`. Testnet products may
   differ from production; the bot stops if ETCUSD perpetual is unavailable,
   inactive, or below the configured notional limit.

Production order placement is disabled by default. To opt in, set
`DELTA_BASE_URL` to the appropriate production URL
(`https://api.india.delta.exchange` or `https://api.delta.exchange`), set
`ENABLE_TRADING=true` and `LIVE_TRADING=true`, and set
`LIVE_TRADING_CONFIRM=I_UNDERSTAND_REAL_ORDERS`. Never use testnet credentials
with a production URL. Do not commit `.env` or share API secrets.

Leveraged futures can lose money rapidly. The 1 USD cap is an order-notional
limit, not a guarantee of maximum loss; slippage, fees, funding, liquidation,
exchange outages, and stop execution behavior can affect results. Validate the
strategy on testnet before enabling production trading.

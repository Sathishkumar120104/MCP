from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any
from urllib.parse import quote_plus

import requests
from dotenv import load_dotenv

LOGGER = logging.getLogger("etcusd_bot")
BAR_SECONDS = 5 * 60
SYMBOL = "ETCUSD"
LIVE_CONFIRMATION = "I_UNDERSTAND_REAL_ORDERS"
PRODUCTION_URLS = {
    "https://api.india.delta.exchange",
    "https://api.delta.exchange",
}
ALLOWED_URLS = PRODUCTION_URLS | {
    "https://cdn-ind.testnet.deltaex.org",
    "https://cdn.testnet.deltaex.org",
}


@dataclass(frozen=True)
class Settings:
    api_key: str
    api_secret: str
    base_url: str
    trading_enabled: bool
    live_trading: bool
    max_notional_usd: Decimal
    stop_loss_pct: Decimal
    take_profit_pct: Decimal
    poll_seconds: int


@dataclass(frozen=True)
class Position:
    size: int
    entry_price: Decimal


@dataclass(frozen=True)
class ActiveTrade:
    size: int
    entry_price: Decimal
    stop_order_id: str | None


class DeltaAPIError(RuntimeError):
    pass


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be true or false")


def load_settings() -> Settings:
    load_dotenv()
    base_url = os.getenv(
        "DELTA_BASE_URL", "https://cdn-ind.testnet.deltaex.org"
    ).rstrip("/")
    if base_url not in ALLOWED_URLS:
        raise ValueError("DELTA_BASE_URL must be a supported Delta Exchange URL")

    trading_enabled = _env_bool("ENABLE_TRADING")
    live_trading = _env_bool("LIVE_TRADING")
    if live_trading and base_url not in PRODUCTION_URLS:
        raise ValueError("LIVE_TRADING=true requires a production Delta URL")
    if base_url in PRODUCTION_URLS and trading_enabled:
        if not live_trading:
            raise ValueError("Production order placement requires LIVE_TRADING=true")
        if os.getenv("LIVE_TRADING_CONFIRM") != LIVE_CONFIRMATION:
            raise ValueError(
                f"Set LIVE_TRADING_CONFIRM={LIVE_CONFIRMATION} to enable production orders"
            )
    if trading_enabled and not (
        os.getenv("DELTA_API_KEY") and os.getenv("DELTA_API_SECRET")
    ):
        raise ValueError("Trading requires DELTA_API_KEY and DELTA_API_SECRET")

    max_notional = Decimal(os.getenv("MAX_NOTIONAL_USD", "1"))
    stop_loss_pct = Decimal(os.getenv("STOP_LOSS_PCT", "0.01"))
    take_profit_pct = Decimal(os.getenv("TAKE_PROFIT_PCT", "0.02"))
    poll_seconds = int(os.getenv("POLL_SECONDS", "5"))
    if max_notional <= 0:
        raise ValueError("MAX_NOTIONAL_USD must be positive")
    if not Decimal(0) < stop_loss_pct < Decimal(1):
        raise ValueError("STOP_LOSS_PCT must be between 0 and 1")
    if not Decimal(0) < take_profit_pct < Decimal(1):
        raise ValueError("TAKE_PROFIT_PCT must be between 0 and 1")
    if poll_seconds < 1:
        raise ValueError("POLL_SECONDS must be at least 1")

    return Settings(
        api_key=os.getenv("DELTA_API_KEY", ""),
        api_secret=os.getenv("DELTA_API_SECRET", ""),
        base_url=base_url,
        trading_enabled=trading_enabled,
        live_trading=live_trading,
        max_notional_usd=max_notional,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        poll_seconds=poll_seconds,
    )


class DeltaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        authenticated: bool = False,
    ) -> Any:
        query = ""
        if params:
            query = "?" + "&".join(
                f"{key}={quote_plus(str(value))}" for key, value in params.items()
            )
        body = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            if payload is not None
            else ""
        )
        headers = {"Content-Type": "application/json"}
        if authenticated:
            if not self.settings.api_key or not self.settings.api_secret:
                raise ValueError("Authenticated request requires Delta API credentials")
            timestamp = str(int(time.time()))
            signature_source = method.upper() + timestamp + path + query + body
            signature = hmac.new(
                self.settings.api_secret.encode(),
                signature_source.encode(),
                hashlib.sha256,
            ).hexdigest()
            headers.update(
                {
                    "api-key": self.settings.api_key,
                    "timestamp": timestamp,
                    "signature": signature,
                }
            )

        response = self.session.request(
            method.upper(),
            self.settings.base_url + path,
            params=params,
            data=body if body else None,
            headers=headers,
            timeout=(3, 10),
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("success"):
            error = data.get("error", data)
            raise DeltaAPIError(f"Delta API request failed: {error}")
        return data.get("result")

    def products(self) -> list[dict[str, Any]]:
        result = self.request(
            "GET",
            "/v2/products",
            params={"contract_types": "perpetual_futures", "page_size": 1000},
        )
        if not isinstance(result, list):
            raise DeltaAPIError("Unexpected product-list response")
        return result

    def candles(self, symbol: str, start: int, end: int) -> list[dict[str, Any]]:
        result = self.request(
            "GET",
            "/v2/history/candles",
            params={
                "resolution": "5m",
                "symbol": symbol,
                "start": start,
                "end": end,
            },
        )
        if not isinstance(result, list):
            raise DeltaAPIError("Unexpected candle response")
        return result

    def ticker(self, symbol: str) -> dict[str, Any]:
        result = self.request("GET", f"/v2/tickers/{symbol}")
        if not isinstance(result, dict):
            raise DeltaAPIError(f"No ticker data returned for {symbol}")
        return result

    def position(self, product_id: int) -> Position:
        result = self.request(
            "GET",
            "/v2/positions",
            params={"product_id": product_id},
            authenticated=True,
        )
        if isinstance(result, list):
            result = next(
                (
                    item
                    for item in result
                    if int(item.get("product_id", -1)) == product_id
                ),
                None,
            )
        if not isinstance(result, dict) or int(result.get("size", 0)) == 0:
            return Position(0, Decimal(0))
        return Position(
            size=int(result["size"]),
            entry_price=Decimal(str(result["entry_price"])),
        )

    def set_leverage(self, product_id: int) -> None:
        self.request(
            "POST",
            f"/v2/products/{product_id}/orders/leverage",
            payload={"leverage": 1},
            authenticated=True,
        )

    def market_order(
        self, product_id: int, size: int, side: str, *, reduce_only: bool
    ) -> dict[str, Any]:
        result = self.request(
            "POST",
            "/v2/orders",
            payload={
                "product_id": product_id,
                "size": size,
                "side": side,
                "order_type": "market_order",
                "reduce_only": reduce_only,
            },
            authenticated=True,
        )
        if not isinstance(result, dict):
            raise DeltaAPIError("Unexpected order response")
        return result

    def stop_order(
        self, product_id: int, size: int, stop_price: Decimal
    ) -> dict[str, Any]:
        result = self.request(
            "POST",
            "/v2/orders",
            payload={
                "product_id": product_id,
                "size": size,
                "side": "buy",
                "order_type": "market_order",
                "stop_order_type": "stop_loss_order",
                "stop_price": str(stop_price),
                "reduce_only": True,
            },
            authenticated=True,
        )
        if not isinstance(result, dict):
            raise DeltaAPIError("Unexpected stop-order response")
        return result

    def cancel_order(self, product_id: int, order_id: str) -> None:
        self.request(
            "DELETE",
            "/v2/orders",
            payload={"id": order_id, "product_id": product_id},
            authenticated=True,
        )


def _ema(values: list[Decimal], period: int) -> list[Decimal]:
    if len(values) < period:
        raise ValueError(f"At least {period} close prices are required")
    multiplier = Decimal(2) / Decimal(period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def _rsi(values: list[Decimal], period: int = 14) -> Decimal:
    changes = [current - previous for previous, current in zip(values, values[1:])]
    if len(changes) < period:
        raise ValueError(f"At least {period + 1} close prices are required")
    initial = changes[:period]
    average_gain = sum(
        (max(change, Decimal(0)) for change in initial), Decimal(0)
    ) / Decimal(period)
    average_loss = sum(
        (max(-change, Decimal(0)) for change in initial), Decimal(0)
    ) / Decimal(period)
    for change in changes[period:]:
        average_gain = (
            average_gain * Decimal(period - 1) + max(change, Decimal(0))
        ) / Decimal(period)
        average_loss = (
            average_loss * Decimal(period - 1) + max(-change, Decimal(0))
        ) / Decimal(period)
    if average_gain == 0 and average_loss == 0:
        return Decimal(50)
    if average_loss == 0:
        return Decimal(100)
    relative_strength = average_gain / average_loss
    return Decimal(100) - Decimal(100) / (Decimal(1) + relative_strength)


def short_signal(closes: list[Decimal]) -> bool:
    if len(closes) < 21:
        return False
    ema_values = _ema(closes, 20)
    return (
        closes[-2] >= ema_values[-2]
        and closes[-1] < ema_values[-1]
        and _rsi(closes) < Decimal(40)
    )


def completed_candles(
    candles: list[dict[str, Any]], now: int
) -> list[dict[str, Any]]:
    completed = [
        candle
        for candle in candles
        if int(candle["time"]) + BAR_SECONDS <= now
    ]
    return sorted(completed, key=lambda candle: int(candle["time"]))


def contracts_for_notional(
    max_notional: Decimal, price: Decimal, contract_value: Decimal
) -> int:
    if price <= 0 or contract_value <= 0:
        raise ValueError("Price and contract value must be positive")
    unit_notional = price * contract_value
    return int((max_notional / unit_notional).to_integral_value(rounding=ROUND_FLOOR))


def round_to_tick(price: Decimal, tick_size: Decimal, *, upward: bool) -> Decimal:
    if price <= 0 or tick_size <= 0:
        raise ValueError("Price and tick size must be positive")
    rounding = ROUND_CEILING if upward else ROUND_FLOOR
    return (price / tick_size).to_integral_value(rounding=rounding) * tick_size


def _find_product(products: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [
        product
        for product in products
        if product.get("symbol") == SYMBOL
        and product.get("contract_type") == "perpetual_futures"
        and product.get("state") == "live"
        and product.get("trading_status") == "operational"
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"{SYMBOL} perpetual futures is not uniquely available and operational "
            "on the configured Delta environment"
        )
    return matches[0]


def _mark_price(ticker: dict[str, Any]) -> Decimal:
    value = ticker.get("mark_price") or ticker.get("close")
    if value is None:
        raise DeltaAPIError("Ticker response has no mark_price or close")
    return Decimal(str(value))


def _open_short(
    client: DeltaClient,
    settings: Settings,
    product: dict[str, Any],
    price: Decimal,
) -> ActiveTrade:
    product_id = int(product["id"])
    contract_value = Decimal(str(product["contract_value"]))
    size = contracts_for_notional(
        settings.max_notional_usd, price, contract_value
    )
    if size < 1:
        raise RuntimeError(
            f"A single {SYMBOL} contract exceeds MAX_NOTIONAL_USD; no order sent"
        )
    actual_notional = Decimal(size) * contract_value * price
    if actual_notional > settings.max_notional_usd:
        raise RuntimeError("Calculated order size exceeds MAX_NOTIONAL_USD")

    client.set_leverage(product_id)
    client.market_order(product_id, size, "sell", reduce_only=False)
    position = client.position(product_id)
    if position.size >= 0:
        raise RuntimeError(
            "Market short did not create a confirmed short position; inspect the "
            "Delta account before restarting"
        )

    tick_size = Decimal(str(product["tick_size"]))
    stop_price = round_to_tick(
        position.entry_price * (Decimal(1) + settings.stop_loss_pct),
        tick_size,
        upward=True,
    )
    try:
        stop_response = client.stop_order(product_id, abs(position.size), stop_price)
        order_id = stop_response.get("id")
        if order_id is None:
            raise DeltaAPIError("Stop response omitted its order id")
    except (DeltaAPIError, requests.RequestException) as error:
        LOGGER.exception("Could not place exchange-side stop; attempting reduce-only close")
        client.market_order(
            product_id, abs(position.size), "buy", reduce_only=True
        )
        raise RuntimeError(
            "Stop placement failed; a reduce-only close was sent. Verify the account."
        ) from error

    LOGGER.warning(
        "Opened %s short: contracts=%s entry=%s notional≈%s USD stop=%s",
        SYMBOL,
        abs(position.size),
        position.entry_price,
        actual_notional,
        stop_price,
    )
    return ActiveTrade(
        size=abs(position.size),
        entry_price=position.entry_price,
        stop_order_id=str(order_id),
    )


def _close_short(
    client: DeltaClient, product_id: int, trade: ActiveTrade
) -> None:
    client.market_order(product_id, trade.size, "buy", reduce_only=True)
    if trade.stop_order_id is not None:
        client.cancel_order(product_id, trade.stop_order_id)
    LOGGER.warning("Closed %s short after the take-profit condition", SYMBOL)


def run() -> None:
    settings = load_settings()
    client = DeltaClient(settings)
    product = _find_product(client.products())
    product_id = int(product["id"])
    if settings.trading_enabled:
        existing_position = client.position(product_id)
        if existing_position.size != 0:
            raise RuntimeError(
                f"An {SYMBOL} position already exists. This bot will not adopt or "
                "modify an existing position."
            )

    LOGGER.info(
        "%s 5m bot started (%s; trading_enabled=%s; live_trading=%s)",
        SYMBOL,
        settings.base_url,
        settings.trading_enabled,
        settings.live_trading,
    )
    last_candle_time: int | None = None
    active_trade: ActiveTrade | None = None

    while True:
        now = int(time.time())
        raw_candles = client.candles(
            SYMBOL, now - 250 * BAR_SECONDS, now
        )
        candles = completed_candles(raw_candles, now)
        if len(candles) < 21:
            raise RuntimeError("Delta returned fewer than 21 completed 5-minute candles")

        ticker_price = _mark_price(client.ticker(SYMBOL))
        if active_trade is not None:
            current_position = client.position(product_id)
            if current_position.size == 0:
                LOGGER.info("Exchange reports the %s position is closed", SYMBOL)
                active_trade = None
            elif current_position.size > 0:
                raise RuntimeError(
                    f"Unexpected long {SYMBOL} position; bot stopped without changing it"
                )
            else:
                active_trade = ActiveTrade(
                    size=abs(current_position.size),
                    entry_price=current_position.entry_price,
                    stop_order_id=active_trade.stop_order_id,
                )
                target_price = active_trade.entry_price * (
                    Decimal(1) - settings.take_profit_pct
                )
                if ticker_price <= target_price:
                    _close_short(client, product_id, active_trade)
                    active_trade = None
        elif last_candle_time != int(candles[-1]["time"]):
            last_candle_time = int(candles[-1]["time"])
            closes = [Decimal(str(candle["close"])) for candle in candles]
            if short_signal(closes):
                LOGGER.info(
                    "Short signal at completed candle %s; mark price=%s",
                    last_candle_time,
                    ticker_price,
                )
                if settings.trading_enabled:
                    active_trade = _open_short(
                        client, settings, product, ticker_price
                    )

        time.sleep(settings.poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run()

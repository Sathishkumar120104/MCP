import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from main import (
    LIVE_CONFIRMATION,
    completed_candles,
    contracts_for_notional,
    load_settings,
    round_to_tick,
    short_signal,
    _find_product,
)


class BotLogicTests(unittest.TestCase):
    def test_completed_candles_excludes_current_bar(self) -> None:
        candles = [
            {"time": 900, "close": "1"},
            {"time": 1200, "close": "2"},
        ]
        self.assertEqual(completed_candles(candles, now=1499), candles[:1])

    def test_contract_sizing_does_not_exceed_notional(self) -> None:
        size = contracts_for_notional(
            Decimal("1"), Decimal("20"), Decimal("0.01")
        )
        self.assertEqual(size, 5)
        self.assertLessEqual(size * Decimal("20") * Decimal("0.01"), Decimal("1"))

    def test_rounding_keeps_short_stop_above_price(self) -> None:
        rounded = round_to_tick(
            Decimal("10.001"), Decimal("0.01"), upward=True
        )
        self.assertEqual(rounded, Decimal("10.01"))

    def test_selects_delta_etcusd_not_a_different_contract(self) -> None:
        product = {
            "id": 19615,
            "symbol": "ETCUSD",
            "contract_type": "perpetual_futures",
            "state": "live",
            "trading_status": "operational",
        }
        self.assertIs(_find_product([product]), product)
        with self.assertRaisesRegex(RuntimeError, "ETCUSD"):
            _find_product([{**product, "symbol": "ETCUSDT"}])

    def test_short_signal_requires_cross_and_low_rsi(self) -> None:
        crossing_closes = [Decimal(100)] * 24 + [Decimal(80)]
        self.assertTrue(short_signal(crossing_closes))
        self.assertFalse(short_signal([Decimal(100)] * 25))

    def test_live_orders_require_explicit_confirmation(self) -> None:
        environment = {
            "DELTA_BASE_URL": "https://api.india.delta.exchange",
            "ENABLE_TRADING": "true",
            "LIVE_TRADING": "true",
            "DELTA_API_KEY": "test-key",
            "DELTA_API_SECRET": "test-secret",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "LIVE_TRADING_CONFIRM"):
                load_settings()

        environment["LIVE_TRADING_CONFIRM"] = LIVE_CONFIRMATION
        with patch.dict(os.environ, environment, clear=True):
            settings = load_settings()
        self.assertTrue(settings.trading_enabled)
        self.assertTrue(settings.live_trading)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from historical_backfill.acquire import _to_ns
from historical_backfill.inventory import ASSETS, binance_specs, days
from historical_backfill.model import AuthorityError


class InventoryTests(unittest.TestCase):
    def test_frozen_assets_and_days(self) -> None:
        self.assertEqual(ASSETS, ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE"))
        self.assertEqual(days("2026-07-07", "2026-07-09"), ["2026-07-07", "2026-07-08"])

    def test_hype_is_perpetual_only_and_others_have_four_sources(self) -> None:
        hype = binance_specs("HYPE", "2026-07-08")
        self.assertEqual([item.family for item in hype], ["um_klines_1m", "um_agg_trades"])
        self.assertEqual(len(binance_specs("BTC", "2026-07-08")), 4)

    def test_source_identity_is_deterministic_and_binds_provider(self) -> None:
        first = binance_specs("BTC", "2026-07-08")[0]
        second = binance_specs("BTC", "2026-07-08")[0]
        self.assertEqual(first.identity(), second.identity())
        self.assertEqual(len(first.identity()), 64)

    def test_timestamp_units_are_explicit(self) -> None:
        self.assertEqual(_to_ns(1_000_000, "us"), 1_000_000_000)
        self.assertEqual(_to_ns(1_000, "ms"), 1_000_000_000)
        with self.assertRaises(AuthorityError):
            _to_ns(1, "inferred")


if __name__ == "__main__":
    unittest.main()

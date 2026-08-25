from __future__ import annotations

import unittest
from pathlib import Path


class PilotContractTests(unittest.TestCase):
    def test_parquet_physical_order_is_not_claimed_as_capture_order(self) -> None:
        from historical_backfill import pilot

        assert pilot.__file__ is not None
        source = Path(pilot.__file__).read_text(encoding="utf-8")
        self.assertIn("physical_order_regressions", source)
        self.assertIn("zip(sorted(times), sorted(times)[1:]", source)
        self.assertNotIn('raise AuthorityError("Arena capture order regressed', source)


if __name__ == "__main__":
    unittest.main()

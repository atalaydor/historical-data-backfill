from __future__ import annotations

import csv
import tempfile
import unittest
import zipfile
from pathlib import Path

import pyarrow.parquet as pq

from historical_backfill.acquire import _normalize_zip
from historical_backfill.inventory import binance_specs
from historical_backfill.model import AuthorityError


class NormalizeTests(unittest.TestCase):
    def _archive(self, root: Path, rows: list[list[object]]) -> Path:
        csv_path = root / "source.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)
        archive = root / "source.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.write(csv_path, "source.csv")
        return archive

    def test_normalization_is_ordered_and_exactly_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self._archive(
                root,
                [
                    ["open_time", "open"],
                    [1783468800000000, "1", "2", "0", "1", "3"],
                    [1783468801000000, "2", "3", "1", "2", "4"],
                ],
            )
            target = root / "normalized.parquet"
            count, first, last = _normalize_zip(
                binance_specs("BTC", "2026-07-08")[0], archive, target
            )
            self.assertEqual(count, 2)
            self.assertLess(first, last)
            self.assertEqual(pq.read_table(target).num_rows, 2)

    def test_time_regression_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self._archive(
                root,
                [
                    [1783468801000000, "1", "2", "0", "1", "3"],
                    [1783468800000000, "2", "3", "1", "2", "4"],
                ],
            )
            with self.assertRaises(AuthorityError):
                _normalize_zip(
                    binance_specs("BTC", "2026-07-08")[0],
                    archive,
                    root / "normalized.parquet",
                )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_backfill.chainlink_semantics import build_semantic_authority
from historical_backfill.http import Receipt


def _payload(epoch: int, window: int) -> bytes:
    slug = f"btc-updown-5m-{epoch}"
    start = "2026-08-13T23:55:00Z" if window == 30 else "2026-08-14T00:00:00Z"
    end = "2026-08-14T00:00:00Z" if window == 30 else "2026-08-14T00:05:00Z"
    source = f"https://data.chain.link/streams/btc-usd-twap-{window}s-streams"
    return json.dumps(
        [
            {
                "eventMetadata": {"priceToBeat": "60000.123", "finalPrice": "60000.123"},
                "markets": [
                    {
                        "slug": slug,
                        "eventStartTime": start,
                        "endDate": end,
                        "description": "Up if greater than or equal to opening; otherwise Down.",
                        "resolutionSource": source,
                        "cryptoMarketConfigId": f"btc-5m-twap-{window}",
                        "cryptoMarketConfig": {
                            "id": f"btc-5m-twap-{window}",
                            "asset": "btc",
                            "duration": "5m",
                            "twapEnabled": True,
                            "twapLookbackSeconds": window,
                        },
                    }
                ],
            }
        ]
    ).encode()


class ChainlinkSemanticTests(unittest.TestCase):
    def test_boundary_proves_empty_target_overlap(self) -> None:
        def fake_download(url: str, target: Path, maximum_bytes: int) -> Receipt:
            del maximum_bytes
            epoch = int(url.rsplit("-", 1)[1])
            window = 30 if epoch == 1_786_665_300 else 60
            target.write_bytes(_payload(epoch, window))
            return Receipt(url, '"etag"', None, target.stat().st_size)

        with tempfile.TemporaryDirectory() as temp, patch(
            "historical_backfill.chainlink_semantics.download", fake_download
        ):
            built = build_semantic_authority(Path(temp) / "authority")
            raw = json.loads((built / "semantic-determination.json").read_text())
        self.assertEqual(raw["current_regime"]["effective_start"], "2026-08-14T00:00:00Z")
        self.assertEqual(raw["overlap"]["candidate_markets"], 0)
        self.assertEqual(raw["overlap"]["independent_utc_hour_clusters"], 0)
        self.assertEqual(raw["power"]["required_independent_utc_hour_clusters"], 125)
        self.assertEqual(raw["decision"], "ABANDON")
        self.assertFalse(raw["acquisition_launched"])


if __name__ == "__main__":
    unittest.main()

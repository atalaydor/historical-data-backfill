from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class ContractTests(unittest.TestCase):
    def test_preregistration_is_frozen_and_classes_are_separate(self) -> None:
        raw = json.loads((ROOT / "config" / "preregistration.json").read_text())
        self.assertEqual(raw["canonical_target"]["observed_markets"], 5660)
        decisions = {item["id"]: item["decision"] for item in raw["candidates"]}
        self.assertEqual(decisions["binance-spot-perp-tape-v1"], "PROCEED")
        self.assertEqual(decisions["arena-cap-book-v1"], "BOUNDED_PILOT")
        self.assertEqual(decisions["chainlink-current-twap-60s-v1"], "ABANDON")

    def test_workflow_has_gate_parallel_compute_and_exclusive_assembly(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "run-1.yml").read_text()
        self.assertIn("needs: [canary-binance, canary-coinbase]", workflow)
        self.assertIn("max-parallel: 7", workflow)
        self.assertIn("historical-backfill assemble-btc", workflow)
        self.assertIn("needs: [production-btc, production-coinbase]", workflow)
        self.assertIn("historical-run-1-canonical-publication", workflow)
        self.assertNotIn("actions/cache", workflow)
        self.assertNotIn("upload-artifact", workflow)


if __name__ == "__main__":
    unittest.main()

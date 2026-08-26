from __future__ import annotations

import json
import unittest
from pathlib import Path

from historical_backfill.prospective_plane import identity

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

    def test_run_2_reconciliation_closes_acquisition_without_recovery(self) -> None:
        raw = json.loads((ROOT / "docs" / "run-2-reconciliation.json").read_text())
        self.assertEqual(raw["binance"]["planned_segments"], 28)
        self.assertEqual(raw["binance"]["durable_segments"], 28)
        self.assertEqual(raw["coinbase_btc"]["durable_acquisitions"], 1)
        self.assertEqual(raw["arena_class_b_pilot"]["decision"], "ABANDON")
        self.assertEqual(raw["result"]["residual"], [])
        self.assertTrue(raw["result"]["btc_certified"])

    def test_btc_handoff_is_class_a_causal_and_immutable(self) -> None:
        raw = json.loads((ROOT / "docs" / "btc-gamma-linux-handoff.json").read_text())
        self.assertEqual(raw["external_evidence"]["class"], "A")
        self.assertEqual(raw["external_evidence"]["complete_stages"], 5)
        self.assertFalse(raw["integrity_contract"]["class_b_evidence_allowed"])
        self.assertTrue(raw["integrity_contract"]["require_event_time_at_or_before_feature_cutoff"])
        self.assertFalse(raw["integrity_contract"]["prospective_authority_mutation_allowed"])

    def test_prospective_plane_contract_and_workflows_preserve_authority_boundary(self) -> None:
        raw = json.loads(
            (ROOT / "config" / "prospective-processing-plane-contract.json").read_text()
        )
        self.assertEqual(raw["input_schema"], "prospective-sealed-input.v1")
        self.assertEqual(
            raw["transformations"]["v55_chainlink_twap60"],
            "v55-normalized-twap60-primitives.v1",
        )
        self.assertEqual(
            raw["transformations"]["v51_polymarket_full_depth"],
            "v51-normalized-depth-primitives.v1",
        )
        workflow = (ROOT / ".github" / "workflows" / "prospective-processing-plane.yml").read_text()
        canary = (
            ROOT / ".github" / "workflows" / "prospective-processing-plane-canary.yml"
        ).read_text()
        self.assertIn("permissions: {contents: read}", workflow)
        self.assertIn("permissions: {contents: write}", workflow)
        self.assertIn("max-parallel: 4", workflow)
        self.assertIn(
            "authority_type }}-${{ needs.validate-input.outputs.processing_identity", workflow
        )
        self.assertIn("plane-negative-canary", canary)
        self.assertIn("authenticated-rerun-v55", canary)
        self.assertNotIn("score", workflow.lower())

    def test_v55_seven_asset_adapter_contract_is_self_authenticated(self) -> None:
        raw = json.loads(
            (ROOT / "config" / "v55-seven-asset-adapter-contract.json").read_text()
        )
        claimed = raw.pop("adapter_identity")
        self.assertEqual(identity(raw), claimed)
        self.assertEqual(
            raw["canonical_assets"], ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE"]
        )
        self.assertIn("cross-asset substitution or pooling", raw["prohibitions"])


if __name__ == "__main__":
    unittest.main()

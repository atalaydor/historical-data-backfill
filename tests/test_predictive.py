from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from historical_backfill.model import AuthorityError, canonical_bytes
from historical_backfill.predictive import (
    BINANCE_SEGMENTS,
    EXPECTED_CANDIDATES,
    INTERVAL_END,
    INTERVAL_START,
    TARGET_SEGMENTS,
    TRACK_IDS,
    candidate_epochs,
    normalize_gamma_market,
)

ROOT = Path(__file__).parents[1]


def _gamma(epoch: int, prices: list[str] | None = None) -> bytes:
    return json.dumps(
        {
            "id": "123",
            "slug": f"btc-updown-5m-{epoch}",
            "conditionId": "0x" + "a" * 64,
            "endDate": "2026-07-08T23:05:00Z",
            "closed": True,
            "outcomes": json.dumps(["Up", "Down"]),
            "outcomePrices": json.dumps(prices or ["1", "0"]),
            "clobTokenIds": json.dumps(["123", "456"]),
            "events": [
                {
                    "slug": f"btc-updown-5m-{epoch}",
                    "startTime": "2026-07-08T23:00:00Z",
                }
            ],
        },
        separators=(",", ":"),
    ).encode()


class PredictiveValidationTests(unittest.TestCase):
    def test_frozen_inventory_is_exact_and_partitioned_once(self) -> None:
        expected = candidate_epochs(INTERVAL_START, INTERVAL_END)
        self.assertEqual(len(expected), EXPECTED_CANDIDATES)
        flattened = [
            epoch for start, end in TARGET_SEGMENTS for epoch in candidate_epochs(start, end)
        ]
        self.assertEqual(flattened, list(expected))
        self.assertEqual(len(BINANCE_SEGMENTS), 4)

    def test_official_target_normalization_binds_identity_time_cutoff_and_outcome(self) -> None:
        epoch = 1783551600
        row = normalize_gamma_market(epoch, _gamma(epoch))
        self.assertEqual(row["start"], "2026-07-08T23:00:00Z")
        self.assertEqual(row["end"], "2026-07-08T23:05:00Z")
        self.assertEqual(row["decision_cutoff"], "2026-07-08T23:04:00Z")
        self.assertEqual(row["outcome"], "Up")
        self.assertEqual(row["token_up"], "123")
        self.assertEqual(len(str(row["target_source_identity"])), 64)

    def test_ambiguous_outcome_and_time_divergence_fail_closed(self) -> None:
        epoch = 1783551600
        with self.assertRaises(AuthorityError):
            normalize_gamma_market(epoch, _gamma(epoch, ["0.5", "0.5"]))
        raw = json.loads(_gamma(epoch))
        raw["endDate"] = "2026-07-08T23:10:00Z"
        with self.assertRaises(AuthorityError):
            normalize_gamma_market(epoch, json.dumps(raw).encode())

    def test_preregistration_identity_and_scientific_freeze(self) -> None:
        raw = json.loads(
            (ROOT / "config" / "btc-predictive-validation-preregistration.json").read_text()
        )
        identity = raw.pop("preregistration_identity")
        self.assertEqual(identity, hashlib.sha256(canonical_bytes(raw)).hexdigest())
        self.assertEqual(raw["statistics"]["required_evaluation_clusters_per_track"], 125)
        self.assertEqual(raw["statistics"]["bonferroni_alpha_per_track"], 0.003125)
        self.assertEqual(tuple(item["id"] for item in raw["tracks"]), TRACK_IDS)
        self.assertEqual(raw["cohort"]["candidate_markets"], EXPECTED_CANDIDATES)

    def test_plan_identity_and_workflow_topology(self) -> None:
        raw = json.loads((ROOT / "config" / "btc-predictive-validation-plan.json").read_text())
        identity = raw.pop("plan_identity")
        self.assertEqual(identity, hashlib.sha256(canonical_bytes(raw)).hexdigest())
        workflow = (ROOT / ".github" / "workflows" / "btc-predictive-validation.yml").read_text()
        self.assertIn("max-parallel: 8", workflow)
        self.assertIn("max-parallel: 4", workflow)
        self.assertIn("needs: [target, binance, coinbase]", workflow)
        self.assertIn("btc-predictive-v1-exclusive-publication", workflow)
        self.assertNotIn("actions/cache", workflow)
        self.assertNotIn("upload-artifact", workflow)

    def test_step_1_inventory_is_content_addressed_and_fails_at_11_of_125(self) -> None:
        raw = json.loads(
            (ROOT / "docs" / "step-1-untouched-strict-residual-inventory.json").read_text()
        )
        identity = raw.pop("inventory_identity")
        self.assertEqual(identity, hashlib.sha256(canonical_bytes(raw)).hexdigest())
        self.assertFalse(raw["strict_residual_recoverable"])
        self.assertEqual(raw["authorities"][1]["eligible_complete_utc_hour_clusters"], 11)
        self.assertEqual(raw["decision"], "STEP_1_FAIL_PROCEED_STEP_2")

    def test_additive_final_certification_and_handoff_are_content_addressed(self) -> None:
        certification = json.loads(
            (ROOT / "docs" / "btc-predictive-validation-final-certification.json").read_text()
        )
        certification_identity = certification.pop("certification_identity")
        self.assertEqual(
            certification_identity,
            hashlib.sha256(canonical_bytes(certification)).hexdigest(),
        )
        self.assertEqual(
            certification["authority"]["authority_identity"],
            "00cd31e94351518c07ef590e1bff0d24291795adadca77bef8a270c8ac552ef9",
        )
        self.assertEqual(certification["frozen_bindings"]["eligible_markets"], 3552)
        self.assertEqual(certification["frozen_bindings"]["evaluation_utc_hour_clusters"], 148)
        self.assertEqual(certification["power"]["gate"], "PASS")

        handoff = json.loads(
            (ROOT / "docs" / "btc-predictive-validation-gamma-linux-handoff-v2.json").read_text()
        )
        handoff_identity = handoff.pop("handoff_identity")
        self.assertEqual(handoff_identity, hashlib.sha256(canonical_bytes(handoff)).hexdigest())
        self.assertEqual(
            handoff["acquisition_plan"]["identity"],
            "87224486fc3ff65b044a45c4005f42cc55dbd940d7212348d4ac9973139058e8",
        )
        self.assertEqual(tuple(handoff["supported_tracks"]), TRACK_IDS)
        self.assertEqual(handoff["claim_boundary"]["scoring_owner"], "Gamma/Linux")


if __name__ == "__main__":
    unittest.main()

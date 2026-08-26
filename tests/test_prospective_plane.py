from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from historical_backfill.model import AuthorityError, hash_file
from historical_backfill.prospective_plane import (
    FIXTURE_ROOT,
    SUPPORTED_V55_ASSETS,
    V51,
    V55,
    _contract,
    _read_jsonl,
    _v55_transform,
    build_chunk,
    identity,
    processing_plan,
    validate_chunk_indexes,
    validate_input_contract,
    verify_chunk_directory,
)

COMMIT = "1" * 40


def _reauthenticate(contract: dict[str, Any]) -> None:
    core = copy.deepcopy(contract)
    core.pop("input_manifest_identity")
    contract["input_manifest_identity"] = identity(core)


def _partitions(stem: str) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for ordinal in range(2):
        path = FIXTURE_ROOT / f"{stem}-part-{ordinal}.jsonl"
        size, digest = hash_file(path)
        result.append(
            {
                "ordinal": ordinal,
                "asset_id": ordinal + 1,
                "asset_name": f"fixture--{digest}--{path.name}",
                "bytes": size,
                "sha256": digest,
                "row_count": path.read_text(encoding="utf-8").count("\n"),
                "format": "canonical-jsonl",
            }
        )
    return result


def _rows(stem: str, ordinal: int) -> list[dict[str, object]]:
    path = FIXTURE_ROOT / f"{stem}-part-{ordinal}.jsonl"
    return _read_jsonl(path, path.read_text(encoding="utf-8").count("\n"))


def _generalized_rows(contract: dict[str, Any], ordinal: int = 0) -> list[dict[str, Any]]:
    bindings = contract["market_bindings"]
    assert isinstance(bindings, list)
    binding = next(item for item in bindings if item["partition_ordinal"] == ordinal)
    source = contract["v55_source_authority"]
    assert isinstance(source, dict)
    common = {
        "market_id": binding["market_id"],
        "condition_id": binding["condition_id"],
        "canonical_asset": source["canonical_asset"],
        "symbol": source["chainlink_symbol"],
        "chainlink_feed_id": source["chainlink_feed_id"],
        "chainlink_source_id": source["chainlink_source_id"],
        "source_binding_identity": source["source_binding_identity"],
        "window_s": source["twap_window_s"],
        "full_accuracy_scale": source["full_accuracy_scale"],
        "session_id": "fixture-seven-asset-session",
    }
    return [
        {
            **common,
            "record_type": "report",
            "report_id": binding["opening_report_id"],
            "role": "opening",
            "full_accuracy_value": "100000000000000000000",
            "source_timestamp_ms": 1_000_000,
            "effective_timestamp_ms": 1_000_000,
            "linux_received_at_ms": 1_010_000,
            "linux_receipt_seq": 1,
        },
        {
            **common,
            "record_type": "report",
            "report_id": f"{source['canonical_asset']}-observation",
            "role": "observation",
            "full_accuracy_value": "101000000000000000000",
            "source_timestamp_ms": 1_180_000,
            "effective_timestamp_ms": 1_180_000,
            "linux_received_at_ms": 1_200_000,
            "linux_receipt_seq": 2,
        },
    ]


class ProspectivePlaneTests(unittest.TestCase):
    def test_contract_is_generic_but_preserves_owner_semantics(self) -> None:
        v51 = _contract(V51, "owner/repo", 1, "sealed", _partitions("v51"))
        v55 = _contract(V55, "owner/repo", 1, "sealed", _partitions("v55"))
        self.assertNotEqual(v51["input_manifest_identity"], v55["input_manifest_identity"])
        self.assertEqual(set(v51["market_bindings"][0]["tokens"]), {"Up", "Down"})
        self.assertEqual(v55["market_bindings"][0]["symbol"], "btc/usd")
        self.assertEqual(v55["market_bindings"][0]["window_s"], 60)
        self.assertFalse(v55["causality"]["receipt_times_may_be_inferred"])
        self.assertEqual(v55["causality"]["receipt_order_scope"], "partition-global")
        self.assertFalse(v51["continuity"]["cross_gap_reconstruction_allowed"])

    def test_missing_or_ambiguous_authority_fails_closed(self) -> None:
        contract = _contract(V55, "owner/repo", 1, "sealed", _partitions("v55"))
        contract.pop("causality")
        with self.assertRaises(AuthorityError):
            validate_input_contract(contract)

    def test_chunk_matrix_must_exactly_match_sealed_partitions(self) -> None:
        contract = _contract(V55, "owner/repo", 1, "sealed", _partitions("v55"))
        validate_chunk_indexes(contract, "[0,1]")
        with self.assertRaises(AuthorityError):
            validate_chunk_indexes(contract, "[0]")

    def test_v55_is_deterministic_and_excludes_gap_and_missing_opening(self) -> None:
        contract = _contract(V55, "owner/repo", 1, "sealed", _partitions("v55"))

        def fake_fetch(
            contract_value: object, ordinal: int, directory: Path
        ) -> list[dict[str, object]]:
            del contract_value, directory
            return _rows("v55", ordinal)

        with (
            tempfile.TemporaryDirectory() as temp,
            patch("historical_backfill.prospective_plane.fetch_partition", fake_fetch),
        ):
            first = Path(temp) / "first"
            second = Path(temp) / "second"
            one = build_chunk(contract, 1, COMMIT, first)
            two = build_chunk(contract, 1, COMMIT, second)
            self.assertEqual(one, two)
            for name in ("primitives.jsonl", "market-status.jsonl", "exclusions.jsonl"):
                self.assertEqual(hash_file(first / name), hash_file(second / name))
            status = (first / "market-status.jsonl").read_text()
            exclusions = (first / "exclusions.jsonl").read_text()
        self.assertIn("linux_declared_gap_intersects_market_window", exclusions)
        self.assertIn("missing_or_ambiguous_opening_report", exclusions)
        self.assertEqual(status.count('"eligible":false'), 2)

    def test_legacy_btc_fixture_byte_identities_are_unchanged(self) -> None:
        contract = _contract(V55, "owner/repo", 1, "sealed", _partitions("v55"))

        with (
            tempfile.TemporaryDirectory() as temp,
            patch(
                "historical_backfill.prospective_plane.fetch_partition",
                lambda contract_value, ordinal, directory: _rows("v55", ordinal),
            ),
        ):
            output = Path(temp) / "legacy"
            build_chunk(contract, 0, COMMIT, output)
            observed = {
                name: hash_file(output / name)
                for name in ("primitives.jsonl", "market-status.jsonl", "exclusions.jsonl")
            }
        self.assertEqual(
            observed,
            {
                "primitives.jsonl": (
                    1122,
                    "55bcb6a1c61a947a9d3763797fadeea77f2e1895ebde2d00c854c029628c469d",
                ),
                "market-status.jsonl": (
                    194,
                    "284097ef60d3f8806b0a7d056ebbd538688a0fac335e06a17d46e1d24b6c4a78",
                ),
                "exclusions.jsonl": (
                    94,
                    "a3e87010b26616a2525d072c6b2cb26250e1c7a8b244d0ed6703f14188357a0c",
                ),
            },
        )

    def test_all_seven_v55_assets_are_independent_deterministic_and_idempotent(self) -> None:
        source_identities: set[str] = set()
        outputs: set[str] = set()
        for asset in SUPPORTED_V55_ASSETS:
            contract = _contract(
                V55, "owner/repo", 1, "sealed", _partitions("v55"), v55_asset=asset
            )
            source = contract["v55_source_authority"]
            source_identities.add(source["source_binding_identity"])
            rows = _generalized_rows(contract)
            first = _v55_transform(contract, 0, rows)
            restarted = _v55_transform(contract, 0, copy.deepcopy(rows))
            self.assertEqual(first, restarted)
            self.assertEqual(first[0][0]["canonical_asset"], asset)
            self.assertEqual(first[0][0]["chainlink_feed_id"], f"fixture-chainlink-feed:{asset}")
            self.assertEqual(first[1][0]["remaining_time_ms"], 60_000)
            outputs.add(identity(first))
        self.assertEqual(len(source_identities), len(SUPPORTED_V55_ASSETS))
        self.assertEqual(len(outputs), len(SUPPORTED_V55_ASSETS))

    def test_v55_wrong_source_and_cross_asset_contamination_fail_closed(self) -> None:
        eth = _contract(V55, "owner/repo", 1, "sealed", _partitions("v55"), v55_asset="ETH")
        for field, wrong in (
            ("canonical_asset", "SOL"),
            ("symbol", "sol/usd"),
            ("chainlink_feed_id", "fixture-chainlink-feed:SOL"),
            ("chainlink_source_id", "fixture-chainlink-source:SOL"),
        ):
            rows = _generalized_rows(eth)
            rows[0][field] = wrong
            with self.subTest(field=field), self.assertRaises(AuthorityError):
                _v55_transform(eth, 0, rows)

        contaminated = copy.deepcopy(eth)
        contaminated["partitions"][0]["canonical_asset"] = "SOL"
        _reauthenticate(contaminated)
        with self.assertRaises(AuthorityError):
            validate_input_contract(contaminated)

        pooled = copy.deepcopy(eth)
        pooled["market_bindings"][1]["canonical_asset"] = "SOL"
        _reauthenticate(pooled)
        with self.assertRaises(AuthorityError):
            validate_input_contract(pooled)

    def test_v55_unsupported_or_missing_source_authority_fails_closed(self) -> None:
        with self.assertRaises(AuthorityError):
            _contract(V55, "owner/repo", 1, "sealed", _partitions("v55"), v55_asset="ADA")
        eth = _contract(V55, "owner/repo", 1, "sealed", _partitions("v55"), v55_asset="ETH")
        eth["v55_source_authority"].pop("chainlink_feed_id")
        with self.assertRaises(AuthorityError):
            validate_input_contract(eth)

    def test_generalized_v55_gap_remains_explicit_and_excludes_market(self) -> None:
        contract = _contract(
            V55, "owner/repo", 1, "sealed", _partitions("v55"), v55_asset="HYPE"
        )
        rows = _generalized_rows(contract)
        gap = {
            key: rows[-1][key]
            for key in (
                "market_id",
                "condition_id",
                "canonical_asset",
                "symbol",
                "chainlink_feed_id",
                "chainlink_source_id",
                "source_binding_identity",
                "window_s",
                "session_id",
            )
        }
        gap.update(
            {
                "record_type": "gap",
                "linux_received_at_ms": 1_210_000,
                "linux_receipt_seq": 3,
                "gap_start_ms": 1_100_000,
                "gap_end_ms": 1_150_000,
                "reason": "fixture-explicit-gap",
            }
        )
        primitives, statuses, exclusions = _v55_transform(contract, 0, [*rows, gap])
        self.assertTrue(any(item["primitive_type"] == "gap" for item in primitives))
        self.assertFalse(statuses[0]["eligible"])
        self.assertTrue(
            any(
                item["reason"] == "linux_declared_gap_intersects_market_window"
                for item in exclusions
            )
        )

    def test_v51_preserves_events_and_excludes_gapped_or_missing_state(self) -> None:
        contract = _contract(V51, "owner/repo", 1, "sealed", _partitions("v51"))

        def fake_fetch(
            contract_value: object, ordinal: int, directory: Path
        ) -> list[dict[str, object]]:
            del contract_value, directory
            return _rows("v51", ordinal)

        with (
            tempfile.TemporaryDirectory() as temp,
            patch("historical_backfill.prospective_plane.fetch_partition", fake_fetch),
        ):
            valid = Path(temp) / "valid"
            invalid = Path(temp) / "invalid"
            valid_manifest = build_chunk(contract, 0, COMMIT, valid)
            invalid_manifest = build_chunk(contract, 1, COMMIT, invalid)
            valid_rows = (valid / "primitives.jsonl").read_text()
            invalid_exclusions = (invalid / "exclusions.jsonl").read_text()
            valid_exclusions = (valid / "exclusions.jsonl").read_text()
        self.assertEqual(valid_manifest["counts"]["eligible_markets"], 1)
        self.assertEqual(valid_rows.count('"primitive_type":"topn_anchor_book"'), 4)
        self.assertIn("prospective-empty-set.v1", valid_exclusions)
        self.assertEqual(invalid_manifest["counts"]["excluded_markets"], 2)
        self.assertIn("linux_declared_gap_intersects_market_window", invalid_exclusions)
        self.assertIn("missing_authoritative_book:Down", invalid_exclusions)

    def test_corrupt_derived_bytes_fail_closed(self) -> None:
        contract = _contract(V55, "owner/repo", 1, "sealed", _partitions("v55"))

        def fake_fetch(
            contract_value: object, ordinal: int, directory: Path
        ) -> list[dict[str, object]]:
            del contract_value, directory
            return _rows("v55", ordinal)

        with (
            tempfile.TemporaryDirectory() as temp,
            patch("historical_backfill.prospective_plane.fetch_partition", fake_fetch),
        ):
            output = Path(temp) / "chunk"
            build_chunk(contract, 0, COMMIT, output)
            verify_chunk_directory(output)
            with (output / "primitives.jsonl").open("ab") as handle:
                handle.write(b"{}\n")
            with self.assertRaises(AuthorityError):
                verify_chunk_directory(output)

    def test_processing_identity_binds_commit_runtime_input_and_transform(self) -> None:
        contract = _contract(V55, "owner/repo", 1, "sealed", _partitions("v55"))
        first = processing_plan(contract, COMMIT)
        second = processing_plan(contract, "2" * 40)
        self.assertNotEqual(first["processing_identity"], second["processing_identity"])
        self.assertEqual(len(first["stage_tags"]), 2)
        self.assertIn(contract["source_authority_identity"][:16], first["derived_tag"])


if __name__ == "__main__":
    unittest.main()

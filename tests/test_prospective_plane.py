from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_backfill.model import AuthorityError, hash_file
from historical_backfill.prospective_plane import (
    FIXTURE_ROOT,
    V51,
    V55,
    _contract,
    _read_jsonl,
    build_chunk,
    processing_plan,
    validate_chunk_indexes,
    validate_input_contract,
    verify_chunk_directory,
)

COMMIT = "1" * 40


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
        self.assertEqual(valid_manifest["counts"]["eligible_markets"], 1)
        self.assertEqual(valid_rows.count('"primitive_type":"topn_anchor_book"'), 4)
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

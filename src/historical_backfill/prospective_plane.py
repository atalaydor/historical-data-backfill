from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sys
import tempfile
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .model import AuthorityError, canonical_bytes, hash_file, write_json
from .release import Asset, GitHubReleases

INPUT_SCHEMA = "prospective-sealed-input.v1"
OUTPUT_SCHEMA = "prospective-derived-authority.v1"
HANDOFF_SCHEMA = "prospective-gamma-linux-handoff.v1"
V51 = "v51_polymarket_full_depth"
V55 = "v55_chainlink_twap60"
AUTHORITY_TYPES = (V51, V55)
TRANSFORMS = {
    V51: "v51-normalized-depth-primitives.v1",
    V55: "v55-normalized-twap60-primitives.v1",
}
CANARY_INPUT_TAG = "prospective-plane-canary-input-v1"
CANARY_NAMESPACE = "fixture-canary-not-production"
MAX_CONTRACT_BYTES = 1_000_000
MAX_PARTITION_BYTES = 500_000_000
MAX_ROWS = 5_000_000
SHA256 = re.compile(r"[0-9a-f]{64}")
INTEGER = re.compile(r"-?[0-9]+")
COMMIT = re.compile(r"[0-9a-f]{40}")
FIXTURE_ROOT = Path(__file__).parents[2] / "tests" / "fixtures" / "prospective_plane"
SETUP_PYTHON_SHA = "e797f83bcb11b83ae66e0230d6156d7c80228e7c"
UPLOAD_ARTIFACT_SHA = "ea165f8d65b6e75b540449e92b4886f43607fa02"
DOWNLOAD_ARTIFACT_SHA = "634f93cb2916e3fdff6788551b99b062d0335ce0"


def identity(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def identified(value: dict[str, Any], field: str) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result[field] = identity(result)
    return result


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise AuthorityError(f"{label} is not an exact SHA-256")
    return value


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AuthorityError(f"{label} is missing")
    return value


def _require_int(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AuthorityError(f"{label} is invalid")
    return value


def _exact_decimal(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise AuthorityError(f"{label} must be an exact decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise AuthorityError(f"{label} is not decimal") from exc
    if not parsed.is_finite() or parsed < 0:
        raise AuthorityError(f"{label} is not a finite non-negative decimal")
    return value


def _without(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result.pop(key, None)
    return result


def validate_input_contract(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AuthorityError("sealed input contract is not an object")
    contract = copy.deepcopy(raw)
    if contract.get("schema_version") != INPUT_SCHEMA:
        raise AuthorityError("sealed input schema diverged")
    authority_type = contract.get("authority_type")
    if authority_type not in AUTHORITY_TYPES:
        raise AuthorityError("sealed input authority type is unsupported")
    _require_text(contract.get("authority_version"), "authority_version")
    _require_sha(contract.get("source_authority_identity"), "source_authority_identity")
    claimed = _require_sha(contract.get("input_manifest_identity"), "input_manifest_identity")
    if identity(_without(contract, "input_manifest_identity")) != claimed:
        raise AuthorityError("sealed input manifest identity diverged")
    if contract.get("authority_owner") != "Gamma/Linux factory":
        raise AuthorityError("Linux authority owner is not explicit")
    repository = _require_text(contract.get("remote_repository"), "remote_repository")
    if "/" not in repository:
        raise AuthorityError("remote repository locator is malformed")
    release = contract.get("input_release")
    if not isinstance(release, dict):
        raise AuthorityError("input release locator is missing")
    _require_int(release.get("release_id"), "input release id", minimum=1)
    _require_text(release.get("tag"), "input release tag")
    partitions = contract.get("partitions")
    if not isinstance(partitions, list) or not partitions:
        raise AuthorityError("sealed input partitions are missing")
    observed_ordinals: list[int] = []
    asset_ids: set[int] = set()
    for partition in partitions:
        if not isinstance(partition, dict):
            raise AuthorityError("partition locator is malformed")
        ordinal = _require_int(partition.get("ordinal"), "partition ordinal")
        observed_ordinals.append(ordinal)
        asset_id = _require_int(partition.get("asset_id"), "partition asset id", minimum=1)
        if asset_id in asset_ids:
            raise AuthorityError("partition asset is duplicated")
        asset_ids.add(asset_id)
        _require_text(partition.get("asset_name"), "partition asset name")
        _require_sha(partition.get("sha256"), "partition SHA-256")
        size = _require_int(partition.get("bytes"), "partition bytes", minimum=1)
        if size > MAX_PARTITION_BYTES:
            raise AuthorityError("partition exceeds the processing byte breaker")
        _require_int(partition.get("row_count"), "partition row count", minimum=1)
        if partition.get("format") != "canonical-jsonl":
            raise AuthorityError("partition format is unsupported")
    if sorted(observed_ordinals) != list(range(len(partitions))):
        raise AuthorityError("partition ordinals are not contiguous")
    bindings = contract.get("market_bindings")
    if not isinstance(bindings, list) or not bindings:
        raise AuthorityError("market bindings are missing")
    market_ids: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            raise AuthorityError("market binding is malformed")
        market_id = _require_text(binding.get("market_id"), "market_id")
        if market_id in market_ids:
            raise AuthorityError("market binding is duplicated")
        market_ids.add(market_id)
        _require_text(binding.get("condition_id"), "condition_id")
        start = _require_int(binding.get("window_start_ms"), "window_start_ms")
        end = _require_int(binding.get("window_end_ms"), "window_end_ms")
        cutoff = _require_int(binding.get("decision_cutoff_ms"), "decision_cutoff_ms")
        if not start < cutoff <= end:
            raise AuthorityError("market window/cutoff binding is invalid")
        ordinal = _require_int(binding.get("partition_ordinal"), "partition_ordinal")
        if ordinal >= len(partitions):
            raise AuthorityError("market references an absent partition")
        if authority_type == V51:
            tokens = binding.get("tokens")
            if not isinstance(tokens, dict) or set(tokens) != {"Up", "Down"}:
                raise AuthorityError("V51 Up/Down token binding is incomplete")
            if len({_require_text(item, "token id") for item in tokens.values()}) != 2:
                raise AuthorityError("V51 token identities are ambiguous")
        else:
            if binding.get("symbol") != "btc/usd" or binding.get("window_s") != 60:
                raise AuthorityError("V55 BTC/USD TWAP60 binding diverged")
            _require_text(binding.get("opening_report_id"), "opening report id")
    causality = contract.get("causality")
    if not isinstance(causality, dict):
        raise AuthorityError("causality authority is missing")
    event_time_field = "source_timestamp_ms" if authority_type == V55 else "event_timestamp_ms"
    required_causality = {
        "owner": "Gamma/Linux factory",
        "event_time_field": event_time_field,
        "receipt_time_field": "linux_received_at_ms",
        "receipt_order_field": "linux_receipt_seq",
        "receipt_order_scope": "partition-global",
        "decision_cutoff_rule": "linux_received_at_ms <= decision_cutoff_ms",
        "receipt_times_may_be_inferred": False,
    }
    if any(causality.get(key) != expected for key, expected in required_causality.items()):
        raise AuthorityError("causality contract diverged")
    continuity = contract.get("continuity")
    if not isinstance(continuity, dict):
        raise AuthorityError("continuity authority is missing")
    if (
        continuity.get("owner") != "Gamma/Linux factory"
        or continuity.get("gap_record_type") != "gap"
        or continuity.get("interpolation_allowed") is not False
        or continuity.get("cross_gap_reconstruction_allowed") is not False
    ):
        raise AuthorityError("continuity contract is not fail-closed")
    transform = contract.get("transformation_contract")
    if not isinstance(transform, dict) or transform.get("identity") != TRANSFORMS[authority_type]:
        raise AuthorityError("transformation contract is not permitted")
    allowed = transform.get("permitted_primitive_families")
    if not isinstance(allowed, list) or not allowed:
        raise AuthorityError("permitted primitive families are absent")
    if authority_type == V51:
        top_n = _require_int(transform.get("top_n_levels"), "top_n_levels", minimum=1)
        if top_n > 50:
            raise AuthorityError("top-N depth bound is unsafe")
    expected = contract.get("expected_output")
    if not isinstance(expected, dict) or expected.get("schema_version") != OUTPUT_SCHEMA:
        raise AuthorityError("expected output schema diverged")
    exclusions = contract.get("exclusion_authority")
    if (
        not isinstance(exclusions, dict)
        or exclusions.get("missing_policy") != "exclude-no-synthesis"
    ):
        raise AuthorityError("exclusion authority is incomplete")
    return contract


def _contract(
    authority_type: str,
    repository: str,
    release_id: int,
    release_tag: str,
    partitions: list[dict[str, Any]],
) -> dict[str, Any]:
    bindings: list[dict[str, Any]]
    if authority_type == V55:
        bindings = [
            {
                "market_id": "m-v55-ok",
                "condition_id": "c-v55-ok",
                "window_start_ms": 1_000_000,
                "window_end_ms": 1_300_000,
                "decision_cutoff_ms": 1_240_000,
                "partition_ordinal": 0,
                "symbol": "btc/usd",
                "window_s": 60,
                "opening_report_id": "r-v55-open",
            },
            {
                "market_id": "m-v55-gap",
                "condition_id": "c-v55-gap",
                "window_start_ms": 1_000_000,
                "window_end_ms": 1_300_000,
                "decision_cutoff_ms": 1_240_000,
                "partition_ordinal": 1,
                "symbol": "btc/usd",
                "window_s": 60,
                "opening_report_id": "r-v55-gap-open",
            },
            {
                "market_id": "m-v55-missing",
                "condition_id": "c-v55-missing",
                "window_start_ms": 1_000_000,
                "window_end_ms": 1_300_000,
                "decision_cutoff_ms": 1_240_000,
                "partition_ordinal": 1,
                "symbol": "btc/usd",
                "window_s": 60,
                "opening_report_id": "r-v55-missing-open",
            },
        ]
        permitted = [
            "exact_twap60_report_trajectory",
            "opening_and_latest_causal_report_bindings",
            "gap_and_exclusion_primitives",
        ]
        parameters: dict[str, Any] = {"full_accuracy_scale": 18}
    else:
        bindings = [
            {
                "market_id": "m-v51-ok",
                "condition_id": "c-v51-ok",
                "window_start_ms": 1_000_000,
                "window_end_ms": 1_300_000,
                "decision_cutoff_ms": 1_240_000,
                "partition_ordinal": 0,
                "tokens": {"Up": "t-v51-up", "Down": "t-v51-down"},
            },
            {
                "market_id": "m-v51-gap",
                "condition_id": "c-v51-gap",
                "window_start_ms": 1_000_000,
                "window_end_ms": 1_300_000,
                "decision_cutoff_ms": 1_240_000,
                "partition_ordinal": 1,
                "tokens": {"Up": "t-v51-gap-up", "Down": "t-v51-gap-down"},
            },
            {
                "market_id": "m-v51-missing",
                "condition_id": "c-v51-missing",
                "window_start_ms": 1_000_000,
                "window_end_ms": 1_300_000,
                "decision_cutoff_ms": 1_240_000,
                "partition_ordinal": 1,
                "tokens": {"Up": "t-v51-missing-up", "Down": "t-v51-missing-down"},
            },
        ]
        permitted = [
            "normalized_depth_and_trade_events",
            "decision_and_decision_minus_60s_topn_book_primitives",
            "gap_and_exclusion_primitives",
        ]
        parameters = {"top_n_levels": 2, "anchors_ms_before_cutoff": [60_000, 0]}
    event_field = "source_timestamp_ms" if authority_type == V55 else "event_timestamp_ms"
    core: dict[str, Any] = {
        "schema_version": INPUT_SCHEMA,
        "authority_type": authority_type,
        "authority_version": "fixture-v1",
        "authority_owner": "Gamma/Linux factory",
        "namespace": CANARY_NAMESPACE,
        "source_authority_identity": identity(
            {"fixture": authority_type, "version": 1, "not_production": True}
        ),
        "remote_repository": repository,
        "input_release": {"release_id": release_id, "tag": release_tag},
        "partitions": partitions,
        "market_bindings": bindings,
        "causality": {
            "owner": "Gamma/Linux factory",
            "event_time_field": event_field,
            "receipt_time_field": "linux_received_at_ms",
            "receipt_order_field": "linux_receipt_seq",
            "receipt_order_scope": "partition-global",
            "decision_cutoff_rule": "linux_received_at_ms <= decision_cutoff_ms",
            "receipt_times_may_be_inferred": False,
        },
        "continuity": {
            "owner": "Gamma/Linux factory",
            "session_field": "session_id",
            "gap_record_type": "gap",
            "interpolation_allowed": False,
            "cross_gap_reconstruction_allowed": False,
        },
        "transformation_contract": {
            "identity": TRANSFORMS[authority_type],
            "permitted_primitive_families": permitted,
            **parameters,
            "outcome_consumption_allowed": False,
            "feature_selection_allowed": False,
        },
        "exclusion_authority": {
            "owner": "Gamma/Linux factory plus deterministic processing exclusions",
            "missing_policy": "exclude-no-synthesis",
            "linux_exclusions": [],
        },
        "expected_output": {
            "schema_version": OUTPUT_SCHEMA,
            "canonical_serialization": "UTF-8 canonical JSON/JSONL; sorted keys; LF; no NaN",
        },
    }
    return validate_input_contract(identified(core, "input_manifest_identity"))


def _asset_by_id(assets: Iterable[Asset], asset_id: int) -> Asset:
    matches = [asset for asset in assets if asset.asset_id == asset_id]
    if len(matches) != 1:
        raise AuthorityError(f"remote asset identity is ambiguous: {asset_id}")
    return matches[0]


def _verify_expected_asset(asset: Asset, expected: dict[str, Any]) -> None:
    if (
        asset.name != expected.get("asset_name")
        or asset.size != expected.get("bytes")
        or asset.state != "uploaded"
    ):
        raise AuthorityError("remote asset metadata diverged before download")
    digest = _require_sha(expected.get("sha256"), "expected asset SHA-256")
    if digest not in asset.name:
        raise AuthorityError("remote asset name does not bind its expected hash")


def _download_verified(
    backend: GitHubReleases,
    release_id: int,
    expected: dict[str, Any],
    target: Path,
    maximum_bytes: int,
) -> Asset:
    asset_id = _require_int(expected.get("asset_id"), "asset id", minimum=1)
    asset = _asset_by_id(backend.assets(release_id), asset_id)
    _verify_expected_asset(asset, expected)
    observed_size, observed_hash = backend.download_asset(asset, target, maximum_bytes)
    if (observed_size, observed_hash) != (expected["bytes"], expected["sha256"]):
        raise AuthorityError("downloaded asset content diverged")
    return asset


def fetch_contract(
    repository: str,
    release_id: int,
    contract_asset_id: int,
    contract_sha256: str,
    contract_bytes: int,
    directory: Path,
) -> dict[str, Any]:
    _require_sha(contract_sha256, "contract SHA-256")
    backend = GitHubReleases(repository)
    release = backend.release(release_id)
    if release.get("draft") or release.get("prerelease"):
        raise AuthorityError("sealed input release is not immutable final authority")
    asset = _asset_by_id(backend.assets(release_id), contract_asset_id)
    if (
        asset.size != contract_bytes
        or asset.state != "uploaded"
        or contract_sha256 not in asset.name
    ):
        raise AuthorityError("sealed contract locator diverged before download")
    target = directory / "sealed-input-contract.json"
    observed = backend.download_asset(asset, target, MAX_CONTRACT_BYTES)
    if observed != (contract_bytes, contract_sha256):
        raise AuthorityError("sealed contract bytes/hash diverged")
    try:
        contract = validate_input_contract(json.loads(target.read_bytes()))
    except json.JSONDecodeError as exc:
        raise AuthorityError("sealed contract is invalid JSON") from exc
    locator = contract["input_release"]
    if (
        contract["remote_repository"] != repository
        or locator["release_id"] != release_id
        or locator["tag"] != release.get("tag_name")
    ):
        raise AuthorityError("sealed contract remote release binding diverged")
    return contract


def _read_jsonl(path: Path, expected_rows: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.endswith("\n"):
                raise AuthorityError(f"JSONL line lacks LF terminator: {line_number}")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AuthorityError(f"invalid JSONL row: {line_number}") from exc
            if not isinstance(row, dict):
                raise AuthorityError(f"JSONL row is not an object: {line_number}")
            rows.append(row)
            if len(rows) > MAX_ROWS:
                raise AuthorityError("partition crossed the row breaker")
    if len(rows) != expected_rows:
        raise AuthorityError("partition row count diverged")
    return rows


def fetch_partition(
    contract: dict[str, Any], ordinal: int, directory: Path
) -> list[dict[str, Any]]:
    partitions = contract["partitions"]
    if ordinal < 0 or ordinal >= len(partitions):
        raise AuthorityError("chunk ordinal is outside the sealed contract")
    partition = partitions[ordinal]
    backend = GitHubReleases(contract["remote_repository"])
    release_id = contract["input_release"]["release_id"]
    release = backend.release(release_id)
    if release.get("draft") or release.get("tag_name") != contract["input_release"]["tag"]:
        raise AuthorityError("partition release is not immutable bound authority")
    target = directory / f"partition-{ordinal}.jsonl"
    _download_verified(backend, release_id, partition, target, MAX_PARTITION_BYTES)
    return _read_jsonl(target, partition["row_count"])


def runtime_identity() -> dict[str, object]:
    return {
        "runner_contract": "ubuntu-24.04",
        "runner_image_os": os.environ.get("ImageOS", "local-non-actions"),
        "runner_image_version": os.environ.get("ImageVersion", "local-non-actions"),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_cache_tag": sys.implementation.cache_tag,
        "pip_version": importlib.metadata.version("pip"),
        "setup_python_action": SETUP_PYTHON_SHA,
        "upload_artifact_action": UPLOAD_ARTIFACT_SHA,
        "download_artifact_action": DOWNLOAD_ARTIFACT_SHA,
        "dependencies": "exact project pins at source_commit; no adapter runtime dependency",
    }


def processing_plan(contract: dict[str, Any], source_commit: str) -> dict[str, Any]:
    if COMMIT.fullmatch(source_commit) is None:
        raise AuthorityError("processing source commit is not exact")
    transform = contract["transformation_contract"]
    core = {
        "schema_version": "prospective-processing-identity.v1",
        "source_commit": source_commit,
        "runtime": runtime_identity(),
        "input_contract_identity": contract["input_manifest_identity"],
        "transformation_contract_identity": identity(transform),
        "transformation_contract": transform,
        "canonical_rules": {
            "json": "sort_keys,separators-comma-colon,UTF-8,LF,no-NaN",
            "row_order": "market_id,linux_receipt_seq,primitive_type,token_or_report_identity",
            "chunk_order": "sealed partition ordinal ascending",
            "missing": "explicit exclusion; no interpolation or substitution",
        },
    }
    processing = identified(core, "processing_identity")
    token = processing["processing_identity"][:16]
    source = contract["source_authority_identity"][:16]
    namespace = "canary" if contract.get("namespace") == CANARY_NAMESPACE else "authority"
    stage_tags = [
        f"prospective-plane-{namespace}-stage-{source}-{token}-{ordinal}"
        for ordinal in range(len(contract["partitions"]))
    ]
    return {
        **processing,
        "stage_tags": stage_tags,
        "derived_tag": f"prospective-plane-{namespace}-derived-{source}-{token}",
    }


def _binding_map(contract: dict[str, Any], ordinal: int) -> dict[str, dict[str, Any]]:
    return {
        item["market_id"]: item
        for item in contract["market_bindings"]
        if item["partition_ordinal"] == ordinal
    }


def _common_row(row: dict[str, Any], binding: dict[str, Any]) -> tuple[int, int]:
    if (
        row.get("market_id") != binding["market_id"]
        or row.get("condition_id") != binding["condition_id"]
    ):
        raise AuthorityError("row market/condition binding diverged")
    receipt_seq = _require_int(row.get("linux_receipt_seq"), "linux_receipt_seq", minimum=1)
    received = _require_int(row.get("linux_received_at_ms"), "linux_received_at_ms", minimum=1)
    _require_text(row.get("session_id"), "session_id")
    return receipt_seq, received


def _validate_receipt_order(
    rows: list[dict[str, Any]], bindings: dict[str, dict[str, Any]]
) -> None:
    previous_seq = -1
    previous_received = -1
    for row in rows:
        market_id = _require_text(row.get("market_id"), "row market_id")
        binding = bindings.get(market_id)
        if binding is None:
            raise AuthorityError("row market is outside the sealed partition binding")
        sequence, received = _common_row(row, binding)
        if sequence <= previous_seq or received < previous_received:
            raise AuthorityError("Linux receipt authority regressed")
        previous_seq = sequence
        previous_received = received


def _v55_transform(
    contract: dict[str, Any], ordinal: int, rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    bindings = _binding_map(contract, ordinal)
    _validate_receipt_order(rows, bindings)
    primitives: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    by_market: dict[str, list[dict[str, Any]]] = {market: [] for market in bindings}
    gaps: dict[str, list[dict[str, Any]]] = {market: [] for market in bindings}
    for row in rows:
        market_id = row["market_id"]
        binding = bindings[market_id]
        record_type = row.get("record_type")
        sequence, received = _common_row(row, binding)
        if record_type == "gap":
            start = _require_int(row.get("gap_start_ms"), "gap_start_ms", minimum=1)
            end = _require_int(row.get("gap_end_ms"), "gap_end_ms", minimum=1)
            if end <= start:
                raise AuthorityError("gap bounds are invalid")
            gap = {
                "primitive_type": "gap",
                "market_id": market_id,
                "condition_id": binding["condition_id"],
                "linux_receipt_seq": sequence,
                "linux_received_at_ms": received,
                "session_id": row["session_id"],
                "gap_start_ms": start,
                "gap_end_ms": end,
                "reason": _require_text(row.get("reason"), "gap reason"),
            }
            if start < binding["decision_cutoff_ms"] and end >= binding["window_start_ms"]:
                gaps[market_id].append(gap)
            primitives.append(gap)
            continue
        if record_type != "report":
            raise AuthorityError("V55 record type is unsupported")
        if row.get("symbol") != "btc/usd" or row.get("window_s") != 60:
            raise AuthorityError("V55 report is not BTC/USD TWAP60")
        report_id = _require_text(row.get("report_id"), "report_id")
        source_time = _require_int(row.get("source_timestamp_ms"), "source_timestamp_ms", minimum=1)
        full = row.get("full_accuracy_value")
        if not isinstance(full, str) or INTEGER.fullmatch(full) is None:
            raise AuthorityError("V55 full-accuracy value is not an E18 integer string")
        if row.get("role") not in {"opening", "observation"}:
            raise AuthorityError("V55 report role is invalid")
        primitive = {
            "primitive_type": "twap60_report",
            "market_id": market_id,
            "condition_id": binding["condition_id"],
            "report_id": report_id,
            "role": row["role"],
            "symbol": "btc/usd",
            "window_s": 60,
            "full_accuracy_value": full,
            "full_accuracy_scale": 18,
            "source_timestamp_ms": source_time,
            "linux_received_at_ms": received,
            "linux_receipt_seq": sequence,
            "session_id": row["session_id"],
            "causal_at_decision_cutoff": received <= binding["decision_cutoff_ms"],
        }
        primitives.append(primitive)
        by_market[market_id].append(primitive)
    for market_id, binding in sorted(bindings.items()):
        causal = [item for item in by_market[market_id] if item["causal_at_decision_cutoff"]]
        openings = [
            item
            for item in causal
            if item["report_id"] == binding["opening_report_id"] and item["role"] == "opening"
        ]
        reasons: list[str] = []
        if len(openings) != 1:
            reasons.append("missing_or_ambiguous_opening_report")
        if not causal:
            reasons.append("missing_causal_report_at_cutoff")
        if gaps[market_id]:
            reasons.append("linux_declared_gap_intersects_market_window")
        if reasons:
            for reason in reasons:
                exclusions.append(
                    {
                        "market_id": market_id,
                        "condition_id": binding["condition_id"],
                        "reason": reason,
                        "scope": "derived_trajectory_and_anchor_binding",
                    }
                )
            statuses.append(
                {
                    "market_id": market_id,
                    "condition_id": binding["condition_id"],
                    "eligible": False,
                    "exclusion_reasons": sorted(reasons),
                }
            )
        else:
            latest = max(causal, key=lambda item: int(item["linux_receipt_seq"]))
            statuses.append(
                {
                    "market_id": market_id,
                    "condition_id": binding["condition_id"],
                    "eligible": True,
                    "opening_report_id": openings[0]["report_id"],
                    "latest_causal_report_id": latest["report_id"],
                    "causal_report_count": len(causal),
                    "decision_cutoff_ms": binding["decision_cutoff_ms"],
                }
            )
    return primitives, statuses, exclusions


def _levels(value: object, side: str) -> list[list[str]]:
    if not isinstance(value, list) or not value:
        raise AuthorityError(f"{side} levels are missing")
    result: list[list[str]] = []
    prices: list[Decimal] = []
    for level in value:
        if not isinstance(level, list) or len(level) != 2:
            raise AuthorityError(f"{side} level is malformed")
        price = _exact_decimal(level[0], f"{side} price")
        size = _exact_decimal(level[1], f"{side} size")
        if Decimal(size) <= 0:
            raise AuthorityError(f"{side} size is not positive")
        prices.append(Decimal(price))
        result.append([price, size])
    expected = sorted(prices, reverse=side == "bids")
    if prices != expected or len(set(prices)) != len(prices):
        raise AuthorityError(f"{side} levels are not strictly ordered")
    return result


def _v51_transform(
    contract: dict[str, Any], ordinal: int, rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    bindings = _binding_map(contract, ordinal)
    _validate_receipt_order(rows, bindings)
    primitives: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    books: dict[str, list[dict[str, Any]]] = {market: [] for market in bindings}
    gaps: dict[str, list[dict[str, Any]]] = {market: [] for market in bindings}
    top_n = contract["transformation_contract"]["top_n_levels"]
    for row in rows:
        market_id = row["market_id"]
        binding = bindings[market_id]
        record_type = row.get("record_type")
        sequence, received = _common_row(row, binding)
        event_sequence = _require_int(row.get("event_sequence"), "event_sequence", minimum=1)
        base = {
            "primitive_type": record_type,
            "market_id": market_id,
            "condition_id": binding["condition_id"],
            "linux_receipt_seq": sequence,
            "linux_received_at_ms": received,
            "event_sequence": event_sequence,
            "session_id": row["session_id"],
        }
        if record_type == "gap":
            start = _require_int(row.get("gap_start_ms"), "gap_start_ms", minimum=1)
            end = _require_int(row.get("gap_end_ms"), "gap_end_ms", minimum=1)
            if end <= start:
                raise AuthorityError("gap bounds are invalid")
            primitive = {
                **base,
                "gap_start_ms": start,
                "gap_end_ms": end,
                "reason": _require_text(row.get("reason"), "gap reason"),
            }
            if start < binding["decision_cutoff_ms"] and end >= binding["window_start_ms"]:
                gaps[market_id].append(primitive)
            primitives.append(primitive)
            continue
        event_time = _require_int(row.get("event_timestamp_ms"), "event_timestamp_ms", minimum=1)
        token_id = _require_text(row.get("token_id"), "token_id")
        if token_id not in binding["tokens"].values():
            raise AuthorityError("V51 row token is outside the Up/Down binding")
        base.update({"event_timestamp_ms": event_time, "token_id": token_id})
        if record_type == "book":
            bids = _levels(row.get("bids"), "bids")
            asks = _levels(row.get("asks"), "asks")
            if Decimal(bids[0][0]) >= Decimal(asks[0][0]):
                raise AuthorityError("V51 authoritative book is crossed")
            primitive = {**base, "bids": bids, "asks": asks}
            books[market_id].append(primitive)
        elif record_type == "price_change":
            side = row.get("side")
            if side not in {"bid", "ask"}:
                raise AuthorityError("V51 price-change side is invalid")
            primitive = {
                **base,
                "side": side,
                "price": _exact_decimal(row.get("price"), "price-change price"),
                "size": _exact_decimal(row.get("size"), "price-change size"),
            }
        elif record_type == "trade":
            side = row.get("side")
            if side not in {"buy", "sell"}:
                raise AuthorityError("V51 trade side is invalid")
            primitive = {
                **base,
                "side": side,
                "price": _exact_decimal(row.get("price"), "trade price"),
                "size": _exact_decimal(row.get("size"), "trade size"),
            }
        else:
            raise AuthorityError("V51 record type is unsupported")
        primitive["causal_at_decision_cutoff"] = received <= binding["decision_cutoff_ms"]
        primitives.append(primitive)
    for market_id, binding in sorted(bindings.items()):
        reasons: list[str] = []
        if gaps[market_id]:
            reasons.append("linux_declared_gap_intersects_market_window")
        anchors = [binding["decision_cutoff_ms"] - 60_000, binding["decision_cutoff_ms"]]
        anchor_rows: list[dict[str, Any]] = []
        for anchor in anchors:
            for outcome, token_id in sorted(binding["tokens"].items()):
                candidates = [
                    item
                    for item in books[market_id]
                    if item["token_id"] == token_id and item["linux_received_at_ms"] <= anchor
                ]
                if not candidates:
                    reasons.append(f"missing_authoritative_book:{outcome}:{anchor}")
                    continue
                latest = max(candidates, key=lambda item: int(item["linux_receipt_seq"]))
                anchor_rows.append(
                    {
                        "primitive_type": "topn_anchor_book",
                        "market_id": market_id,
                        "condition_id": binding["condition_id"],
                        "outcome": outcome,
                        "token_id": token_id,
                        "anchor_ms": anchor,
                        "source_book_receipt_seq": latest["linux_receipt_seq"],
                        "source_book_event_sequence": latest["event_sequence"],
                        "source_book_event_timestamp_ms": latest["event_timestamp_ms"],
                        "source_book_received_at_ms": latest["linux_received_at_ms"],
                        "top_n": top_n,
                        "bids": latest["bids"][:top_n],
                        "asks": latest["asks"][:top_n],
                    }
                )
        reasons = sorted(set(reasons))
        if reasons:
            for reason in reasons:
                exclusions.append(
                    {
                        "market_id": market_id,
                        "condition_id": binding["condition_id"],
                        "reason": reason,
                        "scope": "decision_anchor_and_continuity_primitives",
                    }
                )
            statuses.append(
                {
                    "market_id": market_id,
                    "condition_id": binding["condition_id"],
                    "eligible": False,
                    "exclusion_reasons": reasons,
                }
            )
        else:
            primitives.extend(anchor_rows)
            statuses.append(
                {
                    "market_id": market_id,
                    "condition_id": binding["condition_id"],
                    "eligible": True,
                    "decision_cutoff_ms": binding["decision_cutoff_ms"],
                    "anchor_primitive_count": len(anchor_rows),
                }
            )
    return primitives, statuses, exclusions


def _row_key(row: dict[str, Any]) -> tuple[str, int, str, str]:
    return (
        str(row.get("market_id", "")),
        int(row.get("linux_receipt_seq", row.get("anchor_ms", -1))),
        str(row.get("primitive_type", row.get("reason", ""))),
        str(row.get("token_id", row.get("report_id", ""))),
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    ordered = sorted((copy.deepcopy(row) for row in rows), key=_row_key)
    path.write_bytes(b"".join(canonical_bytes(row) for row in ordered))


def build_chunk(
    contract: dict[str, Any], ordinal: int, source_commit: str, output: Path
) -> dict[str, Any]:
    validate_input_contract(contract)
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as temp:
        rows = fetch_partition(contract, ordinal, Path(temp))
    if contract["authority_type"] == V55:
        primitives, statuses, exclusions = _v55_transform(contract, ordinal, rows)
    else:
        primitives, statuses, exclusions = _v51_transform(contract, ordinal, rows)
    _write_jsonl(output / "primitives.jsonl", primitives)
    _write_jsonl(output / "market-status.jsonl", statuses)
    _write_jsonl(output / "exclusions.jsonl", exclusions)
    plan = processing_plan(contract, source_commit)
    files = {
        path.name: {"bytes": hash_file(path)[0], "sha256": hash_file(path)[1]}
        for path in sorted(output.glob("*.jsonl"))
    }
    manifest_core: dict[str, Any] = {
        "schema_version": "prospective-derived-chunk.v1",
        "authority_type": contract["authority_type"],
        "namespace": contract.get("namespace", "production"),
        "processing_identity": plan["processing_identity"],
        "input_authority_identity": contract["source_authority_identity"],
        "input_manifest_identity": contract["input_manifest_identity"],
        "transformation_contract_identity": plan["transformation_contract_identity"],
        "partition_ordinal": ordinal,
        "input_partition": contract["partitions"][ordinal],
        "stage_tag": plan["stage_tags"][ordinal],
        "stage_partition": (
            f"prospective/{contract['authority_type']}/{plan['processing_identity']}/chunk-{ordinal}"
        ),
        "counts": {
            "input_rows": len(rows),
            "primitive_rows": len(primitives),
            "market_status_rows": len(statuses),
            "eligible_markets": sum(item["eligible"] is True for item in statuses),
            "excluded_markets": sum(item["eligible"] is False for item in statuses),
            "exclusion_rows": len(exclusions),
        },
        "files": files,
        "scientific_scoring_performed": False,
    }
    manifest = identified(manifest_core, "chunk_manifest_identity")
    write_json(output / "manifest.json", manifest)
    return manifest


def verify_chunk_directory(directory: Path) -> dict[str, Any]:
    path = directory / "manifest.json"
    try:
        manifest = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorityError("chunk manifest is absent or invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "prospective-derived-chunk.v1"
    ):
        raise AuthorityError("chunk manifest schema diverged")
    claimed = _require_sha(manifest.get("chunk_manifest_identity"), "chunk manifest identity")
    if identity(_without(manifest, "chunk_manifest_identity")) != claimed:
        raise AuthorityError("chunk manifest identity diverged")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != {
        "primitives.jsonl",
        "market-status.jsonl",
        "exclusions.jsonl",
    }:
        raise AuthorityError("chunk file inventory diverged")
    for name, expected in files.items():
        if not isinstance(expected, dict):
            raise AuthorityError("chunk file contract is malformed")
        observed = hash_file(directory / name)
        if observed != (expected.get("bytes"), expected.get("sha256")):
            raise AuthorityError(f"chunk file diverged: {name}")
    return manifest


def stage_chunk(directory: Path) -> str:
    manifest = verify_chunk_directory(directory)
    backend = GitHubReleases()
    tag = manifest["stage_tag"]
    existing = backend.release_by_tag(tag)
    if existing is not None and not existing.get("draft"):
        raise AuthorityError("chunk staging tag unexpectedly became canonical")
    if backend.stage_complete(tag, manifest["stage_partition"]):
        _verify_authenticated_stage(backend, tag, manifest["stage_partition"], directory)
        return "NO_OP_AUTHENTICATED_CHUNK"
    backend.publish_directory(tag, manifest["stage_partition"], directory)
    if not backend.stage_complete(tag, manifest["stage_partition"]):
        raise AuthorityError("chunk staging did not become complete")
    _verify_authenticated_stage(backend, tag, manifest["stage_partition"], directory)
    return "PUBLISHED_AUTHENTICATED_CHUNK"


def _verify_authenticated_stage(
    backend: GitHubReleases, tag: str, partition: str, directory: Path
) -> None:
    release = backend.release_by_tag(tag)
    if release is None or not release.get("draft"):
        raise AuthorityError("authenticated staging release is absent")
    prefix = partition.replace("/", "--") + "--"
    remote = {
        item.name: item
        for item in backend.assets(int(release["id"]))
        if item.name.startswith(prefix)
    }
    expected: dict[str, tuple[int, str]] = {}
    for path in directory.iterdir():
        if path.is_file():
            size, digest = hash_file(path)
            expected[f"{prefix}{digest}--{path.name}"] = (size, digest)
    if set(remote) != set(expected):
        raise AuthorityError("authenticated staging inventory diverged")
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for name, (size, digest) in expected.items():
            asset = remote[name]
            if asset.state != "uploaded" or asset.size != size:
                raise AuthorityError("authenticated staging metadata diverged")
            if backend.download_asset(asset, root / name, MAX_PARTITION_BYTES) != (
                size,
                digest,
            ):
                raise AuthorityError("authenticated staging bytes diverged")


def _lines(path: Path) -> list[dict[str, Any]]:
    if path.stat().st_size == 0:
        return []
    return _read_jsonl(path, path.read_text(encoding="utf-8").count("\n"))


def _download_release_assets(
    backend: GitHubReleases, release_id: int, directory: Path
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for asset in backend.assets(release_id):
        if asset.state != "uploaded":
            raise AuthorityError("release contains a non-uploaded asset")
        target = directory / asset.name
        size, digest = backend.download_asset(asset, target, MAX_PARTITION_BYTES)
        if digest not in asset.name or size != asset.size:
            raise AuthorityError("release asset is not content-addressed")
        result[asset.name] = target
    return result


def assemble(
    contract: dict[str, Any], source_commit: str, output_repository: str | None = None
) -> str:
    plan = processing_plan(contract, source_commit)
    backend = GitHubReleases(output_repository)
    derived_tag = _require_text(plan.get("derived_tag"), "derived tag")
    existing = backend.release_by_tag(derived_tag)
    if existing is not None:
        if not existing.get("draft"):
            final_assets = backend.assets(int(existing["id"]))
            if not any(item.name.endswith("--gamma-linux-handoff.json") for item in final_assets):
                raise AuthorityError("final derived release lacks its handoff")
            return derived_tag
    primitives: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    source_chunks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for ordinal, tag in enumerate(plan["stage_tags"]):
            release = backend.release_by_tag(tag)
            if release is None or not release.get("draft"):
                raise AuthorityError(f"staging release is missing or not isolated: {tag}")
            chunk_dir = root / f"chunk-{ordinal}"
            chunk_dir.mkdir()
            downloaded_assets = _download_release_assets(backend, int(release["id"]), chunk_dir)
            logical: dict[str, Path] = {}
            for name, path in downloaded_assets.items():
                for suffix in (
                    "manifest.json",
                    "primitives.jsonl",
                    "market-status.jsonl",
                    "exclusions.jsonl",
                ):
                    if name.endswith(f"--{suffix}"):
                        if suffix in logical:
                            raise AuthorityError("staging logical asset is duplicated")
                        logical[suffix] = path
            if len(logical) != 4:
                raise AuthorityError("staging logical inventory is incomplete")
            materialized = root / f"materialized-{ordinal}"
            materialized.mkdir()
            for name, path in logical.items():
                shutil.copyfile(path, materialized / name)
            manifest = verify_chunk_directory(materialized)
            if (
                manifest["partition_ordinal"] != ordinal
                or manifest["processing_identity"] != plan["processing_identity"]
                or manifest["input_manifest_identity"] != contract["input_manifest_identity"]
            ):
                raise AuthorityError("staging chunk binding diverged")
            primitives.extend(_lines(materialized / "primitives.jsonl"))
            statuses.extend(_lines(materialized / "market-status.jsonl"))
            exclusions.extend(_lines(materialized / "exclusions.jsonl"))
            source_chunks.append(
                {
                    "partition_ordinal": ordinal,
                    "stage_tag": tag,
                    "stage_release_id": int(release["id"]),
                    "chunk_manifest_identity": manifest["chunk_manifest_identity"],
                }
            )
        if len({item["market_id"] for item in statuses}) != len(statuses):
            raise AuthorityError("assembled market status population is duplicated")
        if {item["market_id"] for item in statuses} != {
            item["market_id"] for item in contract["market_bindings"]
        }:
            raise AuthorityError("assembled market population diverged")
        publication = root / "derived-authority"
        publication.mkdir()
        _write_jsonl(publication / "primitives.jsonl", primitives)
        _write_jsonl(publication / "market-status.jsonl", statuses)
        _write_jsonl(publication / "exclusions.jsonl", exclusions)
        files = {
            path.name: {"bytes": hash_file(path)[0], "sha256": hash_file(path)[1]}
            for path in sorted(publication.glob("*.jsonl"))
        }
        manifest_core: dict[str, Any] = {
            "schema_version": OUTPUT_SCHEMA,
            "authority_type": contract["authority_type"],
            "namespace": contract.get("namespace", "production"),
            "processing_identity": plan["processing_identity"],
            "processing_source_commit": source_commit,
            "input_authority_identity": contract["source_authority_identity"],
            "input_manifest_identity": contract["input_manifest_identity"],
            "transformation_contract_identity": plan["transformation_contract_identity"],
            "transformation_contract": contract["transformation_contract"],
            "canonical_row_order_identity": identity([_row_key(item) for item in primitives]),
            "source_to_derived_provenance": source_chunks,
            "market_population": {
                "total": len(statuses),
                "eligible": sum(item["eligible"] is True for item in statuses),
                "excluded": sum(item["eligible"] is False for item in statuses),
            },
            "exclusion_rows": len(exclusions),
            "files": files,
            "cutoff_semantics": contract["causality"]["decision_cutoff_rule"],
            "outcomes_consumed": False,
            "scientific_scoring_performed": False,
        }
        manifest = identified(manifest_core, "derived_authority_identity")
        write_json(publication / "derived-manifest.json", manifest)
        backend.publish_directory(derived_tag, "derived-authority", publication)
    return derived_tag


def _verify_final_core(directory: Path) -> dict[str, Any]:
    manifests = list(directory.glob("*--derived-manifest.json"))
    if len(manifests) != 1:
        raise AuthorityError("derived manifest inventory is ambiguous")
    try:
        manifest = json.loads(manifests[0].read_bytes())
    except json.JSONDecodeError as exc:
        raise AuthorityError("derived manifest is invalid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != OUTPUT_SCHEMA:
        raise AuthorityError("derived manifest schema diverged")
    claimed = _require_sha(manifest.get("derived_authority_identity"), "derived identity")
    if identity(_without(manifest, "derived_authority_identity")) != claimed:
        raise AuthorityError("derived authority identity diverged")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise AuthorityError("derived file inventory is absent")
    for logical, expected in files.items():
        matches = list(directory.glob(f"*--{logical}"))
        if len(matches) != 1 or hash_file(matches[0]) != (
            expected.get("bytes"),
            expected.get("sha256"),
        ):
            raise AuthorityError(f"derived authority file diverged: {logical}")
    return manifest


def reconcile(derived_tag: str, output_repository: str | None = None) -> dict[str, Any]:
    backend = GitHubReleases(output_repository)
    release = backend.release_by_tag(derived_tag)
    if release is None:
        raise AuthorityError("derived publication release is absent")
    release_id = int(release["id"])
    if not release.get("draft"):
        assets = backend.assets(release_id)
        handoffs = [item for item in assets if item.name.endswith("--gamma-linux-handoff.json")]
        if len(handoffs) != 1:
            raise AuthorityError("final release handoff is absent or ambiguous")
        return {"status": "NO_OP_ALREADY_FINAL", "release_id": release_id, "tag": derived_tag}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        _download_release_assets(backend, release_id, root)
        manifest = _verify_final_core(root)
        handoff_core: dict[str, Any] = {
            "schema_version": HANDOFF_SCHEMA,
            "repository": backend.repository,
            "processing_commit": manifest["processing_source_commit"],
            "processing_identity": manifest["processing_identity"],
            "input_authority_identity": manifest["input_authority_identity"],
            "input_manifest_identity": manifest["input_manifest_identity"],
            "derived_authority_identity": manifest["derived_authority_identity"],
            "transformation_contract_identity": manifest["transformation_contract_identity"],
            "authority_type": manifest["authority_type"],
            "derived_release": {"release_id": release_id, "tag": derived_tag},
            "market_population": manifest["market_population"],
            "exclusion_rows": manifest["exclusion_rows"],
            "cutoff_semantics": manifest["cutoff_semantics"],
            "linux_import": [
                f"gh release download {derived_tag} --repo {backend.repository} "
                "--dir prospective-derived",
                "verify every content-addressed asset name, byte size, and SHA-256",
                "verify derived-manifest.json identity and its exact logical file inventory",
                "import primitives and exclusions without adding, repairing, or reordering rows",
                "derive/freeze research features and perform all scientific scoring "
                "only on Gamma/Linux",
            ],
            "claim_boundary": {
                "deterministic_transformation_only": True,
                "outcomes_consumed": False,
                "scientific_scoring_owner": "Gamma/Linux factory",
                "maturity_or_promotion_authority": False,
            },
        }
        handoff = identified(handoff_core, "handoff_identity")
        reconciliation_core: dict[str, Any] = {
            "schema_version": "prospective-remote-reconciliation.v1",
            "derived_release_id": release_id,
            "derived_tag": derived_tag,
            "derived_authority_identity": manifest["derived_authority_identity"],
            "redownloaded_asset_count": len(list(root.iterdir())),
            "content_addressed_hash_verification": "PASS",
            "logical_inventory_verification": "PASS",
            "handoff_identity": handoff["handoff_identity"],
        }
        reconciliation = identified(reconciliation_core, "reconciliation_identity")
        certification = root / "certification"
        certification.mkdir()
        write_json(certification / "gamma-linux-handoff.json", handoff)
        write_json(certification / "reconciliation.json", reconciliation)
        backend.publish_directory(derived_tag, "certification", certification)
        backend.finalize(derived_tag)
    return {
        "status": "FINALIZED",
        "release_id": release_id,
        "tag": derived_tag,
        "derived_authority_identity": manifest["derived_authority_identity"],
        "handoff_identity": handoff["handoff_identity"],
        "reconciliation_identity": reconciliation["reconciliation_identity"],
    }


def _fixture_partition(asset: Asset, ordinal: int, source: Path) -> dict[str, Any]:
    size, digest = hash_file(source)
    if (asset.size, asset.state) != (size, "uploaded") or digest not in asset.name:
        raise AuthorityError("fixture input publication diverged")
    return {
        "ordinal": ordinal,
        "asset_id": asset.asset_id,
        "asset_name": asset.name,
        "bytes": size,
        "sha256": digest,
        "row_count": source.read_text(encoding="utf-8").count("\n"),
        "format": "canonical-jsonl",
    }


def _existing_fixture_locators(backend: GitHubReleases, release: dict[str, Any]) -> dict[str, Any]:
    assets = backend.assets(int(release["id"]))
    result: dict[str, Any] = {"input_release_id": int(release["id"])}
    for authority_type in AUTHORITY_TYPES:
        matches = [
            item
            for item in assets
            if item.name.endswith(f"--{authority_type}-sealed-input-contract.json")
        ]
        if len(matches) != 1:
            raise AuthorityError("existing fixture contract inventory diverged")
        asset = matches[0]
        digest = asset.name.split("--")[-2]
        _require_sha(digest, "fixture contract asset hash")
        key = "v51" if authority_type == V51 else "v55"
        result.update(
            {
                f"{key}_contract_asset_id": asset.asset_id,
                f"{key}_contract_sha256": digest,
                f"{key}_contract_bytes": asset.size,
            }
        )
    return result


def publish_fixture_inputs() -> dict[str, Any]:
    backend = GitHubReleases()
    existing = backend.release_by_tag(CANARY_INPUT_TAG)
    if existing is not None and not existing.get("draft"):
        return _existing_fixture_locators(backend, existing)
    release_id = backend.ensure_draft(CANARY_INPUT_TAG)
    contract_assets: dict[str, Asset] = {}
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        for authority_type, stem in ((V51, "v51"), (V55, "v55")):
            data = root / stem
            data.mkdir()
            sources = [FIXTURE_ROOT / f"{stem}-part-{ordinal}.jsonl" for ordinal in range(2)]
            for source in sources:
                shutil.copyfile(source, data / source.name)
            uploaded = backend.publish_directory(
                CANARY_INPUT_TAG, f"fixture-input/{authority_type}", data
            )
            by_suffix = {
                source.name: next(
                    item for item in uploaded if item.name.endswith(f"--{source.name}")
                )
                for source in sources
            }
            partitions = [
                _fixture_partition(by_suffix[source.name], ordinal, source)
                for ordinal, source in enumerate(sources)
            ]
            contract = _contract(
                authority_type, backend.repository, release_id, CANARY_INPUT_TAG, partitions
            )
            contracts = root / f"{stem}-contract"
            contracts.mkdir()
            name = f"{authority_type}-sealed-input-contract.json"
            write_json(contracts / name, contract)
            assets = backend.publish_directory(
                CANARY_INPUT_TAG, f"fixture-contract/{authority_type}", contracts
            )
            contract_assets[authority_type] = next(
                item for item in assets if item.name.endswith(f"--{name}")
            )
        backend.finalize(CANARY_INPUT_TAG)
    result: dict[str, Any] = {"input_release_id": release_id}
    for authority_type, asset in contract_assets.items():
        key = "v51" if authority_type == V51 else "v55"
        digest = asset.name.split("--")[-2]
        result.update(
            {
                f"{key}_contract_asset_id": asset.asset_id,
                f"{key}_contract_sha256": digest,
                f"{key}_contract_bytes": asset.size,
            }
        )
    return result


def negative_canary(
    contract: dict[str, Any], source_commit: str, chunk_directory: Path
) -> dict[str, str]:
    manifest = verify_chunk_directory(chunk_directory)
    corrupted = copy.deepcopy(contract)
    corrupted["partitions"][0]["sha256"] = "0" * 64
    corrupted["input_manifest_identity"] = identity(_without(corrupted, "input_manifest_identity"))
    with tempfile.TemporaryDirectory() as temp:
        try:
            fetch_partition(corrupted, 0, Path(temp))
        except AuthorityError:
            corrupt_result = "PASS_FAIL_CLOSED"
        else:
            raise AuthorityError("corrupt remote hash was not rejected")
        divergent = Path(temp) / "divergent"
        shutil.copytree(chunk_directory, divergent)
        primitives = divergent / "primitives.jsonl"
        primitives.write_bytes(primitives.read_bytes() + canonical_bytes({"divergent": True}))
        backend = GitHubReleases()
        try:
            backend.publish_directory(manifest["stage_tag"], manifest["stage_partition"], divergent)
        except AuthorityError:
            divergence_result = "PASS_FAIL_CLOSED"
        else:
            raise AuthorityError("divergent durable output was not rejected")
    if (
        manifest["processing_identity"]
        != processing_plan(contract, source_commit)["processing_identity"]
    ):
        raise AuthorityError("negative canary used a mismatched processing identity")
    return {"corrupt_input_hash": corrupt_result, "divergent_output": divergence_result}


def load_remote_contract_from_args(
    repository: str,
    release_id: int,
    contract_asset_id: int,
    contract_sha256: str,
    contract_bytes: int,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp:
        return fetch_contract(
            repository,
            release_id,
            contract_asset_id,
            contract_sha256,
            contract_bytes,
            Path(temp),
        )


def contract_output_lines(values: dict[str, Any]) -> str:
    return "\n".join(f"{key}={values[key]}" for key in sorted(values))


def validate_chunk_indexes(contract: dict[str, Any], raw: str) -> None:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthorityError("chunk index matrix is invalid JSON") from exc
    if not isinstance(parsed, list) or any(
        isinstance(item, bool) or not isinstance(item, int) for item in parsed
    ):
        raise AuthorityError("chunk index matrix must be an integer list")
    if parsed != list(range(len(contract["partitions"]))):
        raise AuthorityError("chunk index matrix diverges from sealed partitions")

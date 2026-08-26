from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .acquire import acquire_binance_segment, acquire_coinbase
from .chainlink_semantics import publish_semantic_authority
from .inventory import ASSETS
from .model import AuthorityError, write_json
from .pilot import run_arena_pilot
from .predictive import (
    assemble_predictive_authority,
)
from .predictive import (
    stage_binance as stage_predictive_binance,
)
from .predictive import (
    stage_coinbase as stage_predictive_coinbase,
)
from .predictive import (
    stage_target as stage_predictive_target,
)
from .prospective_plane import (
    assemble as assemble_prospective,
)
from .prospective_plane import (
    build_chunk as build_prospective_chunk,
)
from .prospective_plane import (
    contract_output_lines,
    load_remote_contract_from_args,
    negative_canary,
    processing_plan,
    publish_fixture_inputs,
    validate_chunk_indexes,
)
from .prospective_plane import (
    reconcile as reconcile_prospective,
)
from .prospective_plane import (
    stage_chunk as stage_prospective_chunk,
)
from .release import GitHubReleases

PREFIX = "historical-rich-information-run-1"


def _publish(tag: str, partition: str, directory: Path) -> None:
    GitHubReleases().publish_directory(tag, partition, directory)


def canary_asset(asset: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        built = acquire_binance_segment(asset, "2026-07-08", "2026-07-09", root)
        _publish(f"{PREFIX}-canary-{asset.lower()}", f"canary/{asset}", built)
        shutil.rmtree(built)


def canary_coinbase() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        built = acquire_coinbase("2026-07-08T22:00:00Z", "2026-07-08T23:00:00Z", root)
        _publish(f"{PREFIX}-canary-coinbase-btc", "canary/coinbase/BTC", built)
        shutil.rmtree(built)


def segment(asset: str, start: str, end: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        built = acquire_binance_segment(asset, start, end, root)
        tag = f"{PREFIX}-stage-{asset.lower()}-{start}-{end}"
        _publish(tag, f"class-a/binance/{asset}/{start}/{end}", built)
        shutil.rmtree(built)


def coinbase() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        built = acquire_coinbase("2026-06-01T00:00:00Z", "2026-07-08T23:00:00Z", root)
        _publish(f"{PREFIX}-stage-coinbase-btc", "class-a/coinbase/BTC", built)
        shutil.rmtree(built)


def arena() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        built = run_arena_pilot(root)
        _publish(f"{PREFIX}-pilot-arena", "class-b/arena/pilot-1", built)
        shutil.rmtree(built)


def assemble(selected_assets: tuple[str, ...] = ASSETS) -> None:
    backend = GitHubReleases()
    releases = backend.releases()
    by_tag = {str(item["tag_name"]): item for item in releases}
    expected: list[str] = []
    segments = (
        ("2026-06-01", "2026-06-11"),
        ("2026-06-11", "2026-06-21"),
        ("2026-06-21", "2026-07-01"),
        ("2026-07-01", "2026-07-09"),
    )
    for asset in selected_assets:
        expected.extend(f"{PREFIX}-stage-{asset.lower()}-{start}-{end}" for start, end in segments)
    if "BTC" in selected_assets:
        expected.append(f"{PREFIX}-stage-coinbase-btc")
    missing = sorted(set(expected) - by_tag.keys())
    if missing:
        raise AuthorityError(f"assembly missing staging releases: {missing}")
    inventory: list[dict[str, Any]] = []
    for tag in expected:
        release = by_tag[tag]
        assets = backend.assets(int(release["id"]))
        manifest_assets = [item for item in assets if item.name.endswith("--manifest.json")]
        if len(manifest_assets) != 1 or any(item.state != "uploaded" for item in assets):
            raise AuthorityError(f"staging release incomplete: {tag}")
        inventory.append(
            {
                "tag": tag,
                "release_id": int(release["id"]),
                "assets": [
                    {"name": item.name, "bytes": item.size}
                    for item in sorted(assets, key=lambda x: x.name)
                ],
            }
        )
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "authority"
        root.mkdir()
        write_json(
            root / "authority-index.json",
            {
                "schema_version": "1.0.0",
                "dataset_id": PREFIX,
                "authority_classes": {"A": inventory, "B": "separate pilot release; never pooled"},
                "complete_stage_count": len(inventory),
                "source_retention": "transient_runner_only",
            },
        )
        scope = "btc" if selected_assets == ("BTC",) else "seven"
        canonical_tag = f"{PREFIX}-{scope}-authority-v1"
        backend.publish_directory(canonical_tag, "authority", root)
        backend.finalize(canonical_tag)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    canary = sub.add_parser("canary-asset")
    canary.add_argument("--asset", choices=ASSETS, required=True)
    sub.add_parser("canary-coinbase")
    stage = sub.add_parser("segment")
    stage.add_argument("--asset", choices=ASSETS, required=True)
    stage.add_argument("--start", required=True)
    stage.add_argument("--end", required=True)
    sub.add_parser("coinbase")
    sub.add_parser("arena-pilot")
    sub.add_parser("assemble-btc")
    sub.add_parser("assemble")
    predictive_target = sub.add_parser("predictive-target")
    predictive_target.add_argument("--start", required=True)
    predictive_target.add_argument("--end", required=True)
    predictive_binance = sub.add_parser("predictive-binance")
    predictive_binance.add_argument("--start", required=True)
    predictive_binance.add_argument("--end", required=True)
    predictive_coinbase = sub.add_parser("predictive-coinbase")
    predictive_coinbase.add_argument("--start", default=None)
    predictive_coinbase.add_argument("--end", default=None)
    sub.add_parser("predictive-assemble")
    sub.add_parser("chainlink-60s-feasibility")
    sub.add_parser("plane-fixture-input")
    plane_validate = sub.add_parser("plane-validate")
    _remote_contract_arguments(plane_validate)
    plane_validate.add_argument("--chunks-json", required=True)
    plane_validate.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", ""))
    plane_transform = sub.add_parser("plane-transform")
    _remote_contract_arguments(plane_transform)
    plane_transform.add_argument("--chunk", type=int, required=True)
    plane_transform.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", ""))
    plane_transform.add_argument("--output", type=Path, required=True)
    plane_stage = sub.add_parser("plane-stage")
    plane_stage.add_argument("--directory", type=Path, required=True)
    plane_assemble = sub.add_parser("plane-assemble")
    _remote_contract_arguments(plane_assemble)
    plane_assemble.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", ""))
    plane_reconcile = sub.add_parser("plane-reconcile")
    plane_reconcile.add_argument("--derived-tag", required=True)
    plane_negative = sub.add_parser("plane-negative-canary")
    _remote_contract_arguments(plane_negative)
    plane_negative.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", ""))
    plane_negative.add_argument("--directory", type=Path, required=True)
    return result


def _remote_contract_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--input-repository", required=True)
    command.add_argument("--input-release-id", type=int, required=True)
    command.add_argument("--contract-asset-id", type=int, required=True)
    command.add_argument("--contract-sha256", required=True)
    command.add_argument("--contract-bytes", type=int, required=True)


def _remote_contract(args: argparse.Namespace) -> dict[str, Any]:
    return load_remote_contract_from_args(
        args.input_repository,
        args.input_release_id,
        args.contract_asset_id,
        args.contract_sha256,
        args.contract_bytes,
    )


def main() -> None:
    args = parser().parse_args()
    if args.command == "canary-asset":
        canary_asset(args.asset)
    elif args.command == "canary-coinbase":
        canary_coinbase()
    elif args.command == "segment":
        segment(args.asset, args.start, args.end)
    elif args.command == "coinbase":
        coinbase()
    elif args.command == "arena-pilot":
        arena()
    elif args.command == "assemble-btc":
        assemble(("BTC",))
    elif args.command == "assemble":
        assemble()
    elif args.command == "predictive-target":
        stage_predictive_target(args.start, args.end)
    elif args.command == "predictive-binance":
        stage_predictive_binance(args.start, args.end)
    elif args.command == "predictive-coinbase":
        if (args.start is None) != (args.end is None):
            raise AuthorityError("predictive Coinbase bounds must be supplied together")
        if args.start is None:
            stage_predictive_coinbase()
        else:
            stage_predictive_coinbase(args.start, args.end)
    elif args.command == "predictive-assemble":
        assemble_predictive_authority()
    elif args.command == "chainlink-60s-feasibility":
        publish_semantic_authority()
    elif args.command == "plane-fixture-input":
        print(contract_output_lines(publish_fixture_inputs()))
    elif args.command == "plane-validate":
        contract = _remote_contract(args)
        validate_chunk_indexes(contract, args.chunks_json)
        plan = processing_plan(contract, args.source_commit)
        print(
            contract_output_lines(
                {
                    "authority_type": contract["authority_type"],
                    "derived_tag": plan["derived_tag"],
                    "input_manifest_identity": contract["input_manifest_identity"],
                    "processing_identity": plan["processing_identity"],
                }
            )
        )
    elif args.command == "plane-transform":
        contract = _remote_contract(args)
        manifest = build_prospective_chunk(contract, args.chunk, args.source_commit, args.output)
        print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    elif args.command == "plane-stage":
        print(stage_prospective_chunk(args.directory))
    elif args.command == "plane-assemble":
        contract = _remote_contract(args)
        print(assemble_prospective(contract, args.source_commit))
    elif args.command == "plane-reconcile":
        print(json.dumps(reconcile_prospective(args.derived_tag), sort_keys=True))
    elif args.command == "plane-negative-canary":
        contract = _remote_contract(args)
        print(
            json.dumps(
                negative_canary(contract, args.source_commit, args.directory), sort_keys=True
            )
        )
    else:
        raise AssertionError(args.command)


if __name__ == "__main__":
    main()

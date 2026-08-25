from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .acquire import acquire_binance_segment, acquire_coinbase
from .inventory import ASSETS
from .model import AuthorityError, write_json
from .pilot import run_arena_pilot
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


def assemble() -> None:
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
    for asset in ASSETS:
        expected.extend(f"{PREFIX}-stage-{asset.lower()}-{start}-{end}" for start, end in segments)
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
        canonical_tag = f"{PREFIX}-authority-v1"
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
    sub.add_parser("assemble")
    return result


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
    elif args.command == "assemble":
        assemble()
    else:
        raise AssertionError(args.command)


if __name__ == "__main__":
    main()

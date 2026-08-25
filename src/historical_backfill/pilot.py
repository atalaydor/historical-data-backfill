from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import pyarrow.parquet as pq

from .http import download
from .model import AuthorityError, hash_file, write_json

ARENA_URL = "https://huggingface.co/datasets/Alezanello/polymarket-arena-capture/resolve/main/daily/cap_book/2026-06-16T162126Z.parquet?download=true"
ARENA_EXPECTED_SHA256 = "76c692b4ba3752a3dec2753206f935ef1d22fbe10997d2bf6e80f40eecb25896"


def run_arena_pilot(root: Path) -> Path:
    source = root / "arena-cap-book.parquet"
    receipt = download(ARENA_URL, source, 60_000_000)
    try:
        size, digest = hash_file(source)
        if digest != ARENA_EXPECTED_SHA256:
            raise AuthorityError("Arena LFS content identity changed")
        parquet = pq.ParquetFile(source)
        required = {
            "ts_ms",
            "asset_id",
            "bids",
            "asks",
            "asset",
            "slug",
            "cond",
            "win_start",
            "end_ts",
        }
        if not required.issubset(parquet.schema_arrow.names):
            raise AuthorityError("Arena schema missing identity/depth fields")
        last_by_token: dict[tuple[str, str], int] = {}
        gaps: list[int] = []
        row_count = 0
        parseable = 0
        crossed = 0
        two_sided = 0
        inversion = 0
        slugs: set[str] = set()
        assets: set[str] = set()
        first_capture: int | None = None
        last_capture: int | None = None
        for batch in parquet.iter_batches(batch_size=65_536, columns=sorted(required)):
            for row in batch.to_pylist():
                stamp = int(row["ts_ms"])
                identity = (str(row["slug"]), str(row["asset_id"]))
                prior = last_by_token.get(identity)
                if prior is not None:
                    if stamp < prior:
                        raise AuthorityError("Arena capture order regressed within token")
                    gaps.append(stamp - prior)
                last_by_token[identity] = stamp
                row_count += 1
                first_capture = stamp if first_capture is None else min(first_capture, stamp)
                last_capture = stamp if last_capture is None else max(last_capture, stamp)
                slugs.add(str(row["slug"]))
                assets.add(str(row["asset"]))
                try:
                    bids = json.loads(row["bids"] or "[]")
                    asks = json.loads(row["asks"] or "[]")
                    if bids and asks:
                        parseable += 1
                        two_sided += 1
                        if float(bids[0][0]) >= float(asks[0][0]):
                            crossed += 1
                except (TypeError, ValueError, json.JSONDecodeError, IndexError):
                    pass
                if stamp > int(row["end_ts"]) * 1000 + 5_000:
                    inversion += 1
        if row_count == 0 or not gaps or first_capture is None or last_capture is None:
            raise AuthorityError("Arena pilot object contains no usable sequence")
        gaps.sort()
        p99 = gaps[min(len(gaps) - 1, int(len(gaps) * 0.99))]
        criteria = {
            "median_gap_ms_lte_3000": statistics.median(gaps) <= 3000,
            "p99_gap_ms_lte_15000": p99 <= 15000,
            "parseable_two_sided_rate_gte_095": parseable / row_count >= 0.95,
            "crossed_two_sided_rate_lte_001": crossed / max(two_sided, 1) <= 0.01,
            "timestamp_inversions_zero": inversion == 0,
            "assets_exact_six": assets == {"BTC", "ETH", "SOL", "XRP", "DOGE", "BNB"},
        }
        output = root / "arena-pilot"
        output.mkdir()
        evidence = {
            "schema_version": "1.0.0",
            "authority_class": "B",
            "decision": "PROMOTE" if all(criteria.values()) else "ABANDON",
            "source_url": ARENA_URL,
            "source_sha256": digest,
            "source_bytes": size,
            "etag": receipt.etag,
            "last_modified": receipt.last_modified,
            "acquired_at_ns": time.time_ns(),
            "rows": row_count,
            "slugs": len(slugs),
            "token_identities": len(last_by_token),
            "assets": sorted(assets),
            "first_capture_ms": first_capture,
            "last_capture_ms": last_capture,
            "median_gap_ms": statistics.median(gaps),
            "p99_gap_ms": p99,
            "max_gap_ms": max(gaps),
            "parseable_two_sided_rate": parseable / row_count,
            "crossed_two_sided_rate": crossed / max(two_sided, 1),
            "timestamp_inversions": inversion,
            "criteria": criteria,
        }
        write_json(output / "pilot-evidence.json", evidence)
        return output
    finally:
        source.unlink(missing_ok=True)

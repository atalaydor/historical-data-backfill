from __future__ import annotations

import csv
import hashlib
import json
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq

from .http import USER_AGENT, download, get_text
from .inventory import binance_specs, days
from .model import AuthorityError, SourceSpec, hash_file, write_json

TRANSFORM = "historical-exchange-normalizer.v1"
MAX_SOURCE_BYTES = 250_000_000
TARGET_END_NS = int(datetime(2026, 7, 8, 23, tzinfo=UTC).timestamp() * 1_000_000_000)


def acquire_binance_segment(
    asset: str,
    start: str,
    end: str,
    root: Path,
    *,
    logical_start: str | None = None,
    logical_end: str | None = None,
) -> Path:
    output = root / f"{asset}-{start}-{end}"
    output.mkdir(parents=True, exist_ok=False)
    sources: list[dict[str, Any]] = []
    try:
        minimum_event_ns = _iso_ns(logical_start) if logical_start is not None else None
        maximum_event_ns = _iso_ns(logical_end) if logical_end is not None else None
        for day in days(start, end):
            for spec in binance_specs(asset, day):
                sources.append(
                    _acquire_binance(
                        spec,
                        output,
                        root,
                        minimum_event_ns=minimum_event_ns,
                        maximum_event_ns=(
                            maximum_event_ns
                            if maximum_event_ns is not None
                            else TARGET_END_NS
                            if spec.day == "2026-07-08"
                            else None
                        ),
                    )
                )
        manifest_end = (
            logical_end
            if logical_end is not None
            else "2026-07-08T23:00:00Z"
            if end == "2026-07-09"
            else end
        )
        manifest = {
            "schema_version": "1.0.0",
            "authority_class": "A",
            "asset": asset,
            "interval": {"start": logical_start or start, "end": manifest_end},
            "transform": TRANSFORM,
            "sources": sources,
        }
        manifest["identity"] = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        write_json(output / "manifest.json", manifest)
        return output
    except Exception:
        for source in root.glob("*.zip"):
            source.unlink(missing_ok=True)
        raise


def _acquire_binance(
    spec: SourceSpec,
    output: Path,
    scratch: Path,
    *,
    minimum_event_ns: int | None = None,
    maximum_event_ns: int | None = None,
) -> dict[str, Any]:
    stem = f"{spec.asset}-{spec.family}-{spec.day}"
    archive = scratch / f"{stem}.zip"
    text, checksum_receipt = get_text(spec.checksum_url or "")
    expected = text.strip().split()[0].lower()
    if len(expected) != 64:
        raise AuthorityError(f"invalid official checksum: {spec.checksum_url}")
    receipt = download(spec.url, archive, MAX_SOURCE_BYTES)
    source_size, source_sha = hash_file(archive)
    if source_sha != expected:
        raise AuthorityError(f"official checksum mismatch: {spec.url}")
    target = output / f"{stem}.parquet"
    try:
        rows, first_ns, last_ns = _normalize_zip(
            spec,
            archive,
            target,
            minimum_event_ns=minimum_event_ns,
            maximum_event_ns=maximum_event_ns,
        )
    finally:
        archive.unlink(missing_ok=True)
    normalized_size, normalized_sha = hash_file(target)
    return {
        "source_identity": spec.identity(),
        "provider": spec.provider,
        "family": spec.family,
        "asset": spec.asset,
        "instrument": spec.instrument,
        "day": spec.day,
        "source_url": spec.url,
        "checksum_url": spec.checksum_url,
        "source_sha256": source_sha,
        "source_bytes": source_size,
        "source_etag": receipt.etag,
        "archive_last_modified": receipt.last_modified,
        "checksum_last_modified": checksum_receipt.last_modified,
        "acquired_at_ns": time.time_ns(),
        "event_time_field": spec.timestamp_column,
        "event_time_unit": spec.timestamp_unit,
        "first_event_time_ns": first_ns,
        "last_event_time_ns": last_ns,
        "rows": rows,
        "normalized_file": target.name,
        "normalized_bytes": normalized_size,
        "normalized_sha256": normalized_sha,
    }


def _normalize_zip(
    spec: SourceSpec,
    archive: Path,
    target: Path,
    minimum_event_ns: int | None = None,
    maximum_event_ns: int | None = None,
) -> tuple[int, int, int]:
    schema = pa.schema(
        [
            ("provider", pa.string()),
            ("family", pa.string()),
            ("asset", pa.string()),
            ("instrument", pa.string()),
            ("event_time_ns", pa.int64()),
            ("sequence", pa.string()),
            ("source_row", pa.int64()),
            ("fields_json", pa.string()),
        ]
    )
    writer = pq.ParquetWriter(target, schema, compression="zstd")
    count = 0
    first_ns: int | None = None
    last_ns: int | None = None
    day_start = int(
        datetime.combine(date.fromisoformat(spec.day), datetime.min.time(), UTC).timestamp()
        * 1_000_000_000
    )
    day_end = day_start + 86_400_000_000_000
    batch: list[dict[str, object]] = []
    try:
        with zipfile.ZipFile(archive) as zipped:
            names = [name for name in zipped.namelist() if not name.endswith("/")]
            if len(names) != 1:
                raise AuthorityError("source ZIP must contain exactly one CSV")
            with zipped.open(names[0]) as raw:
                reader = csv.reader(line.decode("utf-8") for line in raw)
                for source_row, fields in enumerate(reader):
                    if not fields or not fields[0].lstrip("-").isdigit():
                        if source_row == 0:
                            continue
                        raise AuthorityError("unexpected non-numeric source row")
                    if max(spec.timestamp_column, spec.sequence_column) >= len(fields):
                        raise AuthorityError("source schema truncated")
                    event_ns = _to_ns(int(fields[spec.timestamp_column]), spec.timestamp_unit)
                    if not day_start <= event_ns < day_end:
                        raise AuthorityError("source event outside object UTC day")
                    if maximum_event_ns is not None and event_ns >= maximum_event_ns:
                        continue
                    if minimum_event_ns is not None and event_ns < minimum_event_ns:
                        continue
                    if last_ns is not None and event_ns < last_ns:
                        raise AuthorityError("source event time regressed")
                    first_ns = event_ns if first_ns is None else first_ns
                    last_ns = event_ns
                    batch.append(
                        {
                            "provider": spec.provider,
                            "family": spec.family,
                            "asset": spec.asset,
                            "instrument": spec.instrument,
                            "event_time_ns": event_ns,
                            "sequence": fields[spec.sequence_column],
                            "source_row": source_row,
                            "fields_json": json.dumps(fields, separators=(",", ":")),
                        }
                    )
                    count += 1
                    if len(batch) == 65_536:
                        writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                        batch.clear()
                if batch:
                    writer.write_table(pa.Table.from_pylist(batch, schema=schema))
    finally:
        writer.close()
    if count == 0 or first_ns is None or last_ns is None:
        target.unlink(missing_ok=True)
        raise AuthorityError("empty source object")
    return count, first_ns, last_ns


def _to_ns(value: int, unit: str) -> int:
    if unit == "us":
        return value * 1_000
    if unit == "ms":
        return value * 1_000_000
    raise AuthorityError(f"unsupported timestamp unit: {unit}")


def _iso_ns(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AuthorityError("logical source bound must be timezone-aware")
    return int(parsed.timestamp() * 1_000_000_000)


def acquire_coinbase(start: str, end: str, root: Path) -> Path:
    output = root / f"BTC-coinbase-{start[:10]}-{end[:10]}"
    output.mkdir(parents=True, exist_ok=False)
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    rows: dict[int, list[str | int | float]] = {}
    receipts: list[dict[str, object]] = []
    cursor = start_dt
    while cursor < end_dt:
        stop = min(cursor + timedelta(minutes=299), end_dt)
        query = urllib.parse.urlencode(
            {"granularity": 60, "start": cursor.isoformat(), "end": stop.isoformat()}
        )
        url = f"https://api.exchange.coinbase.com/products/BTC-USD/candles?{query}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise AuthorityError("Coinbase response crossed byte breaker")
            payload = cast(list[list[str | int | float]], json.loads(body))
            receipts.append({"url": url, "etag": response.headers.get("ETag"), "bytes": len(body)})
        for row in payload:
            stamp = int(row[0])
            if int(start_dt.timestamp()) <= stamp < int(end_dt.timestamp()):
                prior = rows.setdefault(stamp, row)
                if prior != row:
                    raise AuthorityError("divergent duplicate Coinbase candle")
        cursor = stop
    schema = pa.schema(
        [
            ("provider", pa.string()),
            ("instrument", pa.string()),
            ("event_time_ns", pa.int64()),
            ("low", pa.float64()),
            ("high", pa.float64()),
            ("open", pa.float64()),
            ("close", pa.float64()),
            ("volume", pa.float64()),
        ]
    )
    normalized = [
        {
            "provider": "coinbase-exchange",
            "instrument": "BTC-USD",
            "event_time_ns": stamp * 1_000_000_000,
            "low": float(row[1]),
            "high": float(row[2]),
            "open": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for stamp, row in sorted(rows.items())
    ]
    path = output / "BTC-coinbase-candles-60s.parquet"
    pq.write_table(pa.Table.from_pylist(normalized, schema=schema), path, compression="zstd")
    size, digest = hash_file(path)
    manifest = {
        "schema_version": "1.0.0",
        "authority_class": "A",
        "provider": "coinbase-exchange",
        "instrument": "BTC-USD",
        "interval": {"start": start, "end": end},
        "granularity_seconds": 60,
        "rows": len(normalized),
        "missing_policy": "explicit_no_synthesis",
        "requests": receipts,
        "normalized_file": path.name,
        "normalized_bytes": size,
        "normalized_sha256": digest,
        "acquired_at_ns": time.time_ns(),
        "transform": TRANSFORM,
    }
    manifest["identity"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    write_json(output / "manifest.json", manifest)
    return output

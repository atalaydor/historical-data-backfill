from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

import pyarrow as pa
import pyarrow.parquet as pq

from .acquire import acquire_binance_segment, acquire_coinbase
from .http import DELAYS, USER_AGENT
from .model import AuthorityError, canonical_bytes, hash_file, write_json
from .release import Asset, GitHubReleases

PREFIX = "btc-independent-predictive-validation-v1"
FINAL_TAG = f"{PREFIX}-authority-v1"
INTERVAL_START = "2026-07-08T23:00:00Z"
DEVELOPMENT_END = "2026-07-15T03:00:00Z"
INTERVAL_END = "2026-07-21T07:00:00Z"
TARGET_SEGMENTS = (
    ("2026-07-08T23:00:00Z", "2026-07-10T12:00:00Z"),
    ("2026-07-10T12:00:00Z", "2026-07-12T01:00:00Z"),
    ("2026-07-12T01:00:00Z", "2026-07-13T14:00:00Z"),
    ("2026-07-13T14:00:00Z", "2026-07-15T03:00:00Z"),
    ("2026-07-15T03:00:00Z", "2026-07-16T16:00:00Z"),
    ("2026-07-16T16:00:00Z", "2026-07-18T05:00:00Z"),
    ("2026-07-18T05:00:00Z", "2026-07-19T18:00:00Z"),
    ("2026-07-19T18:00:00Z", "2026-07-21T07:00:00Z"),
)
BINANCE_SEGMENTS = (
    ("2026-07-08", "2026-07-12"),
    ("2026-07-12", "2026-07-16"),
    ("2026-07-16", "2026-07-20"),
    ("2026-07-20", "2026-07-22"),
)
GAMMA_TEMPLATE = "https://gamma-api.polymarket.com/markets/slug/{slug}"
MAX_GAMMA_RESPONSE_BYTES = 2_000_000
MAX_TARGET_SEGMENT_BYTES = 900_000_000
MAX_TARGET_ASSET_BYTES = 950_000_000
REQUIRED_CLUSTERS = 125
EXPECTED_CANDIDATES = 3552
TRACK_IDS = (
    "binance_spot_return_1s",
    "binance_spot_return_5s",
    "binance_spot_return_15s",
    "binance_spot_return_30s",
    "binance_spot_return_60s",
    "binance_perpetual_return_1s",
    "binance_perpetual_return_5s",
    "binance_perpetual_return_15s",
    "binance_perpetual_return_30s",
    "binance_perpetual_return_60s",
    "joint_60s_realized_volatility",
    "spot_perpetual_basis",
    "spot_taker_pressure_60s",
    "perpetual_taker_pressure_60s",
    "binance_coinbase_disagreement_60s",
    "coinbase_lead_lag",
)

TARGET_SOURCE_CONTRACT_IDENTITY = hashlib.sha256(
    canonical_bytes(
        {
            "provider": "official-polymarket-gamma",
            "endpoint": GAMMA_TEMPLATE,
            "identity": "slug-condition-event-start-end",
            "outcome": "closed-Up-Down-exact-1-0",
            "raw_authority": "response-bytes-sha256",
            "cutoff": "end-minus-60-seconds",
            "schema": "btc.predictive-target-authority.v1",
        }
    )
).hexdigest()


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AuthorityError("timestamp is not timezone-aware")
    return parsed.astimezone(UTC)


def _time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _epoch(value: str) -> int:
    return int(_instant(value).timestamp())


def candidate_epochs(start: str, end: str) -> tuple[int, ...]:
    first = _epoch(start)
    stop = _epoch(end)
    if first % 300 or stop % 300 or first >= stop:
        raise AuthorityError("target interval is not a positive five-minute grid")
    return tuple(range(first, stop, 300))


def target_tag(start: str, end: str) -> str:
    return f"{PREFIX}-stage-target-{_epoch(start)}-{_epoch(end)}"


def binance_tag(start: str, end: str) -> str:
    return f"{PREFIX}-stage-binance-{start}-{end}"


def _json_list(value: object, field: str) -> list[object]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AuthorityError(f"Gamma {field} is not JSON") from exc
    if not isinstance(value, list):
        raise AuthorityError(f"Gamma {field} is not a list")
    return cast(list[object], value)


def _gamma_get(url: str) -> tuple[bytes | None, dict[str, str | None]]:
    last: Exception | None = None
    for delay in DELAYS:
        if delay:
            time.sleep(delay)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read(MAX_GAMMA_RESPONSE_BYTES + 1)
                if len(body) > MAX_GAMMA_RESPONSE_BYTES:
                    raise AuthorityError("Gamma response crossed byte breaker")
                return body, {
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                }
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None, {"etag": None, "last_modified": None}
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                raise AuthorityError(f"Gamma source HTTP {exc.code}: {url}") from exc
            last = exc
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            last = exc
    raise AuthorityError(f"bounded Gamma retry exhausted: {url}") from last


def _event_start(raw: dict[str, object], slug: str) -> datetime:
    events = raw.get("events")
    if not isinstance(events, list):
        raise AuthorityError("Gamma event authority is absent")
    matches = [
        item
        for item in events
        if isinstance(item, dict)
        and item.get("slug") == slug
        and isinstance(item.get("startTime"), str)
    ]
    if len(matches) != 1:
        raise AuthorityError("Gamma event identity or start time is ambiguous")
    return _instant(cast(str, matches[0]["startTime"]))


def normalize_gamma_market(epoch: int, body: bytes) -> dict[str, object]:
    slug = f"btc-updown-5m-{epoch}"
    try:
        raw: object = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthorityError("Gamma response is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise AuthorityError("Gamma response is not a market object")
    market = cast(dict[str, object], raw)
    condition = market.get("conditionId")
    market_id = market.get("id")
    if market.get("slug") != slug:
        raise AuthorityError("Gamma slug diverged")
    if (
        not isinstance(condition, str)
        or len(condition) != 66
        or not condition.startswith("0x")
        or any(char not in "0123456789abcdef" for char in condition[2:].lower())
    ):
        raise AuthorityError("Gamma condition identity is malformed")
    if not isinstance(market_id, str | int) or not str(market_id):
        raise AuthorityError("Gamma market identity is malformed")
    start = datetime.fromtimestamp(epoch, UTC)
    if _event_start(market, slug) != start:
        raise AuthorityError("Gamma event start diverged from slug epoch")
    end_raw = market.get("endDate")
    if not isinstance(end_raw, str) or _instant(end_raw) != start + timedelta(seconds=300):
        raise AuthorityError("Gamma market end diverged")
    if market.get("closed") is not True:
        raise AuthorityError("Gamma market is not closed")
    outcomes = _json_list(market.get("outcomes"), "outcomes")
    prices = _json_list(market.get("outcomePrices"), "outcomePrices")
    if len(outcomes) != 2 or len(prices) != 2 or set(outcomes) != {"Up", "Down"}:
        raise AuthorityError("Gamma outcomes are not exact Up/Down")
    try:
        decimal_prices = [Decimal(str(item)) for item in prices]
    except (InvalidOperation, ValueError) as exc:
        raise AuthorityError("Gamma outcome prices are malformed") from exc
    if sorted(decimal_prices) != [Decimal(0), Decimal(1)]:
        raise AuthorityError("Gamma final outcome is unresolved or ambiguous")
    winner = cast(str, outcomes[decimal_prices.index(Decimal(1))])
    token_ids = _json_list(market.get("clobTokenIds"), "clobTokenIds")
    if len(token_ids) != 2 or any(
        not isinstance(item, str) or not item.isdecimal() for item in token_ids
    ):
        raise AuthorityError("Gamma token mapping is malformed")
    raw_sha256 = hashlib.sha256(body).hexdigest()
    source_identity = hashlib.sha256(
        canonical_bytes(
            {
                "contract_identity": TARGET_SOURCE_CONTRACT_IDENTITY,
                "slug": slug,
                "condition_id": condition,
                "raw_sha256": raw_sha256,
            }
        )
    ).hexdigest()
    return {
        "epoch": epoch,
        "slug": slug,
        "market_id": str(market_id),
        "condition_id": condition,
        "start": _time(start),
        "end": _time(start + timedelta(seconds=300)),
        "decision_cutoff": _time(start + timedelta(seconds=240)),
        "outcome": winner,
        "token_up": cast(str, token_ids[outcomes.index("Up")]),
        "token_down": cast(str, token_ids[outcomes.index("Down")]),
        "raw_gamma_sha256": raw_sha256,
        "target_source_identity": source_identity,
        "raw_gamma_json": body,
    }


def acquire_target_segment(start: str, end: str, root: Path) -> Path:
    epochs = candidate_epochs(start, end)
    output = root / f"target-{epochs[0]}-{epochs[-1] + 300}"
    output.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, object]] = []
    total_bytes = 0
    reasons: Counter[str] = Counter()
    for epoch in epochs:
        slug = f"btc-updown-5m-{epoch}"
        url = GAMMA_TEMPLATE.format(slug=slug)
        body, receipt = _gamma_get(url)
        received_at_ns = time.time_ns()
        base: dict[str, object] = {
            "epoch": epoch,
            "slug": slug,
            "status": "excluded",
            "reason": "official-market-absent",
            "source_url": url,
            "source_etag": receipt["etag"],
            "source_last_modified": receipt["last_modified"],
            "received_at_ns": received_at_ns,
            "target_contract_identity": TARGET_SOURCE_CONTRACT_IDENTITY,
        }
        if body is None:
            reasons[cast(str, base["reason"])] += 1
            rows.append(base)
            continue
        total_bytes += len(body)
        if total_bytes > MAX_TARGET_SEGMENT_BYTES:
            raise AuthorityError("target segment crossed byte breaker")
        try:
            normalized = normalize_gamma_market(epoch, body)
        except AuthorityError as exc:
            base.update(
                {
                    "reason": str(exc),
                    "raw_gamma_sha256": hashlib.sha256(body).hexdigest(),
                    "raw_gamma_json": body,
                }
            )
            reasons[str(exc)] += 1
            rows.append(base)
            continue
        base.update(normalized)
        base.update({"status": "eligible", "reason": None})
        rows.append(base)
    schema = pa.schema(
        [
            ("epoch", pa.int64()),
            ("slug", pa.string()),
            ("status", pa.string()),
            ("reason", pa.string()),
            ("market_id", pa.string()),
            ("condition_id", pa.string()),
            ("start", pa.string()),
            ("end", pa.string()),
            ("decision_cutoff", pa.string()),
            ("outcome", pa.string()),
            ("token_up", pa.string()),
            ("token_down", pa.string()),
            ("source_url", pa.string()),
            ("source_etag", pa.string()),
            ("source_last_modified", pa.string()),
            ("received_at_ns", pa.int64()),
            ("raw_gamma_sha256", pa.string()),
            ("target_source_identity", pa.string()),
            ("target_contract_identity", pa.string()),
            ("raw_gamma_json", pa.binary()),
        ]
    )
    target = output / "target.parquet"
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), target, compression="zstd")
    target_size, target_sha256 = hash_file(target)
    manifest: dict[str, object] = {
        "schema_version": "1.0.0",
        "authority_class": "A",
        "dataset_id": PREFIX,
        "provider": "official-polymarket-gamma",
        "source_url_template": GAMMA_TEMPLATE,
        "source_access_date": "2026-08-25",
        "source_semantics": (
            "official identity and final outcome snapshot acquired after settlement"
        ),
        "target_contract_identity": TARGET_SOURCE_CONTRACT_IDENTITY,
        "interval": {"start": start, "end": end},
        "candidate_count": len(epochs),
        "eligible_count": sum(item["status"] == "eligible" for item in rows),
        "excluded_count": sum(item["status"] != "eligible" for item in rows),
        "exclusion_reasons": dict(sorted(reasons.items())),
        "target_file": target.name,
        "target_bytes": target_size,
        "target_sha256": target_sha256,
        "raw_response_bytes": total_bytes,
        "source_retention": "embedded-content-addressed-official-responses",
    }
    manifest["identity"] = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    write_json(output / "manifest.json", manifest)
    return output


def stage_target(start: str, end: str) -> None:
    backend = GitHubReleases()
    tag = target_tag(start, end)
    partition = f"step-2/target/{_epoch(start)}/{_epoch(end)}"
    if backend.stage_complete(tag, partition):
        return
    with tempfile.TemporaryDirectory() as temp:
        built = acquire_target_segment(start, end, Path(temp))
        backend.publish_directory(tag, partition, built)
        shutil.rmtree(built)


def stage_binance(start: str, end: str) -> None:
    backend = GitHubReleases()
    tag = binance_tag(start, end)
    partition = f"step-2/external/binance/BTC/{start}/{end}"
    if backend.stage_complete(tag, partition):
        return
    segment_start = max(_instant(INTERVAL_START), _instant(f"{start}T00:00:00Z"))
    segment_end = min(_instant(INTERVAL_END), _instant(f"{end}T00:00:00Z"))
    if segment_start >= segment_end:
        raise AuthorityError("predictive Binance segment does not intersect the frozen interval")
    with tempfile.TemporaryDirectory() as temp:
        built = acquire_binance_segment(
            "BTC",
            start,
            end,
            Path(temp),
            logical_start=_time(segment_start),
            logical_end=_time(segment_end),
        )
        backend.publish_directory(tag, partition, built)
        shutil.rmtree(built)


def stage_coinbase(start: str = INTERVAL_START, end: str = INTERVAL_END) -> None:
    backend = GitHubReleases()
    production = (start, end) == (INTERVAL_START, INTERVAL_END)
    tag = (
        f"{PREFIX}-stage-coinbase"
        if production
        else f"{PREFIX}-canary-coinbase-{_epoch(start)}-{_epoch(end)}"
    )
    partition = (
        "step-2/external/coinbase/BTC"
        if production
        else f"step-2/canary/coinbase/BTC/{_epoch(start)}/{_epoch(end)}"
    )
    if backend.stage_complete(tag, partition):
        return
    with tempfile.TemporaryDirectory() as temp:
        built = acquire_coinbase(start, end, Path(temp))
        backend.publish_directory(tag, partition, built)
        shutil.rmtree(built)


def _asset_digest(asset: Asset, filename: str) -> str:
    prefix, digest, observed_filename = asset.name.rsplit("--", 2)
    if not prefix or observed_filename != filename or len(digest) != 64:
        raise AuthorityError("content-addressed release asset name is malformed")
    return digest


def _stage_release(
    backend: GitHubReleases, by_tag: dict[str, dict[str, Any]], tag: str
) -> tuple[int, list[Asset]]:
    release = by_tag.get(tag)
    if release is None or not release.get("draft"):
        raise AuthorityError(f"required immutable staging checkpoint is absent: {tag}")
    release_id = int(release["id"])
    assets = backend.assets(release_id)
    if not assets or any(item.state != "uploaded" for item in assets):
        raise AuthorityError(f"staging checkpoint is partial: {tag}")
    if len([item for item in assets if item.name.endswith("--manifest.json")]) != 1:
        raise AuthorityError(f"staging manifest is absent or divergent: {tag}")
    return release_id, sorted(assets, key=lambda item: item.name)


def _load_preregistration() -> dict[str, object]:
    path = Path("config/btc-predictive-validation-preregistration.json")
    try:
        raw: object = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorityError("predictive preregistration is unavailable") from exc
    if not isinstance(raw, dict):
        raise AuthorityError("predictive preregistration is malformed")
    return cast(dict[str, object], raw)


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("xb") as handle:
        for row in rows:
            handle.write(canonical_bytes(row))


def assemble_predictive_authority() -> None:
    backend = GitHubReleases()
    if any(
        item.get("tag_name") == FINAL_TAG and not item.get("draft") for item in backend.releases()
    ):
        return
    by_tag = {str(item["tag_name"]): item for item in backend.releases()}
    target_inventory: list[dict[str, object]] = []
    external_inventory: list[dict[str, object]] = []
    all_rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as raw_temp:
        temp = Path(raw_temp)
        for ordinal, (start, end) in enumerate(TARGET_SEGMENTS):
            tag = target_tag(start, end)
            release_id, assets = _stage_release(backend, by_tag, tag)
            target_assets = [item for item in assets if item.name.endswith("--target.parquet")]
            if len(target_assets) != 1:
                raise AuthorityError(f"target staging asset is absent or divergent: {tag}")
            asset = target_assets[0]
            digest = _asset_digest(asset, "target.parquet")
            local = temp / f"target-{ordinal}.parquet"
            observed_size, observed_digest = backend.download_asset(
                asset, local, MAX_TARGET_ASSET_BYTES
            )
            if observed_digest != digest:
                raise AuthorityError("target staging content hash diverged")
            table = pq.read_table(local)
            rows = cast(list[dict[str, object]], table.to_pylist())
            all_rows.extend(rows)
            target_inventory.append(
                {
                    "tag": tag,
                    "release_id": release_id,
                    "asset_id": asset.asset_id,
                    "asset_name": asset.name,
                    "bytes": observed_size,
                    "sha256": observed_digest,
                    "interval": [start, end],
                }
            )
            local.unlink()
        for start, end in BINANCE_SEGMENTS:
            tag = binance_tag(start, end)
            release_id, assets = _stage_release(backend, by_tag, tag)
            external_inventory.append(
                {
                    "provider": "official-binance-public-data",
                    "tag": tag,
                    "release_id": release_id,
                    "assets": [
                        {"asset_id": item.asset_id, "name": item.name, "bytes": item.size}
                        for item in assets
                    ],
                }
            )
        coinbase_tag = f"{PREFIX}-stage-coinbase"
        release_id, assets = _stage_release(backend, by_tag, coinbase_tag)
        external_inventory.append(
            {
                "provider": "official-coinbase-exchange",
                "tag": coinbase_tag,
                "release_id": release_id,
                "assets": [
                    {"asset_id": item.asset_id, "name": item.name, "bytes": item.size}
                    for item in assets
                ],
            }
        )
        expected = candidate_epochs(INTERVAL_START, INTERVAL_END)
        observed_epochs = [cast(int, item["epoch"]) for item in all_rows]
        if len(observed_epochs) != EXPECTED_CANDIDATES or sorted(observed_epochs) != list(expected):
            raise AuthorityError("target candidate inventory is partial, duplicate, or out of plan")
        if len(set(observed_epochs)) != len(observed_epochs):
            raise AuthorityError("target candidate inventory contains duplicates")
        eligible = [item for item in all_rows if item.get("status") == "eligible"]
        required = (
            "market_id",
            "condition_id",
            "start",
            "end",
            "decision_cutoff",
            "outcome",
            "raw_gamma_sha256",
            "target_source_identity",
        )
        if any(any(not item.get(key) for key in required) for item in eligible):
            raise AuthorityError("eligible target row is missing authority")
        conditions = [cast(str, item["condition_id"]) for item in eligible]
        if len(set(conditions)) != len(conditions):
            raise AuthorityError("eligible target condition identity is duplicated")
        split_epoch = _epoch(DEVELOPMENT_END)
        development_clusters = {
            cast(int, item["epoch"]) // 3600
            for item in eligible
            if cast(int, item["epoch"]) < split_epoch
        }
        evaluation_clusters = {
            cast(int, item["epoch"]) // 3600
            for item in eligible
            if cast(int, item["epoch"]) >= split_epoch
        }
        if (
            len(development_clusters) < REQUIRED_CLUSTERS
            or len(evaluation_clusters) < REQUIRED_CLUSTERS
        ):
            raise AuthorityError("eligible target authority does not satisfy frozen cluster power")
        preregistration = _load_preregistration()
        preregistration_identity = preregistration.get("preregistration_identity")
        if not isinstance(preregistration_identity, str) or len(preregistration_identity) != 64:
            raise AuthorityError("predictive preregistration identity is not frozen")
        external_identity = hashlib.sha256(canonical_bytes(external_inventory)).hexdigest()
        with tempfile.TemporaryDirectory() as publication_temp:
            publication = Path(publication_temp)
            cohort_rows: list[dict[str, object]] = []
            target_by_epoch = {cast(int, item["epoch"]): item for item in eligible}
            target_locator_by_epoch: dict[int, dict[str, object]] = {}
            for inventory in target_inventory:
                start_epoch = _epoch(cast(list[str], inventory["interval"])[0])
                end_epoch = _epoch(cast(list[str], inventory["interval"])[1])
                for epoch in range(start_epoch, end_epoch, 300):
                    target_locator_by_epoch[epoch] = inventory
            for order, epoch in enumerate(sorted(target_by_epoch)):
                item = target_by_epoch[epoch]
                locator = target_locator_by_epoch[epoch]
                cohort_rows.append(
                    {
                        "order": order,
                        "epoch": epoch,
                        "slug": item["slug"],
                        "market_id": item["market_id"],
                        "condition_id": item["condition_id"],
                        "start": item["start"],
                        "end": item["end"],
                        "decision_cutoff": item["decision_cutoff"],
                        "outcome": item["outcome"],
                        "utc_hour_cluster": _time(
                            datetime.fromtimestamp(epoch - epoch % 3600, UTC)
                        ),
                        "split": "development" if epoch < split_epoch else "evaluation",
                        "target_source_identity": item["target_source_identity"],
                        "raw_gamma_sha256": item["raw_gamma_sha256"],
                        "raw_authority_locator": {
                            "tag": locator["tag"],
                            "release_id": locator["release_id"],
                            "asset_id": locator["asset_id"],
                            "asset_name": locator["asset_name"],
                            "asset_sha256": locator["sha256"],
                        },
                    }
                )
            cohort = publication / "target-cohort.jsonl"
            _write_jsonl(cohort, cohort_rows)
            cohort_size, cohort_sha256 = hash_file(cohort)
            authority_core: dict[str, object] = {
                "schema_version": "1.0.0",
                "dataset_id": PREFIX,
                "preregistration_identity": preregistration_identity,
                "interval": {"start": INTERVAL_START, "end": INTERVAL_END},
                "development_end": DEVELOPMENT_END,
                "candidate_count": EXPECTED_CANDIDATES,
                "eligible_count": len(eligible),
                "excluded_count": EXPECTED_CANDIDATES - len(eligible),
                "development_clusters": len(development_clusters),
                "evaluation_clusters": len(evaluation_clusters),
                "required_evaluation_clusters_per_track": REQUIRED_CLUSTERS,
                "target_contract_identity": TARGET_SOURCE_CONTRACT_IDENTITY,
                "target_cohort": {
                    "file": cohort.name,
                    "bytes": cohort_size,
                    "sha256": cohort_sha256,
                },
                "target_staging": target_inventory,
                "external_authority_identity": external_identity,
                "external_staging": external_inventory,
                "supported_tracks": list(TRACK_IDS),
                "causality": (
                    "external event time must be at or before each row's fixed decision cutoff"
                ),
                "class": "A",
                "scoring_owner": "Gamma/Linux",
                "scored_on_windows": False,
            }
            authority_identity = hashlib.sha256(canonical_bytes(authority_core)).hexdigest()
            authority_index = dict(authority_core)
            authority_index["authority_identity"] = authority_identity
            write_json(publication / "authority-index.json", authority_index)
            handoff = {
                "schema_version": "1.0.0",
                "identity": f"{PREFIX}-gamma-linux-handoff-v1",
                "authority_identity": authority_identity,
                "preregistration_identity": preregistration_identity,
                "authority_tag": FINAL_TAG,
                "target_cohort_sha256": cohort_sha256,
                "external_authority_identity": external_identity,
                "supported_tracks": list(TRACK_IDS),
                "import": [
                    f"gh release download {FINAL_TAG} --repo atalaydor/historical-data-backfill",
                    (
                        "verify every downloaded asset SHA-256 from its content-addressed name "
                        "and authority-index.json"
                    ),
                    (
                        "download only the target/external staging assets named in "
                        "authority-index.json"
                    ),
                    (
                        "derive frozen features and score only on Gamma/Linux under the "
                        "preregistration"
                    ),
                ],
                "decision_boundary": {
                    "PASS": "may authorize isolated experimental Ultra development only",
                    "FAIL": "close or deprioritize historical external-information lane",
                    "promotion": (
                        "still requires separate residual-midpoint validation and existing gates"
                    ),
                },
            }
            write_json(publication / "gamma-linux-handoff.json", handoff)
            manifest = {
                "schema_version": "1.0.0",
                "authority_identity": authority_identity,
                "preregistration_identity": preregistration_identity,
                "external_authority_identity": external_identity,
                "files": {
                    path.name: {"bytes": hash_file(path)[0], "sha256": hash_file(path)[1]}
                    for path in sorted(publication.iterdir())
                },
                "remote_verification": (
                    "publisher performs independent authenticated redownload and hash verification "
                    "before finalization"
                ),
            }
            manifest["identity"] = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
            write_json(publication / "manifest.json", manifest)
            backend.publish_directory(FINAL_TAG, "step-2/authority", publication)
            backend.finalize(FINAL_TAG)

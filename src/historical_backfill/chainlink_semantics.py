from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

from .http import download
from .model import AuthorityError, write_json
from .release import GitHubReleases

PREFIX = "current-chainlink-60s-feasibility-v1"
FIRST_CURRENT_EPOCH = 1_786_665_600
PREVIOUS_EPOCH = FIRST_CURRENT_EPOCH - 300
GAMMA_TEMPLATE = "https://gamma-api.polymarket.com/events?slug={slug}"
RESOLUTION_SOURCE = "https://data.chain.link/streams/btc-usd-twap-60s-streams"
TARGET_INTERVAL = ("2026-03-04T03:55:00Z", "2026-07-08T21:30:00Z")
REQUIRED_INDEPENDENT_CLUSTERS = 125
MAX_GAMMA_BYTES = 2_000_000


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def _market(payload: object, slug: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise AuthorityError(f"Gamma event identity is ambiguous: {slug}")
    event = payload[0]
    markets = event.get("markets")
    if not isinstance(markets, list):
        raise AuthorityError(f"Gamma markets are absent: {slug}")
    matches = [item for item in markets if isinstance(item, dict) and item.get("slug") == slug]
    if len(matches) != 1:
        raise AuthorityError(f"Gamma market identity is ambiguous: {slug}")
    return event, matches[0]


def _extract(payload: object, slug: str, expected_window: int) -> dict[str, object]:
    event, market = _market(payload, slug)
    config = market.get("cryptoMarketConfig")
    metadata = event.get("eventMetadata")
    if not isinstance(config, dict) or not isinstance(metadata, dict):
        raise AuthorityError(f"Gamma TWAP authority is incomplete: {slug}")
    expected_id = f"btc-5m-twap-{expected_window}"
    resolution_source = market.get("resolutionSource")
    if (
        config.get("asset") != "btc"
        or config.get("duration") != "5m"
        or config.get("twapEnabled") is not True
        or config.get("twapLookbackSeconds") != expected_window
        or (market.get("cryptoMarketConfigId") or config.get("id")) != expected_id
    ):
        raise AuthorityError(f"Gamma TWAP config diverged: {slug}")
    expected_source = RESOLUTION_SOURCE.replace("60s", f"{expected_window}s")
    if resolution_source != expected_source:
        raise AuthorityError(f"Gamma resolution source diverged: {slug}")
    description = market.get("description")
    if not isinstance(description, str) or "greater than or equal to" not in description:
        raise AuthorityError(f"Gamma equality rule is absent: {slug}")
    opening = metadata.get("priceToBeat")
    final = metadata.get("finalPrice")
    if not isinstance(opening, (str, int, float)) or not isinstance(final, (str, int, float)):
        raise AuthorityError(f"Gamma opening/final authority is absent: {slug}")
    return {
        "slug": slug,
        "event_start": market.get("eventStartTime"),
        "end": market.get("endDate"),
        "config_id": expected_id,
        "twap_lookback_seconds": expected_window,
        "resolution_source": resolution_source,
        "price_to_beat": str(opening),
        "final_price": str(final),
        "equality_resolves_up": True,
    }


def build_semantic_authority(output: Path) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    observations: list[dict[str, object]] = []
    for epoch, window in ((PREVIOUS_EPOCH, 30), (FIRST_CURRENT_EPOCH, 60)):
        slug = f"btc-updown-5m-{epoch}"
        url = GAMMA_TEMPLATE.format(slug=slug)
        raw_path = output / f"gamma-{slug}.json"
        receipt = download(url, raw_path, MAX_GAMMA_BYTES)
        raw = raw_path.read_bytes()
        try:
            payload = json.loads(raw, parse_float=str)
        except json.JSONDecodeError as exc:
            raise AuthorityError(f"Gamma response is invalid JSON: {slug}") from exc
        row = _extract(payload, slug, window)
        row.update(
            {
                "source_url": url,
                "raw_bytes": len(raw),
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "etag": receipt.etag,
                "last_modified": receipt.last_modified,
            }
        )
        observations.append(row)

    previous, current = observations
    if previous["end"] != "2026-08-14T00:00:00Z":
        raise AuthorityError("adjacent pre-regime market does not end at the boundary")
    if current["event_start"] != "2026-08-14T00:00:00Z":
        raise AuthorityError("current regime does not start at the frozen boundary")

    determination: dict[str, object] = {
        "schema_version": "1.0.0",
        "kind": "current_chainlink_60s_semantic_and_feasibility_authority",
        "access_date": "2026-08-26",
        "decision": "ABANDON",
        "current_regime": {
            "effective_start": "2026-08-14T00:00:00Z",
            "asset_pair": "BTC/USD",
            "market_duration_seconds": 300,
            "config_id": "btc-5m-twap-60",
            "resolution_source": RESOLUTION_SOURCE,
            "rtds_legacy_topic": "crypto_prices_twap_sixty",
            "official_sdk_topic": "prices.crypto.chainlink.twap",
            "official_sdk_window_seconds": 60,
            "opening_field": "eventMetadata.priceToBeat",
            "closing_field": "eventMetadata.finalPrice",
            "comparison": "finalPrice >= priceToBeat resolves Up; otherwise Down",
            "rolling_state_is_not_final_resolution": True,
        },
        "boundary_observations": observations,
        "target_population": {
            "authority": "Gamma lawful midpoint/outcome population supplied by Gamma/Linux",
            "conditions": 22_190,
            "interval": list(TARGET_INTERVAL),
        },
        "overlap": {
            "interval": None,
            "candidate_markets": 0,
            "projected_eligible_markets": 0,
            "independent_utc_hour_clusters": 0,
        },
        "power": {
            "required_independent_utc_hour_clusters": REQUIRED_INDEPENDENT_CLUSTERS,
            "source_design": "existing Gamma residual benchmark design",
            "gate": "FAIL",
        },
        "causal_historical_availability": {
            "result": "NOT_REACHED_FOR_TARGET_POPULATION",
            "reason": "the current regime begins after the target population ends",
            "retrospective_timestamp_is_not_receipt_time": True,
        },
        "blocker": (
            "The current BTC five-minute Chainlink 60-second regime begins "
            "2026-08-14T00:00:00Z, after the Gamma authority ends "
            "2026-07-08T21:30:00Z; the exact intersection is empty."
        ),
        "acquisition_launched": False,
        "scoring_performed": False,
    }
    determination["semantic_authority_identity"] = _canonical_sha256(determination)
    write_json(output / "semantic-determination.json", determination)
    write_json(
        output / "manifest.json",
        {
            "schema_version": "1.0.0",
            "semantic_authority_identity": determination["semantic_authority_identity"],
            "files": {
                path.name: {
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
                for path in sorted(output.glob("gamma-*.json"))
            },
        },
    )
    return output


def publish_semantic_authority() -> None:
    with tempfile.TemporaryDirectory() as temp:
        built = build_semantic_authority(Path(temp) / "authority")
        backend = GitHubReleases()
        backend.publish_directory(PREFIX, "semantic-authority", built)
        backend.finalize(PREFIX)

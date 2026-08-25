from __future__ import annotations

from datetime import date, timedelta

from .model import SourceSpec

ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "HYPE")
SPOT_ASSETS = frozenset(ASSETS) - {"HYPE"}


def days(start: str, end: str) -> list[str]:
    current = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    result: list[str] = []
    while current < stop:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def binance_specs(asset: str, day: str) -> list[SourceSpec]:
    if asset not in ASSETS:
        raise ValueError(f"out-of-plan asset: {asset}")
    symbol = f"{asset}USDT"
    specs: list[SourceSpec] = []
    if asset in SPOT_ASSETS:
        specs.extend(
            [
                _binance("spot", "spot_klines_1s", asset, symbol, day, "klines", "1s", 0, 0, "us"),
                _binance(
                    "spot", "spot_agg_trades", asset, symbol, day, "aggTrades", None, 5, 0, "us"
                ),
            ]
        )
    specs.extend(
        [
            _binance("futures/um", "um_klines_1m", asset, symbol, day, "klines", "1m", 0, 0, "ms"),
            _binance(
                "futures/um", "um_agg_trades", asset, symbol, day, "aggTrades", None, 5, 0, "ms"
            ),
        ]
    )
    return specs


def _binance(
    venue: str,
    family: str,
    asset: str,
    symbol: str,
    day: str,
    kind: str,
    interval: str | None,
    timestamp_column: int,
    sequence_column: int,
    unit: str,
) -> SourceSpec:
    folder = f"{kind}/{symbol}"
    suffix = f"-{kind}-{day}.zip"
    if interval is not None:
        folder += f"/{interval}"
        suffix = f"-{interval}-{day}.zip"
    url = f"https://data.binance.vision/data/{venue}/daily/{folder}/{symbol}{suffix}"
    return SourceSpec(
        provider="binance-public-data",
        family=family,
        asset=asset,
        instrument=symbol,
        day=day,
        url=url,
        checksum_url=f"{url}.CHECKSUM",
        timestamp_column=timestamp_column,
        sequence_column=sequence_column,
        timestamp_unit=unit,
    )

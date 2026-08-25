# Run 1 authority and source decisions

Access date: 2026-08-25.

The local scientific intersection is BTC 5-minute canonical source identity
`3802c8f362aded0ff2dcd001f9e393fbd7f8be85619e70822901d224a348a227`, with 5,660
distinct markets observed from 2026-06-01 through 2026-07-08 22:55 UTC. Other assets may be
acquired independently but cannot be declared benchmark-complete until matching settled-market
authority exists.

## Class A external exchanges

Binance's official public archive documents daily/monthly spot and futures klines and aggregate
trades, their endpoint provenance, timestamp columns, and adjacent SHA-256 checksum objects:
https://github.com/binance/binance-public-data/blob/master/README.md. Real HEAD probes found all
four BTC families, and spot/perpetual objects for BTC/ETH/SOL/XRP/DOGE/BNB. HYPE has USD-M 1m
klines and aggregate trades but its spot paths return 404. The archive is acquired later, while
event timestamps identify contemporaneously public exchange observations. Archive publication
time is recorded separately and is never imputed as event time.

Coinbase Exchange public 60-second candles are documented at
https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles.
A real 2026-07-08 probe returned 11 BTC-USD buckets. The API warns that buckets can be missing
when no trades occur and can precede the requested start, so production filters exact bounds and
records omissions; it does not synthesize candles.

## Chainlink current 60-second TWAP

Fresh Polymarket rules for an actual SOL 5-minute market state that both endpoints are Chainlink's
SOL/USD 60-second TWAP stream:
https://polymarket.com/event/sol-updown-5m-1787314200. This current semantic regime is later than
the existing canonical BTC authority ending 2026-07-08. Arena's June `cap_prices` is a collector
capture of older reference ticks, not proof of current TWAP equivalence. The Run-1 intersection is
therefore empty and this lane is abandoned rather than backfilled with a proxy.

## Class B Polymarket depth

The accessible Arena inventory at
https://huggingface.co/api/datasets/Alezanello/polymarket-arena-capture/tree/main contains real
Parquet files for six assets, no HYPE. A 955,211-byte file probe produced 23,794 rows, 36 market
slugs, 72 token identities, 2026-06-15 15:55:37.655/16:19:03.095 UTC capture bounds, 1.002s median
within-token cadence, 16.423s p99 gap, 65.068s maximum gap, and 22,202/23,794 non-empty ladders on
both sides. Because source time is collector receipt time and gaps exist, it is Class B and only
the preregistered 52,248,636-byte full-day pilot is allowed.

PolyOrderbooks' actual public inventory contains seven requested asset files, but its card says
only 352 resolved markets from 2026-08-23 09:10/12:50 UTC and reports 3.078% crossed rows. It does
not overlap the existing authority and is too small for the preregistered benchmark:
https://huggingface.co/datasets/polyorderbooks/polymarket-crypto-5min-l2.

PMXT full-depth is rejected because its public exporter explodes grouped `price_changes`, sorts
only by market/token/receive timestamp, and omits native receive sequence and row position. The
evidence is reproducible from
https://github.com/pmxt-dev/polymarket-orderbook-collector/blob/main/services/r2-archive/exporter/run.py
and
https://github.com/pmxt-dev/polymarket-orderbook-collector/blob/main/shared/rust/polymarket-orderbook-rust/src/events.rs.

All normalized observations bind provider, product, source URL, adjacent official checksum when
available, HTTP ETag/Last-Modified, source/event-time field, archive time, acquisition time,
transform version, row count, byte count, and content SHA-256. No source family is mixed without
its provider identity. Missing objects and discontinuities are durable exclusions, not filled.

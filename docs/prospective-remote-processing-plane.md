# Prospective V51/V55 remote processing plane

This finite public control plane transforms sealed prospective evidence. Gamma/Linux remains the
sole authority for causal recording, raw V51/V55 evidence, bindings, outcomes, research features,
scoring, maturity, and promotion. Windows receives only compact identities and run status.

The machine-readable contract catalog is
`config/prospective-processing-plane-contract.json`. The sealed input schema is
`prospective-sealed-input.v1`; the derived schema is `prospective-derived-authority.v1`; and the
return package is `prospective-gamma-linux-handoff.v1`. Each input manifest, processing plan,
chunk manifest, derived manifest, reconciliation, and handoff has a SHA-256 identity over canonical
JSON excluding only its own identity field.

## Reused architecture and execution

The plane reuses the repository's GitHub Releases backend, immutable content-addressed asset names,
draft staging releases, divergence rejection, short final publication, finalization, and
read-after-write verification. Added code is limited to the two sealed-input adapters, their
versioned contracts, chunk/assembly/reconciliation commands, and bounded Actions orchestration.

Workers first redownload the final sealed-input Release contract and partition by exact repository,
release ID/tag, asset ID/name, byte size, and SHA-256. Transform workers have read-only repository
permission. They emit transient Actions artifacts; short writer jobs authenticate and publish one
chunk per isolated draft tag. A rerun accepts an already complete byte-identical chunk as a no-op.
Only exact unfinished chunks need rerunning. Assembly orders sealed partition ordinals and canonical
row keys, rejects missing/duplicate/divergent market populations, publishes a draft derived Release,
and reconciliation independently redownloads and hashes every asset before adding the compact
handoff and finalizing. Actions artifacts and staging drafts are never canonical authority.
An otherwise zero-byte logical JSONL is represented by the single authenticated
`prospective-empty-set.v1` envelope; assembly validates and removes it before combining rows, and a
final empty logical set retains that explicit envelope because GitHub Release assets must be
non-empty.

Processing identity binds the exact source commit, Python implementation/version/cache tag,
`ubuntu-24.04`, pinned setup/upload/download Actions, sealed input-manifest identity,
transformation contract and its identity, and canonical serialization/order/missing-data rules.
Same authenticated input plus processing identity must produce byte-identical output; an existing
different asset under the same logical name fails closed.

## Gamma to Xenon: exact sealed package

Gamma publishes raw evidence directly from Linux as immutable final Release assets, then supplies
only these compact values to the production workflow:

- input repository, final release ID/tag, sealed-contract asset ID, exact byte size, and SHA-256;
- `authority_type` (`v51_polymarket_full_depth` or `v55_chainlink_twap60`), authority version,
  source-authority identity, and self-authenticated input-manifest identity;
- every partition's ordinal, asset ID/name, byte size, SHA-256, row count, and canonical-JSONL format;
- exact market, condition, window start/end, decision cutoff, partition, and—when applicable—Up/Down
  token bindings or BTC/USD TWAP60/opening-report binding;
- source/event timestamp field, Linux receipt timestamp and sequence authority, exact cutoff rule,
  partition-global Linux receipt sequence, session identity, explicit gap records,
  no-inference/no-interpolation continuity policy, and Linux exclusions;
- the permitted owner-specific transformation identity/parameters and expected output schema;
- exact contiguous chunk matrix matching all sealed partitions.

Any absent, ambiguous, draft/prerelease, mismatched, oversized, non-contiguous, or unauthenticated
authority fails before transformation. Raw evidence never passes through chat or Windows.

## V55 adapter

`v55-normalized-twap60-primitives.v1` accepts only supplied BTC/USD 60-second report authority. It
preserves exact E18/full-accuracy decimal strings, report/source timestamps, Linux receipt time and
order, session, opening-report identity, market/window/cutoff, and explicit gaps. It emits the exact
causal report trajectory and opening/latest-causal report bindings needed for Linux to derive
current-versus-opening distance, movement, bounded trajectory/volatility, and distance-by-time
states. Missing opening/current reports or a declared intersecting gap cause explicit exclusion.
It never interpolates, substitutes spot prices, infers timing, or consumes outcomes.

## V51 adapter

`v51-normalized-depth-primitives.v1` preserves authenticated book, price-change, trade, and gap
events with condition/market/token identity, exact prices/sizes, event time, Linux receipt order,
event sequence, session/reconnect, and cutoff. It emits normalized events plus top-N authoritative
book anchors at decision time and decision-minus-60 seconds where actual snapshots exist. These are
sufficient for Linux-owned depth imbalance/shape, microprice/pressure, depletion/replenishment,
liquidity-flow, trade, and change diagnostics. A missing token snapshot, invalid order, or declared
gap explicitly excludes the market; no continuity is reconstructed.

## Xenon to Gamma: return authority

The final Release returns the processing commit/tag and processing identity; input source and
manifest identities; transformation definition and identity; derived-authority identity;
source-chunk to derived provenance; canonical row-order identity; exact market eligible/excluded
counts and reasons; every content-addressed asset name/hash/size and Release locator; remote
reconciliation identity; and handoff identity/path.

Linux imports without reacquisition using the handoff's `gh release download` command, verifies each
asset name/size/SHA-256, verifies the derived-manifest identity and inventory, treats an exact
`prospective-empty-set.v1` envelope as zero rows, and imports primitives and exclusions without
adding, repairing, or reordering rows. Linux then derives/freezes its own
research features and alone performs scientific scoring.

Canary tags contain `prospective-plane-canary` and all fixture contracts contain namespace
`fixture-canary-not-production`; they are prohibited as V51/V55 prospective evidence.

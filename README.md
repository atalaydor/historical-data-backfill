# Historical rich-information backfill

Private, finite acquisition control plane for source evidence relevant to settled Polymarket
5-minute Up/Down markets. It never modifies prospective V51/V55/V57 authorities.

Run 1 freezes the shortest recent interval supported by the existing BTC 5-minute canonical
authority: `2026-06-01T00:00:00Z` through `2026-07-08T23:00:00Z` (end exclusive). External
exchange observations are Class A; third-party historical Polymarket snapshots are isolated
Class B pilots. Current Chainlink 60-second TWAP history has no overlap with that authority.

The remote topology is isolated compute, one immutable content-addressed staging Release per
asset/segment, deterministic reconciliation, then a short assembly publication. Source archives
exist only in runner temporary directories and are deleted before job completion.

See `docs/run-1-authority.md` and `config/preregistration.json`.

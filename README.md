# Historical rich-information backfill

Public, finite acquisition control plane for source evidence relevant to settled Polymarket
5-minute Up/Down markets. It never modifies prospective V51/V55/V57 authorities.

Run 1 freezes the shortest recent interval supported by the existing BTC 5-minute canonical
authority: `2026-06-01T00:00:00Z` through `2026-07-08T23:00:00Z` (end exclusive). External
exchange observations are Class A; third-party historical Polymarket snapshots are isolated
Class B pilots. Current Chainlink 60-second TWAP history has no overlap with that authority.

The remote topology is isolated compute, one immutable content-addressed staging Release per
asset/segment, deterministic reconciliation, then a short assembly publication. Source archives
exist only in runner temporary directories and are deleted before job completion.

Run 2 reconciled all 29 production stages against authenticated Release authority: acquisition is
complete, BTC and seven-asset authority indexes are certified, and the frozen Arena Class-B pilot
is abandoned after failing its preregistered quality gates. No residual acquisition remains.

See `docs/run-1-authority.md`, `docs/run-2-reconciliation.json`,
`docs/btc-gamma-linux-handoff.json`, and `config/preregistration.json`.

The closed Run-1 experiment is never reused as validation evidence. The separately preregistered
`btc-independent-predictive-validation-v1` experiment uses the untouched half-open interval
`[2026-07-08T23:00:00Z, 2026-07-21T07:00:00Z)`. It asks only whether the same 16 Class-A
external-information tracks independently predict final BTC 5-minute Up/Down outcomes; it does
not test residual value beyond a Polymarket midpoint. Official Gamma supplies immutable target
identity/outcome snapshots, and Binance/Coinbase supply external observations. Acquisition is
segmented and parallel, while only final canonical publication is exclusive. Gamma/Linux owns
all feature derivation and scoring.

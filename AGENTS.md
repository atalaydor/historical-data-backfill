# Repository instructions

- This public repository is a finite control plane for free historical evidence relevant to Polymarket 5-minute Up/Down markets for BTC, ETH, SOL, XRP, DOGE, BNB, and HYPE.
- Keep prospective-compatible Class A evidence physically and logically separate from historical-only Class B evidence.
- Never add credentials, wallets, strategy material, live trading, paid data, or synthetic/interpolated order books.
- Windows is control plane only. Historical source archives are transient on GitHub-hosted runners; immutable content-addressed GitHub Releases are permanent authority.
- Every source claim requires URL, access date, source/event/archive/acquisition timing semantics, and a deterministic checksum chain.
- Run unit tests, Ruff, strict mypy, workflow validation, and `git diff --check` for production changes.

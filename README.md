# Birdeye Token Radar

Birdeye Token Radar is a small Solana market-intelligence dashboard built for Birdeye Data Sprint 4.

It uses Birdeye's public API to rank liquid Solana tokens by a blended momentum score that weighs liquidity, 24h volume, trader breadth, holder count, buy/sell balance, and price movement. The output is a static dashboard under `docs/`, so it can be published directly with GitHub Pages.

## Live Build

- Dashboard: `docs/index.html`
- Data artifact: `docs/data/radar.json`
- API call log: generated locally as `docs/data/call-log.json` and intentionally ignored from git.

## Birdeye Endpoints Used

- `GET /defi/tokenlist`
- `GET /defi/token_overview`
- `GET /defi/price`

I also checked `GET /defi/token_security`, but this API key does not have permission for that endpoint. The dashboard therefore uses transparent, available market structure signals instead of privileged security metadata.

## Run

Set a key, then collect current data:

```bash
export BIRDEYE_API_KEY="..."
python3 scripts/collect.py
```

For this local workspace, the script also supports reading `../API_keys.local.json` or `API_keys.local.json` when present. The key is never written to generated artifacts.

## Method

The collector pulls several pages of Solana token list data, selects high-activity candidates, then enriches each candidate with token overview and price data. It records a sanitized call log and requires at least 50 successful Birdeye calls before marking the run as submission-ready.

The scoring model is intentionally simple and auditable:

- liquidity and volume reward real market depth;
- holder count and unique wallets reward participation breadth;
- buy/sell balance reduces washout from one-sided sell pressure;
- price-change handling rewards strong movement without letting a single spike dominate.

This is not financial advice. It is a product prototype showing how Birdeye data can be converted into an explainable monitoring surface.

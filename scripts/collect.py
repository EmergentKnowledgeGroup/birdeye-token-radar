#!/usr/bin/env python3
"""Collect Birdeye Solana token data and build a static radar artifact."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "docs" / "data"
RADAR_PATH = DATA_DIR / "radar.json"
CALL_LOG_PATH = DATA_DIR / "call-log.json"

BASE_URL = "https://public-api.birdeye.so"
CHAIN = "solana"
MIN_SUCCESSFUL_CALLS = 50
REQUEST_DELAY_SECONDS = 1.45
MAX_RETRIES = 5


@dataclass
class ApiCall:
    endpoint: str
    path: str
    status: int
    ok: bool
    timestamp: str
    note: str = ""


class BirdeyeClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.calls: list[ApiCall] = []
        self.last_request_at = 0.0

    @property
    def successful_calls(self) -> int:
        return sum(1 for call in self.calls if call.ok)

    def get(self, endpoint: str, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        query = urllib.parse.urlencode(params)
        url = f"{BASE_URL}{path}?{query}" if query else f"{BASE_URL}{path}"

        for attempt in range(MAX_RETRIES):
            elapsed = time.time() - self.last_request_at
            if elapsed < REQUEST_DELAY_SECONDS:
                time.sleep(REQUEST_DELAY_SECONDS - elapsed)

            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "X-API-KEY": self.api_key,
                    "x-chain": CHAIN,
                    "User-Agent": "birdeye-token-radar/1.0",
                },
            )

            status = 0
            note = ""
            try:
                self.last_request_at = time.time()
                with urllib.request.urlopen(request, timeout=35) as response:
                    status = response.status
                    payload = json.loads(response.read().decode("utf-8"))
                ok = status == 200 and bool(payload.get("success", True))
                if ok:
                    self._log(endpoint, path, status, True)
                    return payload
                note = str(payload.get("message") or payload.get("error") or "api returned success=false")
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    body = exc.read().decode("utf-8")
                    parsed = json.loads(body)
                    note = str(parsed.get("message") or parsed.get("error") or body[:160])
                except Exception:
                    note = exc.reason or "http error"
            except Exception as exc:
                note = str(exc)

            self._log(endpoint, path, status, False, note)
            if status == 429 or "too many" in note.lower():
                time.sleep(min(12, 2 + attempt * 2))
                continue
            if status in {500, 502, 503, 504}:
                time.sleep(min(10, 1 + attempt * 2))
                continue
            return None

        return None

    def _log(self, endpoint: str, path: str, status: int, ok: bool, note: str = "") -> None:
        self.calls.append(
            ApiCall(
                endpoint=endpoint,
                path=path,
                status=status,
                ok=ok,
                timestamp=datetime.now(timezone.utc).isoformat(),
                note=note[:220],
            )
        )


def load_api_key() -> str:
    env_key = os.environ.get("BIRDEYE_API_KEY", "").strip()
    if env_key:
        return env_key

    for candidate in (ROOT / "API_keys.local.json", ROOT.parent / "API_keys.local.json"):
        if candidate.exists():
            payload = json.loads(candidate.read_text())
            key = str(payload.get("birdeye_api_key", "")).strip()
            if key:
                return key

    raise SystemExit("BIRDEYE_API_KEY not set and no local API_keys.local.json found")


def as_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return 0.0
        return parsed
    except (TypeError, ValueError):
        return 0.0


def first_number(data: dict[str, Any], names: tuple[str, ...]) -> float:
    for name in names:
        if name in data:
            value = as_float(data.get(name))
            if value:
                return value
    return 0.0


def list_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("tokens", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def token_address(token: dict[str, Any]) -> str:
    return str(token.get("address") or token.get("mint") or token.get("tokenAddress") or "").strip()


def token_symbol(token: dict[str, Any]) -> str:
    return str(token.get("symbol") or token.get("name") or "UNKNOWN").strip()[:24]


def candidate_seed_score(token: dict[str, Any]) -> float:
    liquidity = first_number(token, ("liquidity", "liquidityUSD"))
    volume = first_number(token, ("v24hUSD", "volume24hUSD", "volume24h"))
    change = abs(first_number(token, ("priceChange24hPercent", "priceChange24h", "priceChange24hPercent")))
    return math.log1p(max(liquidity, 0)) * 1.8 + math.log1p(max(volume, 0)) * 2.2 + min(change, 80) * 0.04


def score_token(seed: dict[str, Any], overview: dict[str, Any], price: dict[str, Any]) -> dict[str, Any]:
    overview_data = overview.get("data") if isinstance(overview.get("data"), dict) else {}
    price_data = price.get("data") if isinstance(price.get("data"), dict) else {}

    liquidity = first_number(overview_data, ("liquidity", "liquidityUSD")) or first_number(seed, ("liquidity", "liquidityUSD"))
    volume_24h = first_number(overview_data, ("v24hUSD", "volume24hUSD", "volume24h")) or first_number(seed, ("v24hUSD", "volume24hUSD", "volume24h"))
    holders = first_number(overview_data, ("holder", "holders", "holderCount"))
    unique_wallets = first_number(overview_data, ("uniqueWallet24h", "uniqueWallets24h", "uaw24h"))
    buys = first_number(overview_data, ("buy24h", "buyCount24h", "numberBuy24h"))
    sells = first_number(overview_data, ("sell24h", "sellCount24h", "numberSell24h"))
    trade_count = buys + sells
    price_change = first_number(price_data, ("priceChange24h", "priceChange24hPercent")) or first_number(overview_data, ("priceChange24hPercent", "priceChange24h"))
    price_value = first_number(price_data, ("value", "price")) or first_number(seed, ("price",))

    buy_sell_balance = 0.5
    if trade_count:
        buy_sell_balance = buys / trade_count

    liquidity_score = math.log1p(max(liquidity, 0)) * 1.9
    volume_score = math.log1p(max(volume_24h, 0)) * 2.3
    holder_score = math.log1p(max(holders, 0)) * 1.2
    wallet_score = math.log1p(max(unique_wallets, 0)) * 1.5
    balance_score = max(0.0, 1 - abs(buy_sell_balance - 0.52) * 1.8) * 9
    movement_score = max(-8, min(12, price_change * 0.18))

    score = liquidity_score + volume_score + holder_score + wallet_score + balance_score + movement_score

    return {
        "address": token_address(seed),
        "symbol": token_symbol(seed),
        "name": str(seed.get("name") or overview_data.get("name") or token_symbol(seed)).strip()[:80],
        "logoURI": str(seed.get("logoURI") or seed.get("logoUrl") or overview_data.get("logoURI") or ""),
        "score": round(score, 2),
        "price": price_value,
        "priceChange24h": round(price_change, 4),
        "liquidityUSD": round(liquidity, 2),
        "volume24hUSD": round(volume_24h, 2),
        "holders": int(holders) if holders else 0,
        "uniqueWallet24h": int(unique_wallets) if unique_wallets else 0,
        "buy24h": int(buys) if buys else 0,
        "sell24h": int(sells) if sells else 0,
        "buySellBalance": round(buy_sell_balance, 4),
        "explain": [
            f"${volume_24h:,.0f} 24h volume",
            f"${liquidity:,.0f} liquidity",
            f"{int(unique_wallets):,} unique wallets" if unique_wallets else "wallet breadth unavailable",
            f"{price_change:+.2f}% 24h price move",
        ],
    }


def collect() -> dict[str, Any]:
    client = BirdeyeClient(load_api_key())
    seen: dict[str, dict[str, Any]] = {}

    for offset in (0, 50, 100, 150):
        payload = client.get(
            "tokenlist",
            "/defi/tokenlist",
            {
                "sort_by": "v24hUSD",
                "sort_type": "desc",
                "offset": offset,
                "limit": 50,
            },
        )
        if not payload:
            continue
        for item in list_items(payload):
            address = token_address(item)
            if address:
                seen[address] = item

    seeds = sorted(seen.values(), key=candidate_seed_score, reverse=True)[:28]
    ranked: list[dict[str, Any]] = []

    for seed in seeds:
        address = token_address(seed)
        if not address:
            continue
        overview = client.get("token_overview", "/defi/token_overview", {"address": address})
        price = client.get("price", "/defi/price", {"address": address})
        if overview and price:
            ranked.append(score_token(seed, overview, price))
        if client.successful_calls >= MIN_SUCCESSFUL_CALLS and len(ranked) >= 22:
            break

    ranked.sort(key=lambda item: item["score"], reverse=True)

    endpoint_counts: dict[str, int] = {}
    for call in client.calls:
        if call.ok:
            endpoint_counts[call.endpoint] = endpoint_counts.get(call.endpoint, 0) + 1

    artifact = {
        "project": "Birdeye Token Radar",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "chain": CHAIN,
        "submissionReady": client.successful_calls >= MIN_SUCCESSFUL_CALLS,
        "calls": {
            "successful": client.successful_calls,
            "total": len(client.calls),
            "minimumRequired": MIN_SUCCESSFUL_CALLS,
            "endpoints": endpoint_counts,
        },
        "method": {
            "candidateUniverse": len(seen),
            "rankedTokens": len(ranked),
            "features": [
                "liquidity",
                "24h volume",
                "holder count",
                "24h unique wallets",
                "buy/sell balance",
                "24h price change",
            ],
        },
        "tokens": ranked,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RADAR_PATH.write_text(json.dumps(artifact, indent=2) + "\n")
    CALL_LOG_PATH.write_text(
        json.dumps([call.__dict__ for call in client.calls], indent=2) + "\n"
    )
    return artifact


def main() -> None:
    artifact = collect()
    print(
        "Birdeye Token Radar generated: "
        f"{artifact['calls']['successful']} successful calls, "
        f"{artifact['method']['rankedTokens']} ranked tokens, "
        f"submissionReady={artifact['submissionReady']}"
    )
    print(f"Data: {RADAR_PATH.relative_to(ROOT)}")
    print(f"Call log: {CALL_LOG_PATH.relative_to(ROOT)}")
    if not artifact["submissionReady"]:
        raise SystemExit("Run did not reach the 50 successful call eligibility threshold")


if __name__ == "__main__":
    main()

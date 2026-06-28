"""Fast scanner using DexScreener API — no RPC needed for token discovery."""
import httpx
import asyncio
import re
from dataclasses import dataclass
from typing import Optional

PUMPFUN_MINT_RE = re.compile(r"pump$", re.IGNORECASE)


@dataclass
class DexPair:
    chain_id: str
    dex_id: str
    url: str
    pair_address: str
    base_token: dict
    quote_token: dict
    price_native: float
    price_usd: float
    liquidity_usd: float
    fdv: float
    volume_24h: float
    price_change_24h: float


async def fetch_token_profiles() -> list[dict]:
    """Get latest token profiles from DexScreener (30 most recent)."""
    async with httpx.AsyncClient(timeout=15) as cl:
        r = await cl.get("https://api.dexscreener.com/token-profiles/latest/v1")
        return r.json()


async def search_pairs(query: str) -> list[DexPair]:
    """Search for trading pairs on DexScreener."""
    async with httpx.AsyncClient(timeout=15) as cl:
        r = await cl.get(
            f"https://api.dexscreener.com/latest/dex/search",
            params={"q": query},
        )
        data = r.json()
    pairs = []
    for p in data.get("pairs", []):
        try:
            pairs.append(DexPair(
                chain_id=p.get("chainId", ""),
                dex_id=p.get("dexId", ""),
                url=p.get("url", ""),
                pair_address=p.get("pairAddress", ""),
                base_token=p.get("baseToken", {}),
                quote_token=p.get("quoteToken", {}),
                price_native=float(p.get("priceNative", 0) or 0),
                price_usd=float(p.get("priceUsd", 0) or 0),
                liquidity_usd=float(p.get("liquidity", {}).get("usd", 0) or 0),
                fdv=float(p.get("fdv", 0) or 0),
                volume_24h=float(p.get("volume", {}).get("h24", 0) or 0),
                price_change_24h=float(p.get("priceChange", {}).get("h24", 0) or 0),
            ))
        except (ValueError, TypeError):
            continue
    return pairs


async def find_arb_opportunities(min_liquidity_usd: float = 500) -> list[dict]:
    """Find cross-DEX arbitrage opportunities for pump.fun tokens."""
    profiles = await fetch_token_profiles()
    opportunities = []
    
    for profile in profiles[:20]:  # max 20 tokens per scan
        mint = profile.get("tokenAddress", "")
        if not mint or not PUMPFUN_MINT_RE.search(mint):
            continue

        # Search DexScreener for all pairs involving this token
        pairs = await search_pairs(mint)
        if not pairs:
            continue

        # Group pairs by DEX
        by_dex: dict[str, list[DexPair]] = {}
        for p in pairs:
            by_dex.setdefault(p.dex_id, []).append(p)

        # Look for price differences between pump.fun and other DEXes
        pump_pairs = [p for p in pairs if "pump" in p.dex_id.lower()]
        other_pairs = [p for p in pairs if "pump" not in p.dex_id.lower()]

        if not pump_pairs or not other_pairs:
            continue

        pump_price = pump_pairs[0].price_native
        if pump_price <= 0:
            continue

        for dex_pair in other_pairs:
            if dex_pair.liquidity_usd < min_liquidity_usd:
                continue
            dex_price = dex_pair.price_native
            if dex_price <= 0:
                continue

            # Calculate arb profit
            price_diff_pct = abs(dex_price - pump_price) / pump_price * 100
            if price_diff_pct < 0.5:  # skip tiny price differences
                continue

            trade_size_sol = min(0.1, dex_pair.liquidity_usd / 160 * 0.05)
            profit_sol = trade_size_sol * (price_diff_pct / 100) * 0.7  # after fees

            opportunities.append({
                "strategy": "cross_dex",
                "mint": mint,
                "pump_price": pump_price,
                "dex_price": dex_price,
                "dex": dex_pair.dex_id,
                "dex_pair": dex_pair.pair_address,
                "price_diff_pct": price_diff_pct,
                "profit_sol": profit_sol,
                "profit_percent": price_diff_pct * 0.7,
                "confidence": 0.5 if price_diff_pct > 2 else 0.3,
                "liquidity_usd": dex_pair.liquidity_usd,
            })

            # Only keep the best opportunity per token
            break

    return opportunities

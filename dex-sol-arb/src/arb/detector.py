import asyncio
import time
from typing import Optional

from ..client import SolanaClient
from ..scanner.pump_fun import PumpFunScanner
from ..scanner.dex_pools import DexPoolScanner
from ..pricing.pump_bonding import PumpBondingPricer
from ..pricing.dex_amm import DexPricer
from .calculator import ArbCalculator, ArbOpportunity


class ArbDetector:
    def __init__(self, client: SolanaClient, cfg):
        self.client = client
        self.cfg = cfg
        self.pump_scanner = PumpFunScanner(client)
        self.dex_scanner = DexPoolScanner(client)
        self.pump_pricer = PumpBondingPricer(client)
        self.dex_pricer = DexPricer(client)
        self.calculator = ArbCalculator(
            min_profit_sol=cfg.arbitrage.min_profit_sol,
            min_profit_percent=cfg.arbitrage.min_profit_percent,
            max_trade_sol=cfg.arbitrage.max_trade_sol,
            min_trade_sol=cfg.arbitrage.min_trade_sol,
        )
        self.token_cache: dict[str, float] = {}  # mint -> price_in_sol

    async def scan_once(self) -> list[ArbOpportunity]:
        """Run one full scan cycle and return opportunities found."""
        opportunities = []

        # 1. Get active pump.fun tokens
        tokens = await self.pump_scanner.scan_recent_tokens(
            self.cfg.pumpfun.max_token_age_minutes
        )
        if not tokens:
            return opportunities

        # 2. For each token, check prices on pump.fun and DEX pools
        tasks = []
        for t in tokens[:30]:  # limit to 30 tokens per scan
            tasks.append(self._evaluate_token(t.mint))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, ArbOpportunity):
                opportunities.append(r)
            elif isinstance(r, list):
                opportunities.extend(r)

        # 3. Multi-pair arb across pump.fun tokens
        if self.cfg.arbitrage.enable_multi_pair and len(self.token_cache) >= 3:
            mp = self._check_multi_pair()
            if mp:
                opportunities.append(mp)

        return opportunities

    async def _evaluate_token(self, mint: str) -> Optional[ArbOpportunity | list]:
        """Check if a single token has arb opps across DEXes."""
        # Get pump.fun price
        pump_price = await self.pump_pricer.get_price(mint, self.cfg.arbitrage.min_trade_sol)
        if "error" in pump_price:
            return None
        token_price_sol = pump_price["token_price_sol"]

        # Cache for multi-pair
        self.token_cache[mint] = token_price_sol

        if not self.cfg.arbitrage.enable_cross_dex:
            return None

        # Find DEX pools for this token
        pools = await self.dex_scanner.find_pools_for_token(mint)
        if not pools:
            return None

        opportunities = []
        for pool in pools[:5]:  # max 5 pools per token
            pool_price = await self.dex_pricer.get_pool_price(pool.dex, pool.pool_address)
            if not pool_price or pool_price.get("price", 0) == 0:
                continue

            dex_price = pool_price["price"]
            trade_size = self.cfg.arbitrage.min_trade_sol

            opp = self.calculator.evaluate_cross_dex(
                pump_price_buy=token_price_sol,
                pump_price_sell=token_price_sol,
                dex_price_buy=dex_price,
                dex_price_sell=dex_price,
                trade_size_sol=trade_size,
            )
            if opp:
                opp.legs[1].dex = pool.dex
                opp.legs[1].pool_address = pool.pool_address
                opp.legs[0].token_mint = mint
                opp.legs[1].token_mint = mint
                opportunities.append(opp)

        return opportunities if opportunities else None

    def _check_multi_pair(self) -> Optional[ArbOpportunity]:
        prices = dict(self.token_cache)
        if len(prices) < 3:
            return None
        return self.calculator.evaluate_multi_pair(
            prices, self.cfg.arbitrage.min_trade_sol
        )

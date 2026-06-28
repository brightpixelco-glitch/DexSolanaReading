from dataclasses import dataclass
from typing import Optional


@dataclass
class ArbLeg:
    dex: str
    pool_address: str
    side: str  # "buy" or "sell"
    token_mint: str
    amount: float
    price: float


@dataclass
class ArbOpportunity:
    strategy: str  # "cross_dex" or "multi_pair"
    profit_sol: float
    profit_percent: float
    legs: list[ArbLeg]
    total_cost_sol: float
    confidence: float  # 0-1


FEE_ESTIMATE_SOL = 0.0001  # per tx
PUMPFUN_FEE_BPS = 100  # 1%
DEX_FEE_BPS = 30  # typical 0.3%


class ArbCalculator:
    def __init__(self, min_profit_sol: float = 0.001,
                 min_profit_percent: float = 0.5,
                 max_trade_sol: float = 5.0,
                 min_trade_sol: float = 0.1):
        self.min_profit_sol = min_profit_sol
        self.min_profit_percent = min_profit_percent
        self.max_trade_sol = max_trade_sol
        self.min_trade_sol = min_trade_sol

    def evaluate_cross_dex(self, pump_price_buy: float, pump_price_sell: float,
                           dex_price_buy: float, dex_price_sell: float,
                           trade_size_sol: float) -> Optional[ArbOpportunity]:
        """Evaluate cross-DEX arb: pump.fun vs DEX for the same token pair."""
        if trade_size_sol < self.min_trade_sol or trade_size_sol > self.max_trade_sol:
            return None

        # Scenario 1: Buy on pump.fun, sell on DEX
        tokens_bought = trade_size_sol * (1 - PUMPFUN_FEE_BPS / 10000) / pump_price_buy
        sol_received = tokens_bought * dex_price_sell * (1 - DEX_FEE_BPS / 10000)
        profit_1 = sol_received - trade_size_sol - FEE_ESTIMATE_SOL
        pct_1 = (profit_1 / trade_size_sol) * 100

        # Scenario 2: Buy on DEX, sell on pump.fun
        tokens_bought_2 = trade_size_sol * (1 - DEX_FEE_BPS / 10000) / dex_price_buy
        sol_received_2 = tokens_bought_2 * pump_price_sell * (1 - PUMPFUN_FEE_BPS / 10000)
        profit_2 = sol_received_2 - trade_size_sol - FEE_ESTIMATE_SOL
        pct_2 = (profit_2 / trade_size_sol) * 100

        best_profit = max(profit_1, profit_2)
        best_pct = max(pct_1, pct_2)

        if best_profit < self.min_profit_sol or best_pct < self.min_profit_percent:
            return None

        if profit_1 >= profit_2:
            legs = [
                ArbLeg("pump_fun", "", "buy", "", trade_size_sol, pump_price_buy),
                ArbLeg("dex", "", "sell", "", tokens_bought, dex_price_sell),
            ]
        else:
            legs = [
                ArbLeg("dex", "", "buy", "", trade_size_sol, dex_price_buy),
                ArbLeg("pump_fun", "", "sell", "", tokens_bought_2, pump_price_sell),
            ]

        return ArbOpportunity(
            strategy="cross_dex",
            profit_sol=best_profit,
            profit_percent=best_pct,
            legs=legs,
            total_cost_sol=trade_size_sol,
            confidence=0.7 if best_profit > self.min_profit_sol * 2 else 0.5,
        )

    def evaluate_multi_pair(self, prices: dict[str, float],
                             trade_size_sol: float) -> Optional[ArbOpportunity]:
        """Evaluate multi-pair routing: SOL -> A -> B -> SOL.

        prices: dict mapping token_mint -> price_in_sol
        """
        if trade_size_sol < self.min_trade_sol or trade_size_sol > self.max_trade_sol:
            return None

        tokens = list(prices.keys())
        best_profit = 0
        best_path = None

        for i, token_a in enumerate(tokens):
            for token_b in tokens:
                if token_a == token_b:
                    continue
                pa = prices[token_a]
                pb = prices[token_b]

                # SOL -> A -> B -> SOL
                a_bought = trade_size_sol * (1 - PUMPFUN_FEE_BPS / 10000) / pa
                b_bought = a_bought * pa * (1 - PUMPFUN_FEE_BPS / 10000) / pb
                sol_back = b_bought * pb * (1 - PUMPFUN_FEE_BPS / 10000)
                profit = sol_back - trade_size_sol - FEE_ESTIMATE_SOL * 3
                pct = (profit / trade_size_sol) * 100

                if profit > best_profit:
                    best_profit = profit
                    best_pct = pct
                    best_path = (token_a, token_b, profit, pct)

        if best_path and best_profit >= self.min_profit_sol and best_path[3] >= self.min_profit_percent:
            return ArbOpportunity(
                strategy="multi_pair",
                profit_sol=best_path[2],
                profit_percent=best_path[3],
                legs=[
                    ArbLeg("pump_fun", "", "buy", best_path[0], trade_size_sol, 0),
                    ArbLeg("pump_fun", "", "buy", best_path[1], 0, 0),
                    ArbLeg("pump_fun", "", "sell", "", 0, 0),
                ],
                total_cost_sol=trade_size_sol,
                confidence=0.4,
            )
        return None

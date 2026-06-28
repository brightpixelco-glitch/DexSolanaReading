#!/usr/bin/env python3
"""Paper trader using DexScreener API — no RPC needed."""
import asyncio
import sys
import os
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.scanner.dexscreener import find_arb_opportunities
from src.executor.paper import TradeDB
from src.arb.calculator import ArbOpportunity, ArbLeg
from src.utils.excel_report import generate_report


async def run_session(session_label: str, rpc_provider: str, duration_min: int = 3):
    """Run one paper-trade session using DexScreener."""
    db = TradeDB()
    session_id = db.create_session(session_label, rpc_provider, "api.dexscreener.com")
    total_opps = 0
    start = time.time()
    end = start + duration_min * 60
    cycle = 0

    print(f"\n  SESSION: {session_label} ({rpc_provider}) — {duration_min} min", flush=True)

    while time.time() < end:
        cycle += 1
        remaining = int(end - time.time())
        print(f"  Cycle {cycle} — {remaining//60:02d}:{remaining%60:02d} remaining", flush=True)

        try:
            raw_opps = await find_arb_opportunities(min_liquidity_usd=200)
        except Exception as e:
            print(f"    ✗ Error: {e}", flush=True)
            await asyncio.sleep(10)
            continue

        for ro in raw_opps:
            opp = ArbOpportunity(
                strategy=ro["strategy"],
                profit_sol=ro["profit_sol"],
                profit_percent=ro["profit_percent"],
                legs=[
                    ArbLeg(
                        dex="pump_fun",
                        pool_address="",
                        side="buy",
                        token_mint=ro["mint"],
                        amount=ro["profit_sol"],
                        price=ro["pump_price"],
                    ),
                    ArbLeg(
                        dex=ro["dex"],
                        pool_address=ro["dex_pair"],
                        side="sell",
                        token_mint=ro["mint"],
                        amount=ro["profit_sol"] / ro["dex_price"],
                        price=ro["dex_price"],
                    ),
                ],
                total_cost_sol=ro["profit_sol"] / 2 if ro["profit_sol"] > 0 else 0.01,
                confidence=ro["confidence"],
            )
            db.record_trade(session_id, opp)
            total_opps += 1
            print(f"    ✓ {ro['mint'][:12]}... {ro['dex']} "
                  f"profit={ro['profit_sol']:.6f} SOL ({ro['profit_percent']:.1f}%) "
                  f"conf={ro['confidence']:.0%}", flush=True)

        await asyncio.sleep(10)

    db.end_session(session_id)
    elapsed = time.time() - start
    print(f"  [{session_label}] Done — {cycle} cycles, {total_opps} opps, "
          f"{elapsed/60:.1f} min\n", flush=True)


async def main():
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 5  # default 5 min per session

    db = TradeDB()
    db.clear_all()

    # Session 1: Helius (from Jupiter) — free tier
    await run_session("Helius-Free-Jup", "Helius (from Jupiter)", dur)

    # Interim report
    print(f"  >>> Interim: {generate_report()}\n")

    # Session 2: Helius (paid)
    await run_session("Helius-Paid", "Helius (paid)", dur)

    # Final report
    path = generate_report()
    total = len(db.get_trades_with_session())
    print(f"\n{'='*60}")
    print(f"  REPORT: {path}")
    print(f"  Total trades: {total}")
    print(f"{'='*60}")

    for s in db.get_session_summary():
        print(f"\n  {s['label']} ({s['rpc_provider']}):")
        print(f"    Trades: {s['total_trades']}  "
              f"PnL: {s['total_profit_sol']:.6f} SOL  "
              f"Avg: {s['avg_profit_pct']:.2f}%")
        print(f"    Wins: {s['wins']}  Losses: {s['losses']}  "
              f"Best: {s['best_trade']:.6f}  Worst: {s['worst_trade']:.6f}")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""Paper-trade runner — compares two RPC providers for fixed durations."""
import asyncio
import sys
import os
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.config import load_config, AppConfig
from src.client import SolanaClient
from src.arb.detector import ArbDetector
from src.executor.paper import PaperExecutor, TradeDB
from src.utils.excel_report import generate_report


RPC_CONFIGS = {
    "helius_free_jup": {
        "endpoint": "https://rpc.helius.com/?api-key={key}",
        "label": "Helius (from Jupiter)",
    },
    "helius_paid": {
        "endpoint": "https://mainnet.helius-rpc.com/?api-key={key}",
        "label": "Helius (paid)",
    },
}


def make_paper_config(rpc_url: str) -> AppConfig:
    cfg = load_config()
    cfg.rpc.endpoint = rpc_url
    cfg.rpc.ws_endpoint = rpc_url.replace("https", "wss")
    cfg.arbitrage.scan_interval = 5.0
    cfg.arbitrage.min_profit_sol = 0.0001
    cfg.arbitrage.min_profit_percent = 0.1
    cfg.arbitrage.min_trade_sol = 0.01
    cfg.arbitrage.max_trade_sol = 0.1
    cfg.arbitrage.enable_cross_dex = True
    cfg.arbitrage.enable_multi_pair = True
    cfg.logging.level = "WARNING"
    return cfg


async def run_session(api_key: str, provider_key: str,
                      duration_min: int, session_label: str) -> int:
    provider = RPC_CONFIGS[provider_key]
    rpc_url = provider["endpoint"].format(key=api_key)
    provider_name = provider["label"]

    print(f"\n{'='*60}")
    print(f"  SESSION: {session_label}")
    print(f"  RPC:     {provider_name}")
    print(f"  URL:     {rpc_url}")
    print(f"  DURATION:{duration_min} min")
    print(f"{'='*60}")

    cfg = make_paper_config(rpc_url)
    client = SolanaClient(cfg)
    detector = ArbDetector(client, cfg)

    db = TradeDB()
    session_id = db.create_session(session_label, provider_name, rpc_url)
    paper_ex = PaperExecutor(db, session_id)

    log = logging.getLogger("paper_trade")

    start = time.time()
    end = start + duration_min * 60
    cycle = 0
    total_opps = 0

    try:
        while time.time() < end:
            cycle += 1
            remaining = int(end - time.time())
            print(f"  [{session_label}] Cycle {cycle} — {remaining//60:02d}:{remaining%60:02d} remaining")

            try:
                opps = await detector.scan_once()
            except Exception as e:
                print(f"    ✗ Scan error: {e}")
                await asyncio.sleep(cfg.arbitrage.scan_interval)
                continue

            if opps:
                for opp in opps:
                    paper_ex.execute(opp)
                    total_opps += 1
                    print(f"    ✓ {opp.strategy} profit={opp.profit_sol:.6f} SOL "
                          f"({opp.profit_percent:.2f}%) conf={opp.confidence:.1%}")
            else:
                print(f"    — No opportunities")

            await asyncio.sleep(cfg.arbitrage.scan_interval)

    except KeyboardInterrupt:
        print(f"\n  [{session_label}] Interrupted")

    db.end_session(session_id)
    elapsed = time.time() - start
    print(f"\n  [{session_label}] Done — {cycle} cycles, {total_opps} opps, "
          f"{elapsed/60:.1f} min elapsed\n")
    await client.close()
    return total_opps


async def main():
    if len(sys.argv) < 2:
        print("Usage: python paper_trade.py <helius_api_key>")
        print()
        print("To get a Helius API key:")
        print("  1. Go to https://helius.dev and sign up (free tier available)")
        print("  2. Or get a Jupiter RPC key from https://jup.ag → Settings")
        sys.exit(1)

    api_key = sys.argv[1]

    # Optional: override durations via command-line args
    dur_free = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    dur_paid = int(sys.argv[3]) if len(sys.argv) > 3 else 30

    db = TradeDB()
    db.clear_all()

    # Session 1: Helius (from Jupiter) - free
    await run_session(api_key, "helius_free_jup", dur_free, "Helius-Free-Jup")

    # Interim report
    print(f"\n  >>> Interim report: {generate_report()}\n")

    # Session 2: Helius (paid)
    await run_session(api_key, "helius_paid", dur_paid, "Helius-Paid")

    # Final report
    path = generate_report()
    total_trades = len(db.get_trades_with_session())
    print(f"\n{'='*60}")
    print(f"  REPORT: {path}")
    print(f"  Total trades recorded: {total_trades}")
    print(f"{'='*60}")

    # Print summary to console
    for s in db.get_session_summary():
        print(f"\n  {s['label']} ({s['rpc_provider']}):")
        print(f"    Trades: {s['total_trades']}  "
              f"PnL: {s['total_profit_sol']:.6f} SOL  "
              f"Avg: {s['avg_profit_pct']:.2f}%")
        print(f"    Wins: {s['wins']}  Losses: {s['losses']}  "
              f"Best: {s['best_trade']:.6f}  Worst: {s['worst_trade']:.6f}")


if __name__ == "__main__":
    asyncio.run(main())

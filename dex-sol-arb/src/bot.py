"""Core bot loop — importable from CLI (main.py) or UI (src/ui/app.py)."""
import asyncio
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from .config import load_config
from .client import SolanaClient
from .arb.detector import ArbDetector
from .executor.builder import ArbExecutor


def setup_logging(cfg, log_cb: Optional[Callable] = None):
    log_dir = Path(cfg.logging.file).parent
    log_dir.mkdir(parents=True, exist_ok=True)

    # Nuke any previous UI handler so we don't duplicate on restart
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_ui_callback", False):
            root.removeHandler(h)
        elif isinstance(h, logging.FileHandler) and h.baseFilename.endswith("arb.log"):
            root.removeHandler(h)

    handlers = [logging.FileHandler(cfg.logging.file)]
    if not log_cb:
        handlers.append(logging.StreamHandler())

    logging.basicConfig(
        level=getattr(logging, cfg.logging.level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
        force=True,
    )
    if log_cb:
        class UICallbackHandler(logging.Handler):
            _ui_callback = True
            def emit(self, record):
                log_cb(self.format(record))
        root.addHandler(UICallbackHandler())


async def run_bot(cfg=None, callback: Optional[Callable] = None):
    """Main bot loop. Pass a callback to receive log lines (for UI)."""
    if cfg is None:
        cfg = load_config()
    setup_logging(cfg, callback)
    log = logging.getLogger("bot")

    log.info("Starting DexSolanaArbitrage bot")
    log.info(f"  RPC: {cfg.rpc.endpoint}")
    log.info(f"  Wallet: {cfg.wallet.keypair_path}")
    log.info(f"  Scan interval: {cfg.arbitrage.scan_interval}s")

    client = SolanaClient(cfg)
    detector = ArbDetector(client, cfg)
    executor = ArbExecutor(client)

    try:
        balance = await client.get_sol_balance()
        log.info(f"  Wallet balance: {balance:.4f} SOL ({balance * 160:.2f} USD)")
        if balance < 0.01:
            log.warning("Wallet balance is critically low!")
    except Exception as e:
        log.error(f"Wallet check failed: {e}")
        await client.close()
        return

    cycle = 0
    try:
        while True:
            cycle += 1
            log.info(f"Scan cycle {cycle}")

            try:
                opps = await detector.scan_once()
            except Exception as e:
                log.error(f"Scan error: {e}")
                await asyncio.sleep(cfg.arbitrage.scan_interval)
                continue

            if opps:
                log.info(f"Found {len(opps)} opportunity(ies)")
                for opp in opps:
                    log.info(
                        f"  {opp.strategy}: profit={opp.profit_sol:.6f} SOL "
                        f"({opp.profit_percent:.2f}%) "
                        f"confidence={opp.confidence:.1%}"
                    )
                    if opp.confidence > 0.5:
                        await executor.execute(opp)
            else:
                log.debug("No opportunities found")

            await asyncio.sleep(cfg.arbitrage.scan_interval)

    except asyncio.CancelledError:
        log.info("Bot cancelled by user")
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        await client.close()

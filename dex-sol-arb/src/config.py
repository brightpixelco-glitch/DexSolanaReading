import os
from pathlib import Path
import yaml
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RPCConfig:
    endpoint: str = "https://api.mainnet-beta.solana.com"
    ws_endpoint: str = "wss://api.mainnet-beta.solana.com"


@dataclass
class WalletConfig:
    keypair_path: str = "~/.config/solana/id.json"
    min_sol_balance: float = 0.1


@dataclass
class ArbitrageConfig:
    enable_cross_dex: bool = True
    enable_multi_pair: bool = True
    min_profit_sol: float = 0.001
    min_profit_percent: float = 0.5
    max_trade_sol: float = 5.0
    min_trade_sol: float = 0.1
    slippage_bps: int = 100
    priority_fee: int = 5000
    scan_interval: float = 3.0


@dataclass
class DexConfig:
    enabled: bool = True
    program_id: str = ""


@dataclass
class DexesConfig:
    raydium: DexConfig = field(default_factory=lambda: DexConfig())
    orca: DexConfig = field(default_factory=lambda: DexConfig())
    meteora: DexConfig = field(default_factory=lambda: DexConfig(
        program_id="LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo"
    ))


@dataclass
class PumpFunConfig:
    program_id: str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
    max_token_age_minutes: int = 60


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/arb.log"


@dataclass
class AppConfig:
    rpc: RPCConfig = field(default_factory=RPCConfig)
    wallet: WalletConfig = field(default_factory=WalletConfig)
    arbitrage: ArbitrageConfig = field(default_factory=ArbitrageConfig)
    dexes: DexesConfig = field(default_factory=DexesConfig)
    pumpfun: PumpFunConfig = field(default_factory=PumpFunConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def load_config(path: str = "config.yaml") -> AppConfig:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return AppConfig()

    with open(cfg_path) as f:
        raw = yaml.safe_load(f) or {}

    c = AppConfig()

    if "rpc" in raw:
        c.rpc.endpoint = raw["rpc"].get("endpoint", c.rpc.endpoint)
        c.rpc.ws_endpoint = raw["rpc"].get("ws_endpoint", c.rpc.ws_endpoint)

    if "wallet" in raw:
        c.wallet.keypair_path = raw["wallet"].get("keypair_path", c.wallet.keypair_path)
        c.wallet.min_sol_balance = raw["wallet"].get("min_sol_balance", c.wallet.min_sol_balance)

    if "arbitrage" in raw:
        a = raw["arbitrage"]
        c.arbitrage.enable_cross_dex = a.get("enable_cross_dex", c.arbitrage.enable_cross_dex)
        c.arbitrage.enable_multi_pair = a.get("enable_multi_pair", c.arbitrage.enable_multi_pair)
        c.arbitrage.min_profit_sol = a.get("min_profit_sol", c.arbitrage.min_profit_sol)
        c.arbitrage.min_profit_percent = a.get("min_profit_percent", c.arbitrage.min_profit_percent)
        c.arbitrage.max_trade_sol = a.get("max_trade_sol", c.arbitrage.max_trade_sol)
        c.arbitrage.min_trade_sol = a.get("min_trade_sol", c.arbitrage.min_trade_sol)
        c.arbitrage.slippage_bps = a.get("slippage_bps", c.arbitrage.slippage_bps)
        c.arbitrage.priority_fee = a.get("priority_fee", c.arbitrage.priority_fee)
        c.arbitrage.scan_interval = a.get("scan_interval", c.arbitrage.scan_interval)

    if "dexes" in raw:
        d = raw["dexes"]
        if "raydium" in d:
            c.dexes.raydium.enabled = d["raydium"].get("enabled", True)
            c.dexes.raydium.program_id = d["raydium"].get("program_id", c.dexes.raydium.program_id)
        if "orca" in d:
            c.dexes.orca.enabled = d["orca"].get("enabled", True)
            c.dexes.orca.program_id = d["orca"].get("program_id", c.dexes.orca.program_id)
        if "meteora" in d:
            c.dexes.meteora.enabled = d["meteora"].get("enabled", True)
            dlmm = d["meteora"].get("dlmm_program_id", "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo")
            pamm = d["meteora"].get("pamm_program_id", "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA")
            c.dexes.meteora.program_id = f"{dlmm},{pamm}"

    if "pumpfun" in raw:
        p = raw["pumpfun"]
        c.pumpfun.program_id = p.get("program_id", c.pumpfun.program_id)
        c.pumpfun.max_token_age_minutes = p.get("max_token_age_minutes", c.pumpfun.max_token_age_minutes)

    if "logging" in raw:
        lc = raw["logging"]
        c.logging.level = lc.get("level", c.logging.level)
        c.logging.file = lc.get("file", c.logging.file)

    return c

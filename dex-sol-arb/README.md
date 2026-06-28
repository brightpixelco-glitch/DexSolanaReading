# DexSolanaArbitrage

Cross-DEX + cross-pair arbitrage bot for pump.fun tokens on Solana.

## Strategy

The bot monitors pump.fun bonding curves and DEX pools (Raydium, Orca, Meteora) simultaneously:

- **Cross-DEX**: Same token, buy on the cheaper exchange and sell on the more expensive one (pump.fun bonding curve vs Raydium/Orca/Meteora pool)
- **Multi-pair**: Route through intermediate tokens (SOL → A → B → SOL) on pump.fun bonding curves

## Setup

```bash
cd dex-sol-arb
pip install -r requirements.txt
```

Edit `config.yaml`:
- Set `wallet.keypair_path` to your Solana keypair JSON file (or base58 key)
- Optionally set a paid RPC endpoint in `rpc.endpoint`
- Adjust profit thresholds and trade sizes in `arbitrage`

## Usage

```bash
python main.py
```

The bot will scan continuously at the configured interval and log findings to `logs/arb.log`.

## Config

| Key | Default | Description |
|---|---|---|
| `rpc.endpoint` | `https://api.mainnet-beta.solana.com` | Solana RPC URL (set paid endpoint for production) |
| `arbitrage.min_profit_sol` | `0.001` | Minimum profit to execute a trade |
| `arbitrage.scan_interval` | `3.0` | Seconds between scan cycles |
| `arbitrage.slippage_bps` | `100` | Slippage tolerance (1% = 100 bps) |
| `arbitrage.max_trade_sol` | `5.0` | Maximum SOL per trade leg |
| `arbitrage.enable_cross_dex` | `true` | Toggle cross-DEX scanning |
| `arbitrage.enable_multi_pair` | `true` | Toggle multi-pair scanning |

## Architecture

```
main.py                  → Entry point, main loop
src/config.py            → YAML config loader
src/client.py            → Solana RPC + wallet wrapper
src/scanner/
  pump_fun.py            → Token discovery on pump.fun
  dex_pools.py           → Pool discovery on Raydium/Orca/Meteora
src/pricing/
  pump_bonding.py        → Pump.fun bonding curve price calc
  dex_amm.py             → DEX pool price fetcher
src/arb/
  detector.py            → Finds arb opportunities
  calculator.py          → Profit/loss evaluation
src/executor/
  builder.py             → Instruction builder + tx submission
```

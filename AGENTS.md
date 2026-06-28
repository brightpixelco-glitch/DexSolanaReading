# AGENTS.md — DexSolanaReading

## Purpose

Reference collection + Python arbitrage bot (`dex-sol-arb/`) for cross-DEX arbitrage on pump.fun tokens.
The `Arbitrage/` folder contains reference screenshots; the actual project lives in `dex-sol-arb/`.

## Project: dex-sol-arb (Python arbitrage bot)

A Solana arbitrage bot lives at `dex-sol-arb/`. It does cross-DEX + cross-pair arbitrage
on pump.fun tokens.

**Commands:**
- Install deps: `pip install -r requirements.txt`
- Run (CLI): `python main.py`
- Run (UI): `python main.py --ui`
- Paper trade: `python paper_trade.py <helius_api_key> [min_per_session]`
- Paper trade (DexScreener): `python paper_trade_dex.py [min_per_session]`
- Config: `config.yaml` (RPC endpoint, wallet key, profit thresholds)

**Architecture notes:**
- Scanner discovers pump.fun tokens → checks DEX pools → evaluates arb opps → executes
- Cross-DEX: pump.fun bonding curve vs Raydium/Orca/Meteora pool for the same token
- Multi-pair: SOL → TOKEN_A → TOKEN_B → SOL routing on pump.fun curves
- DEX instruction building is implemented for all 4 DEXes (Raydium AMM, Orca Whirlpools, Meteora DLMM, Meteora pAMM) — each handler fetches its own pool data and builds the correct swap instruction
- WSOL lifecycle (create → fund → swap → close) is handled automatically for SOL-sided trades
- Multi-pair execution is still scan-only (no tx building)
- DexScreener API scanner (`src/scanner/dexscreener.py`) is the only reliably working scanner — on-chain scanner is blocked by RPC rate limits

**Important:**
- Requires a Solana keypair (set path in `config.yaml` `wallet.keypair_path`)
- Public RPC will time out on `getProgramAccounts`. Helius paid works but rate-limits aggressively (~5 req/min for large queries).
- Free Helius (`rpc.helius.com`) failed to connect entirely.
- The two reference accounts studied (`J7GR6X...` and `8L2y55...`) demonstrate this exact strategy — most txns fail with error 202 (pump.fun slippage) or error 33 (Meteora slippage); only a small fraction succeed

**Known issues:**
- `client.py:15` — use `Confirmed` (string) not `CommitmentLevel.Confirmed` (unhashable enum) for `AsyncClient`
- `pump_fun.py` — pump.fun swap transactions use AMM program `DV7FoBF...`, not main program `6EF8r...`
- `pump_fun.py` — token mint is at `accounts[1]` in pump.fun AMM instructions, not `accounts[0]`
- `pump_bonding.py` — use `get_account_info()` (raw bytes) not `get_account_info_json_parsed()`
- Solana Python lib 0.36.6: `get_transaction()` expects `Signature` type, not string
- Helius requires `getProgramAccountsV2` with pagination for large datasets
- Bonding curve offset layout may have changed — parsed data often shows wrong mint addresses

**Paper trade results (from FINDINGS.md):**
- Both RPCs produced similar results since scanner fell back to DexScreener API
- Free Helius: 40 recorded opps, 138.83 SOL hypothetical PnL
- Paid Helius: 42 recorded opps, 178.86 SOL hypothetical PnL
- The profit % (3000-5000%) reflects migration listing spreads, not realistic arb

## Constraints

- Do not modify or delete the image files unless asked.
- `Accounts.txt` — treat as sensitive; do not fill with placeholder or test data unless instructed.

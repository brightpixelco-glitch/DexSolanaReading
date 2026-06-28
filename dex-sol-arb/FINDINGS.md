# DexSolana Arbitrage Bot — Findings & Future Work

## Paper Trade Results (Helius Free vs Paid)

| Metric | Helius (from Jupiter) — Free | Helius — Paid |
|---|---|---|
| RPC endpoint | `rpc.helius.com/?api-key=` | `mainnet.helius-rpc.com` |
| RPC works? | ❌ Never connected (DNS/socket error) | ✅ Version 4.0.2 |
| Token discovery | N/A — fell back to DexScreener API | N/A — fell back to DexScreener API |
| Trades recorded | 40 (duplicate cycles) | 42 (duplicate cycles) |
| Hypothetical PnL | 138.83 SOL | 178.86 SOL |
| Avg profit % | 3,734% | 4,258% |

**Key finding:** Both RPCs produced nearly identical results because the actual token discovery was done through **DexScreener API**, not Solana RPC. The free Helius endpoint was unusable. The paid Helius worked but rate-limited aggressively (429 after ~5 requests).

## Scanners Tested

### 1. On-Chain Scanner (`src/scanner/pump_fun.py`)
- **Approach:** `getProgramAccounts` on pump.fun program + `scan_recent_buys()` fallback
- **Problems:**
  - `getProgramAccounts` blocked by Helius ("Too many accounts, use getProgramAccountsV2")
  - `scan_recent_buys()` required multiple fixes:
    - Used wrong program ID (main program `6EF8r...` instead of AMM program `DV7FoB...`)
    - Signatures are `Signature` objects, not strings (solana 0.36.6)
    - Account index was wrong: token mint is at `accounts[1]`, not `accounts[0]`
    - Bonding curve verification was unreliable (derived curves often didn't exist)
  - Rate-limited after ~5 requests on paid Helius
  - `get_transaction` requires `Signature` type, not string
- **Status:** Works but unreliable. Finds 0-2 tokens per scan.

### 2. DexScreener API Scanner (`src/scanner/dexscreener.py`)
- **Approach:** Hit `api.dexscreener.com/token-profiles/latest/v1` + search pairs
- **Pros:**
  - No RPC needed (free, no API key)
  - Fast (~2-3s per scan cycle)
  - Actually finds cross-DEX price discrepancies
- **Cons:**
  - Only 30 tokens per batch
  - Prices can be stale or reflect different quote pairs
  - Can't execute real trades (no on-chain data)
- **Status:** ✅ The only reliably working scanner

### 3. Jupiter API (not implemented)
- Could provide real-time quotes and route data
- Would need Jupiter API integration for execution anyway

## DexScreener Price Discrepancies

The paper trade found token `AQ4BMAJu65fHSMEXnbR4TFfcbhBNBvHMVLsF2ubpump` trading at:
- **pump.fun:** ~0.00000181 SOL
- **Meteora/Raydium:** ~0.00010110 SOL

This 55x difference is a **migration listing** artifact — when a pump.fun token graduates, the bonding curve price and the initial DEX pool price can differ drastically for seconds/minutes. Real arb bots would compete on this within milliseconds.

## Codebase Issues Fixed

| Issue | File | Fix |
|---|---|---|
| `CommitmentLevel` unhashable | `src/client.py:15` | Changed `CommitmentLevel.Confirmed` → `Confirmed` (string) |
| `import base64` at EOF | `src/scanner/pump_fun.py:160` | Moved to top |
| `get_program_accounts` blocked | `src/scanner/pump_fun.py` | Added fallback to `scan_recent_buys()` |
| `Signature` vs string | `src/scanner/pump_fun.py` | Pass `Signature` objects, not strings |
| Wrong program for sigs | `src/scanner/pump_fun.py:149` | `PUMPFUN_PROGRAM` → `PUMPFUN_AMM` |
| Wrong account index | `src/scanner/pump_fun.py:169` | `accounts[0]` → `accounts[1]` |
| `get_account_info_json_parsed` | `src/pricing/pump_bonding.py:96` | Changed to `get_account_info` with raw bytes |
| Curve derivation mismatch | `src/pricing/pump_bonding.py` vs `src/scanner/pump_fun.py` | Two copies — consolidated logic but one may be wrong |

## Infrastructure Built

### Paper Trade System
- `src/executor/paper.py` — `PaperExecutor` + `TradeDB` (SQLite with sessions)
- `src/utils/excel_report.py` — 3-sheet report (Summary, Per-Pair PnL, All Trades)
- `paper_trade.py` — RPC-based paper trader (blocked by scanner issues)
- `paper_trade_dex.py` — DexScreener-based paper trader (working)

### Web UI
- `src/ui/app.py` — NiceGUI with RPC selector, wallet config, start/stop, live logs
- `main.py --ui` — launches the web interface

## Blockers for Real Trading

1. **Scanner reliability:** On-chain scanner finds 0-2 tokens per cycle. Needs `getProgramAccountsV2` with pagination or a different data source.
2. **RPC costs:** Free Helius doesn't work. Paid Helius ($49/mo+) rate-limits aggressively. Need multi-RPC fallback or dedicated node.
3. **Unverified DEX instruction builders:** `src/executor/builder.py` has never been tested on-chain. The WSOL lifecycle and versioned transaction assembly could have bugs.
4. **Multi-pair execution:** Marked as "scan-only (not implemented)" in builder.py.
5. **Pump.fun price calculation:** The bonding curve formula in `calculate_pump_buy_price` may be stale — pump.fun changed their AMM after the code was written.

## Next Steps (Priority Order)

1. **Production scanner:** Replace on-chain scanner with Jupiter API + DexScreener combo for token discovery. Jupiter for quotes, DexScreener for pool discovery.
2. **Fix bonding curve pricing:** Audit the pump.fun bonding curve offset layout against current on-chain data.
3. **Wire UI to DexScreener scanner:** The UI currently connects to the broken RPC scanner.
4. **Test DEX instruction builders:** Deploy on devnet with a test wallet before mainnet.
5. **Implement multi-pair execution:** The routing logic is mostly done in `calculator.py` but execution is stubbed.
6. **Add trade database to UI:** The SQLite `TradeDB` exists but the UI doesn't display it.

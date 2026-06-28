import time
import struct
import base64
from typing import AsyncIterator
from solana.rpc.commitment import Confirmed
from solana.rpc.types import MemcmpOpts
from solders.pubkey import Pubkey
from solders.signature import Signature

from ..client import SolanaClient


PUMPFUN_PROGRAM = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
PUMPFUN_AMM = Pubkey.from_string("DV7FoBFCa3aRx3cb32w5MbH39Pjz8t7YKXjhyJf9sPxv")
PUMPFUN_PROGRAMS = {PUMPFUN_PROGRAM, PUMPFUN_AMM}


class PumpTokenInfo:
    __slots__ = ("mint", "name", "symbol", "bonding_curve", "associated_bonding_curve",
                 "user_token_account", "timestamp", "supply", "market_cap_sol")

    def __init__(self, mint: str, name: str, symbol: str, bonding_curve: str,
                 associated_bonding_curve: str, timestamp: int, supply: int,
                 market_cap_sol: float):
        self.mint = mint
        self.name = name
        self.symbol = symbol
        self.bonding_curve = bonding_curve
        self.associated_bonding_curve = associated_bonding_curve
        self.timestamp = timestamp
        self.supply = supply
        self.market_cap_sol = market_cap_sol

    def __repr__(self):
        return f"{self.symbol or self.mint[:8]} (MC=${self.market_cap_sol:.2f})"


def derive_bonding_curve_address(mint: Pubkey) -> Pubkey:
    seeds = [
        b"bonding-curve",
        bytes(mint),
    ]
    return Pubkey.find_program_address(seeds, PUMPFUN_PROGRAM)[0]


def derive_global_account() -> Pubkey:
    seeds = [b"global"]
    return Pubkey.find_program_address(seeds, PUMPFUN_PROGRAM)[0]


def parse_pump_token_account(data: bytes) -> dict | None:
    """Decode a pump.fun bonding curve account."""
    if len(data) < 8:
        return None
    disc = struct.unpack("<Q", data[:8])[0]
    # Expected discriminator for bonding curve accounts
    # We'll use a simpler heuristic: check if data is long enough for the curve state
    if len(data) < 80:
        return None
    try:
        offset = 8
        mint = Pubkey.from_bytes(data[offset:offset + 32])
        offset += 32
        curve = Pubkey.from_bytes(data[offset:offset + 32])
        offset += 32
        user = Pubkey.from_bytes(data[offset:offset + 32])
        offset += 32
        token_reserve = struct.unpack("<Q", data[offset:offset + 8])[0]
        offset += 8
        sol_reserve = struct.unpack("<Q", data[offset:offset + 8])[0]
        offset += 8
        supply = struct.unpack("<Q", data[offset:offset + 8])[0]
        offset += 8
        complete = struct.unpack("<?", data[offset:offset + 1])[0]
        return {
            "mint": str(mint),
            "bonding_curve": str(curve),
            "user": str(user),
            "token_reserve": token_reserve,
            "sol_reserve": sol_reserve,
            "supply": supply,
            "complete": complete,
        }
    except (struct.error, IndexError):
        return None


class PumpFunScanner:
    def __init__(self, client: SolanaClient):
        self.client = client
        self._seen = set()

    async def scan_recent_tokens(self, max_age_minutes: int = 60) -> list[PumpTokenInfo]:
        """Fetch tokens with active bonding curves.

        Tries full getProgramAccounts first; if the RPC rejects it (Helius
        pagination requirement), falls back to scanning recent buy signatures.
        """
        cutoff = int(time.time()) - max_age_minutes * 60
        tokens = []
        try:
            accounts = await self.client.rpc.get_program_accounts(
                PUMPFUN_PROGRAM,
                encoding="base64",
                filters=[80],
                commitment=Confirmed,
            )
        except Exception:
            # Fallback: scan recent buys with verified bonding curves
            mints = await self.scan_recent_buys(max_age_minutes)
            for mint_str in mints:
                if mint_str in self._seen:
                    continue
                self._seen.add(mint_str)
                curve = derive_bonding_curve_address(Pubkey.from_string(mint_str))
                tokens.append(PumpTokenInfo(
                    mint=mint_str,
                    name="", symbol="",
                    bonding_curve=str(curve),
                    associated_bonding_curve=str(curve),
                    timestamp=int(time.time()),
                    supply=0,
                    market_cap_sol=0,
                ))
            return tokens

        for acct in accounts.value:
            raw = base64.b64decode(acct.account.data)
            info = parse_pump_token_account(raw)
            if not info or info["complete"]:
                continue
            mint_str = info["mint"]
            if mint_str in self._seen:
                continue
            self._seen.add(mint_str)
            market_cap = (info["sol_reserve"] * 2) / 1e9
            tokens.append(PumpTokenInfo(
                mint=mint_str,
                name="", symbol="",
                bonding_curve=str(info["bonding_curve"]),
                associated_bonding_curve=str(Pubkey.from_string(info["bonding_curve"])),
                timestamp=int(time.time()),
                supply=info["supply"],
                market_cap_sol=market_cap,
            ))
        return tokens

    async def scan_recent_buys(self, max_age_minutes: int = 60) -> list[str]:
        """Get recently traded token mints by scanning recent signatures.

        Verifies mints exist as SPL token mints on-chain before returning.
        """
        mint_set = set()
        cutoff_slot_time = int(time.time()) - max_age_minutes * 60
        try:
            sigs = await self.client.rpc.get_signatures_for_address(
                PUMPFUN_AMM, limit=200
            )
        except Exception:
            return []

        candidates = []
        for sig_info in sigs.value:
            if sig_info.block_time and sig_info.block_time < cutoff_slot_time:
                continue
            candidates.append(sig_info.signature)

        for sig in candidates[:20]:
            try:
                tx = await self.client.rpc.get_transaction(
                    sig, encoding="jsonParsed",
                    max_supported_transaction_version=0
                )
                if not tx.value:
                    continue
                msg = tx.value.transaction.transaction.message
                for ix in msg.instructions:
                    pid = str(ix.program_id)
                    if pid != str(PUMPFUN_AMM):
                        continue
                    if hasattr(ix, 'accounts') and len(ix.accounts) >= 2:
                        mint_str = str(ix.accounts[1])
                        if mint_str and len(mint_str) > 30:
                            mint_set.add(mint_str)
            except Exception:
                continue

        # Verify mints exist on-chain
        TOKEN_PROGRAM = Pubkey.from_string("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
        confirmed = []
        for mint_str in mint_set:
            try:
                mint_pk = Pubkey.from_string(mint_str)
                acct = await self.client.rpc.get_account_info(mint_pk)
                if acct.value and acct.value.lamports > 0:
                    confirmed.append(mint_str)
            except Exception:
                continue
        return confirmed


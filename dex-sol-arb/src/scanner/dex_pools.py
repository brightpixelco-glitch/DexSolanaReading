from typing import Optional
from solders.pubkey import Pubkey

from ..client import SolanaClient
from ..utils.constants import PROGRAMS


RAYDIUM_AMM = Pubkey.from_string(PROGRAMS["raydium_amm"])
ORCA_WHIRLPOOLS = Pubkey.from_string(PROGRAMS["orca_whirlpools"])
METEORA_DLMM = Pubkey.from_string(PROGRAMS["meteora_dlmm"])


class DexPoolInfo:
    __slots__ = ("dex", "pool_address", "mint_a", "mint_b", "reserve_a",
                 "reserve_b", "lp_supply")

    def __init__(self, dex: str, pool_address: str, mint_a: str, mint_b: str,
                 reserve_a: int, reserve_b: int, lp_supply: int = 0):
        self.dex = dex
        self.pool_address = pool_address
        self.mint_a = mint_a
        self.mint_b = mint_b
        self.reserve_a = reserve_a
        self.reserve_b = reserve_b
        self.lp_supply = lp_supply


class DexPoolScanner:
    def __init__(self, client: SolanaClient):
        self.client = client

    async def find_pools_for_token(self, token_mint: str) -> list[DexPoolInfo]:
        """Find all DEX pools that include the given token."""
        pools = []
        mint = Pubkey.from_string(token_mint)
        sol = Pubkey.from_string("So11111111111111111111111111111111111111112")

        # Raydium AMM pools
        if self.client.cfg.dexes.raydium.enabled:
            pools.extend(await self._scan_raydium(mint, sol))

        # Orca Whirlpools
        if self.client.cfg.dexes.orca.enabled:
            pools.extend(await self._scan_orca(mint, sol))

        # Meteora DLMM
        if self.client.cfg.dexes.meteora.enabled:
            pools.extend(await self._scan_meteora(mint, sol))

        return pools

    async def _scan_raydium(self, token_mint: Pubkey, sol_mint: Pubkey) -> list[DexPoolInfo]:
        pools = []
        try:
            accounts = await self.client.rpc.get_program_accounts(
                RAYDIUM_AMM, encoding="base64", filters=[752]
            )
        except Exception:
            return pools

        for acct in accounts.value:
            try:
                data = acct.account.data
                # Raydium AMM pool layout (base64 encoded)
                # offset 400: coin mint, 432: pc mint
                raw = base64.b64decode(data)
                coin = Pubkey.from_bytes(raw[400:432])
                pc = Pubkey.from_bytes(raw[432:464])

                if coin in (token_mint, sol_mint) or pc in (token_mint, sol_mint):
                    coin_reserve = struct.unpack("<Q", raw[264:272])[0]
                    pc_reserve = struct.unpack("<Q", raw[296:304])[0]
                    pools.append(DexPoolInfo(
                        dex="raydium",
                        pool_address=str(acct.pubkey),
                        mint_a=str(coin), mint_b=str(pc),
                        reserve_a=coin_reserve, reserve_b=pc_reserve,
                    ))
            except (struct.error, IndexError):
                continue
        return pools

    async def _scan_orca(self, token_mint: Pubkey, sol_mint: Pubkey) -> list[DexPoolInfo]:
        pools = []
        try:
            accounts = await self.client.rpc.get_program_accounts(
                ORCA_WHIRLPOOLS, encoding="base64", filters=[769]
            )
        except Exception:
            return pools

        for acct in accounts.value:
            try:
                raw = base64.b64decode(acct.account.data)
                # Orca whirlpool layout: token_mint_a[8:40], token_mint_b[40:72]
                mint_a = Pubkey.from_bytes(raw[8:40])
                mint_b = Pubkey.from_bytes(raw[40:72])
                if mint_a in (token_mint, sol_mint) or mint_b in (token_mint, sol_mint):
                    pools.append(DexPoolInfo(
                        dex="orca",
                        pool_address=str(acct.pubkey),
                        mint_a=str(mint_a), mint_b=str(mint_b),
                        reserve_a=0, reserve_b=0,
                    ))
            except (struct.error, IndexError):
                continue
        return pools

    async def _scan_meteora(self, token_mint: Pubkey, sol_mint: Pubkey) -> list[DexPoolInfo]:
        pools = []
        try:
            accounts = await self.client.rpc.get_program_accounts(
                METEORA_DLMM, encoding="base64", filters=[2400]
            )
        except Exception:
            return pools

        for acct in accounts.value:
            try:
                raw = base64.b64decode(acct.account.data)
                token_x = Pubkey.from_bytes(raw[8:40])
                token_y = Pubkey.from_bytes(raw[40:72])
                if token_x in (token_mint, sol_mint) or token_y in (token_mint, sol_mint):
                    pools.append(DexPoolInfo(
                        dex="meteora",
                        pool_address=str(acct.pubkey),
                        mint_a=str(token_x), mint_b=str(token_y),
                        reserve_a=0, reserve_b=0,
                    ))
            except (struct.error, IndexError):
                continue
        return pools


import base64
import struct

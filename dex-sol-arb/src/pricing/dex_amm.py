import base64
import struct
from solders.pubkey import Pubkey

from ..client import SolanaClient
from ..utils.constants import PROGRAMS, WSOL


class DexPricer:
    def __init__(self, client: SolanaClient):
        self.client = client

    async def get_pool_price(self, dex: str, pool_address: str) -> dict | None:
        """Fetch price from a DEX pool."""
        handlers = {
            "raydium": self._price_raydium,
            "orca": self._price_orca,
            "meteora": self._price_meteora,
        }
        handler = handlers.get(dex)
        if not handler:
            return None
        return await handler(pool_address)

    async def _price_raydium(self, pool_address: str) -> dict | None:
        try:
            pk = Pubkey.from_string(pool_address)
            resp = await self.client.rpc.get_account_info_json_parsed(pk)
            if not resp.value:
                return None
            raw = resp.value.data
            if isinstance(raw, str):
                raw = base64.b64decode(raw)

            # Raydium AMM pool layout
            coin_reserve = struct.unpack("<Q", raw[264:272])[0]
            pc_reserve = struct.unpack("<Q", raw[296:304])[0]
            coin_decimals = raw[456]
            pc_decimals = raw[457]
            coin_mint = str(Pubkey.from_bytes(raw[400:432]))
            pc_mint = str(Pubkey.from_bytes(raw[432:464]))

            price = (pc_reserve / 10 ** pc_decimals) / (coin_reserve / 10 ** coin_decimals) if coin_reserve else 0

            return {
                "dex": "raydium",
                "pool": pool_address,
                "mint_a": coin_mint,
                "mint_b": pc_mint,
                "reserve_a": coin_reserve,
                "reserve_b": pc_reserve,
                "price": price,
                "decimals_a": coin_decimals,
                "decimals_b": pc_decimals,
            }
        except Exception:
            return None

    async def _price_orca(self, pool_address: str) -> dict | None:
        try:
            pk = Pubkey.from_string(pool_address)
            resp = await self.client.rpc.get_account_info_json_parsed(pk)
            if not resp.value:
                return None
            raw = resp.value.data
            if isinstance(raw, str):
                raw = base64.b64decode(raw)

            mint_a = str(Pubkey.from_bytes(raw[8:40]))
            mint_b = str(Pubkey.from_bytes(raw[40:72]))
            token_vault_a = str(Pubkey.from_bytes(raw[72:104]))
            token_vault_b = str(Pubkey.from_bytes(raw[104:136]))

            price = 0
            return {
                "dex": "orca",
                "pool": pool_address,
                "mint_a": mint_a,
                "mint_b": mint_b,
                "reserve_a": 0,
                "reserve_b": 0,
                "price": price,
                "token_vault_a": token_vault_a,
                "token_vault_b": token_vault_b,
            }
        except Exception:
            return None

    async def _price_meteora(self, pool_address: str) -> dict | None:
        try:
            pk = Pubkey.from_string(pool_address)
            resp = await self.client.rpc.get_account_info_json_parsed(pk)
            if not resp.value:
                return None
            raw = resp.value.data
            if isinstance(raw, str):
                raw = base64.b64decode(raw)

            token_x = str(Pubkey.from_bytes(raw[8:40]))
            token_y = str(Pubkey.from_bytes(raw[40:72]))
            reserve_x = struct.unpack("<Q", raw[72:80])[0]
            reserve_y = struct.unpack("<Q", raw[80:88])[0]

            price = (reserve_y) / (reserve_x) if reserve_x else 0

            return {
                "dex": "meteora",
                "pool": pool_address,
                "mint_a": token_x,
                "mint_b": token_y,
                "reserve_a": reserve_x,
                "reserve_b": reserve_y,
                "price": price,
            }
        except Exception:
            return None

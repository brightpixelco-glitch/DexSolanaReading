import json
from pathlib import Path
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TokenAccountOpts
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from .config import AppConfig


class SolanaClient:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg
        self.rpc = AsyncClient(cfg.rpc.endpoint, commitment=Confirmed)
        self._keypair: Keypair | None = None

    @property
    def keypair(self) -> Keypair:
        if self._keypair is None:
            kp_path = Path(self.cfg.wallet.keypair_path).expanduser()
            if kp_path.exists():
                with open(kp_path) as f:
                    secret = json.load(f)
                self._keypair = Keypair.from_bytes(bytes(secret))
            else:
                self._keypair = Keypair.from_base58_string(
                    self.cfg.wallet.keypair_path
                )
        return self._keypair

    @property
    def payer(self) -> Keypair:
        return self.keypair

    async def get_sol_balance(self) -> float:
        resp = await self.rpc.get_balance(self.keypair.pubkey())
        return resp.value / 1e9

    async def get_token_accounts(self, mint: str | None = None):
        owner = self.keypair.pubkey()
        if mint:
            opts = TokenAccountOpts(mint=Pubkey.from_string(mint))
        else:
            opts = TokenAccountOpts(program_id=Pubkey.from_string(self.cfg.pumpfun.program_id))
        resp = await self.rpc.get_token_accounts_by_owner_json_parsed(owner, opts)
        return resp.value

    async def close(self):
        await self.rpc.close()

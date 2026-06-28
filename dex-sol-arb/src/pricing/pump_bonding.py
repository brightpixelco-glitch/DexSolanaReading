import struct
from solders.pubkey import Pubkey

from ..client import SolanaClient
from ..utils.constants import PROGRAMS

PUMPFUN_PROGRAM = Pubkey.from_string(PROGRAMS["pump_fun"])


def calculate_pump_buy_price(sol_reserve: int, token_reserve: int,
                             sol_input: int, supply: int) -> int:
    """Calculate tokens received for a given SOL input on pump.fun bonding curve.

    pump.fun uses a virtual constant product curve with fees.
    Formula (reverse-engineered from on-chain):
      k = token_reserve * sol_reserve (virtual)
      tokens_out = token_reserve * (1 - sol_reserve / (sol_reserve + sol_input * (1 - fee)))
    Simplified:  tokens_out = (sol_input * token_reserve * (1 - fee)) / (sol_reserve + sol_input * (1 - fee))
    """
    FEE_BPS = 100  # pump.fun takes 1% fee
    fee_mult = 10000 - FEE_BPS  # 9900
    adjusted_input = sol_input * fee_mult // 10000

    if sol_reserve + adjusted_input == 0:
        return 0
    tokens_out = token_reserve * adjusted_input // (sol_reserve + adjusted_input)
    return tokens_out


def calculate_pump_sell_price(sol_reserve: int, token_reserve: int,
                              token_input: int, supply: int) -> int:
    """Calculate SOL received for selling tokens on pump.fun bonding curve.

    sol_out = sol_reserve * (1 - token_reserve / (token_reserve + token_input))
    After fee: multiply by (1 - fee)
    """
    FEE_BPS = 100
    fee_mult = 10000 - FEE_BPS

    if token_reserve + token_input == 0:
        return 0
    sol_out = sol_reserve * token_input // (token_reserve + token_input)
    sol_out = sol_out * fee_mult // 10000
    return sol_out


def derive_bonding_curve_address(mint: Pubkey) -> Pubkey | None:
    try:
        seeds = [b"bonding-curve", bytes(mint)]
        addr, _ = Pubkey.find_program_address(seeds, PUMPFUN_PROGRAM)
        return addr
    except Exception:
        return None


def parse_bonding_curve(data: bytes) -> dict | None:
    """Parse a pump.fun bonding curve account."""
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
        return {
            "mint": str(mint),
            "curve": str(curve),
            "user": str(user),
            "token_reserve": token_reserve,
            "sol_reserve": sol_reserve,
            "supply": supply,
        }
    except (struct.error, IndexError):
        return None


class PumpBondingPricer:
    def __init__(self, client: SolanaClient):
        self.client = client

    async def get_bonding_curve(self, mint: str) -> dict | None:
        """Fetch and parse a pump.fun bonding curve account."""
        mint_pk = Pubkey.from_string(mint)
        curve_pk = derive_bonding_curve_address(mint_pk)
        if not curve_pk:
            return None
        try:
            resp = await self.client.rpc.get_account_info(curve_pk)
            if not resp.value:
                return None
            raw = resp.value.data
            if isinstance(raw, str):
                import base64
                raw = base64.b64decode(raw)
            return parse_bonding_curve(bytes(raw))
        except Exception as e:
            return None

    async def get_price(self, mint: str, trade_sol: float = 0.1) -> dict:
        """Get buy and sell price for a pump.fun token.

        Returns dict with buy/sell prices, reserves, and market cap estimate.
        """
        curve = await self.get_bonding_curve(mint)
        if not curve:
            return {"error": "bonding_curve_not_found"}

        sol_res = curve["sol_reserve"]
        token_res = curve["token_reserve"]
        supply = curve["supply"]
        sol_in_lamports = int(trade_sol * 1e9)

        tokens_out = calculate_pump_buy_price(sol_res, token_res, sol_in_lamports, supply)
        sol_out = calculate_pump_sell_price(sol_res, token_res, tokens_out, supply) if tokens_out else 0

        token_price_sol = sol_res / token_res if token_res else 0 if token_res == 0 else 0
        # Market cap = total supply * token price in SOL
        mc_sol = token_price_sol * (supply)
        mc_usd = mc_sol * 160  # rough SOL price

        return {
            "mint": mint,
            "token_reserve": token_res,
            "sol_reserve": sol_res,
            "supply": supply,
            "token_price_sol": token_price_sol,
            "market_cap_sol": mc_sol,
            "market_cap_usd": mc_usd,
            "buy": {
                "sol_in": trade_sol,
                "tokens_out": tokens_out / 1e6 if "pump" in mint else tokens_out,
            },
            "sell": {
                "tokens_in": tokens_out / 1e6 if "pump" in mint else tokens_out,
                "sol_out": sol_out / 1e9,
            },
        }

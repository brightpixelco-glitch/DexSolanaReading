import base64
import struct
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.pubkey import Pubkey
from solders.system_program import ID as SYSTEM_PROGRAM
from solders.compute_budget import set_compute_unit_price, set_compute_unit_limit
from solders.sysvar import RENT

from ..client import SolanaClient
from ..arb.calculator import ArbOpportunity
from ..utils.constants import PROGRAMS, WSOL, TOKEN_PROGRAM, TOKEN_2022_PROGRAM

PUMPFUN = Pubkey.from_string(PROGRAMS["pump_fun"])
RAYDIUM = Pubkey.from_string(PROGRAMS["raydium_amm"])
ORCA = Pubkey.from_string(PROGRAMS["orca_whirlpools"])
METEORA_DLMM = Pubkey.from_string(PROGRAMS["meteora_dlmm"])
METEORA_PAMM = Pubkey.from_string(PROGRAMS["meteora_pamm"])

# Serum DEX v3 program ID (used by Raydium AMM)
SERUM_PROGRAM = Pubkey.from_string("srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX")
# Associated Token Account program
ATA_PROGRAM = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
# Memo program (optional for some DEXes)
MEMO_PROGRAM = Pubkey.from_string("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr")

# Pump.fun instruction discriminator hashes (first 8 bytes of SHA256)
# "global:buy"   → 16927863322537952870
# "global:sell"  → 12502976600942526055
PUMP_BUY_TAG = 16927863322537952870
PUMP_SELL_TAG = 12502976600942526055

# Orca Whirlpools instruction discriminator for "swap"
# first 8 bytes of SHA256("global:swap")
ORCA_SWAP_TAG = struct.unpack("<Q", bytes.fromhex("f8c69e91e17587c8"))[0]

# Meteora pAMM instruction tags
PAMM_BUY_EXACT_QUOTE_IN = 1  # BuyExactQuoteIn
PAMM_SELL_EXACT_QUOTE_IN = 2  # SellExactQuoteIn or SellExactBaseIn


def _acc(pubkey: str, signer: bool = False, writable: bool = True) -> AccountMeta:
    return AccountMeta(Pubkey.from_string(pubkey), signer, writable)


def _pk(pubkey: str) -> Pubkey:
    return Pubkey.from_string(pubkey)


def _ata_address(owner: Pubkey, mint: Pubkey) -> Pubkey:
    seeds = [bytes(owner), bytes(TOKEN_PROGRAM), bytes(mint)]
    return Pubkey.find_program_address(seeds, ATA_PROGRAM)[0]


class ArbExecutor:
    def __init__(self, client: SolanaClient):
        self.client = client
        self._wsol_account: Pubkey | None = None

    async def execute(self, opp: ArbOpportunity) -> bool:
        print(f"\n[executor] Executing {opp.strategy}: "
              f"profit={opp.profit_sol:.6f} SOL ({opp.profit_percent:.2f}%)")

        if opp.strategy == "cross_dex":
            return await self._execute_cross_dex(opp)
        elif opp.strategy == "multi_pair":
            return await self._execute_multi_pair(opp)
        return False

    async def _execute_cross_dex(self, opp: ArbOpportunity) -> bool:
        buy_leg = opp.legs[0]
        sell_leg = opp.legs[1]
        token_mint = buy_leg.token_mint or sell_leg.token_mint
        trade_sol = buy_leg.amount

        try:
            blockhash_resp = await self.client.rpc.get_latest_blockhash()
            blockhash = blockhash_resp.value.blockhash

            ixs = []
            ixs.append(set_compute_unit_price(self.client.cfg.arbitrage.priority_fee))
            ixs.append(set_compute_unit_limit(400_000))

            if buy_leg.dex == "pump_fun":
                ixs.extend(self._pump_buy_ixs(token_mint, trade_sol))
                ixs.extend(await self._dex_sell_ixs(
                    sell_leg.dex, sell_leg.pool_address, token_mint
                ))
            else:
                ixs.extend(await self._dex_buy_ixs(
                    buy_leg.dex, buy_leg.pool_address, token_mint, trade_sol
                ))
                ixs.extend(self._pump_sell_ixs(token_mint, sell_leg.amount))

            msg = MessageV0.try_compile(
                self.client.payer.pubkey(), ixs, [], blockhash,
            )
            tx = VersionedTransaction(msg, [self.client.payer])
            sig = await self.client.rpc.send_transaction(
                tx, preflight_commitment=None
            )
            print(f"  tx: {sig.value}")
            return True

        except Exception as e:
            print(f"  execution failed: {e}")
            return False

    async def _execute_multi_pair(self, opp: ArbOpportunity) -> bool:
        print("  multi-pair execution not yet implemented (scan-only)")
        return False

    # ── WSOL helpers ────────────────────────────────────────────────

    def _ensure_wsol_ix(self, fund_lamports: int) -> tuple[list[Instruction], Pubkey]:
        """Return instructions to create (if needed) and fund a WSOL account."""
        user = self.client.payer.pubkey()
        wsol = _ata_address(user, _pk(WSOL))

        ixs: list[Instruction] = []
        # Check if WSOL account already exists by looking at the inner ix.
        # We always include the create ATA instruction; it will fail silently
        # if the account already exists, but we can't use that in a single tx.
        # Instead, compute the ATA and assume it exists (common for active wallets).
        ixs.append(
            Instruction(
                ATA_PROGRAM,
                b"\x01",  # create idempotent ATA
                [
                    _acc(str(wsol), writable=True),
                    _acc(str(user)),
                    _acc(WSOL, writable=False),
                    _acc(str(user), signer=True),
                    _acc(TOKEN_PROGRAM.to_string(), writable=False),
                    _acc(SYSTEM_PROGRAM.to_string(), writable=False),
                ],
            )
        )
        # Fund with SOL
        ixs.append(
            Instruction(
                SYSTEM_PROGRAM,
                struct.pack("<I", 2) + struct.pack("<Q", fund_lamports),  # Transfer
                [
                    _acc(str(user), signer=True),
                    _acc(str(wsol)),
                ],
            )
        )
        # Sync native: update WSOL balance
        ixs.append(
            Instruction(
                _pk(TOKEN_PROGRAM),
                struct.pack("<Q", 17),  # SyncNative
                [_acc(str(wsol))],
            )
        )
        return ixs, wsol

    def _close_wsol_ix(self, wsol: Pubkey) -> Instruction:
        return Instruction(
            _pk(TOKEN_PROGRAM),
            struct.pack("<Q", 1),  # CloseAccount
            [
                _acc(str(wsol), writable=True),
                _acc(str(self.client.payer.pubkey()), writable=True),
                _acc(str(self.client.payer.pubkey()), signer=True),
            ],
        )

    # ── Pump.fun instructions ───────────────────────────────────────

    def _pump_buy_ixs(self, token_mint: str, sol_amount: float) -> list[Instruction]:
        mint_pk = _pk(token_mint)
        user = self.client.payer.pubkey()
        bonding_curve = Pubkey.find_program_address(
            [b"bonding-curve", bytes(mint_pk)], PUMPFUN
        )[0]
        global_ = Pubkey.find_program_address([b"global"], PUMPFUN)[0]
        associated_bonding_curve = Pubkey.find_program_address(
            [bytes(bonding_curve)], PUMPFUN
        )[0]

        lamports = int(sol_amount * 1e9)
        # Estimate tokens out (we use 0 min to avoid slippage lock — can be refined)
        min_tokens = 0

        data = struct.pack("<Q", PUMP_BUY_TAG)
        data += struct.pack("<Q", lamports)
        data += struct.pack("<Q", min_tokens)

        accounts = [
            _acc(str(global_)),
            _acc("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM"),  # pump.fun fee account
            _acc(str(mint_pk)),
            _acc(str(bonding_curve)),
            _acc(str(associated_bonding_curve)),
            _acc(str(user), signer=True),
            _acc(WSOL),
            _acc(str(user)),
            _acc(TOKEN_PROGRAM),
            _acc(TOKEN_2022_PROGRAM),
            _acc(SYSTEM_PROGRAM.to_string()),
            _acc("SysvarRent111111111111111111111111111111111"),
        ]
        return [Instruction(PUMPFUN, data, accounts)]

    def _pump_sell_ixs(self, token_mint: str, token_amount: float) -> list[Instruction]:
        mint_pk = _pk(token_mint)
        user = self.client.payer.pubkey()
        bonding_curve = Pubkey.find_program_address(
            [b"bonding-curve", bytes(mint_pk)], PUMPFUN
        )[0]
        global_ = Pubkey.find_program_address([b"global"], PUMPFUN)[0]
        associated_bonding_curve = Pubkey.find_program_address(
            [bytes(bonding_curve)], PUMPFUN
        )[0]
        # User's token account for this pump.fun token (uses TOKEN_2022_PROGRAM)
        user_token_ata = _ata_address(user, mint_pk)

        # Determine decimals — pump.fun tokens typically have 6 decimals
        token_decimals = 6 if "pump" in token_mint else 9
        token_lamports = int(token_amount * (10 ** token_decimals))

        data = struct.pack("<Q", PUMP_SELL_TAG)
        data += struct.pack("<Q", token_lamports)
        data += struct.pack("<Q", 0)  # min sol out (0 = no slippage)

        accounts = [
            _acc(str(global_)),
            _acc("CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM"),  # fee account
            _acc(str(mint_pk)),
            _acc(str(bonding_curve)),
            _acc(str(associated_bonding_curve)),
            _acc(str(user_token_ata)),
            _acc(str(user), signer=True),
            _acc(WSOL),
            _acc(str(WSOL)),  # SOL recv
            _acc(TOKEN_PROGRAM),
            _acc(TOKEN_2022_PROGRAM),
            _acc(SYSTEM_PROGRAM.to_string()),
            _acc("SysvarRent111111111111111111111111111111111"),
        ]
        return [Instruction(PUMPFUN, data, accounts)]

    # ── DEX instruction dispatcher ──────────────────────────────────

    async def _dex_buy_ixs(self, dex: str, pool: str, token_mint: str,
                           sol_amount: float) -> list[Instruction]:
        handlers = {
            "raydium": self._raydium_swap_ixs,
            "orca": self._orca_swap_ixs,
            "meteora_dlmm": self._meteora_dlmm_swap_ixs,
            "meteora_pamm": self._meteora_pamm_swap_ixs,
            "meteora": self._meteora_pamm_swap_ixs,
        }
        handler = handlers.get(dex)
        if not handler:
            print(f"  unknown DEX: {dex}")
            return []
        # Buy on DEX means SOL -> TOKEN
        return await handler(pool, token_mint, sol_amount, buy=True)

    async def _dex_sell_ixs(self, dex: str, pool: str, token_mint: str) -> list[Instruction]:
        handlers = {
            "raydium": self._raydium_swap_ixs,
            "orca": self._orca_swap_ixs,
            "meteora_dlmm": self._meteora_dlmm_swap_ixs,
            "meteora_pamm": self._meteora_pamm_swap_ixs,
            "meteora": self._meteora_pamm_swap_ixs,
        }
        handler = handlers.get(dex)
        if not handler:
            print(f"  unknown DEX: {dex}")
            return []
        # Sell on DEX means TOKEN -> SOL
        return await handler(pool, token_mint, 0, buy=False)

    # ── Raydium AMM v4 swap ────────────────────────────────────────

    async def _raydium_swap_ixs(self, pool_address: str, token_mint: str,
                                 sol_amount: float, buy: bool) -> list[Instruction]:
        user = self.client.payer.pubkey()
        pool_pk = _pk(pool_address)

        # Fetch pool account data to extract serum market info
        resp = await self.client.rpc.get_account_info_json_parsed(pool_pk)
        if not resp.value:
            print("  raydium: pool not found")
            return []
        raw = resp.value.data
        if isinstance(raw, str):
            raw = base64.b64decode(raw)

        # Parse Raydium AMM pool layout
        coin_mint = str(Pubkey.from_bytes(raw[400:432]))
        pc_mint = str(Pubkey.from_bytes(raw[432:464]))
        coin_vault = str(Pubkey.from_bytes(raw[128:160]))
        pc_vault = str(Pubkey.from_bytes(raw[160:192]))
        pool_mint = str(Pubkey.from_bytes(raw[224:256]))  # LP token mint
        serum_market = str(Pubkey.from_bytes(raw[512:544]))
        serum_open_orders = str(Pubkey.from_bytes(raw[584:616]))
        serum_bids = str(Pubkey.from_bytes(raw[616:632]))
        serum_asks = str(Pubkey.from_bytes(raw[632:648]))
        serum_event_queue = str(Pubkey.from_bytes(raw[648:664]))
        serum_coin_vault = str(Pubkey.from_bytes(raw[664:680]))
        serum_pc_vault = str(Pubkey.from_bytes(raw[680:696]))
        serum_vault_bid = str(Pubkey.from_bytes(raw[696:704]))
        serum_vault_ask = str(Pubkey.from_bytes(raw[704:712]))

        # Determine which is which
        is_coin_token = coin_mint == token_mint
        is_pc_token = pc_mint == token_mint
        is_coin_wsol = coin_mint == WSOL
        is_pc_wsol = pc_mint == WSOL

        if not (is_coin_token or is_pc_token) or not (is_coin_wsol or is_pc_wsol):
            print(f"  raydium: pool {pool_address[:8]} doesn't match token {token_mint[:8]}")
            return []

        # Determine direction
        if buy:
            # SOL -> TOKEN: input = WSOL (coin if coin=WSOL, else pc)
            source_mint = WSOL
            dest_mint = token_mint
            amount_in = int(sol_amount * 1e9)
        else:
            # TOKEN -> SOL: input = token, output = WSOL
            source_mint = token_mint
            dest_mint = WSOL
            amount_in = 0  # will compute from token balance

        # Vaults
        if coin_mint == source_mint:
            source_vault = coin_vault
            dest_vault = pc_vault
        else:
            source_vault = pc_vault
            dest_vault = coin_vault

        # Serum vaults
        serum_source_vault = serum_coin_vault if is_coin_wsol else serum_pc_vault
        serum_dest_vault = serum_pc_vault if is_coin_wsol else serum_coin_vault

        # User token accounts
        source_ata = _ata_address(user, _pk(source_mint))
        dest_ata = _ata_address(user, _pk(dest_mint))

        ixs = []

        # If buying with SOL, ensure WSOL account exists and is funded
        if buy:
            wsol_ixs, wsol_account = self._ensure_wsol_ix(amount_in)
            ixs.extend(wsol_ixs)
            source_ata = wsol_account

        # Fee destination (pool-specific)
        amm_authority = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
        fee_dest = coin_vault if is_coin_wsol else pc_vault  # fee goes to the non-WSOL vault

        # Swap data: tag(1) + amount_in(u64) + min_amount_out(u64)
        min_out = 1  # 1 lamport min (slippage handled elsewhere)
        data = bytes([9]) + struct.pack("<QQ", amount_in, min_out)

        accounts = [
            _acc(pool_address, writable=True),
            _acc(str(source_ata), writable=True),
            _acc(str(dest_ata), writable=True),
            _acc(source_vault, writable=True),
            _acc(dest_vault, writable=True),
            _acc(pool_mint, writable=True),
            _acc(fee_dest, writable=True),
            _acc(serum_market, writable=True),
            _acc(serum_bids, writable=True),
            _acc(serum_asks, writable=True),
            _acc(serum_vault_bid, writable=True),
            _acc(serum_vault_ask, writable=True),
            _acc(serum_open_orders, writable=True),
            _acc(SERUM_PROGRAM.to_string(), writable=False),
            _acc(str(user), signer=True),
            _acc(serum_coin_vault, writable=True),
            _acc(serum_pc_vault, writable=True),
            _acc(serum_request_queue="", writable=True),  # will compute below
        ]

        # Need serum request queue - extract from serum market
        serum_resp = await self.client.rpc.get_account_info_json_parsed(_pk(serum_market))
        if serum_resp.value:
            sm_raw = serum_resp.value.data
            if isinstance(sm_raw, str):
                sm_raw = base64.b64decode(sm_raw)
            serum_request_queue = str(Pubkey.from_bytes(sm_raw[168:200]))
            accounts[16] = _acc(serum_coin_vault, writable=True)  # serum_market_vault
            accounts[17] = _acc(serum_request_queue, writable=True)
            # Add serum event queue from market
            serum_event_queue_addr = str(Pubkey.from_bytes(sm_raw[136:168]))
            accounts.append(_acc(serum_event_queue_addr, writable=True))
        else:
            accounts.extend([
                _acc(serum_coin_vault, writable=True),
                _acc("", writable=True),  # placeholder for request queue
                _acc(serum_event_queue, writable=True),
            ])

        ixs.append(Instruction(RAYDIUM, data, accounts))

        # Close WSOL if we created one
        if buy:
            ixs.append(self._close_wsol_ix(_pk(str(source_ata))))

        return ixs

    # ── Orca Whirlpools swap ───────────────────────────────────────

    async def _orca_swap_ixs(self, pool_address: str, token_mint: str,
                              sol_amount: float, buy: bool) -> list[Instruction]:
        user = self.client.payer.pubkey()
        pool_pk = _pk(pool_address)

        resp = await self.client.rpc.get_account_info_json_parsed(pool_pk)
        if not resp.value:
            print("  orca: pool not found")
            return []
        raw = resp.value.data
        if isinstance(raw, str):
            raw = base64.b64decode(raw)

        # Parse Orca Whirlpool layout
        mint_a = str(Pubkey.from_bytes(raw[8:40]))
        mint_b = str(Pubkey.from_bytes(raw[40:72]))
        vault_a = str(Pubkey.from_bytes(raw[72:104]))
        vault_b = str(Pubkey.from_bytes(raw[104:136]))
        sqrt_price = struct.unpack("<Q", raw[648:656])[0]
        sqrt_price <<= 64
        sqrt_price |= struct.unpack("<Q", raw[640:648])[0] if len(raw) > 648 else 0

        a_to_b = (mint_b == token_mint) if buy else (mint_a == token_mint)
        if not a_to_b and not ((mint_a == token_mint) if buy else (mint_b == token_mint)):
            print(f"  orca: pool doesn't match token {token_mint[:8]}")
            return []

        a_to_b = (mint_a == WSOL) if buy else (mint_b == WSOL)

        source_mint = mint_a if a_to_b else mint_b
        dest_mint = mint_b if a_to_b else mint_a
        source_vault = vault_a if a_to_b else vault_b
        dest_vault = vault_b if a_to_b else vault_a

        ixs = []
        if buy:
            amount = int(sol_amount * 1e9)
            wsol_ixs, wsol_account = self._ensure_wsol_ix(amount)
            ixs.extend(wsol_ixs)
            source_ata = wsol_account
        else:
            amount = 0
            source_ata = _ata_address(user, _pk(source_mint))

        dest_ata = _ata_address(user, _pk(dest_mint))

        # Orca swap data
        other_amount_threshold = 1  # 1 lamport minimum
        sqrt_price_limit = 0  # no price limit

        data = struct.pack("<Q", ORCA_SWAP_TAG)
        data += struct.pack("<Q", amount)
        data += struct.pack("<Q", other_amount_threshold)
        data += struct.pack("<QQ",  # u128 sqrt_price_limit
                            sqrt_price_limit & 0xFFFFFFFFFFFFFFFF,
                            (sqrt_price_limit >> 64) & 0xFFFFFFFFFFFFFFFF)
        data += struct.pack("<?", buy or True)  # amount_specified_is_input
        data += struct.pack("<?", a_to_b)

        accounts = [
            _acc(pool_address, writable=True),
            _acc(str(source_vault), writable=True),
            _acc(str(dest_vault), writable=True),
            _acc(str(source_ata), writable=True),
            _acc(str(dest_ata), writable=True),
            _acc(str(user), signer=True),
            _acc(TOKEN_PROGRAM),
            _acc(MEMO_PROGRAM.to_string()),
        ]

        ixs.append(Instruction(ORCA, data, accounts))

        if buy:
            ixs.append(self._close_wsol_ix(_pk(str(source_ata))))

        return ixs

    # ── Meteora DLMM swap ──────────────────────────────────────────

    async def _meteora_dlmm_swap_ixs(self, pool_address: str, token_mint: str,
                                       sol_amount: float, buy: bool) -> list[Instruction]:
        user = self.client.payer.pubkey()
        pool_pk = _pk(pool_address)

        resp = await self.client.rpc.get_account_info_json_parsed(pool_pk)
        if not resp.value:
            print("  meteora: pool not found")
            return []
        raw = resp.value.data
        if isinstance(raw, str):
            raw = base64.b64decode(raw)

        token_x = str(Pubkey.from_bytes(raw[8:40]))
        token_y = str(Pubkey.from_bytes(raw[40:72]))
        vault_x = str(Pubkey.from_bytes(raw[72:104]))
        vault_y = str(Pubkey.from_bytes(raw[104:136]))
        oracle = str(Pubkey.from_bytes(raw[256:288])) if len(raw) > 288 else ""

        if token_mint not in (token_x, token_y) or WSOL not in (token_x, token_y):
            print(f"  meteora: pool doesn't match token or SOL")
            return []

        # Determine direction
        x_is_sol = token_x == WSOL
        y_is_sol = token_y == WSOL

        if buy:
            # SOL -> TOKEN
            amount_in = int(sol_amount * 1e9)
            in_token = WSOL
            out_token = token_mint
        else:
            amount_in = 0
            in_token = token_mint
            out_token = WSOL

        if in_token == token_x:
            in_vault = vault_x
            out_vault = vault_y
        else:
            in_vault = vault_y
            out_vault = vault_x

        ixs = []
        if buy:
            wsol_ixs, wsol_account = self._ensure_wsol_ix(amount_in)
            ixs.extend(wsol_ixs)
            in_ata = wsol_account
        else:
            in_ata = _ata_address(user, _pk(in_token))

        out_ata = _ata_address(user, _pk(out_token))

        # Meteora DLMM Swap instruction signature (first 8 bytes of SHA256("global:swap"))
        METEORA_SWAP_TAG = struct.unpack("<Q", bytes.fromhex("f8c69e91e17587c8"))[0]

        min_out = 1
        data = struct.pack("<Q", METEORA_SWAP_TAG)
        data += struct.pack("<Q", amount_in)
        data += struct.pack("<Q", min_out)

        accounts = [
            _acc(pool_address, writable=True),
            _acc(str(user), signer=True),
            _acc(in_vault, writable=True),
            _acc(out_vault, writable=True),
            _acc(str(in_ata), writable=True),
            _acc(str(out_ata), writable=True),
            _acc(TOKEN_PROGRAM),
        ]
        if oracle:
            accounts.append(_acc(oracle, writable=False))

        ixs.append(Instruction(METEORA_DLMM, data, accounts))

        if buy:
            ixs.append(self._close_wsol_ix(_pk(str(in_ata))))

        return ixs

    # ── Meteora pAMM swap (old Meteora) ────────────────────────────

    async def _meteora_pamm_swap_ixs(self, pool_address: str, token_mint: str,
                                       sol_amount: float, buy: bool) -> list[Instruction]:
        user = self.client.payer.pubkey()
        pool_pk = _pk(pool_address)

        resp = await self.client.rpc.get_account_info_json_parsed(pool_pk)
        if not resp.value:
            print("  meteora_pamm: pool not found")
            return []
        raw = resp.value.data
        if isinstance(raw, str):
            raw = base64.b64decode(raw)

        mint_a = str(Pubkey.from_bytes(raw[8:40]))
        mint_b = str(Pubkey.from_bytes(raw[40:72]))
        vault_a = str(Pubkey.from_bytes(raw[72:104]))
        vault_b = str(Pubkey.from_bytes(raw[104:136]))
        fee_vault_a = str(Pubkey.from_bytes(raw[136:168]))
        fee_vault_b = str(Pubkey.from_bytes(raw[168:200]))

        if token_mint not in (mint_a, mint_b) or WSOL not in (mint_a, mint_b):
            print(f"  meteora_pamm: pool doesn't match")
            return []

        a_is_sol = mint_a == WSOL
        b_is_sol = mint_b == WSOL
        a_is_token = mint_a == token_mint
        b_is_token = mint_b == token_mint

        if buy:
            amount_in = int(sol_amount * 1e9)
            in_mint = WSOL
            out_mint = token_mint
        else:
            amount_in = 0
            in_mint = token_mint
            out_mint = WSOL

        if in_mint == mint_a:
            in_vault = vault_a
            out_vault = vault_b
        else:
            in_vault = vault_b
            out_vault = vault_a

        ixs = []
        if buy:
            wsol_ixs, wsol_account = self._ensure_wsol_ix(amount_in)
            ixs.extend(wsol_ixs)
            in_ata = wsol_account
        else:
            in_ata = _ata_address(user, _pk(in_mint))

        out_ata = _ata_address(user, _pk(out_mint))

        # pAMM BuyExactQuoteIn instruction
        min_out = 1
        data = struct.pack("<B", PAMM_BUY_EXACT_QUOTE_IN if buy else PAMM_SELL_EXACT_QUOTE_IN)
        data += struct.pack("<Q", amount_in)
        data += struct.pack("<Q", min_out)

        accounts = [
            _acc(pool_address, writable=True),
            _acc(str(in_ata), writable=True),
            _acc(str(out_ata), writable=True),
            _acc(in_vault, writable=True),
            _acc(out_vault, writable=True),
            _acc(str(user), signer=True),
            _acc(TOKEN_PROGRAM),
            _acc(TOKEN_2022_PROGRAM),
        ]

        ixs.append(Instruction(METEORA_PAMM, data, accounts))

        if buy:
            ixs.append(self._close_wsol_ix(_pk(str(in_ata))))

        return ixs

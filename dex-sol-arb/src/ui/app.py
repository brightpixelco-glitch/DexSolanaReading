from nicegui import ui, app
import asyncio, yaml
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"

RPC_PROVIDERS = {
    "Public (free)": {
        "endpoint": "https://api.mainnet-beta.solana.com",
        "key_label": None,
        "paid": False,
        "hint": "",
    },
    "Helius (from Jupiter)": {
        "endpoint": "https://rpc.helius.com/?api-key={key}",
        "key_label": "Helius API Key",
        "paid": False,
        "hint": "Get from jup.ag → Settings → Helius RPC, or https://helius.dev",
    },
    "Helius (paid)": {
        "endpoint": "https://mainnet.helius-rpc.com/?api-key={key}",
        "key_label": "Helius API Key",
        "paid": True,
        "hint": "Paid plans from $49/mo at https://helius.dev",
    },
    "QuickNode": {
        "endpoint": "https://{key}.solana-mainnet.quiknode.pro/{key}",
        "key_label": "QuickNode Endpoint",
        "paid": True,
        "hint": "Paste the full HTTPS URL from your QuickNode dashboard",
    },
    "Alchemy (free)": {
        "endpoint": "https://solana-mainnet.g.alchemy.com/v2/{key}",
        "key_label": "Alchemy API Key",
        "paid": False,
        "hint": "Free tier at https://alchemy.com",
    },
    "Custom": {
        "endpoint": "{key}",
        "key_label": "Custom RPC URL",
        "paid": None,
        "hint": "Enter any Solana RPC URL directly",
    },
}


def _load_cfg() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_cfg(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)


def _rpc_url(provider_name: str, api_key: str) -> str:
    info = RPC_PROVIDERS.get(provider_name)
    if not info:
        return "https://api.mainnet-beta.solana.com"
    if info["key_label"] and api_key:
        return info["endpoint"].format(key=api_key)
    return info["endpoint"].format(key="")


def _detect_provider(rpc_url: str) -> str:
    if not rpc_url or "api.mainnet-beta" in rpc_url:
        return "Public (free)"
    if "helius" in rpc_url and "api-key" in rpc_url:
        return "Helius (from Jupiter)"
    if "helius" in rpc_url:
        return "Helius (paid)"
    if "quiknode" in rpc_url:
        return "QuickNode"
    if "alchemy" in rpc_url:
        return "Alchemy (free)"
    return "Custom"


class ArbUI:
    def __init__(self):
        self.cfg = _load_cfg()
        self.bot_task: Optional[asyncio.Task] = None
        self.running = False
        self._cb_queue: asyncio.Queue | None = None

    def build(self):
        # Detect current provider from saved config
        current_rpc = self.cfg.get("rpc", {}).get("endpoint", "")
        current_provider = _detect_provider(current_rpc)
        # Extract key from URL
        current_key = ""
        for name, info in RPC_PROVIDERS.items():
            if info["key_label"] and current_rpc:
                tmpl = info["endpoint"]
                # try to extract key
                parts = tmpl.split("{key}")
                if len(parts) == 2 and parts[0] in current_rpc and parts[1] in current_rpc:
                    current_key = current_rpc.replace(parts[0], "").replace(parts[1], "")
                    break

        with ui.header(elevated=True).classes("items-center justify-between"):
            ui.label("DexSolana Arbitrage").classes("text-2xl font-bold")
            self.status_badge = ui.label("● Stopped").classes("text-gray-500 ml-4")
            self.start_btn = ui.button("▶ Start", on_click=self.start_bot, icon="play_arrow")
            self.start_btn.classes("bg-green-600 text-white")
            self.stop_btn = ui.button("■ Stop", on_click=self.stop_bot, icon="stop").props("disabled")
            self.stop_btn.classes("bg-red-600 text-white ml-2")

        with ui.tabs().classes("w-full") as tabs:
            st = ui.tab("Configuration", icon="settings")
            db = ui.tab("Dashboard", icon="dashboard")
            lg = ui.tab("Logs", icon="terminal")

        with ui.tab_panels(tabs, value=st).classes("w-full"):
            with ui.tab_panel("Configuration"):
                self._settings_panel(current_provider, current_key)
            with ui.tab_panel("Dashboard"):
                self._dashboard_panel()
            with ui.tab_panel("Logs"):
                self._logs_panel()

    def _settings_panel(self, current_provider: str, current_key: str):
        with ui.grid(columns=2).classes("w-full gap-6"):
            # ── LEFT: RPC ──
            with ui.card().classes("w-full"):
                ui.label("RPC Provider").classes("text-lg font-bold mb-1")
                ui.label("Choose or customise your Solana RPC endpoint."
                         " Paid RPCs are strongly recommended for production."
                         "").classes("text-sm text-gray-500 mb-3")

                provider_keys = list(RPC_PROVIDERS.keys())
                if current_provider not in provider_keys:
                    current_provider = "Public (free)"
                self.rpc_provider = ui.select(
                    provider_keys,
                    value=current_provider,
                    label="Provider",
                    on_change=self._on_provider_change,
                ).classes("w-full")

                self.rpc_key = ui.input(
                    "API Key / Endpoint URL",
                    value=current_key,
                    placeholder="Leave blank for public RPC",
                ).classes("w-full").props("clearable")
                self.rpc_hint = ui.label("").classes("text-xs text-blue-600")
                self._on_provider_change()

                ui.separator()
                self.rpc_url_display = ui.input(
                    "Resolved RPC URL",
                    value=_rpc_url(current_provider, current_key),
                    readonly=True,
                ).classes("w-full")

            # ── RIGHT: Wallet + Bot ──
            with ui.column().classes("w-full gap-4"):
                with ui.card().classes("w-full"):
                    ui.label("Wallet").classes("text-lg font-bold mb-1")
                    kp = self.cfg.get("wallet", {}).get("keypair_path", "~/.config/solana/id.json")
                    self.wallet_path = ui.input(
                        "Keypair Path", value=kp,
                        placeholder="~/.config/solana/id.json",
                    ).classes("w-full")
                    self.wallet_balance = ui.label("Balance: (check after saving)")
                    ui.button("Check Balance", on_click=self._check_balance,
                              icon="account_balance").props("outline")

                with ui.card().classes("w-full"):
                    ui.label("Arbitrage Settings").classes("text-lg font-bold mb-1")
                    arb = self.cfg.get("arbitrage", {})
                    self.min_profit = ui.number(
                        "Min Profit (SOL)", value=arb.get("min_profit_sol", 0.001),
                        min=0.0001, max=10.0, step=0.0001, format="%.4f",
                    ).classes("w-full")
                    self.trade_size = ui.number(
                        "Max Trade (SOL)", value=arb.get("max_trade_sol", 1.0),
                        min=0.01, max=100.0, step=0.1,
                    ).classes("w-full")
                    self.scan_interval = ui.number(
                        "Scan Interval (s)", value=arb.get("scan_interval", 3.0),
                        min=0.5, max=60.0, step=0.5,
                    ).classes("w-full")
                    self.slippage = ui.number(
                        "Slippage (bps)", value=arb.get("slippage_bps", 100),
                        min=10, max=5000, step=10,
                    ).classes("w-full")

                    self.cross_dex = ui.switch(
                        "Cross-DEX arb", value=arb.get("enable_cross_dex", True)
                    )
                    self.multi_pair = ui.switch(
                        "Multi-pair arb", value=arb.get("enable_multi_pair", True)
                    )

                ui.button("Save & Apply", on_click=self._save_settings,
                          icon="save", color="primary")

    def _dashboard_panel(self):
        with ui.grid(columns=3).classes("w-full gap-4"):
            with ui.card().classes("w-full"):
                ui.label("Wallet").classes("font-bold")
                self.dash_balance = ui.label("— SOL").classes("text-2xl")
                self.dash_usdc = ui.label("— USDC")
            with ui.card().classes("w-full"):
                ui.label("Bot").classes("font-bold")
                self.dash_status = ui.label("Stopped").classes("text-xl")
                self.dash_uptime = ui.label("—")
            with ui.card().classes("w-full"):
                ui.label("Opportunities").classes("font-bold")
                self.dash_found = ui.label("0").classes("text-2xl")
                self.dash_executed = ui.label("0 executed")

        ui.separator()
        ui.label("Recent Opportunities").classes("text-lg font-bold")
        self.opp_table = ui.table(
            columns=[
                {"name": "time", "label": "Time", "field": "time"},
                {"name": "strategy", "label": "Strategy", "field": "strategy"},
                {"name": "profit", "label": "Profit (SOL)", "field": "profit"},
                {"name": "pct", "label": "Profit %", "field": "pct"},
                {"name": "conf", "label": "Conf.", "field": "conf"},
            ],
            rows=[],
        ).classes("w-full")

    def _logs_panel(self):
        self.log_area = ui.log().classes("w-full h-[70vh]")
        with ui.row():
            ui.button("Clear", on_click=lambda: self.log_area.clear(), icon="cleaning_services").props("outline")
            ui.button("Copy All", on_click=lambda: ui.clipboard.write(
                "\n".join(self.log_area.lines) if hasattr(self.log_area, "lines") else ""
            ), icon="content_copy").props("outline")

    # ── event handlers ────────────────────────────────────────────

    def _on_provider_change(self):
        name = self.rpc_provider.value
        info = RPC_PROVIDERS.get(name)
        if info and info["key_label"]:
            self.rpc_key.visible = True
            self.rpc_hint.text = info.get("hint", "")
            self.rpc_hint.visible = True
        else:
            self.rpc_key.visible = False
            self.rpc_hint.visible = False
        self._update_rpc_display()

    def _update_rpc_display(self):
        name = self.rpc_provider.value
        key = self.rpc_key.value or ""
        self.rpc_url_display.value = _rpc_url(name, key)

    # because NiceGUI can't do async in on_change directly
    def _rpc_key_update(self, e):
        self._update_rpc_display()

    async def _check_balance(self):
        from solana.rpc.async_api import AsyncClient
        from solders.keypair import Keypair
        import json
        try:
            kp_path = Path(self.wallet_path.value).expanduser()
            if not kp_path.exists():
                self.wallet_balance.text = "Balance: keypair not found"
                return
            with open(kp_path) as f:
                kp = Keypair.from_bytes(bytes(json.load(f)))
            async with AsyncClient("https://api.mainnet-beta.solana.com") as cl:
                bal = await cl.get_balance(kp.pubkey())
            self.wallet_balance.text = f"Balance: {bal.value / 1e9:.4f} SOL"
        except Exception as e:
            self.wallet_balance.text = f"Error: {e}"

    def _save_settings(self):
        url = _rpc_url(self.rpc_provider.value, self.rpc_key.value or "")
        cfg = {
            "rpc": {"endpoint": url},
            "wallet": {"keypair_path": self.wallet_path.value},
            "arbitrage": {
                "min_profit_sol": self.min_profit.value,
                "max_trade_sol": self.trade_size.value,
                "min_trade_sol": 0.01,
                "scan_interval": self.scan_interval.value,
                "slippage_bps": int(self.slippage.value),
                "enable_cross_dex": self.cross_dex.value,
                "enable_multi_pair": self.multi_pair.value,
                "priority_fee": 5000,
            },
            "pumpfun": {"max_token_age_minutes": 60},
            "logging": {"level": "INFO", "file": "logs/arb.log"},
        }
        _save_cfg(cfg)
        self.cfg = cfg
        ui.notify("Settings saved! Launch the bot from the Dashboard.", type="positive")

    # ── bot lifecycle ──────────────────────────────────────────────

    def log_callback(self, msg: str):
        """Called from bot thread."""
        if hasattr(self, "log_area"):
            ui.timer(0.01, lambda: self.log_area.push(msg), once=True)

    async def start_bot(self):
        if self.running:
            return
        self._save_settings()
        self.running = True
        self.start_btn.props("disabled")
        self.stop_btn.props(remove="disabled")
        self.status_badge.text = "● Running"
        self.status_badge.classes("text-green-500", replace="text-gray-500")
        self.dash_status.text = "Running"

        from src.bot import run_bot
        self.bot_task = asyncio.create_task(self._run_wrapper(run_bot))

    async def _run_wrapper(self, run_fn):
        try:
            await run_fn(cfg=None, callback=self.log_callback)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.log_callback(f"Bot crashed: {e}")
        finally:
            self.running = False
            self.start_btn.props(remove="disabled")
            self.stop_btn.props("disabled")
            self.status_badge.text = "● Stopped"
            self.status_badge.classes("text-gray-500", replace="text-green-500")
            self.dash_status.text = "Stopped"

    async def stop_bot(self):
        if self.bot_task and not self.bot_task.done():
            self.bot_task.cancel()
            self.log_callback("Bot stopping...")
        self.stop_btn.props("disabled")


def start_ui():
    ui_inst = ArbUI()

    @ui.page("/")
    def index():
        ui_inst.build()

    ui.run(title="DexSolana Arbitrage", host="127.0.0.1", port=8080, reload=False)

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from dotenv import load_dotenv

# Load environment variables from a .env file if present
load_dotenv()


class Settings(BaseSettings):
    app_name: str = Field(default="Onchain Tools")
    environment: str = Field(default="development")

    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    log_level: str = Field(default="INFO")

    redis_url: str = Field(default="redis://localhost:6379/0")

    api_key_required: bool = Field(default=False)
    public_api_key: str | None = Field(default=None)

    openai_api_key: str | None = Field(default=None)
    twitterapi_key: str | None = Field(default=None)

    # Telegram
    telegram_bot_token: str | None = Field(default=None)
    telegram_chat_id: str | None = Field(default=None)
    enable_telegram: bool = Field(default=True)

    # Webhook forwarder (OpenClaw a-bot)
    abot_webhook_url: str | None = Field(default=None)
    abot_proxy_token: str | None = Field(default=None)
    abot_webhook_secret: str | None = Field(default=None)

    # Buyer
    solana_private_key: str | None = Field(default=None)
    evm_private_key: str | None = Field(default=None)
    buy_amount_usd: float = Field(default=50.0)
    solana_rpc_url: str = Field(default="https://api.mainnet-beta.solana.com")

    # ── Launch monitor ──────────────────────────────────────────────
    # Per-chain toggles. All chains are monitored by default; set one to
    # false in .env to skip it (e.g. BSC=false).
    solana: bool = Field(default=True)
    base: bool = Field(default=True)
    bsc: bool = Field(default=True)
    robinhood: bool = Field(default=True)

    # Filtering thresholds. Defaults preserve prior hardcoded behavior.
    launch_min_liquidity: float = Field(default=6000.0)
    launch_min_market_cap: float = Field(default=0.0)  # 0 = no minimum
    launch_max_market_cap: float = Field(default=0.0)  # 0 = no maximum
    launch_min_twitter_followers: int = Field(default=0)  # 0 = no minimum
    launch_require_twitter: bool = Field(default=True)
    launch_require_website: bool = Field(default=False)

    # Scan timing / behavior.
    launch_poll_seconds: int = Field(default=30)
    launch_lookback_hours: float = Field(default=1.0)
    launch_top_n_for_no_time: int = Field(default=50)

    # Smart-wallet activity filter (optional; OFF unless wallets are listed).
    # Comma/space/newline separated Solana and/or EVM (0x…) addresses.
    launch_smart_wallets: str = Field(default="")
    launch_min_smart_wallets: int = Field(default=0)       # ≥N wallets must hold; 0 -> defaults to 1 when wallets set
    launch_min_smart_wallet_pct: float = Field(default=0.0)  # ≥X% of tracked wallets must hold; 0 = off
    launch_smart_wallet_refresh_seconds: int = Field(default=60)

    # Holdings data providers for the smart-wallet filter.
    helius_api_key: str | None = Field(default=None)   # Solana holdings
    alchemy_api_key: str | None = Field(default=None)  # Base/BSC/Robinhood holdings

    # ── CabalSpy labeled-wallet enrichment (optional; OFF without key) ──
    # Score alerts by which CabalSpy-labeled KOL/smart wallets already bought
    # the token. Free API key: https://apidashboard.cabalspy.xyz/register
    cabalspy_api_key: str | None = Field(default=None)
    cabalspy_chains: str = Field(default="robinhood")  # comma-separated; start small
    cabalspy_wallet_type: str = Field(default="")      # kol|smart|whale; empty = all labels
    cabalspy_min_buyers: int = Field(default=0)        # ≥N labeled buyers to alert; 0 = enrich only
    cabalspy_tx_limit: int = Field(default=100)        # newest labeled txs examined per token
    cabalspy_max_checks: int = Field(default=6)        # metered API calls per token (gate re-checks)

    # ── Claude Code analysis (optional; OFF by default) ─────────────
    # Second-stage token research through the LOCAL Claude Code CLI
    # (headless `claude -p`), parallel to the Hermes webhook forwarder.
    claude_code_enabled: bool = Field(default=False)
    claude_code_model: str = Field(default="claude-opus-4-8")  # or claude-fable-5 (aliases opus/fable work)
    claude_code_bin: str = Field(default="claude")
    claude_code_timeout_seconds: int = Field(default=600)
    claude_code_max_budget_usd: float = Field(default=1.0)   # hard per-run API cost cap
    claude_code_max_concurrent: int = Field(default=1)
    claude_code_max_pending: int = Field(default=3)  # skip alerts beyond this backlog
    claude_code_permission_mode: str = Field(default="dontAsk")
    # Web-only by default. The prompt embeds untrusted token metadata, so
    # granting Read/Glob/Grep would let a prompt-injected session read local
    # files (.env holds private keys) with WebFetch available to exfiltrate.
    claude_code_allowed_tools: str = Field(default="WebSearch,WebFetch")
    claude_code_effort: str = Field(default="")   # low|medium|high|xhigh|max; empty = CLI default
    claude_code_workdir: str = Field(default="")  # empty = monitor's cwd

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="allow")


settings = Settings()

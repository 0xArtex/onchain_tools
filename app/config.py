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

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="allow")


settings = Settings()

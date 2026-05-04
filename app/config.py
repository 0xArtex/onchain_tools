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

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="allow")


settings = Settings()

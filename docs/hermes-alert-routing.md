# Hermes Alert Routing

`launch_monitor` can forward every scanner hit to a Hermes/OpenClaw webhook for second-stage research. The webhook receives a structured JSON event, not just a Telegram-formatted text blob, so the downstream agent can reliably parse chain, token address, pair, liquidity, market cap, volume, socials, and risk flags.

## Flow

1. `LaunchMonitorAgent` finds a new Base/Solana/BSC token that passes scanner filters.
2. The normal Telegram group alert is still sent when `ENABLE_TELEGRAM=true`.
3. `_forward_to_abot()` posts a `new_token_alert` JSON payload to `ABOT_WEBHOOK_URL`.
4. Hermes should load/use `token-research`, `x-research`, and `agents-infra`:
   - `SKIP`: do not message Z and do not write to watchlist.
   - `WATCH`: DM Z privately and append to the monthly watchlist.
   - `APE` / `BUY`: DM Z, append to watchlist, and call Z via AgentOS.

## Local Docker on the Hermes host (no public exposure)

If the scanner runs in Docker on the same machine as Hermes, keep the Hermes gateway private and call it from the container through Docker's host gateway:

```bash
ABOT_WEBHOOK_URL=http://host.docker.internal:8644/webhooks/onchain-alerts
```

`docker-compose.yml` includes:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

So Linux Docker containers can resolve `host.docker.internal` to the host machine.

Run:

```bash
cp .env.example .env
# fill .env
docker compose up -d --build
```

## Environment

```bash
# Existing Telegram settings can remain enabled for raw scanner group alerts.
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
ENABLE_TELEGRAM=true

# Hermes/OpenClaw webhook endpoint.
# If this is a Hermes subscription route, use the exact /webhooks/<name> URL.
# If this is an OpenClaw base URL, the app will try /hooks/wake then /hooks/agent.
ABOT_WEBHOOK_URL=https://your-hermes-host/webhooks/onchain-alerts

# Optional. Sent as X-Proxy-Token when present.
ABOT_PROXY_TOKEN=...
```

## Creating the Hermes route

On the machine running Hermes:

```bash
hermes webhook subscribe onchain-alerts
hermes webhook list
```

Expose the gateway/webhook server to the scanner host with your preferred tunnel or reverse proxy. Put the public route into `ABOT_WEBHOOK_URL`.

## Payload shape

The forwarded body includes:

```json
{
  "type": "new_token_alert",
  "source": "onchain_tools.launch_monitor",
  "wakeMode": "now",
  "deliver": false,
  "delivery_policy": "only_watch_ape_buy",
  "required_skills": ["token-research", "x-research", "agents-infra"],
  "text": "human readable summary",
  "message": "summary + instructions",
  "instructions": "classification and action rules",
  "token": {
    "chain": "base",
    "symbol": "TOKEN",
    "name": "Token Name",
    "token_address": "0x...",
    "pair_address": "0x...",
    "dex_url": "https://dexscreener.com/...",
    "metrics": {
      "price_usd": "0.0001",
      "liquidity_usd": 12000,
      "market_cap": 85000,
      "volume_24h": 300000
    },
    "socials": {
      "twitter_url": "https://x.com/project",
      "twitter_followers": 1234,
      "twitter_profile": {}
    },
    "risk": {
      "rug_check": {},
      "red_flags": []
    }
  },
  "raw_token": {}
}
```

## Testing

```bash
python -m pytest tests/test_hermes_forwarder.py -q
```

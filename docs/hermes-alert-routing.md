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

If the scanner runs in Docker on the same machine as Hermes, keep the Hermes gateway private and call it from the scanner container through host networking:

```bash
ABOT_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/onchain-alerts
ABOT_WEBHOOK_SECRET=<secret printed by hermes webhook subscribe>
```

`docker-compose.yml` includes:

```yaml
network_mode: host
```

So on Linux, the scanner container shares the host network namespace and can reach Hermes on host loopback without exposing the webhook publicly. On Docker Desktop/macOS/Windows, remove `network_mode: host` and use `host.docker.internal` instead.

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

# Native Hermes webhooks require HMAC by default. This secret signs the JSON body
# with X-Webhook-Signature.
ABOT_WEBHOOK_SECRET=...

# Optional legacy OpenClaw/a-bot proxy token. Sent as X-Proxy-Token when present.
ABOT_PROXY_TOKEN=...
```

## Creating the Hermes route

On the machine running Hermes:

```bash
hermes config set platforms.webhook.enabled true
hermes config set platforms.webhook.extra.host 127.0.0.1
hermes config set platforms.webhook.extra.port 8644
hermes gateway restart
hermes webhook subscribe onchain-alerts \
  --skills token-research,x-research,agents-infra \
  --deliver log \
  --prompt 'Research this new token alert using token-research and x-research. If weak, final answer SKIP and do not notify. If WATCH, APE, or BUY, send a Telegram DM to the operator with verdict, chain, token address, pair URL, key metrics, X/community findings, risks, and next action. Raw alert: {__raw__}'
hermes webhook list
```

For same-machine Docker, keep the webhook bound to loopback and use host networking for the scanner so it is reachable from the container but not exposed publicly. Put the route URL and generated HMAC secret into `ABOT_WEBHOOK_URL` and `ABOT_WEBHOOK_SECRET`.

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

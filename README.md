## Onchain Tools - Multi-Agent Trading Platform

A microservices-based, multi-agent architecture for monitoring and analyzing new token launches across multiple blockchains.

### Architecture
```
app/
├── agents/           # Specialized agents
│   ├── launch_monitor/  # Monitors new token launches (IMPLEMENTED ✅)
│   ├── volume_bot/      # Volume analysis (stub)
│   ├── twitter_screener/  # Twitter sentiment (stub)
│   └── wallet_analyzer/   # Wallet analysis (stub)
├── api/              # FastAPI gateway
├── mq/               # Redis message queue
└── main.py           # Orchestrator & CLI
```

### Features
**Launch Monitor Agent** ✅
- Monitors BSC, Solana, Base, and Robinhood Chain for new token launches
- Filters by liquidity, market cap, age, and social presence
- Fetches Twitter follower counts
- Publishes alerts via message queue
- Redis-based deduplication with auto-expiring cache (no duplicate alerts!)

**Volume Bot, Twitter Screener, Wallet Analyzer** (stubs ready for implementation)

### Quickstart

1. **Install dependencies:**
```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1  # Windows PowerShell
# or: source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

2. **Configure environment:**
```bash
copy .env.example .env
# Edit .env and add your TWITTERAPI_KEY (optional)
```

3. **Start Redis** (required for message queue):
```bash
# Install Redis or use Docker:
docker run -d -p 6379:6379 redis:alpine
```

4. **Run the orchestrator** (starts all agents):
```bash
python -m app.main orchestrator
```

5. **In another terminal, run the API gateway:**
```bash
python -m app.main gateway
# API available at http://localhost:8000
```

### API Endpoints

- `GET /health` - Health check
- `POST /v1/wallet/analysis` - Analyze top wallets (stub)
- `POST /v1/volume/analysis` - Analyze volume patterns (stub)
- `POST /v1/twitter/analysis` - Analyze Twitter sentiment (stub)

## Local Docker deployment with private Hermes webhook

If Hermes and the scanner run on this machine, the Hermes webhook does not need to be public. Run Hermes' webhook gateway on the host loopback and run the scanner container with host networking so it can call `127.0.0.1:8644` privately.

### Hermes/OpenClaw agent setup brief

This is the recommended same-machine setup for Hermes/OpenClaw agents that want private token-alert routing:

1. Bind Hermes webhooks to host loopback, not the public internet:
```bash
hermes config set platforms.webhook.enabled true
hermes config set platforms.webhook.extra.host 127.0.0.1
hermes config set platforms.webhook.extra.port 8644
hermes gateway restart
```

2. Create the Hermes route and save the generated HMAC secret:
```bash
hermes webhook subscribe onchain-alerts \
  --skills token-research,x-research,agents-infra \
  --deliver log \
  --prompt 'Research this new token alert using token-research and x-research. If weak, final answer SKIP and do not notify. If WATCH, APE, or BUY, send a Telegram DM to the operator with verdict, chain, token address, pair URL, key metrics, X/community findings, risks, and next action. Raw alert: {__raw__}'
```

3. Put the route URL and secret into this repo's `.env`:
```bash
ABOT_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/onchain-alerts
ABOT_WEBHOOK_SECRET=<secret printed by hermes webhook subscribe>
ENABLE_TELEGRAM=false   # optional: disables raw scanner Telegram alerts; Hermes will DM researched alerts
```

4. Run the stack:
```bash
docker compose up -d --build
```

Notes:
- `docker-compose.yml` uses `network_mode: host` for the scanner on Linux, so `127.0.0.1:8644` reaches the host Hermes gateway.
- On Docker Desktop/macOS/Windows, remove `network_mode: host` and use `host.docker.internal` instead.
- Hermes webhooks require HMAC by default. `ABOT_WEBHOOK_SECRET` signs requests with `X-Webhook-Signature`.
- Do not commit `.env`; commit only `.env.example` placeholders.

### Manual step-by-step
```bash
hermes gateway setup
# enable Webhooks, or set WEBHOOK_ENABLED=true / WEBHOOK_PORT=8644 in Hermes env
hermes gateway run
```

2. Subscribe a route:
```bash
hermes webhook subscribe onchain-alerts
```

3. Configure scanner env:
```bash
cp .env.example .env
# edit .env and set TWITTERAPI_KEY, Telegram settings, and ABOT_WEBHOOK_SECRET
ABOT_WEBHOOK_URL=http://127.0.0.1:8644/webhooks/onchain-alerts
ABOT_WEBHOOK_SECRET=<secret printed by hermes webhook subscribe>
```

4. Run scanner + Redis locally:
```bash
docker compose up -d --build
```

5. Check logs:
```bash
docker compose logs -f launch-monitor
```

### Configuration

Edit `.env` file:
- `REDIS_URL` - Redis connection string
- `TWITTERAPI_KEY` - Get from https://twitterapi.io (optional)
- `LOG_LEVEL` - DEBUG, INFO, WARNING, ERROR
- `ABOT_WEBHOOK_URL` - optional Hermes/OpenClaw webhook for second-stage token research
- `ABOT_WEBHOOK_SECRET` - optional HMAC secret for native Hermes `/webhooks/<route>` URLs; sends `X-Webhook-Signature`
- `ABOT_PROXY_TOKEN` - optional legacy proxy token sent as `X-Proxy-Token` to OpenClaw/a-bot style hooks

See `docs/hermes-alert-routing.md` for the WATCH/APE/BUY routing flow.

#### Launch Monitor tuning (`.env`, no code edits required)

These map to the `LaunchConfig` defaults in `app/agents/launch_monitor/agent.py` and
can all be overridden via `.env` (defaults shown):

| Variable | Default | Purpose |
| --- | --- | --- |
| `SOLANA` | `true` | Scan Solana (set `false` to disable) |
| `BASE` | `true` | Scan Base (set `false` to disable) |
| `BSC` | `true` | Scan BSC (set `false` to disable) |
| `ROBINHOOD` | `true` | Scan Robinhood Chain (set `false` to disable) |
| `LAUNCH_MIN_LIQUIDITY` | `6000` | Minimum pool liquidity (USD) to alert |
| `LAUNCH_MIN_MARKET_CAP` | `0` | Minimum market cap (USD); `0` = off |
| `LAUNCH_MAX_MARKET_CAP` | `0` | Maximum market cap (USD); `0` = off |
| `LAUNCH_REQUIRE_TWITTER` | `true` | Require a Twitter/X account |
| `LAUNCH_REQUIRE_WEBSITE` | `false` | Require a website |
| `LAUNCH_MIN_TWITTER_FOLLOWERS` | `0` | Minimum follower count; `0` = off |
| `LAUNCH_POLL_SECONDS` | `30` | Seconds between scans |
| `LAUNCH_LOOKBACK_HOURS` | `1` | Max age of a pair to consider |
| `LAUNCH_TOP_N_FOR_NO_TIME` | `50` | Top-liquidity pairs kept when no creation timestamp |

All chains are enabled by default; disabling every chain logs a warning and the monitor scans nothing.

#### Smart-wallet activity filter (optional)

Only alert on tokens that tracked "smart money" wallets currently hold. **Off by
default** — list no wallets and the monitor behaves exactly as before. Implemented
in `app/services/smart_wallets.py` and applied as a filter step in `_scan_once`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LAUNCH_SMART_WALLETS` | _(empty)_ | Solana and/or EVM (`0x…`) addresses, comma/space/newline separated. Empty = filter off |
| `LAUNCH_MIN_SMART_WALLETS` | `0` | Min wallets that must hold the token. `0` → defaults to `1` once wallets are listed |
| `LAUNCH_MIN_SMART_WALLET_PCT` | `0` | Also require ≥X% of tracked wallets (per chain). `0` = off |
| `LAUNCH_SMART_WALLET_REFRESH_SECONDS` | `60` | How often to refresh wallet holdings |
| `HELIUS_API_KEY` | _(empty)_ | Solana holdings source (required if you list Solana wallets) |
| `ALCHEMY_API_KEY` | _(empty)_ | Base/BSC/Robinhood holdings source (required if you list EVM wallets) |

How it works: each scan, the tracker refreshes the current token holdings of your
wallets (Solana via Helius `getTokenAccountsByOwner`, EVM via Alchemy
`alchemy_getTokenBalances` on Base + BNB + Robinhood Chain) and builds a
`chain → {token → wallets}` map. A candidate token is dropped unless enough of your
wallets hold it. Address type (Solana vs EVM) is auto-detected; EVM wallets are
checked on Base, BSC, and Robinhood Chain.

**Fail-open:** if a provider key is missing or a refresh errors, that chain is
treated as "unknown" and tokens pass through (with a warning) — you never get
silent zero-alerts. Need a Helius key? See the `helius` skill (signup ~1 USDC).

#### Claude Code analysis (optional)

Second-stage research through your **local Claude Code install**, parallel to the
Hermes webhook. Each alert spawns a headless `claude -p` session that researches
the token (using the `token-research` skill if installed, plus web search) and
returns a strict-JSON verdict — `SKIP` / `WATCH` / `APE` / `BUY` with confidence,
reasons, and risks. The verdict is posted to Telegram as a reply to the original
alert and published on the `token_analysis` MQ channel. **Off by default** —
runs are billed against your Claude login.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CLAUDE_CODE_ENABLED` | `false` | Master switch; also requires the `claude` CLI on PATH |
| `CLAUDE_CODE_MODEL` | `claude-opus-4-8` | Analysis model; set `claude-fable-5` (or aliases `opus`/`fable`) |
| `CLAUDE_CODE_MAX_BUDGET_USD` | `1.0` | Hard API-cost cap per analysis run |
| `CLAUDE_CODE_TIMEOUT_SECONDS` | `600` | Wall-clock kill switch per run |
| `CLAUDE_CODE_MAX_CONCURRENT` | `1` | Max simultaneous Claude Code sessions |
| `CLAUDE_CODE_MAX_PENDING` | `3` | Backlog cap — alerts beyond this are skipped, not queued forever |
| `CLAUDE_CODE_PERMISSION_MODE` | `dontAsk` | Headless-safe: auto-denies non-allow-listed tools instead of prompting |
| `CLAUDE_CODE_ALLOWED_TOOLS` | `WebSearch,WebFetch` | Web-only research allow-list (see security note) |
| `CLAUDE_CODE_EFFORT` | _(empty)_ | Optional reasoning effort: `low`–`max`; empty = CLI default |
| `CLAUDE_CODE_BIN` / `CLAUDE_CODE_WORKDIR` | `claude` / _(empty)_ | CLI binary and working directory overrides |

Implemented in `app/services/claude_code.py`; a verdict fires as a background
task from `_scan_once`, so analysis latency never blocks scanning.

**Security note:** the analysis prompt embeds untrusted token metadata (names,
bios, URLs scraped from DexScreener), so treat every verdict as advisory — a
hostile token's website can try to talk the model into a bullish verdict. Never
wire the `token_analysis` channel to automated buying. Keep the tool allow-list
web-only: adding `Read`/`Glob`/`Grep` would let a prompt-injected session read
local files (`.env` holds private keys) with `WebFetch` available to exfiltrate
them. If you must grant file tools, point `CLAUDE_CODE_WORKDIR` at a clean
directory.

### Development

Run individual agents:
```bash
python -m app.main agent launch
python -m app.main agent wallet
python -m app.main agent volume
python -m app.main agent twitter
```

### Migration Note
JavaScript files (`index.js`, `twitterService.js`) have been migrated to Python and renamed to `.old` for reference.

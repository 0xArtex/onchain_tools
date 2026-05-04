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
- Monitors BSC, Solana, and Base chains for new token launches
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

If Hermes and the scanner run on this machine, the Hermes webhook does not need to be public. Run Hermes' webhook gateway on the host and let the Docker container call it through `host.docker.internal`.

1. Enable/start the Hermes webhook gateway on the host:
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
# edit .env and set TWITTERAPI_KEY, Telegram settings, and ABOT_PROXY_TOKEN if needed
ABOT_WEBHOOK_URL=http://host.docker.internal:8644/webhooks/onchain-alerts
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
- `ABOT_PROXY_TOKEN` - optional proxy token sent as `X-Proxy-Token` to the webhook

See `docs/hermes-alert-routing.md` for the WATCH/APE/BUY routing flow.

Launch Monitor settings in `app/agents/launch_monitor/agent.py`:
- `CHAINS` - Blockchains to monitor
- `MIN_LIQUIDITY` - Minimum liquidity filter
- `REQUIRE_TWITTER` - Require Twitter account
- And more...

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

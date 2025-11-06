from __future__ import annotations

import argparse
import asyncio
import os
import sys

import uvicorn

from .api.gateway import app as fastapi_app
from .agents.launch_monitor.agent import LaunchMonitorAgent
from .agents.volume_bot.agent import VolumeBotAgent
from .agents.twitter_screener.agent import TwitterScreenerAgent
from .agents.wallet_analyzer.agent import WalletAnalyzerAgent
from .logging_config import configure_logging, logger


async def run_gateway() -> None:
    configure_logging()
    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    config = uvicorn.Config(fastapi_app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def run_agent(name: str) -> None:
    agents_map = {
        "launch": LaunchMonitorAgent,
        "volume": VolumeBotAgent,
        "twitter": TwitterScreenerAgent,
        "wallet": WalletAnalyzerAgent,
    }
    if name not in agents_map:
        raise SystemExit(f"Unknown agent '{name}'. Options: {', '.join(agents_map)}")

    agent = agents_map[name]()
    await agent.start()

    # Keep the agent alive
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await agent.stop()


async def run_orchestrator() -> None:
    """Run all stub agents in-process for convenience."""
    launch = LaunchMonitorAgent()
    volume = VolumeBotAgent()
    twitter = TwitterScreenerAgent()
    wallet = WalletAnalyzerAgent()

    await asyncio.gather(
        launch.start(),
        volume.start(),
        twitter.start(),
        wallet.start(),
    )

    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Onchain Tools entrypoint")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gateway")

    p_agent = sub.add_parser("agent")
    p_agent.add_argument("name", choices=["launch", "volume", "twitter", "wallet"]) 

    sub.add_parser("orchestrator")

    args = parser.parse_args()

    if args.cmd == "gateway":
        asyncio.run(run_gateway())
    elif args.cmd == "agent":
        asyncio.run(run_agent(args.name))
    elif args.cmd == "orchestrator":
        asyncio.run(run_orchestrator())


if __name__ == "__main__":
    main()

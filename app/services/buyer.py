"""Token buy service — executes swaps across Solana, BSC, and Base."""
from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass
from typing import Optional

import httpx

from ..config import settings
from ..logging_config import logger

# ── Optional chain libs ──────────────────────────────────────────────
try:
    from solders.keypair import Keypair as SolanaKeypair
    from solders.transaction import VersionedTransaction
    HAS_SOLANA = True
except ImportError:
    HAS_SOLANA = False

try:
    from web3 import Web3
    from eth_account import Account as EthAccount
    HAS_WEB3 = True
except ImportError:
    HAS_WEB3 = False

# ── Constants ─────────────────────────────────────────────────────────
EXPLORERS = {
    "solana": "https://solscan.io/tx/",
    "bsc": "https://bscscan.com/tx/",
    "base": "https://basescan.org/tx/",
}

# Robinhood Chain (4663) is deliberately absent: Odos doesn't route it, so
# robinhood alerts render no Buy button (is_configured -> False).
ODOS_CHAIN_IDS = {"bsc": 56, "base": 8453}
EVM_RPCS = {
    "bsc": "https://bsc-dataseed1.binance.org",
    "base": "https://mainnet.base.org",
}
NATIVE_TOKEN = "0x0000000000000000000000000000000000000000"

SOL_MINT = "So11111111111111111111111111111111111111112"
JUPITER_QUOTE = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP = "https://quote-api.jup.ag/v6/swap"
JUPITER_PRICE = "https://api.jup.ag/price/v2"


@dataclass
class BuyResult:
    success: bool
    tx_hash: str = ""
    chain: str = ""
    token_address: str = ""
    amount_usd: float = 0.0
    error: str = ""
    explorer_url: str = ""


class BuyService:
    """Executes token buys across Solana, BSC, and Base."""

    def __init__(self):
        self._sol_kp: Optional[SolanaKeypair] = None  # type: ignore[name-defined]
        self._evm_acct = None
        self._init_wallets()

    # ── Setup ─────────────────────────────────────────────────────────

    def _init_wallets(self):
        sol_key = getattr(settings, "solana_private_key", None)
        if sol_key and HAS_SOLANA:
            try:
                self._sol_kp = SolanaKeypair.from_base58_string(sol_key)
                logger.info(f"🔑 Solana wallet: {self._sol_kp.pubkey()}")
            except Exception as e:
                logger.error(f"Solana wallet load failed: {e}")

        evm_key = getattr(settings, "evm_private_key", None)
        if evm_key and HAS_WEB3:
            try:
                self._evm_acct = EthAccount.from_key(evm_key)
                logger.info(f"🔑 EVM wallet: {self._evm_acct.address}")
            except Exception as e:
                logger.error(f"EVM wallet load failed: {e}")

    def is_configured(self, chain: str) -> bool:
        if chain == "solana":
            return self._sol_kp is not None
        return self._evm_acct is not None if chain in ("bsc", "base") else False

    # ── Public ────────────────────────────────────────────────────────

    async def buy(self, chain: str, token_address: str, amount_usd: float) -> BuyResult:
        if chain == "solana":
            return await self._buy_solana(token_address, amount_usd)
        if chain in ("bsc", "base"):
            return await self._buy_evm(chain, token_address, amount_usd)
        return BuyResult(success=False, error=f"Unsupported chain: {chain}")

    # ── Solana (Jupiter) ──────────────────────────────────────────────

    async def _buy_solana(self, token_address: str, amount_usd: float) -> BuyResult:
        if not self._sol_kp:
            return BuyResult(success=False, error="Solana wallet not configured")
        try:
            sol_price = await self._sol_price()
            if not sol_price:
                return BuyResult(success=False, error="Cannot fetch SOL price")

            lamports = int((amount_usd / sol_price) * 1_000_000_000)
            logger.info(f"Jupiter: ${amount_usd} ≈ {lamports} lamports → {token_address[:12]}…")

            quote = await self._jup_quote(token_address, lamports)
            if not quote:
                return BuyResult(success=False, error="No Jupiter route found")

            swap_b64 = await self._jup_swap(quote)
            if not swap_b64:
                return BuyResult(success=False, error="Jupiter swap tx failed")

            tx_hash = await self._send_sol_tx(swap_b64)
            if not tx_hash:
                return BuyResult(success=False, error="Solana RPC send failed")

            return BuyResult(
                success=True, tx_hash=tx_hash, chain="solana",
                token_address=token_address, amount_usd=amount_usd,
                explorer_url=f"{EXPLORERS['solana']}{tx_hash}",
            )
        except Exception as e:
            logger.error(f"Solana buy error: {e}")
            return BuyResult(success=False, error=str(e))

    async def _sol_price(self) -> float | None:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(JUPITER_PRICE, params={"ids": SOL_MINT})
                return float(r.json()["data"][SOL_MINT]["price"])
        except Exception as e:
            logger.error(f"SOL price error: {e}")
            return None

    async def _jup_quote(self, out_mint: str, amount: int) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(JUPITER_QUOTE, params={
                    "inputMint": SOL_MINT, "outputMint": out_mint,
                    "amount": str(amount), "slippageBps": "1000",
                    "swapMode": "ExactIn",
                })
                return r.json() if r.status_code == 200 else None
        except Exception as e:
            logger.error(f"Jupiter quote error: {e}")
            return None

    async def _jup_swap(self, quote: dict) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(JUPITER_SWAP, json={
                    "quoteResponse": quote,
                    "userPublicKey": str(self._sol_kp.pubkey()),
                    "wrapAndUnwrapSol": True,
                    "dynamicComputeUnitLimit": True,
                    "prioritizationFeeLamports": "auto",
                })
                return r.json().get("swapTransaction") if r.status_code == 200 else None
        except Exception as e:
            logger.error(f"Jupiter swap error: {e}")
            return None

    async def _send_sol_tx(self, swap_b64: str) -> str | None:
        try:
            raw = base64.b64decode(swap_b64)
            tx = VersionedTransaction.from_bytes(raw)
            signed = VersionedTransaction(tx.message, [self._sol_kp])
            encoded = base64.b64encode(bytes(signed)).decode()

            rpc = getattr(settings, "solana_rpc_url", "https://api.mainnet-beta.solana.com")
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(rpc, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "sendTransaction",
                    "params": [encoded, {
                        "encoding": "base64",
                        "skipPreflight": True,
                        "maxRetries": 3,
                        "preflightCommitment": "confirmed",
                    }],
                })
                result = r.json()
                if "result" in result:
                    return result["result"]
                logger.error(f"Solana RPC error: {result.get('error')}")
                return None
        except Exception as e:
            logger.error(f"Solana send error: {e}")
            return None

    # ── EVM (Odos aggregator) ─────────────────────────────────────────

    async def _buy_evm(self, chain: str, token_address: str, amount_usd: float) -> BuyResult:
        if not self._evm_acct:
            return BuyResult(success=False, error="EVM wallet not configured")
        chain_id = ODOS_CHAIN_IDS.get(chain)
        if not chain_id:
            return BuyResult(success=False, error=f"Unknown chain: {chain}")

        try:
            price = await self._native_price(chain)
            if not price:
                return BuyResult(success=False, error=f"Cannot fetch {chain} native price")

            wei = int((amount_usd / price) * 10**18)
            logger.info(f"Odos: ${amount_usd} ≈ {wei} wei → {token_address[:12]}… on {chain}")

            quote = await self._odos_quote(chain_id, token_address, str(wei))
            if not quote or not quote.get("pathId"):
                return BuyResult(success=False, error="No Odos route found")

            tx_data = await self._odos_assemble(quote["pathId"])
            if not tx_data:
                return BuyResult(success=False, error="Odos assemble failed")

            tx_hash = await self._send_evm_tx(chain, chain_id, tx_data)
            if not tx_hash:
                return BuyResult(success=False, error="EVM tx send failed")

            return BuyResult(
                success=True, tx_hash=tx_hash, chain=chain,
                token_address=token_address, amount_usd=amount_usd,
                explorer_url=f"{EXPLORERS.get(chain, '')}{tx_hash}",
            )
        except Exception as e:
            logger.error(f"EVM buy error ({chain}): {e}")
            return BuyResult(success=False, error=str(e))

    async def _native_price(self, chain: str) -> float | None:
        ids = {"bsc": "binancecoin", "base": "ethereum"}
        cid = ids.get(chain)
        if not cid:
            return None
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": cid, "vs_currencies": "usd"},
                )
                return r.json().get(cid, {}).get("usd")
        except Exception as e:
            logger.error(f"Native price error ({chain}): {e}")
            return None

    async def _odos_quote(self, chain_id: int, token: str, amount_wei: str) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.odos.xyz/sor/quote/v2", json={
                    "chainId": chain_id,
                    "inputTokens": [{"tokenAddress": NATIVE_TOKEN, "amount": amount_wei}],
                    "outputTokens": [{"tokenAddress": token, "proportion": 1}],
                    "userAddr": self._evm_acct.address,
                    "slippageLimitPercent": 10,
                    "referralCode": 0,
                    "disableRFQs": True,
                    "compact": True,
                })
                if r.status_code == 200:
                    return r.json()
                logger.error(f"Odos quote {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"Odos quote error: {e}")
        return None

    async def _odos_assemble(self, path_id: str) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post("https://api.odos.xyz/sor/assemble", json={
                    "userAddr": self._evm_acct.address,
                    "pathId": path_id,
                    "simulate": False,
                })
                if r.status_code == 200:
                    return r.json().get("transaction")
                logger.error(f"Odos assemble {r.status_code}: {r.text[:200]}")
        except Exception as e:
            logger.error(f"Odos assemble error: {e}")
        return None

    async def _send_evm_tx(self, chain: str, chain_id: int, tx_data: dict) -> str | None:
        try:
            w3 = Web3(Web3.HTTPProvider(EVM_RPCS[chain]))

            def _int(v, default=0):
                if isinstance(v, int):
                    return v
                if isinstance(v, str):
                    return int(v, 16) if v.startswith("0x") else int(v)
                return default

            tx = {
                "to": Web3.to_checksum_address(tx_data["to"]),
                "data": tx_data["data"],
                "value": _int(tx_data.get("value", 0)),
                "gas": _int(tx_data.get("gas", 300_000)),
                "gasPrice": _int(tx_data.get("gasPrice", 0)) or w3.eth.gas_price,
                "nonce": w3.eth.get_transaction_count(self._evm_acct.address),
                "chainId": chain_id,
            }

            signed = w3.eth.account.sign_transaction(tx, self._evm_acct.key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            return tx_hash.hex()
        except Exception as e:
            logger.error(f"EVM send error ({chain}): {e}")
            return None

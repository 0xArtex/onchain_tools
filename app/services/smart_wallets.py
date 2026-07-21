"""Smart-wallet activity tracker.

Polls a configured set of "smart" wallets for their *current* token holdings and
answers "which of these wallets currently hold token X on chain C?". The launch
monitor uses this to filter alerts down to tokens that tracked smart money is
already in.

Data sources (current holdings):
  - Solana : Helius RPC ``getTokenAccountsByOwner`` (needs HELIUS_API_KEY)
  - EVM    : Alchemy ``alchemy_getTokenBalances`` on Base + BNB + Robinhood Chain
             (needs ALCHEMY_API_KEY)

Design notes:
  - OFF by default: if no wallets are configured the tracker is disabled and the
    launch monitor behaves exactly as before.
  - FAIL-OPEN: if a provider key is missing or a refresh errors, the affected
    chain reports "unknown" and the filter lets tokens through (with a warning)
    rather than silently dropping every alert.
  - Holdings are refreshed on a TTL and cached as a ``chain -> {token -> {wallets}}``
    map, so the per-token check during a scan is just a dict lookup.
"""
from __future__ import annotations

import asyncio
import math
import re
import time

import httpx

from ..config import settings
from ..logging_config import logger

_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# SPL Token + Token-2022 program ids (a wallet can hold either kind)
_SOL_TOKEN_PROGRAMS = [
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
]
# launch-monitor chain id -> Alchemy network slug
_ALCHEMY_NETWORKS = {
    "base": "base-mainnet",
    "bsc": "bnb-mainnet",
    "robinhood": "robinhood-mainnet",
}
_EVM_CHAINS = ("base", "bsc", "robinhood")
_ALL_CHAINS = ("solana", *_EVM_CHAINS)
_ZERO_HEX = {"", "0x", "0x0", "0x" + "0" * 64}


def classify_wallet(addr: str) -> str | None:
    """Return 'evm', 'solana', or None for an address string."""
    a = (addr or "").strip()
    if _EVM_RE.match(a):
        return "evm"
    if _BASE58_RE.match(a):
        return "solana"
    return None


def parse_wallets(raw: str) -> dict[str, list[str]]:
    """Parse a raw config string into ``{'solana': [...], 'evm': [...]}``.

    Accepts comma / whitespace / newline separated addresses. EVM addresses are
    lowercased; unrecognized tokens are warned about and skipped.
    """
    out: dict[str, list[str]] = {"solana": [], "evm": []}
    if not raw:
        return out
    for tok in re.split(r"[\s,]+", raw.strip()):
        if not tok:
            continue
        kind = classify_wallet(tok)
        if kind == "evm":
            out["evm"].append(tok.lower())
        elif kind == "solana":
            out["solana"].append(tok)
        else:
            logger.warning(f"SmartWallets: ignoring unrecognized address {tok!r}")
    # de-dupe, preserve order
    out["solana"] = list(dict.fromkeys(out["solana"]))
    out["evm"] = list(dict.fromkeys(out["evm"]))
    return out


class SmartWalletTracker:
    def __init__(self) -> None:
        self.wallets = parse_wallets(getattr(settings, "launch_smart_wallets", "") or "")
        self.min_count = int(getattr(settings, "launch_min_smart_wallets", 0) or 0)
        self.min_pct = float(getattr(settings, "launch_min_smart_wallet_pct", 0.0) or 0.0)
        self.refresh_seconds = int(getattr(settings, "launch_smart_wallet_refresh_seconds", 60) or 60)
        self.helius_key = getattr(settings, "helius_api_key", None)
        self.alchemy_key = getattr(settings, "alchemy_api_key", None)
        # Chains the launch monitor actually scans (per-chain .env toggles).
        # A disabled chain is never queried by the scan loop, so skip its
        # holdings fetches too instead of burning provider quota on it.
        self.active_chains = tuple(c for c in _ALL_CHAINS if getattr(settings, c, True))

        self.has_solana = bool(self.wallets["solana"])
        self.has_evm = bool(self.wallets["evm"])
        self.enabled = self.has_solana or self.has_evm
        # Once wallets are configured, default to "at least 1 must hold it".
        if self.enabled and self.min_count <= 0 and self.min_pct <= 0:
            self.min_count = 1

        # chain -> { token_address -> set(wallet) }   (evm addrs lowercased)
        self._map: dict[str, dict[str, set[str]]] = {}
        # chain -> whether the last refresh produced usable data (else fail-open)
        self._healthy: dict[str, bool] = {}
        # chain -> whether ALL applicable wallet fetches succeeded (complete picture)
        self._complete: dict[str, bool] = {}
        self._last_refresh = 0.0
        self._lock = asyncio.Lock()

    # ── introspection ────────────────────────────────────────────────
    def describe(self) -> str:
        if not self.enabled:
            return "SmartWallets: disabled (no LAUNCH_SMART_WALLETS configured)"
        parts = [
            f"SmartWallets: ENABLED | solana={len(self.wallets['solana'])} evm={len(self.wallets['evm'])}",
            f"min_count={self.min_count}",
            f"min_pct={self.min_pct or 'off'}",
            f"refresh={self.refresh_seconds}s",
        ]
        if self.has_solana and not self.helius_key:
            parts.append("⚠️ HELIUS_API_KEY missing -> Solana checks fail-open")
        if self.has_evm and not self.alchemy_key:
            parts.append("⚠️ ALCHEMY_API_KEY missing -> EVM checks fail-open")
        if self.min_count > 0:
            if self.has_solana and self.min_count > len(self.wallets["solana"]):
                parts.append(f"⚠️ min_count {self.min_count} > {len(self.wallets['solana'])} solana wallets (nothing will pass)")
            if self.has_evm and self.min_count > len(self.wallets["evm"]):
                parts.append(f"⚠️ min_count {self.min_count} > {len(self.wallets['evm'])} evm wallets (nothing will pass)")
        return " | ".join(parts)

    def _applicable_wallets(self, chain: str) -> list[str]:
        return self.wallets["solana"] if chain == "solana" else self.wallets["evm"]

    def required_for(self, chain: str) -> int:
        """Minimum number of holding wallets a token needs to pass, for ``chain``."""
        applicable = len(self._applicable_wallets(chain))
        req = self.min_count
        if self.min_pct > 0 and applicable > 0:
            req = max(req, math.ceil(self.min_pct / 100.0 * applicable))
        return max(req, 1)

    # ── refresh ──────────────────────────────────────────────────────
    async def refresh_if_stale(self) -> None:
        if not self.enabled:
            return
        if self._map and (time.monotonic() - self._last_refresh) < self.refresh_seconds:
            return
        async with self._lock:
            if self._map and (time.monotonic() - self._last_refresh) < self.refresh_seconds:
                return
            await self._refresh()
            self._last_refresh = time.monotonic()

    async def _refresh(self) -> None:
        new_map: dict[str, dict[str, set[str]]] = {c: {} for c in _ALL_CHAINS}
        # (chain, wallet, coro) jobs
        jobs: list[tuple[str, str, "asyncio.Future"]] = []
        if self.has_solana and self.helius_key and "solana" in self.active_chains:
            for w in self.wallets["solana"]:
                jobs.append(("solana", w, self._fetch_solana_holdings(w)))
        if self.has_evm and self.alchemy_key:
            for chain in _EVM_CHAINS:
                if chain not in self.active_chains:
                    continue
                for w in self.wallets["evm"]:
                    jobs.append((chain, w, self._fetch_evm_holdings(chain, w)))

        results = await asyncio.gather(*(c for _, _, c in jobs), return_exceptions=True)

        ok_counts: dict[str, int] = {c: 0 for c in _ALL_CHAINS}
        err_counts: dict[str, int] = {c: 0 for c in _ALL_CHAINS}
        for (chain, wallet, _), res in zip(jobs, results):
            if isinstance(res, Exception):
                err_counts[chain] += 1
                logger.warning(f"SmartWallets: holdings fetch failed for {wallet[:10]}… on {chain}: {res}")
                continue
            ok_counts[chain] += 1
            for token in res:
                new_map[chain].setdefault(token, set()).add(wallet)

        # "healthy" = at least one wallet fetch succeeded (some data to use).
        self._healthy = {c: ok_counts[c] > 0 for c in new_map}
        # "complete" = every attempted fetch for the chain succeeded, so the holders
        # set is the full picture and a below-threshold token may be dropped.
        self._complete = {c: (ok_counts[c] > 0 and err_counts[c] == 0) for c in new_map}
        self._map = new_map
        active = [c for c in _ALL_CHAINS if ok_counts[c] or err_counts[c]]
        logger.info(
            "SmartWallets refresh: "
            + ", ".join(f"{c}: {ok_counts[c]} ok/{err_counts[c]} err, {len(new_map[c])} tokens" for c in active)
            if active else "SmartWallets refresh: no provider calls (missing keys?)"
        )

    # ── queries ──────────────────────────────────────────────────────
    async def holders_of(self, chain: str, token_address: str | None) -> set[str] | None:
        """Configured wallets currently holding ``token_address`` on ``chain``.

        Returns ``None`` when holdings data for that chain is unavailable (caller
        should fail open). Returns a (possibly empty) set when data is available.
        """
        if not token_address:
            return None
        if not self._healthy.get(chain, False):
            return None
        token = token_address if chain == "solana" else token_address.lower()
        return set(self._map.get(chain, {}).get(token, set()))

    def passes(self, chain: str, holders: set[str] | None) -> tuple[bool, int, int]:
        """Return ``(passed, holder_count, required)``.

        Fail-open rules: ``holders=None`` (no data for the chain) passes with
        count -1. If a token is below threshold but the chain's holdings are
        INCOMPLETE (some wallet fetches failed this refresh), pass it anyway
        rather than risk dropping a token a missing wallet might hold.
        """
        required = self.required_for(chain)
        if holders is None:
            return True, -1, required
        if len(holders) >= required:
            return True, len(holders), required
        if not self._complete.get(chain, True):
            return True, len(holders), required  # incomplete data -> fail open
        return False, len(holders), required

    # ── providers ────────────────────────────────────────────────────
    async def _fetch_solana_holdings(self, wallet: str) -> set[str]:
        """SPL mints with a positive balance for a Solana wallet (via Helius)."""
        url = f"https://mainnet.helius-rpc.com/?api-key={self.helius_key}"
        mints: set[str] = set()
        async with httpx.AsyncClient(timeout=15.0) as client:
            for program in _SOL_TOKEN_PROGRAMS:
                payload = {
                    "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
                    "params": [wallet, {"programId": program}, {"encoding": "jsonParsed"}],
                }
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                # JSON-RPC errors come back as HTTP 200 with an `error` body; raise so
                # the caller counts this as a failure (-> fail open), not "0 held".
                if data.get("error"):
                    raise RuntimeError(f"Helius RPC error: {data['error']}")
                for acc in ((data.get("result") or {}).get("value") or []):
                    info = (((acc.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
                    # Decide from the raw integer amount (always present/exact);
                    # uiAmount is a float that can be null for valid balances.
                    raw = str((info.get("tokenAmount") or {}).get("amount") or "0")
                    mint = info.get("mint")
                    if mint and raw.isdigit() and int(raw) > 0:
                        mints.add(mint)
        return mints

    async def _fetch_evm_holdings(self, chain: str, wallet: str) -> set[str]:
        """ERC-20 contracts with a non-zero balance for an EVM wallet (via Alchemy)."""
        net = _ALCHEMY_NETWORKS[chain]
        url = f"https://{net}.g.alchemy.com/v2/{self.alchemy_key}"
        held: set[str] = set()
        page_key = None
        async with httpx.AsyncClient(timeout=15.0) as client:
            for _ in range(10):  # bounded pagination
                params: list = [wallet, "erc20"]
                if page_key:
                    params = [wallet, "erc20", {"pageKey": page_key}]
                payload = {"jsonrpc": "2.0", "id": 1, "method": "alchemy_getTokenBalances", "params": params}
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                body = resp.json()
                # JSON-RPC errors arrive as HTTP 200 with an `error` body; raise so the
                # caller counts this as a failure (-> fail open), not "0 held".
                if body.get("error"):
                    raise RuntimeError(f"Alchemy RPC error: {body['error']}")
                result = body.get("result") or {}
                for b in (result.get("tokenBalances") or []):
                    if b.get("error"):  # per-token fetch failure -> skip, don't count as 0
                        logger.debug(f"SmartWallets: token balance error on {chain}: {b['error']}")
                        continue
                    contract = b.get("contractAddress")
                    bal = (b.get("tokenBalance") or "0x0").strip()
                    if not contract or bal in _ZERO_HEX:
                        continue
                    try:
                        if int(bal, 16) > 0:
                            held.add(contract.lower())
                    except ValueError:
                        logger.debug(f"SmartWallets: malformed balance {bal!r} for {contract}")
                page_key = result.get("pageKey")
                if not page_key:
                    break
        return held


__all__ = ["SmartWalletTracker", "classify_wallet", "parse_wallets"]

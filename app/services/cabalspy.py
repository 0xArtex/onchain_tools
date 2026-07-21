"""CabalSpy labeled-wallet enrichment.

CabalSpy (cabalspy.xyz) tracks hand-labeled KOL / Smart Money / Whale wallets
across Solana, BNB, Base, ETH and Robinhood Chain. Before the launch monitor
fires an alert, this service asks CabalSpy which labeled wallets already bought
the candidate token and derives a 0-100 legitimacy score — the more (and the
higher-quality, still-holding) smart buyers, the higher the score.

Design notes:
  - OFF unless CABALSPY_API_KEY is set (free key: apidashboard.cabalspy.xyz).
    Scoped to CABALSPY_CHAINS — default "robinhood" only, to start small.
  - FAIL-OPEN: any API failure returns None and the alert goes out unscored.
  - Credit-aware: the free tier is 10,000 credits/month and one REST call
    costs 10. In enrich-only mode (CABALSPY_MIN_BUYERS=0) a candidate costs
    exactly one call — it alerts immediately and is deduped. With the
    min-buyers gate on, a still-pending token is re-checked as its cache
    expires, capped at CABALSPY_MAX_CHECKS metered calls per token; after
    that the last result is served from cache.
  - Chain codes: CabalSpy uses "rh" for Robinhood and "bnb" for BSC. Their
    REST docs lag the 2026-07-10 Robinhood launch, so when the API rejects a
    code we retry the documented alias once and remember which one worked.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from ..config import settings
from ..logging_config import logger

API_BASE = "https://api.cabalspy.xyz/v1"

# launch-monitor chain id -> CabalSpy blockchain codes to try, in order.
CHAIN_CODES = {
    "solana": ["solana"],
    "bsc": ["bnb"],
    "base": ["base"],
    "robinhood": ["rh", "robinhood"],
}

# Score weights: what one distinct buyer of each label is worth. A buyer who
# already exited (still_holding false) counts half — smart money that dumped
# is a much weaker legitimacy signal than smart money still in the token.
BUYER_WEIGHTS = {"kol": 3.0, "smart": 3.0, "insider": 2.0, "whale": 2.0}
DEFAULT_WEIGHT = 1.0
EXITED_FACTOR = 0.5
SCORE_SCALE = 10.0  # 3 quality still-holding buyers ≈ score 90-100

CACHE_TTL_SECONDS = 300


@dataclass
class CabalReport:
    score: int
    total_buyers: int
    kol_buyers: int
    smart_buyers: int
    other_buyers: int
    still_holding: int
    buyers: list[dict] = field(default_factory=list)  # {wallet,name,twitter,type,still_holding}
    chain_code: str = ""

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "total_buyers": self.total_buyers,
            "kol_buyers": self.kol_buyers,
            "smart_buyers": self.smart_buyers,
            "other_buyers": self.other_buyers,
            "still_holding": self.still_holding,
            "buyers": self.buyers,
        }

    def summary(self) -> str:
        parts = []
        if self.kol_buyers:
            parts.append(f"{self.kol_buyers} KOL")
        if self.smart_buyers:
            parts.append(f"{self.smart_buyers} smart")
        if self.other_buyers:
            parts.append(f"{self.other_buyers} other")
        label = " + ".join(parts) if parts else "no labeled"
        return f"score {self.score}/100 ({label} buyer{'s' if self.total_buyers != 1 else ''})"


class CabalSpyService:
    def __init__(self) -> None:
        self.api_key = (getattr(settings, "cabalspy_api_key", None) or "").strip()
        raw_chains = getattr(settings, "cabalspy_chains", "robinhood") or ""
        self.chains = {c.strip().lower() for c in raw_chains.split(",") if c.strip()}
        self.wallet_type = (getattr(settings, "cabalspy_wallet_type", "") or "").strip().lower()
        self.min_buyers = int(getattr(settings, "cabalspy_min_buyers", 0) or 0)
        self.tx_limit = int(getattr(settings, "cabalspy_tx_limit", 100) or 100)
        self.max_checks = max(1, int(getattr(settings, "cabalspy_max_checks", 6) or 6))
        self.enabled = bool(self.api_key)

        # chain -> code last accepted by the live REST API (tried first)
        self._working_code: dict[str, str] = {}
        # token cache: (chain, token) -> (monotonic_ts, report, fetch_count)
        self._cache: dict[tuple[str, str], tuple[float, CabalReport | None, int]] = {}

    # ── introspection ────────────────────────────────────────────────
    def describe(self) -> str:
        if not self.enabled:
            return "CabalSpy: disabled (no CABALSPY_API_KEY)"
        unknown = self.chains - set(CHAIN_CODES)
        return (
            f"CabalSpy: ENABLED | chains={sorted(self.chains)}"
            + (f" (⚠️ unsupported: {sorted(unknown)})" if unknown else "")
            + f" | wallet_type={self.wallet_type or 'all'}"
            + f" | min_buyers={self.min_buyers or 'off (enrich only)'}"
        )

    def covers(self, chain: str) -> bool:
        return self.enabled and chain in self.chains and chain in CHAIN_CODES

    def passes(self, report: CabalReport | None) -> bool:
        """Optional gate: require ≥ CABALSPY_MIN_BUYERS labeled buyers.

        Fail-open on missing data (report None) so a CabalSpy outage or an
        uncovered chain never suppresses alerts.
        """
        if self.min_buyers <= 0 or report is None:
            return True
        return report.total_buyers >= self.min_buyers

    # ── scoring ──────────────────────────────────────────────────────
    @staticmethod
    def score_buyers(buyers: list[dict]) -> int:
        points = 0.0
        for b in buyers:
            weight = BUYER_WEIGHTS.get((b.get("type") or "").lower(), DEFAULT_WEIGHT)
            if not b.get("still_holding", True):
                weight *= EXITED_FACTOR
            points += weight
        return min(100, round(points * SCORE_SCALE))

    # ── API ──────────────────────────────────────────────────────────
    async def analyze(self, chain: str, token_address: str) -> CabalReport | None:
        """Labeled-wallet buys of ``token_address`` on ``chain``, scored.

        Returns None (fail-open) when disabled, uncovered, or on any API error.
        """
        if not token_address or not self.covers(chain):
            return None

        cache_key = (chain, token_address.lower())
        now = time.monotonic()
        fetch_count = 0
        cached = self._cache.get(cache_key)
        if cached:
            ts, report, fetch_count = cached
            if (now - ts) < CACHE_TTL_SECONDS:
                return report
            if fetch_count >= self.max_checks:
                # Re-check budget for this token is spent (min-buyers gate can
                # re-query a pending token every cache expiry) — serve the
                # last result instead of burning more credits.
                return report

        report = await self._fetch_report(chain, token_address)
        self._cache[cache_key] = (now, report, fetch_count + 1)
        # Bounded cache: entries must outlive the lookback window so the
        # per-token fetch budget holds; prune only clearly dead ones.
        if len(self._cache) > 1000:
            cutoff = time.monotonic() - 6 * 3600
            self._cache = {k: v for k, v in self._cache.items() if v[0] > cutoff}
        return report

    async def _fetch_report(self, chain: str, token_address: str) -> CabalReport | None:
        # Try the last code the API accepted first, but keep the aliases as
        # fallback — a cached code the API stops accepting must not silently
        # kill enrichment until restart.
        codes = list(CHAIN_CODES[chain])
        cached_code = self._working_code.get(chain)
        if cached_code in codes:
            codes = [cached_code] + [c for c in codes if c != cached_code]
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "User-Agent": "onchain-tools/1.0",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                for code in codes:
                    params = {
                        "blockchain": code,
                        "mint": token_address,
                        "limit": self.tx_limit,
                    }
                    if self.wallet_type:
                        params["type"] = self.wallet_type
                    resp = await client.get(
                        f"{API_BASE}/tokens/transactions", params=params, headers=headers
                    )
                    if resp.status_code == 200:
                        body = resp.json()
                        # A 200 soft-failure envelope must fail OPEN, not
                        # parse as "zero buyers" (which the min-buyers gate
                        # would turn into a suppressed alert).
                        if isinstance(body, dict) and body.get("success") is False:
                            logger.warning(
                                f"CabalSpy: soft failure for blockchain={code!r}: "
                                f"{str(body)[:200]} — failing open"
                            )
                            return None
                        self._working_code[chain] = code
                        return self._parse_transactions(body, code)
                    if resp.status_code == 400:
                        # Likely the chain code the REST layer doesn't accept
                        # yet (docs lag Robinhood) — try the next alias.
                        logger.debug(
                            f"CabalSpy: 400 for blockchain={code!r}: {resp.text[:200]}"
                        )
                        if self._working_code.get(chain) == code:
                            del self._working_code[chain]
                        continue
                    if resp.status_code in (401, 403):
                        logger.warning(
                            f"CabalSpy: auth/credits problem ({resp.status_code}): "
                            f"{resp.text[:200]} — failing open"
                        )
                        return None
                    if resp.status_code == 429:
                        logger.warning("CabalSpy: rate limited — failing open")
                        return None
                    logger.warning(
                        f"CabalSpy: unexpected {resp.status_code}: {resp.text[:200]}"
                    )
                    return None
            logger.warning(
                f"CabalSpy: no accepted blockchain code for {chain} "
                f"(tried {codes}) — failing open"
            )
            return None
        except Exception as e:
            logger.warning(f"CabalSpy: fetch failed for {token_address[:12]}… on {chain}: {e}")
            return None

    def _parse_transactions(self, body: dict, chain_code: str) -> CabalReport:
        txs = ((body or {}).get("data") or {}).get("transactions") or []
        # Newest-first feed. A row's holdings_after is the wallet's position
        # AFTER that transaction — a buy row is therefore always "holding";
        # the exit signal only ever appears on SELL rows. So take each
        # wallet's position from its newest row of ANY type, and count a
        # wallet as a buyer only if it has at least one buy row.
        latest: dict[str, dict] = {}
        bought: set[str] = set()
        for tx in txs:
            profile = tx.get("profile") or {}
            wallet = (tx.get("wallet_address") or profile.get("wallet_address") or "").lower()
            key = wallet or (profile.get("name") or "").lower()
            if not key:
                continue
            if key not in latest:
                holdings = tx.get("holdings_after") or {}
                latest[key] = {
                    "wallet": wallet,
                    "name": profile.get("name") or "",
                    "twitter": profile.get("twitter") or "",
                    "type": (profile.get("type") or "").lower(),
                    "still_holding": bool(holdings.get("still_holding", True)),
                }
            if (tx.get("transaction_type") or "").lower() == "buy":
                bought.add(key)

        buyer_list = [info for key, info in latest.items() if key in bought]
        kol = sum(1 for b in buyer_list if b["type"] == "kol")
        smart = sum(1 for b in buyer_list if b["type"] == "smart")
        return CabalReport(
            score=self.score_buyers(buyer_list),
            total_buyers=len(buyer_list),
            kol_buyers=kol,
            smart_buyers=smart,
            other_buyers=len(buyer_list) - kol - smart,
            still_holding=sum(1 for b in buyer_list if b["still_holding"]),
            buyers=buyer_list,
            chain_code=chain_code,
        )


__all__ = ["CabalSpyService", "CabalReport", "CHAIN_CODES", "BUYER_WEIGHTS"]


if __name__ == "__main__":
    # Manual live check once you have a key in .env:
    #   python -m app.services.cabalspy robinhood 0x8f100e99dDF699320724e37Cb866770381d47382
    import asyncio
    import sys

    async def _main() -> None:
        chain, token = sys.argv[1], sys.argv[2]
        svc = CabalSpyService()
        print(svc.describe())
        report = await svc.analyze(chain, token)
        if report is None:
            print("no report (disabled, uncovered chain, or API failure — see log)")
            return
        print(report.summary(), f"| chain_code={report.chain_code}")
        for b in report.buyers:
            hold = "holding" if b["still_holding"] else "sold"
            print(f"  {b['type'] or '?':7} {b['name'] or b['wallet'][:10]} ({b['twitter']}) {hold}")

    asyncio.run(_main())

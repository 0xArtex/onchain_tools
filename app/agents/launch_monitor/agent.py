from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx

from ..base import BaseAgent
from ...config import settings
from ...logging_config import logger


class LaunchConfig:
    """Configuration for launch monitor agent"""
    CHAINS = ["bsc", "solana", "base"]
    POLL_SECONDS = 30
    LOOKBACK_HOURS = 1
    MIN_LIQUIDITY = 6000
    MIN_MARKET_CAP = 0
    MAX_MARKET_CAP = 0
    MIN_TWITTER_FOLLOWERS = 0
    TOP_N_FOR_NO_TIME = 50
    REQUIRE_TWITTER = True
    REQUIRE_WEBSITE = False


class TelegramService:
    """Service for sending Telegram notifications"""
    
    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token and chat_id)
    
    async def send_message(self, message: str, image_url: str | None = None) -> bool:
        """Send message to Telegram"""
        if not self.enabled:
            logger.debug("Telegram not configured, skipping notification")
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}"
            
            payload: dict[str, Any] = {
                "chat_id": self.chat_id,
                "parse_mode": "HTML",
            }
            
            # Send photo with caption or text message
            if image_url:
                url += "/sendPhoto"
                payload["photo"] = image_url
                payload["caption"] = message
            else:
                url += "/sendMessage"
                payload["text"] = message
                payload["disable_web_page_preview"] = False
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
                result = response.json()
                
                if result.get("ok"):
                    logger.info("✅ Telegram message sent successfully")
                    return True
                else:
                    logger.error(f"❌ Telegram error: {result.get('description', 'Unknown')}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Telegram send failed: {e}")
            return False
    
    @staticmethod
    def _esc(text: str) -> str:
        """Escape HTML special characters in user content"""
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
    
    def format_token_message(self, token: dict) -> tuple[str, str | None]:
        """Format token data as Telegram message"""
        base_sym = self._esc(token.get("base_symbol", "UNKNOWN"))
        quote_sym = self._esc(token.get("quote_symbol", "?"))
        chain = self._esc(token.get("chain", "").upper())
        liquidity = token.get("liquidity_usd", 0)
        price = token.get("price_usd")
        market_cap = token.get("market_cap", 0)
        volume = token.get("volume_24h")
        age = token.get("age_minutes")
        twitter_url = token.get("twitter_url", "")
        followers = token.get("twitter_followers")
        dex_url = token.get("dex_url", "")
        pair_id = self._esc(token.get("pair_id", ""))
        
        message = f"🚀 <b>NEW TOKEN ALERT</b> 🚀\n\n"
        message += f"<b>{base_sym}/{quote_sym}</b>\n"
        message += f"━━━━━━━━━━━━━━━━━━━━\n"
        message += f"⛓ <b>Chain:</b> {chain}\n"
        
        if price:
            message += f"💰 <b>Price:</b> ${float(price):.8f}\n"
        
        message += f"💧 <b>Liquidity:</b> ${liquidity:,.0f}\n"
        
        if market_cap > 0:
            message += f"📊 <b>Market Cap:</b> ${market_cap:,.0f}\n"
        
        if volume:
            message += f"📈 <b>Volume 24h:</b> ${float(volume):,.0f}\n"
        
        if age is not None:
            if age < 60:
                message += f"⏰ <b>Age:</b> {age}m\n"
            else:
                hours = age // 60
                mins = age % 60
                message += f"⏰ <b>Age:</b> {hours}h {mins}m\n"
        
        # Rug check section (Solana only)
        rug_check = token.get("rug_check")
        if rug_check:
            level = rug_check.get("risk_level", "unknown")
            score = rug_check.get("score_normalised", -1)
            lp_locked = rug_check.get("lp_locked_pct", 0)
            top_risks = rug_check.get("top_risks", [])
            level_emoji = {"good": "🟢", "warn": "🟡", "danger": "🔴"}.get(level, "⚪")
            
            message += f"\n⚠️ <b>Risk:</b> {level_emoji} {score}/100 | "
            message += f"🔒 <b>LP Locked:</b> {lp_locked:.0f}%\n"
            
            if top_risks:
                for rname, rlevel in top_risks:
                    r_emoji = {"danger": "🔴", "warn": "🟡"}.get(rlevel, "⚪")
                    message += f"  {r_emoji} {self._esc(rname)}\n"
        
        message += f"\n"
        
        # Twitter/X profile section
        profile = token.get("twitter_profile")
        if profile and twitter_url:
            tw_user = self._esc(profile.get("username", ""))
            tw_name = self._esc(profile.get("name", ""))
            tw_bio = self._esc(profile.get("bio", ""))
            safe_twitter = self._esc(twitter_url)
            
            message += f'🐦 <a href="{safe_twitter}"><b>@{tw_user}</b></a>'
            if tw_name and tw_name != tw_user:
                message += f" ({tw_name})"
            if followers:
                message += f" • {followers:,} followers"
            message += "\n"
            
            if tw_bio:
                message += f"<i>{tw_bio}</i>\n"
        elif twitter_url:
            safe_twitter = self._esc(twitter_url)
            message += f'🐦 <a href="{safe_twitter}">Twitter/X</a>'
            if followers:
                message += f" ({followers:,} followers)"
            message += "\n"
        
        message += "\n"
        
        if dex_url:
            safe_dex = self._esc(dex_url)
            message += f'📈 <a href="{safe_dex}">View on DexScreener</a>\n'
        
        message += f"\n<code>{pair_id}</code>"
        
        # Use banner or profile pic as the photo
        image_url = None
        if profile:
            image_url = profile.get("banner_url") or profile.get("profile_image_url") or None
        
        return message, image_url


class TwitterService:
    """Service for fetching Twitter/X account information"""
    
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or ""
        
    @staticmethod
    def extract_username(twitter_url: str | None) -> str | None:
        """Extract Twitter username from various URL formats"""
        if not twitter_url or not isinstance(twitter_url, str):
            return None
        
        patterns = [
            # Tweet URL: https://x.com/username/status/123
            r"(?:twitter|x)\.com/([^/?]+)/status/\d+",
            # Profile URL: https://x.com/username
            r"(?:twitter|x)\.com/([^/?]+)",
            # Just username with or without @
            r"@?([a-zA-Z0-9_]+)$",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, twitter_url, re.IGNORECASE)
            if match:
                username = match.group(1).replace("@", "").strip()
                # Filter out common Twitter paths
                if username.lower() not in ["home", "explore", "notifications", "messages", "i", "settings"]:
                    return username
        return None
    
    async def get_profile(self, username: str) -> dict | None:
        """Fetch full Twitter profile for a user.
        
        Returns dict with keys: followers, username, name, bio, 
        profile_image_url, banner_url (or None on failure).
        """
        if not self.api_key or self.api_key == "YOUR_TWITTERAPI_KEY_HERE":
            logger.debug("TwitterAPI key not configured, skipping profile fetch")
            return None
        
        try:
            await asyncio.sleep(0.1)  # Rate limit respect
            
            url = f"https://api.twitterapi.io/twitter/user/{username}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    url,
                    headers={
                        "X-API-Key": self.api_key,
                        "User-Agent": "onchain-tools/1.0"
                    }
                )
                
                if response.status_code != 200:
                    logger.warning(f"TwitterAPI error ({response.status_code}) for @{username}")
                    return None
                
                result = response.json()
                
                if result.get("status") == "error":
                    logger.warning(f"TwitterAPI error: {result.get('message', 'Unknown')}")
                    return None
                
                if result and result.get("data"):
                    data = result["data"]
                    followers = data.get("followers", 0)
                    logger.info(f"@{username} has {followers:,} followers")
                    
                    # Get high-res profile image (replace _normal with _400x400)
                    pfp = data.get("profileImageUrl") or data.get("avatar") or ""
                    if pfp:
                        pfp = pfp.replace("_normal.", "_400x400.")
                    
                    return {
                        "followers": followers,
                        "username": data.get("userName") or data.get("screenName") or username,
                        "name": data.get("name") or "",
                        "bio": data.get("description") or data.get("bio") or "",
                        "profile_image_url": pfp,
                        "banner_url": data.get("profileBannerUrl") or data.get("coverImageUrl") or "",
                    }
                
                return None
                
        except Exception as e:
            logger.warning(f"Failed to fetch profile for @{username}: {e}")
            return None


class RugCheckService:
    """Service for checking token risk via RugCheck.xyz API (Solana only)"""
    
    API_BASE = "https://api.rugcheck.xyz/v1"
    
    async def get_summary(self, mint_address: str) -> dict | None:
        """Get risk summary for a Solana token.
        
        Returns dict with keys: score, score_normalised, risks, lp_locked_pct, risk_level
        Or None on failure.
        """
        try:
            url = f"{self.API_BASE}/tokens/{mint_address}/report/summary"
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
                
                if response.status_code != 200:
                    logger.warning(f"RugCheck error ({response.status_code}) for {mint_address[:12]}...")
                    return None
                
                data = response.json()
                score_norm = data.get("score_normalised", -1)
                
                # Determine risk level
                if score_norm < 0:
                    risk_level = "unknown"
                elif score_norm <= 30:
                    risk_level = "good"
                elif score_norm <= 60:
                    risk_level = "warn"
                else:
                    risk_level = "danger"
                
                # Extract top risks (max 3)
                risks = data.get("risks", [])
                top_risks = []
                for r in sorted(risks, key=lambda x: x.get("score", 0), reverse=True)[:3]:
                    name = r.get("name", "")
                    value = r.get("value", "")
                    level = r.get("level", "")
                    if name:
                        entry = name
                        if value:
                            entry += f" ({value})"
                        top_risks.append((entry, level))
                
                return {
                    "score": data.get("score", 0),
                    "score_normalised": score_norm,
                    "lp_locked_pct": data.get("lpLockedPct", 0),
                    "risk_level": risk_level,
                    "top_risks": top_risks,
                }
                
        except Exception as e:
            logger.warning(f"RugCheck failed for {mint_address[:12]}...: {e}")
            return None
    
    @staticmethod
    def format_risk_label(summary: dict) -> str:
        """Format risk summary as a compact label for Telegram messages."""
        level = summary.get("risk_level", "unknown")
        score = summary.get("score_normalised", -1)
        lp_locked = summary.get("lp_locked_pct", 0)
        top_risks = summary.get("top_risks", [])
        
        # Risk emoji
        level_emoji = {"good": "🟢", "warn": "🟡", "danger": "🔴"}.get(level, "⚪")
        
        lines = []
        lines.append(f"⚠️ <b>Risk:</b> {level_emoji} {score}/100")
        lines.append(f"🔒 <b>LP Locked:</b> {lp_locked:.0f}%")
        
        if top_risks:
            risk_strs = []
            for name, rlevel in top_risks:
                r_emoji = {"danger": "🔴", "warn": "🟡"}.get(rlevel, "⚪")
                risk_strs.append(f"{r_emoji} {name}")
            lines.append("\n".join(risk_strs))
        
        return "\n".join(lines)


class LaunchMonitorAgent(BaseAgent):
    name = "launch_monitor"
    request_channel = "launch_monitor:request"
    publish_channel = "new_token_alert"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = LaunchConfig()
        self.twitter = TwitterService(api_key=getattr(settings, "twitterapi_key", None))
        self.rugcheck = RugCheckService()
        self.telegram = TelegramService(
            bot_token=getattr(settings, "telegram_bot_token", None),
            chat_id=getattr(settings, "telegram_chat_id", None)
        )
        self._monitoring = False
        self._cache_ttl = (self.config.LOOKBACK_HOURS * 60 * 60) + 300
        
    async def on_start(self) -> None:
        logger.info("LaunchMonitorAgent starting...")
        logger.info(f"Using Redis cache with TTL: {self._cache_ttl}s ({self._cache_ttl/3600:.1f} hours)")
        # Start monitoring loop
        self._monitoring = True
        asyncio.create_task(self._monitor_loop())
        
    async def on_stop(self) -> None:
        logger.info("LaunchMonitorAgent stopping...")
        self._monitoring = False
        
    async def _is_seen(self, chain: str, pair_id: str) -> bool:
        """Check if token pair has been seen (using Redis cache with TTL)"""
        try:
            key = f"launch_monitor:seen:{chain}:{pair_id}"
            result = await self.mq._redis.get(key)
            return result is not None
        except Exception as e:
            logger.warning(f"Redis check failed: {e}, assuming not seen")
            return False
    
    async def _mark_seen(self, chain: str, pair_id: str) -> None:
        """Mark token pair as seen (auto-expires after TTL)"""
        try:
            key = f"launch_monitor:seen:{chain}:{pair_id}"
            await self.mq._redis.setex(key, self._cache_ttl, "1")
            logger.debug(f"Marked {pair_id} as seen (TTL: {self._cache_ttl}s)")
        except Exception as e:
            logger.error(f"Failed to mark as seen: {e}")
    
    async def _monitor_loop(self) -> None:
        """Main monitoring loop"""
        while self._monitoring:
            try:
                await self._scan_once()
            except Exception as e:
                logger.error(f"Scan failed: {e}")
            
            # Wait for next poll
            await asyncio.sleep(self.config.POLL_SECONDS)
    
    async def _fetch_json(self, url: str) -> dict | list | None:
        """Fetch JSON from URL with retry"""
        try:
            await asyncio.sleep(0.15)  # Rate limit
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    url,
                    headers={
                        "User-Agent": "onchain-tools/1.0",
                        "Accept": "application/json"
                    }
                )
                
                if response.status_code != 200:
                    return None
                
                return response.json()
        except Exception as e:
            logger.debug(f"Fetch error for {url}: {e}")
            return None
    
    async def _fetch_new_pairs(self, chain: str) -> list[dict]:
        """Fetch new token pairs for a chain"""
        logger.info(f"Fetching new pairs for {chain}...")
        
        # Get token profiles
        url = "https://api.dexscreener.com/token-profiles/latest/v1"
        data = await self._fetch_json(url)
        
        if not isinstance(data, list):
            logger.warning(f"Unexpected data format from token profiles")
            return []
        
        logger.info(f"Got {len(data)} token profiles")
        
        # Filter for target chain
        chain_tokens = [t for t in data if chain in (t.get("chainId") or "")]
        logger.info(f"Filtered to {len(chain_tokens)} tokens on {chain}")
        
        # Fetch pairs for each token
        all_pairs = []
        max_fetch = 200
        
        for i, token in enumerate(chain_tokens[:max_fetch], 1):
            if not token.get("tokenAddress"):
                continue
            
            logger.debug(f"[{i}/{min(len(chain_tokens), max_fetch)}] Fetching pairs for {token['tokenAddress'][:8]}...")
            
            pairs_url = f"https://api.dexscreener.com/latest/dex/tokens/{token['tokenAddress']}"
            pair_data = await self._fetch_json(pairs_url)
            
            if pair_data and isinstance(pair_data.get("pairs"), list):
                chain_pairs = [p for p in pair_data["pairs"] if p.get("chainId") == chain]
                all_pairs.extend(chain_pairs)
        
        logger.info(f"Total pairs collected: {len(all_pairs)}")
        
        # Sort by liquidity
        all_pairs.sort(key=lambda p: self._parse_liquidity(p), reverse=True)
        
        return all_pairs
    
    @staticmethod
    def _normalize_timestamp(val: Any) -> int:
        """Normalize timestamp to milliseconds"""
        if not val:
            return 0
        try:
            n = float(val)
            if n <= 0:
                return 0
            # Convert seconds to ms if needed
            if n < 1e11:
                return int(n * 1000)
            return int(n)
        except (ValueError, TypeError):
            return 0
    
    def _extract_created_ms(self, pair: dict) -> int:
        """Extract creation timestamp from pair data"""
        candidates = [
            pair.get("pairCreatedAt"),
            pair.get("createdAt"),
            pair.get("firstTradeAt"),
            pair.get("info", {}).get("createdAt"),
            pair.get("info", {}).get("firstTradeAt"),
            pair.get("firstTradeUnix"),
        ]
        
        for c in candidates:
            ms = self._normalize_timestamp(c)
            if ms > 0:
                return ms
        return 0
    
    @staticmethod
    def _parse_liquidity(pair: dict) -> float:
        """Parse liquidity USD from pair"""
        try:
            return float(pair.get("liquidity", {}).get("usd", 0))
        except (ValueError, TypeError):
            return 0.0
    
    @staticmethod
    def _has_twitter(pair: dict) -> str | None:
        """Check if token has Twitter account"""
        info = pair.get("info", {})
        socials = info.get("socials", [])
        
        # Check socials array
        for social in socials:
            if social.get("type") == "twitter":
                return social.get("url")
        
        # Check direct fields
        if info.get("twitter"):
            return info["twitter"]
        
        # Check websites for Twitter links
        for site in info.get("websites", []):
            url = site.get("url") if isinstance(site, dict) else site
            if url and ("twitter.com" in url or "x.com" in url):
                return url
        
        return None
    
    @staticmethod
    def _has_website(pair: dict) -> str | None:
        """Check if token has website"""
        info = pair.get("info", {})
        websites = info.get("websites", [])
        
        if websites:
            return websites[0].get("url") if isinstance(websites[0], dict) else websites[0]
        
        return None
    
    async def _scan_once(self) -> None:
        """Scan all chains once for new tokens"""
        logger.info(f"Scan start: {datetime.now()}")
        now = int(datetime.now().timestamp() * 1000)
        lookback_ms = self.config.LOOKBACK_HOURS * 60 * 60 * 1000
        
        for chain in self.config.CHAINS:
            logger.info(f"--- Chain: {chain.upper()} ---")
            pairs = await self._fetch_new_pairs(chain)
            logger.info(f"Processing {len(pairs)} candidate pairs for {chain}")
            
            found_count = 0
            
            for i, pair in enumerate(pairs, 1):
                pair_id = pair.get("pairAddress") or ""
                if not pair_id:
                    continue
                
                # Check if we've seen this token before (using Redis cache)
                if await self._is_seen(chain, pair_id):
                    continue
                
                base_sym = (pair.get("baseToken", {}).get("symbol") or 
                           pair.get("baseToken", {}).get("name") or "UNKNOWN")
                quote_sym = (pair.get("quoteToken", {}).get("symbol") or 
                            pair.get("quoteToken", {}).get("name") or "?")
                
                # Time check
                created_ms = self._extract_created_ms(pair)
                if created_ms > 0:
                    age = now - created_ms
                    if age > lookback_ms:
                        continue
                elif i > self.config.TOP_N_FOR_NO_TIME:
                    continue
                
                # Liquidity check
                liquidity = self._parse_liquidity(pair)
                if liquidity < self.config.MIN_LIQUIDITY:
                    continue
                
                # Market cap check
                market_cap = pair.get("fdv") or pair.get("marketCap") or 0
                if self.config.MIN_MARKET_CAP > 0 and 0 < market_cap < self.config.MIN_MARKET_CAP:
                    continue
                if self.config.MAX_MARKET_CAP > 0 and market_cap > self.config.MAX_MARKET_CAP:
                    continue
                
                # Website check
                if self.config.REQUIRE_WEBSITE and not self._has_website(pair):
                    continue
                
                # Twitter check
                twitter_url = self._has_twitter(pair)
                if self.config.REQUIRE_TWITTER and not twitter_url:
                    continue
                
                # Fetch Twitter profile
                followers = None
                twitter_profile = None
                if twitter_url:
                    username = self.twitter.extract_username(twitter_url)
                    if username:
                        twitter_profile = await self.twitter.get_profile(username)
                        if twitter_profile:
                            followers = twitter_profile.get("followers")
                        
                        if (self.config.MIN_TWITTER_FOLLOWERS > 0 and 
                            (followers is None or followers < self.config.MIN_TWITTER_FOLLOWERS)):
                            continue
                
                # Passed all checks - publish new token alert
                found_count += 1
                
                # Fetch rug check for Solana tokens
                rug_summary = None
                if chain == "solana":
                    token_address = pair.get("baseToken", {}).get("address", "")
                    if token_address:
                        rug_summary = await self.rugcheck.get_summary(token_address)
                
                token_data = {
                    "pair_id": pair_id,
                    "chain": chain,
                    "base_symbol": base_sym,
                    "quote_symbol": quote_sym,
                    "price_usd": pair.get("priceUsd"),
                    "liquidity_usd": liquidity,
                    "market_cap": market_cap,
                    "volume_24h": pair.get("volume", {}).get("h24"),
                    "twitter_url": twitter_url,
                    "twitter_followers": followers,
                    "twitter_profile": twitter_profile,
                    "rug_check": rug_summary,
                    "dex_url": pair.get("url") or f"https://dexscreener.com/{chain}/{pair_id}",
                    "created_at": datetime.fromtimestamp(created_ms / 1000).isoformat() if created_ms else None,
                    "age_minutes": int((now - created_ms) / 60000) if created_ms else None,
                }
                
                logger.info(f"✅ NEW TOKEN: {base_sym}/{quote_sym} | Liq: ${liquidity:,.0f} | Chain: {chain}")
                
                # Send Telegram notification
                if self.telegram.enabled and getattr(settings, "enable_telegram", True):
                    message, image = self.telegram.format_token_message(token_data)
                    logger.info(f"Telegram HTML:\n{message}")
                    logger.info(f"Twitter URL: {twitter_url!r} | Dex URL: {token_data['dex_url']!r} | Image: {image!r}")
                    await self.telegram.send_message(message, image)
                
                # Publish to message queue
                await self.mq.publish(self.publish_channel, {
                    "type": "new_token",
                    "data": token_data
                })
                
                # Mark as seen (auto-expires after TTL)
                await self._mark_seen(chain, pair_id)
            
            if found_count == 0:
                logger.info(f"No new tokens found on {chain.upper()}")
            else:
                logger.info(f"✨ Found {found_count} new token(s) on {chain.upper()}")
        
        logger.info(f"✅ Scan finished: {datetime.now()}")
    
    async def handle_request(self, message: dict) -> dict:
        """Handle requests to the agent"""
        action = message.get("action", "status")
        
        if action == "status":
            return {
                "monitoring": self._monitoring,
                "chains": self.config.CHAINS,
                "cache_ttl_hours": self._cache_ttl / 3600,
            }
        
        if action == "scan_now":
            asyncio.create_task(self._scan_once())
            return {"status": "scan_started"}
        
        if action == "ping":
            return {"pong": True}
        
        return {"error": f"Unknown action: {action}"}

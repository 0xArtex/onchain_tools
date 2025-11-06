"""
Script to enrich token data with Dexscreener information.
Extracts tickers from CSV and fetches contract address, Dexscreener URL, and Twitter info.
Uses parallel requests with rate limiting for faster processing.
"""

import csv
import asyncio
import aiohttp
import time
from typing import Dict, List, Optional
from pathlib import Path


class DexscreenerAPI:
    """Handler for Dexscreener API calls with parallel processing and rate limiting."""
    
    BASE_URL = "https://api.dexscreener.com/latest/dex"
    MAX_REQUESTS_PER_MINUTE = 60
    CONCURRENT_REQUESTS = 10  # Process 10 tokens at a time
    
    def __init__(self):
        self.request_times = []
    
    async def _rate_limit(self):
        """Ensure we don't exceed 60 requests per minute."""
        now = time.time()
        # Remove requests older than 60 seconds
        self.request_times = [t for t in self.request_times if now - t < 60]
        
        # If we've made 60 requests in the last minute, wait
        if len(self.request_times) >= self.MAX_REQUESTS_PER_MINUTE:
            sleep_time = 60 - (now - self.request_times[0]) + 0.1
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
                # Clear old times after sleeping
                now = time.time()
                self.request_times = [t for t in self.request_times if now - t < 60]
        
        self.request_times.append(time.time())
    
    async def search_token(self, session: aiohttp.ClientSession, ticker: str) -> Optional[Dict]:
        """
        Search for a token by ticker on Dexscreener.
        
        Args:
            session: aiohttp session
            ticker: Token ticker symbol
            
        Returns:
            Dictionary with token info or None if not found
        """
        await self._rate_limit()
        
        try:
            url = f"{self.BASE_URL}/search/?q={ticker}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    pairs = data.get('pairs', [])
                    
                    if pairs:
                        # Get the first matching pair (usually most liquid)
                        best_pair = pairs[0]
                        return {
                            'contract_address': best_pair.get('baseToken', {}).get('address', ''),
                            'dexscreener_url': f"https://dexscreener.com/{best_pair.get('chainId', '')}/{best_pair.get('pairAddress', '')}",
                            'twitter': self._extract_twitter(best_pair)
                        }
                elif response.status == 429:
                    # Rate limited, wait and retry
                    await asyncio.sleep(5)
                    return await self.search_token(session, ticker)
                    
        except Exception as e:
            print(f"\nError searching for {ticker}: {str(e)}", flush=True)
        
        return None
    
    def _extract_twitter(self, pair_data: Dict) -> str:
        """Extract Twitter username or URL from pair data."""
        info = pair_data.get('info', {})
        
        # Check for Twitter in socials
        socials = info.get('socials', [])
        for social in socials:
            if social.get('type') == 'twitter':
                url = social.get('url', '')
                if url:
                    return url
        
        # Check for Twitter URL directly
        websites = info.get('websites', [])
        for website in websites:
            url = website.get('url', '')
            if 'twitter.com' in url or 'x.com' in url:
                return url
        
        return ''


def extract_ticker(token_field: str) -> str:
    """
    Extract ticker from the token field.
    
    Args:
        token_field: Raw token field (e.g., "PCULE\nPolycule")
        
    Returns:
        Ticker symbol
    """
    # Split by newline and take the first part
    lines = token_field.split('\n')
    return lines[0].strip() if lines else token_field.strip()


async def process_token(api: DexscreenerAPI, session: aiohttp.ClientSession, 
                       row: Dict, idx: int, total: int) -> Dict:
    """Process a single token asynchronously."""
    ticker = extract_ticker(row['Token'])
    print(f"[{idx}/{total}] {ticker}...", end=' ', flush=True)
    
    row['Ticker'] = ticker
    
    # Fetch Dexscreener data
    token_data = await api.search_token(session, ticker)
    
    if token_data:
        row['Contract Address'] = token_data['contract_address']
        row['Dexscreener URL'] = token_data['dexscreener_url']
        row['Twitter'] = token_data['twitter']
        contract_preview = token_data['contract_address'][:10] if token_data['contract_address'] else 'N/A'
        print(f"OK ({contract_preview}...)", flush=True)
    else:
        row['Contract Address'] = 'Not Found'
        row['Dexscreener URL'] = 'Not Found'
        row['Twitter'] = 'Not Found'
        print(f"NOT FOUND", flush=True)
    
    return row


async def process_csv_async(input_file: str, output_file: str):
    """
    Process the CSV file and enrich with Dexscreener data using parallel requests.
    
    Args:
        input_file: Path to input CSV
        output_file: Path to output CSV
    """
    api = DexscreenerAPI()
    
    # Read the CSV
    with open(input_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames) + ['Ticker', 'Contract Address', 'Dexscreener URL', 'Twitter']
        rows = list(reader)
    
    total = len(rows)
    print(f"Found {total} tokens to process.", flush=True)
    print(f"Processing {api.CONCURRENT_REQUESTS} tokens in parallel (max 60 req/min)...\n", flush=True)
    
    # Process rows in parallel batches
    enriched_rows = [None] * total  # Preserve order
    
    async with aiohttp.ClientSession() as session:
        # Create tasks for all rows
        tasks = []
        for idx, row in enumerate(rows, 1):
            task = process_token(api, session, row.copy(), idx, total)
            tasks.append((idx - 1, task))
        
        # Process with concurrency limit
        semaphore = asyncio.Semaphore(api.CONCURRENT_REQUESTS)
        
        async def bounded_task(idx, task):
            async with semaphore:
                return idx, await task
        
        # Run all tasks with concurrency limit
        results = await asyncio.gather(*[bounded_task(idx, task) for idx, task in tasks])
        
        # Put results back in order
        for idx, result in results:
            enriched_rows[idx] = result
    
    # Write the enriched CSV
    with open(output_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(enriched_rows)
    
    print(f"\n[COMPLETE] Enriched data saved to: {output_file}", flush=True)


def process_csv(input_file: str, output_file: str):
    """Wrapper to run async CSV processing."""
    asyncio.run(process_csv_async(input_file, output_file))


if __name__ == "__main__":
    # File paths
    script_dir = Path(__file__).parent
    input_csv = script_dir / "Coins - Believe Projects.csv"
    output_csv = script_dir / "Coins - Believe Projects - Enriched.csv"
    
    print("="*60)
    print("Token Enrichment Script - Dexscreener Data")
    print("="*60)
    print(f"Input:  {input_csv}")
    print(f"Output: {output_csv}")
    print("="*60)
    print()
    
    process_csv(str(input_csv), str(output_csv))


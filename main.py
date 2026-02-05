import os
import re
import time
import datetime
import logging
import asyncio
import aiohttp
import requests

# Configuration
# Keywords to search for (Region, ISP)
KEYWORDS = os.getenv("KEYWORDS", "北京,联通").split(",")
# Max sources per keyword
MAX_SOURCES = int(os.getenv("MAX_SOURCES", "10"))
# Timeout for validation
TIMEOUT = int(os.getenv("TIMEOUT", "5"))

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Spider:
    def __init__(self):
        self.results = []

    async def search_fofa(self, keyword):
        """
        Search using FOFA API if key is provided.
        """
        fofa_email = os.getenv("FOFA_EMAIL")
        fofa_key = os.getenv("FOFA_KEY")
        if not (fofa_email and fofa_key):
            logger.info("FOFA credentials not found, skipping FOFA search.")
            return []
            
        logger.info(f"Searching FOFA for: {keyword}")
        # Implementation of FOFA search logic (simplified)
        # In a real scenario, you'd call the API: https://fofa.info/api/v1/search/all
        # query = f'header="HTTP/1.1 200 OK" && body="udpxy" && region="{keyword}"'
        # For now, return empty to avoid breaking without keys
        return []

    async def search_tonkiang(self, keyword):
        """
        Search using Tonkiang (FoodieGuide) - public scraping.
        Note: This is often unstable due to anti-scraping.
        """
        logger.info(f"Searching Tonkiang for: {keyword}")
        search_url = f"http://tonkiang.us/hoteliptv.php?s={keyword}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, timeout=10) as response:
                    text = await response.text()
                    # Extract IPs/Links using Regex (Simplified)
                    # Pattern: http://1.2.3.4:1234/udp/239.1.1.1:1234
                    links = re.findall(r'http://[\d\.]+:[\d]+', text)
                    return list(set(links)) # Deduplicate
        except Exception as e:
            logger.error(f"Tonkiang search failed: {e}")
            return []

    async def run(self):
        all_candidates = []
        for kw in KEYWORDS:
            # 1. FOFA
            fofa_res = await self.search_fofa(kw)
            all_candidates.extend(fofa_res)
            # 2. Tonkiang
            tk_res = await self.search_tonkiang(kw)
            all_candidates.extend(tk_res)
            
            # 3. Add more sources here (e.g., subscribing to other repos)
            
        return list(set(all_candidates))

class Validator:
    async def check_url(self, session, url):
        try:
            start = time.time()
            # Try a common endpoint or just the root
            # For UDPXY, we might check /status
            # For HLS, we need a .m3u8 link. 
            # This is a heuristic check.
            target = f"{url}/stat" # Common for udpxy
            async with session.get(target, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    latency = (time.time() - start) * 1000
                    return (url, latency)
        except:
            pass
        return None

    async def validate(self, candidates):
        logger.info(f"Validating {len(candidates)} candidates...")
        valid_sources = []
        async with aiohttp.ClientSession() as session:
            tasks = [self.check_url(session, url) for url in candidates]
            results = await asyncio.gather(*tasks)
            valid_sources = [r for r in results if r is not None]
        
        # Sort by latency
        valid_sources.sort(key=lambda x: x[1])
        return [v[0] for v in valid_sources]

class Aggregator:
    def generate_playlist(self, sources):
        # Generate basic M3U
        content = "#EXTM3U\n"
        for i, source in enumerate(sources):
            # We need actual channel paths. 
            # A real spider would scan the /udpxy/status page to find channels.
            # Here we act as a 'Base URL' finder or assume a template.
            # For demonstration, we just list the found gateways.
            content += f"#EXTINF:-1 group-title=\"Live\", Source {i+1}\n{source}\n"
        return content

async def main():
    spider = Spider()
    validator = Validator()
    aggregator = Aggregator()

    # 1. Spider
    candidates = await spider.run()
    logger.info(f"Found {len(candidates)} candidates.")

    if not candidates:
        logger.warning("No candidates found. Check keywords or search sources.")
        # Fallback: Read from a local 'subs.txt' if exists
        if os.path.exists("subs.txt"):
            with open("subs.txt", "r") as f:
                candidates = [line.strip() for line in f if line.strip().startswith("http")]

    # 2. Validate
    valid_sources = await validator.validate(candidates)
    logger.info(f"Valid sources: {len(valid_sources)}")

    # 3. Save
    m3u_content = aggregator.generate_playlist(valid_sources)
    
    os.makedirs("output", exist_ok=True)
    with open("output/iptv.m3u", "w") as f:
        f.write(m3u_content)
    
    with open("output/iptv.txt", "w") as f:
        for source in valid_sources:
            f.write(f"{source}\n")

if __name__ == "__main__":
    asyncio.run(main())

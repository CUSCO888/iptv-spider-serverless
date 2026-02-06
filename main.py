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
# Filter keywords (Whitelist/Blacklist)
FILTER_INCLUDE = os.getenv("FILTER_INCLUDE", "").split(",") if os.getenv("FILTER_INCLUDE") else []
FILTER_EXCLUDE = os.getenv("FILTER_EXCLUDE", "").split(",") if os.getenv("FILTER_EXCLUDE") else []

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
            target = url
            # If it doesn't look like a file, assume it's a udpxy gateway
            if not url.endswith(('.m3u', '.m3u8', '.txt')):
                 target = f"{url}/stat"

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
        content = "#EXTM3U\n"
        
        # Helper to check filters
        def is_allowed(name):
            if FILTER_EXCLUDE:
                for kw in FILTER_EXCLUDE:
                    if kw and kw in name:
                        return False
            if FILTER_INCLUDE:
                for kw in FILTER_INCLUDE:
                    if kw and kw in name:
                        return True
                return False # If whitelist exists but no match, block
            return True

        for i, source in enumerate(sources):
            if source.endswith(('.m3u', '.m3u8', '.txt')):
                try:
                    resp = requests.get(source, timeout=10)
                    if resp.status_code == 200:
                        lines = resp.text.splitlines()
                        # Simple M3U parser
                        pending_inf = None
                        for line in lines:
                            line = line.strip()
                            if not line: continue
                            
                            if line.startswith("#EXTINF"):
                                pending_inf = line
                            elif not line.startswith("#"):
                                # This is a URL line
                                channel_name = ""
                                if pending_inf:
                                    # Try to extract name from EXTINF:-1,Channel Name
                                    # or group-title="XX",Channel Name
                                    parts = pending_inf.split(",")
                                    if len(parts) > 1:
                                        channel_name = parts[-1]
                                
                                if is_allowed(channel_name) or is_allowed(pending_inf or ""):
                                    if pending_inf:
                                        content += pending_inf + "\n"
                                    content += line + "\n"
                                pending_inf = None
                        continue
                except Exception as e:
                    logger.error(f"Error processing source {source}: {e}")
            
            # Fallback for raw URLs (assume allowed if no metadata to check, or check URL itself)
            if is_allowed(source):
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

import os
import re
import time
import json
import logging
import asyncio
import aiohttp
import requests

# Configuration
KEYWORDS = os.getenv("KEYWORDS", "北京,联通").split(",")
TIMEOUT = int(os.getenv("TIMEOUT", "5"))

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class Spider:
    def __init__(self):
        self.results = []

    async def search_tonkiang(self, keyword):
        """Search using Tonkiang (FoodieGuide)"""
        logger.info(f"Searching Tonkiang for: {keyword}")
        search_url = f"http://tonkiang.us/hoteliptv.php?s={keyword}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url, timeout=10) as response:
                    text = await response.text()
                    links = re.findall(r'http://[\d\.]+:[\d]+', text)
                    return list(set(links))
        except Exception as e:
            logger.error(f"Tonkiang search failed: {e}")
            return []

    async def run(self):
        all_candidates = []
        for kw in KEYWORDS:
            res = await self.search_tonkiang(kw)
            all_candidates.extend(res)
        return list(set(all_candidates))

class Validator:
    async def check_url(self, session, url):
        try:
            start = time.time()
            target = url
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
        valid_sources.sort(key=lambda x: x[1])
        return [v[0] for v in valid_sources]

class ChannelParser:
    """Parses M3U/Text sources into a unified list of channel objects"""
    def __init__(self):
        self.channels = [] # [{'name': 'CCTV1', 'url': '...', 'group': '...'}, ...]

    def parse_source(self, url):
        new_channels = []
        try:
            # For simplicity using requests here, could be async too
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200: return []
            
            lines = resp.text.splitlines()
            current_meta = {}
            
            for line in lines:
                line = line.strip()
                if not line: continue
                
                if line.startswith("#EXTINF"):
                    # Parse EXTINF:-1 group-title="XX",Name
                    meta = {'group': 'Unknown', 'name': 'Unknown'}
                    # Extract group
                    g_match = re.search(r'group-title="([^"]+)"', line)
                    if g_match: meta['group'] = g_match.group(1)
                    # Extract name (last part after comma)
                    name_match = line.rsplit(',', 1)
                    if len(name_match) > 1: meta['name'] = name_match[1].strip()
                    current_meta = meta
                elif not line.startswith("#"):
                    # It's a URL
                    if current_meta:
                        new_channels.append({
                            'name': current_meta.get('name', 'Unknown'),
                            'group': current_meta.get('group', 'Unknown'),
                            'url': line
                        })
                        current_meta = {}
                    else:
                        # Raw URL without metadata
                        new_channels.append({
                            'name': 'Unknown', 
                            'group': 'Unknown', 
                            'url': line
                        })
            return new_channels
        except Exception as e:
            logger.error(f"Failed to parse source {url}: {e}")
            return []

    async def fetch_all(self, sources):
        # We'll use a loop here for simplicity or asyncio if needed. 
        # Given parsing is CPU bound partly, straightforward loop is fine for serverless.
        logger.info("Parsing channels from sources...")
        all_channels = []
        for source in sources:
            if source.endswith(('.m3u', '.m3u8', '.txt')):
                chans = self.parse_source(source)
                all_channels.extend(chans)
            else:
                # It's a gateway root, maybe add manually if needed, or skip
                # For this version, we focus on M3U aggregation
                pass
        
        logger.info(f"Total channels parsed: {len(all_channels)}")
        return all_channels

class Exporter:
    def __init__(self, config_path="config.json"):
        self.config = []
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                self.config = json.load(f)
        else:
            # Default fallback
            self.config = [{
                "filename": "iptv.m3u",
                "include": [],
                "exclude": []
            }]

    def is_match(self, name, include, exclude):
        if exclude:
            for x in exclude:
                if x and x in name: return False
        
        if include:
            matched = False
            for i in include:
                if i and i in name: matched = True
            return matched
        
        return True

    def export(self, channels, output_dir="output"):
        os.makedirs(output_dir, exist_ok=True)
        
        for cfg in self.config:
            filename = cfg.get("filename", "iptv.m3u")
            include = cfg.get("include", [])
            exclude = cfg.get("exclude", [])
            
            filtered = [c for c in channels if self.is_match(c['name'], include, exclude)]
            
            # Write M3U
            with open(f"{output_dir}/{filename}", "w") as f:
                f.write("#EXTM3U\n")
                for c in filtered:
                    f.write(f"#EXTINF:-1 group-title=\"{c['group']}\",{c['name']}\n{c['url']}\n")
            
            # Write TXT
            txt_name = filename.replace(".m3u", ".txt")
            with open(f"{output_dir}/{txt_name}", "w") as f:
                for c in filtered:
                    f.write(f"{c['name']},{c['url']}\n")
            
            logger.info(f"Generated {filename} with {len(filtered)} channels.")

async def main():
    spider = Spider()
    validator = Validator()
    parser = ChannelParser()
    exporter = Exporter()

    # 1. Collect Sources
    candidates = await spider.run()
    if os.path.exists("subs.txt"):
        with open("subs.txt", "r") as f:
            candidates.extend([line.strip() for line in f if line.strip().startswith("http")])
    candidates = list(set(candidates))
    
    # 2. Validate Sources
    valid_sources = await validator.validate(candidates)
    
    # 3. Parse All Channels
    all_channels = await parser.fetch_all(valid_sources)
    
    # 4. Export based on Config
    exporter.export(all_channels)

if __name__ == "__main__":
    asyncio.run(main())

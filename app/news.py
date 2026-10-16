"""Read-only official releases. Fixed upstream, bounded XML and shared cache."""
import asyncio
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

SOURCE_URL = 'https://www.federalreserve.gov/feeds/press_all.xml'
MAX_BYTES = 512_000


def parse_feed(raw):
    if len(raw) > MAX_BYTES or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper() or b'\x00' in raw:
        raise ValueError('Unsafe or oversized XML')
    root = ElementTree.fromstring(raw)
    if root.tag != 'rss':
        raise ValueError('Unexpected feed format')
    items, seen = [], set()
    for node in root.findall('./channel/item'):
        title = ' '.join((node.findtext('title') or '').split())[:500]
        url = (node.findtext('link') or '').strip()
        try:
            parts = urlsplit(url)
            if parts.scheme != 'https' or parts.hostname != 'www.federalreserve.gov' or parts.username or parts.password or parts.port not in (None,443):
                continue
            date = parsedate_to_datetime(node.findtext('pubDate') or '')
            if date.tzinfo is None: date = date.replace(tzinfo=timezone.utc)
            published = date.timestamp()
        except (ValueError, TypeError, OverflowError):
            continue
        if not title or url in seen: continue
        seen.add(url)
        items.append(dict(title=title,url=url,published_at=published,source='Federal Reserve'))
    return sorted(items,key=lambda x:x['published_at'],reverse=True)[:30]


class NewsFeed:
    def __init__(self, client, clock=time.time):
        self.client, self.clock = client, clock
        self.lock = asyncio.Lock()
        self.items, self.updated_at, self.next_attempt = [], None, 0
        self.status = 'unavailable'

    async def get(self):
        async with self.lock:
            now = self.clock()
            if now >= self.next_attempt:
                try:
                    async with asyncio.timeout(10):
                        async with self.client.stream('GET', SOURCE_URL, follow_redirects=False) as response:
                            response.raise_for_status()
                            raw = bytearray()
                            async for chunk in response.aiter_bytes():
                                raw.extend(chunk)
                                if len(raw) > MAX_BYTES: raise ValueError('Oversized feed')
                            items = parse_feed(bytes(raw))
                    self.items, self.updated_at = items, self.clock()
                    self.status, self.next_attempt = 'live', self.clock()+300
                except Exception:
                    self.status = 'stale' if self.updated_at is not None else 'unavailable'
                    self.next_attempt = self.clock()+60
            return dict(items=self.items,status=self.status,updated_at=self.updated_at,
                        source='Federal Reserve',source_url=SOURCE_URL)

"""Read-only HiFiSync HTTPS client."""
import asyncio
import json
import re
import aiohttp
from .core import Missing, Transient, Unauthorized, uuid, validate_systems

DEFAULT_ORIGIN = 'https://hifisync.com'
API_PATH = '/api/integrations/home-assistant/v1'
BASE = DEFAULT_ORIGIN + API_PATH
# Limit token delivery to the service and deployment URLs in its Vercel team.
_PREVIEW_ORIGIN = re.compile(r'https://hifisync-[a-z0-9]{7,20}-zerostorypoints\.vercel\.app')


def site_origin(value: str | None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_ORIGIN
    if not isinstance(value, str):
        raise ValueError('Unsupported HiFiSync origin')
    origin = value.strip().removesuffix('/')
    if origin == DEFAULT_ORIGIN or _PREVIEW_ORIGIN.fullmatch(origin):
        return origin
    raise ValueError('Unsupported HiFiSync origin')


def api_base(value: str | None) -> str:
    return site_origin(value) + API_PATH

MAX_RESPONSE = 1024 * 1024

class Client:
    def __init__(self, session, token, base=BASE):
        self.session, self.token, self.base = session, token, base.rstrip('/')
    async def _get(self, path):
        try:
            async with self.session.get(self.base + path, headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'}, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=False) as response:
                if response.status == 401: raise Unauthorized()
                if response.status == 404: raise Missing()
                if response.status != 200: raise Transient()
                # Limit the body before parsing. The real aiohttp stream may return short reads.
                chunks = []; size = 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_RESPONSE: raise Transient()
                    chunks.append(chunk)
                value = json.loads(b''.join(chunks))
                if not isinstance(value, dict): raise Transient()
                return value
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise Transient() from exc
    async def systems(self):
        try: return validate_systems(await self._get('/systems'))
        except (ValueError, KeyError, TypeError, AttributeError) as exc: raise Transient() from exc
    async def detail(self, system_id):
        return await self._get('/systems/' + uuid(system_id))

"""Read-only HiFiSync HTTPS client."""
import asyncio
import json
import aiohttp
from .core import Missing, Transient, Unauthorized, uuid, validate_systems

BASE = 'https://hifisync.com/api/integrations/home-assistant/v1'
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

"""HiFiSync integration for ordinary Home Assistant installations."""
import asyncio
import logging
from datetime import timedelta
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from .client import Client
from .const import DOMAIN, PROVIDER
from .core import Cache, date

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    api = Client(async_get_clientsession(hass), entry.data['token'])
    cache = Cache(api, entry.data.get('system_id'), entry.data['player_registry_id'], token_expiry=date(entry.data['token_expiry']))
    registry = er.async_get(hass)
    last_attempt = None
    retry_minutes = 5
    failures = 0
    refresh_lock = asyncio.Lock()
    async def refresh(force=False):
        nonlocal last_attempt, retry_minutes, failures
        async with refresh_lock:
            if not force and last_attempt is not None and cache.now() - last_attempt < timedelta(minutes=retry_minutes):
                return
            last_attempt = cache.now()
            available = registry.async_get(cache.player_id) is not None
            await cache.refresh(player_available=available)
            failures = failures + 1 if cache.transient else 0
            retry_minutes = min(5, 2 ** min(failures - 1, 3)) if failures else 5
            if cache.status == 'reauth':
                # Home Assistant deduplicates active reauthentication flows.
                entry.async_start_reauth(hass)
            elif cache.status in ('select_system', 'select_player') and cache.system_id is None and entry.data.get('system_id'):
                hass.config_entries.async_update_entry(entry, data={**entry.data, 'system_id': None})
            async_dispatcher_send(hass, f"{DOMAIN}_status_{entry.entry_id}")
    def provider(entity_id):
        entity = registry.async_get(entity_id)
        if not entity or entity.domain != 'media_player' or entity.id != cache.player_id:
            return None
        return cache.read(entity.id)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = cache
    hass.data[DOMAIN][PROVIDER] = provider
    await refresh(force=True)
    async def tick(_now):
        if last_attempt is None or cache.now() - last_attempt >= timedelta(minutes=retry_minutes): await refresh()
    entry.async_on_unload(async_track_time_interval(hass, tick, timedelta(minutes=1)))
    @callback
    def state_changed(event):
        nonlocal last_attempt
        entity = registry.async_get(event.data['entity_id'])
        new = event.data.get('new_state')
        if entity and entity.id == cache.player_id and new and new.state == 'playing' and (last_attempt is None or cache.now() - last_attempt >= timedelta(minutes=retry_minutes)):
            hass.async_create_task(refresh())
    entry.async_on_unload(hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed))
    @callback
    def registry_changed(event):
        if not registry.async_get(cache.player_id) and cache.player_available:
            reauth = cache.status == 'reauth'
            cache.select(cache.system_id, cache.player_id)
            cache.player_available = False
            cache.status = 'reauth' if reauth else 'select_player'
            async_dispatcher_send(hass, f"{DOMAIN}_status_{entry.entry_id}")
    entry.async_on_unload(hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, registry_changed))
    await hass.config_entries.async_forward_entry_setups(entry, ['sensor'])
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    if not await hass.config_entries.async_unload_platforms(entry, ['sensor']): return False
    data = hass.data[DOMAIN]; cache = data.pop(entry.entry_id)
    cache.select(None, None)
    data.pop(PROVIDER, None)
    return True

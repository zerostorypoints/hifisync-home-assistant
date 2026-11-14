"""Administrator setup, token replacement and system/player selection."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .client import Client
from .const import DOMAIN
from .core import Missing, Transient, Unauthorized, validate_snapshot, date
from datetime import datetime, timezone

class HiFiSyncFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    def __init__(self):
        self.token = None; self.expiry = None; self.systems = []; self.entry = None
    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return await self._token_step('user', user_input)
    async def _token_step(self, step, user_input):
        errors = {}
        if user_input is not None:
            token = user_input['token'].strip()
            try:
                expiry, self.systems = await Client(async_get_clientsession(self.hass), token).systems()
                if expiry <= datetime.now(timezone.utc): raise Unauthorized()
                self.token = token; self.expiry = expiry
                if self.entry:
                    old = self.entry.data.get('system_id')
                    if old and any(x['id'] == old for x in self.systems):
                        try:
                            result = await Client(async_get_clientsession(self.hass), token).detail(old)
                            snap = validate_snapshot(result['snapshot'])
                            if snap['system']['id'] != old or date(result['expiresAt']) <= datetime.now(timezone.utc): raise Transient()
                        except Missing: old = None
                    else: old = None
                    cache = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id)
                    if cache: cache.select(None, None)
                    self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, 'token': token, 'token_expiry': expiry.isoformat(), 'system_id': old})
                    await self.hass.config_entries.async_reload(self.entry.entry_id)
                    return self.async_abort(reason='reauth_successful')
                return await self.async_step_select()
            except Unauthorized: errors['base'] = 'invalid_auth'
            except (Transient, Missing, ValueError, KeyError, TypeError): errors['base'] = 'cannot_connect'
        return self.async_show_form(step_id=step, data_schema=vol.Schema({vol.Required('token'): str}), errors=errors)
    async def async_step_reauth(self, entry_data):
        self.entry = self.hass.config_entries.async_get_entry(self.context['entry_id'])
        return await self.async_step_reauth_confirm()
    async def async_step_reauth_confirm(self, user_input=None):
        return await self._token_step('reauth_confirm', user_input)
    async def async_step_reconfigure(self, user_input=None):
        self.entry = self._get_reconfigure_entry(); self.token = self.entry.data['token']
        try:
            expiry, self.systems = await Client(async_get_clientsession(self.hass), self.token).systems()
            if expiry <= datetime.now(timezone.utc): raise Unauthorized()
            self.expiry = expiry
        except Unauthorized:
            self.entry.async_start_reauth(self.hass)
            return self.async_abort(reason='reauth_required')
        except (Transient, Missing): return self.async_abort(reason='cannot_connect')
        return await self.async_step_select(user_input)
    async def async_step_select(self, user_input=None):
        if not self.systems:
            return self.async_abort(reason='no_systems')
        registry = er.async_get(self.hass)
        players = {e.id: f'{e.name or e.original_name or e.entity_id} ({e.entity_id})' for e in registry.entities.values() if e.domain == 'media_player' and not e.disabled}
        if not players: return self.async_abort(reason='no_players')
        options = {s['id']: f"{s['name']} ({s['visibility']}{' • ' + s['roomName'] if s['roomName'] else ''})" for s in self.systems}
        if user_input is not None:
            if user_input['system_id'] not in options or user_input['player_registry_id'] not in players:
                return self.async_abort(reason='selection_changed')
            data = {'token': self.token, 'token_expiry': self.expiry.isoformat(), **user_input}
            if self.entry:
                # Immediately invalidate any old in-flight result before reload.
                cache = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id)
                if cache: cache.select(None, None)
                self.hass.config_entries.async_update_entry(self.entry, data=data)
                await self.hass.config_entries.async_reload(self.entry.entry_id)
                return self.async_abort(reason='reconfigure_successful')
            return self.async_create_entry(title='HiFiSync', data=data)
        return self.async_show_form(step_id='select', data_schema=vol.Schema({vol.Required('system_id'): vol.In(options), vol.Required('player_registry_id'): vol.In(players)}))

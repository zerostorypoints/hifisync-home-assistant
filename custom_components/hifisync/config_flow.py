"""Administrator setup, token replacement and system/player selection."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .client import Client, api_base, site_origin
from .const import DOMAIN
from .core import Missing, Transient, Unauthorized, validate_snapshot, date
from datetime import datetime, timezone

class HiFiSyncFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    def __init__(self):
        self.token = None; self.expiry = None; self.systems = []; self.entry = None
        self.origin = None
    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return await self._token_step('user', user_input)
    async def _token_step(self, step, user_input):
        errors = {}
        if user_input is not None:
            token = user_input['token'].strip()
            configured_site = self.entry.data.get('site_url') if self.entry else user_input.get('site_url')
            try:
                self.origin = site_origin(configured_site)
            except ValueError:
                errors['site_url' if step == 'user' else 'base'] = 'invalid_site_url'
                return self._token_form(step, errors)
            client = Client(async_get_clientsession(self.hass), token, base=api_base(self.origin))
            try:
                expiry, self.systems = await client.systems()
                if expiry <= datetime.now(timezone.utc): raise Unauthorized()
                self.token = token; self.expiry = expiry
                if self.entry:
                    old = self.entry.data.get('system_id')
                    if old and any(x['id'] == old for x in self.systems):
                        try:
                            result = await client.detail(old)
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
        return self._token_form(step, errors)

    def _token_form(self, step, errors):
        fields = {vol.Required('token'): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
        if step == 'user':
            fields[vol.Optional('site_url')] = TextSelector()
        return self.async_show_form(step_id=step, data_schema=vol.Schema(fields), errors=errors)
    async def async_step_reauth(self, entry_data):
        self.entry = self.hass.config_entries.async_get_entry(self.context['entry_id'])
        return await self.async_step_reauth_confirm()
    async def async_step_reauth_confirm(self, user_input=None):
        return await self._token_step('reauth_confirm', user_input)
    async def async_step_reconfigure(self, user_input=None):
        self.entry = self._get_reconfigure_entry(); self.token = self.entry.data['token']
        try:
            self.origin = site_origin(self.entry.data.get('site_url'))
            client = Client(async_get_clientsession(self.hass), self.token, base=api_base(self.origin))
            expiry, self.systems = await client.systems()
            if expiry <= datetime.now(timezone.utc): raise Unauthorized()
            self.expiry = expiry
        except Unauthorized:
            self.entry.async_start_reauth(self.hass)
            return self.async_abort(reason='reauth_required')
        except (Transient, Missing, ValueError): return self.async_abort(reason='cannot_connect')
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
            data = {
                **(self.entry.data if self.entry else {}),
                'token': self.token,
                'token_expiry': self.expiry.isoformat(),
                'site_url': self.origin,
                **user_input,
            }
            if self.entry:
                # Immediately invalidate any old in-flight result before reload.
                cache = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id)
                if cache: cache.select(None, None)
                self.hass.config_entries.async_update_entry(self.entry, data=data)
                await self.hass.config_entries.async_reload(self.entry.entry_id)
                return self.async_abort(reason='reconfigure_successful')
            return self.async_create_entry(title='HiFiSync', data=data)
        return self.async_show_form(step_id='select', data_schema=vol.Schema({vol.Required('system_id'): vol.In(options), vol.Required('player_registry_id'): vol.In(players)}))

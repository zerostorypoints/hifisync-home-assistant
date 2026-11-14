"""Exercise the integration inside a fresh Home Assistant instance."""
import json
from unittest.mock import patch
import pytest
from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from custom_components.hifisync.client import Client
from tests.test_core import SNAPSHOT

pytestmark = pytest.mark.asyncio
SYSTEM = SNAPSHOT['system']['id']

class FixtureClient(Client):
    async def _get(self, path):
        assert self.token == 'synthetic-token'
        if path == '/systems':
            return {'expiresAt':'2099-01-01T00:00:00Z', 'systems':[{'id': SYSTEM, 'name':'Fixture', 'visibility':'private', 'roomName':None}]}
        assert path == '/systems/' + SYSTEM
        return {'expiresAt':'2099-01-01T00:00:00Z', 'snapshot':SNAPSHOT}

async def test_setup_rename_and_replacement(hass, enable_custom_integrations):
    registry = er.async_get(hass)
    player = registry.async_get_or_create('media_player', 'fixture', 'player1', suggested_object_id='fixture')
    with patch('custom_components.hifisync.config_flow.Client', FixtureClient), patch('custom_components.hifisync.Client', FixtureClient):
        result = await hass.config_entries.flow.async_init('hifisync', context={'source': config_entries.SOURCE_USER})
        assert result['type'] == 'form'
        result = await hass.config_entries.flow.async_configure(result['flow_id'], {'token':'synthetic-token'})
        assert result['step_id'] == 'select'
        result = await hass.config_entries.flow.async_configure(result['flow_id'], {'system_id':SYSTEM, 'player_registry_id':player.id})
        assert result['type'] == 'create_entry'
        await hass.async_block_till_done()
        entry = hass.config_entries.async_entries('hifisync')[0]
        assert entry.state == config_entries.ConfigEntryState.LOADED
        provider = hass.data['hifisync']['studio_snapshot_v1']
        assert provider(player.entity_id)['snapshot']['system']['name'] == 'Stereo'
        renamed = registry.async_update_entity(player.entity_id, new_entity_id='media_player.renamed')
        assert provider(renamed.entity_id) is not None
        registry.async_remove(renamed.entity_id)
        replacement = registry.async_get_or_create('media_player', 'fixture', 'player2', suggested_object_id='renamed')
        assert provider(replacement.entity_id) is None

async def test_missing_system_allows_reselection_without_reauth(hass, enable_custom_integrations):
    from custom_components.hifisync.core import Missing
    class DeletedClient(FixtureClient):
        async def _get(self, path):
            if path != '/systems': raise Missing()
            return await super()._get(path)
    registry = er.async_get(hass)
    player = registry.async_get_or_create('media_player', 'fixture', 'p', suggested_object_id='fixture')
    with patch('custom_components.hifisync.config_flow.Client', DeletedClient), patch('custom_components.hifisync.Client', DeletedClient):
        result = await hass.config_entries.flow.async_init('hifisync', context={'source': config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result['flow_id'], {'token':'synthetic-token'})
        await hass.config_entries.flow.async_configure(result['flow_id'], {'system_id':SYSTEM, 'player_registry_id':player.id})
        await hass.async_block_till_done()
        entry = hass.config_entries.async_entries('hifisync')[0]
        assert entry.state == config_entries.ConfigEntryState.LOADED
        assert entry.data['system_id'] is None
        assert hass.data['hifisync']['studio_snapshot_v1'](player.entity_id) is None
        assert not any(flow['context']['source'] == 'reauth' for flow in hass.config_entries.flow.async_progress())
        result = await hass.config_entries.flow.async_init('hifisync', context={'source': 'reconfigure', 'entry_id': entry.entry_id})
        assert result['step_id'] == 'select'

async def test_unauthorized_starts_reauth_and_replacement_restores_selection(hass, enable_custom_integrations):
    from custom_components.hifisync.core import Unauthorized
    class RevokedClient(FixtureClient):
        async def _get(self, path): raise Unauthorized()
    registry = er.async_get(hass)
    player = registry.async_get_or_create('media_player', 'fixture', 'p', suggested_object_id='fixture')
    with patch('custom_components.hifisync.config_flow.Client', FixtureClient), patch('custom_components.hifisync.Client', FixtureClient):
        result = await hass.config_entries.flow.async_init('hifisync', context={'source': config_entries.SOURCE_USER})
        result = await hass.config_entries.flow.async_configure(result['flow_id'], {'token':'synthetic-token'})
        await hass.config_entries.flow.async_configure(result['flow_id'], {'system_id':SYSTEM, 'player_registry_id':player.id})
        await hass.async_block_till_done()
        entry = hass.config_entries.async_entries('hifisync')[0]
        cache = hass.data['hifisync'][entry.entry_id]; cache.api = RevokedClient(None, 'synthetic-token')
        # Run the periodic callback through HA time tracking in separate tests; this exercises the actual refresh and auth flow.
        await cache.refresh()
        assert cache.status == 'reauth'
        entry.async_start_reauth(hass)
        await hass.async_block_till_done()
        flows = [f for f in hass.config_entries.flow.async_progress() if f['context']['source'] == 'reauth']
        assert len(flows) == 1
        result = await hass.config_entries.flow.async_configure(flows[0]['flow_id'], {'token':'synthetic-token'})
        assert result['reason'] == 'reauth_successful'
        await hass.async_block_till_done()
        assert entry.data['system_id'] == SYSTEM
        assert hass.data['hifisync']['studio_snapshot_v1'](player.entity_id) is not None

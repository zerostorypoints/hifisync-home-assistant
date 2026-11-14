"""Expose link readiness without recording room or equipment data."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta
from .const import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([Readiness(entry, hass.data[DOMAIN][entry.entry_id])])

class Readiness(SensorEntity):
    _attr_name = 'HiFiSync link status'
    _attr_has_entity_name = False
    def __init__(self, entry, cache):
        self._attr_unique_id = entry.entry_id + '_status'; self.entry_id = entry.entry_id; self.cache = cache
    @property
    def native_value(self):
        if self.cache.status == 'ready' and self.cache.read(self.cache.player_id) is None: return 'unavailable'
        return self.cache.status
    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, f"{DOMAIN}_status_{self.entry_id}", self.async_write_ha_state))
        @callback
        def update(_):
            self.async_write_ha_state()
        self.async_on_remove(async_track_time_interval(self.hass, update, timedelta(minutes=1)))

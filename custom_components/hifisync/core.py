"""Bounded, network-independent snapshot cache and contract validation."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import math
from datetime import datetime, timedelta, timezone
from uuid import UUID

class Unauthorized(Exception): pass
class Missing(Exception): pass
class Transient(Exception): pass

def date(value):
    if not isinstance(value, str): raise ValueError('Invalid expiry')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None: raise ValueError('Expiry needs timezone')
    return result.astimezone(timezone.utc)

def text(value, max_length=200):
    if not isinstance(value, str) or not value or len(value) > max_length: raise ValueError('Invalid text')
    return value

def uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value.lower(): raise ValueError('Invalid UUID')
    return value

def number(value, minimum, maximum, nullable=False):
    if value is None and nullable: return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum: raise ValueError('Invalid number')
    return value

def quantity(value):
    if type(value) is not int or not 1 <= value <= 64: raise ValueError("Invalid quantity")
    return value

def validate_systems(data):
    expiry = date(data['expiresAt'])
    systems = data['systems']
    if not isinstance(systems, list) or len(systems) > 500: raise ValueError('Invalid systems')
    out = []
    for item in systems:
        if item['visibility'] not in ('public', 'private'): raise ValueError('Invalid visibility')
        out.append({'id': uuid(item['id']), 'name': text(item['name']), 'visibility': item['visibility'], 'roomName': None if item['roomName'] is None else text(item['roomName'])})
    return expiry, out

def _validate_snapshot(value):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value.get('version') != 1: raise ValueError('Invalid snapshot')
    system = value['system']; components = system['components']; rack = system['rack']
    if not isinstance(components, list) or len(components) > 64 or not isinstance(rack, list) or len(rack) > 32: raise ValueError('Invalid equipment')
    items = [{'id': text(x['id']), 'label': text(x['label']), 'role': text(x['role']), 'quantity': quantity(x['quantity'])} for x in components]
    ids = {x['id'] for x in items}
    if len(ids) != len(items): raise ValueError('Duplicate component')
    rows = []
    for row in rack:
        if not isinstance(row, list) or len(row) > 32 or any(not isinstance(x, str) or x not in ids for x in row): raise ValueError('Invalid rack')
        rows.append(list(row))
    room = value['room']
    if room is not None:
        w = number(room['widthM'], .01, 200); length = number(room['lengthM'], .01, 200)
        placements = room['placements']
        if not isinstance(placements, list) or len(placements) > 16: raise ValueError('Invalid placements')
        points = []
        for p in placements:
            if p['role'] not in ('left', 'right', 'subwoofer', 'listening'): raise ValueError('Invalid role')
            points.append({'role': p['role'], 'xM': number(p['xM'], 0, w), 'yM': number(p['yM'], 0, length), 'rotationDeg': number(p['rotationDeg'], -360, 360, True)})
        room = {'name': text(room['name']), 'widthM': w, 'lengthM': length, 'heightM': number(room['heightM'], .01, 200, True), 'placements': points}
    return {'version': 1, 'revision': text(value['revision']), 'system': {'id': uuid(system['id']), 'name': text(system['name']), 'components': items, 'rack': rows}, 'room': room}

def validate_snapshot(value):
    try:
        return _validate_snapshot(value)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid snapshot") from exc

class Cache:
    def __init__(self, api, system_id, player_id, now=lambda: datetime.now(timezone.utc), token_expiry=None):
        self.api, self.system_id, self.player_id, self.now = api, system_id, player_id, now
        self.token_expiry = token_expiry
        self.player_available = True; self.transient = False
        self.generation = 0; self.snapshot = None; self.valid_until = None; self.stale = False; self.status = 'loading' if system_id else 'select_system'; self._task = None
    def select(self, system_id, player_id):
        self.generation += 1; self.system_id = system_id; self.player_id = player_id; self.snapshot = None; self.valid_until = None; self.status = 'loading' if system_id else 'select_system'
    def read(self, player_id):
        if player_id != self.player_id or not self.snapshot or self.now() >= self.valid_until: return None
        return {'snapshot': deepcopy(self.snapshot), 'stale': self.stale, 'validUntil': self.valid_until.isoformat()}
    async def refresh(self, player_available=True):
        if self.player_available != player_available:
            self.player_available = player_available
            self.generation += 1; self.snapshot = None; self.valid_until = None
        if self.status == 'reauth': return
        if self.token_expiry and self.now() >= self.token_expiry:
            self.generation += 1; self.snapshot = None; self.transient = False; self.status = 'reauth'
            return
        if self._task and not self._task.done():
            if self._task_generation == self.generation: return await asyncio.shield(self._task)
        generation = self.generation; system = self.system_id
        self._task_generation = generation
        self._task = asyncio.create_task(self._refresh(generation, system, player_available))
        return await asyncio.shield(self._task)
    async def _refresh(self, generation, system, player_available):
        probe = not system or not player_available
        try:
            if probe:
                expiry, _systems = await self.api.systems()
                if expiry <= self.now(): raise Unauthorized()
            else:
                data = await self.api.detail(system)
                expiry = date(data['expiresAt']); snapshot = validate_snapshot(data['snapshot'])
                if snapshot['system']['id'] != system or expiry <= self.now(): raise ValueError('Invalid selection or expiry')
        except Unauthorized:
            if generation == self.generation:
                self.snapshot = None; self.transient = False; self.status = 'reauth'
        except Missing:
            if generation == self.generation:
                self.snapshot = None
                if probe:
                    self.transient = True; self.status = 'select_player' if not player_available else 'select_system'
                else:
                    self.system_id = None; self.transient = False; self.status = 'select_system'
        except (Transient, ValueError, KeyError, TypeError):
            if generation == self.generation:
                self.transient = True; self.stale = True
                self.status = 'reauth' if self.token_expiry and self.now() >= self.token_expiry else ('select_player' if not player_available else ('select_system' if not system else 'unavailable'))
                if self.status == 'reauth': self.snapshot = None
        else:
            if generation == self.generation:
                self.token_expiry = expiry; self.transient = False; self.stale = False
                if probe:
                    self.snapshot = None; self.status = 'select_player' if not player_available else 'select_system'
                else:
                    self.snapshot = snapshot; self.valid_until = min(expiry, self.now() + timedelta(hours=1)); self.status = 'ready'

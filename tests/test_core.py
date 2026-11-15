import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from custom_components.hifisync.core import Cache, Unauthorized, Missing, Transient, validate_snapshot

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
SNAPSHOT = {"version": 1, "revision": "a", "system": {"id": "12345678-1234-1234-1234-123456789abc", "name": "Stereo", "components": [], "rack": []}, "room": None}

class Fake:
    def __init__(self): self.result = {"expiresAt": "2026-01-03T00:00:00Z", "snapshot": SNAPSHOT}
    async def detail(self, system):
        if isinstance(self.result, Exception): raise self.result
        if isinstance(self.result, asyncio.Future): return await self.result
        return self.result

class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = Fake(); self.now = NOW; self.cache = Cache(self.api, SNAPSHOT['system']['id'], 'registry-1', lambda: self.now)
    async def test_transient_stale_and_expiry(self):
        await self.cache.refresh(); self.api.result = Transient(); self.now += timedelta(minutes=10); await self.cache.refresh()
        self.assertTrue(self.cache.read('registry-1')['stale'])
        self.now += timedelta(minutes=51); self.assertIsNone(self.cache.read('registry-1'))
    async def test_link_summary_contains_only_current_names_and_readiness(self):
        player = 'registry-1'
        self.api.result['snapshot'] = {
            **SNAPSHOT,
            'room': {
                'name': 'Listening room',
                'widthM': 4,
                'lengthM': 6,
                'heightM': 2.5,
                'placements': [{'role': 'left', 'xM': 1, 'yM': 1, 'rotationDeg': 0}],
            },
        }
        await self.cache.refresh()
        names = {'systemName': 'Stereo', 'roomName': 'Listening room'}
        empty = {'systemName': None, 'roomName': None}
        self.assertEqual(self.cache.link(player), {'status': 'ready', **names})
        self.assertIsNone(self.cache.link('another-player'))
        self.api.result = Transient()
        self.now += timedelta(minutes=10)
        await self.cache.refresh()
        self.assertEqual(self.cache.link(player), {'status': 'stale', **names})
        self.now += timedelta(minutes=51)
        self.assertEqual(self.cache.link(player), {'status': 'unavailable', **empty})
        self.cache.select('another-system', player)
        self.assertEqual(self.cache.link(player), {'status': 'loading', **empty})
    async def test_missing_selection_and_unauthorized_differ(self):
        await self.cache.refresh(); self.api.result = Missing(); await self.cache.refresh()
        self.assertIsNone(self.cache.system_id); self.assertEqual(self.cache.status, 'select_system'); self.assertIsNone(self.cache.read('registry-1'))
        self.cache.system_id = SNAPSHOT['system']['id']; self.api.result = Unauthorized(); await self.cache.refresh()
        self.assertEqual(self.cache.status, 'reauth'); self.assertIsNone(self.cache.read('registry-1'))
    async def test_late_response_cannot_restore_old_selection(self):
        future = asyncio.get_running_loop().create_future(); self.api.result = future
        task = asyncio.create_task(self.cache.refresh()); await asyncio.sleep(0)
        self.cache.select('another', 'registry-1'); future.set_result({'expiresAt': '2026-01-03T00:00:00Z', 'snapshot': SNAPSHOT}); await task
        self.assertIsNone(self.cache.read('registry-1'))
    async def test_registry_identity_and_token_expiry(self):
        await self.cache.refresh(); self.assertIsNotNone(self.cache.read('registry-1')); self.assertIsNone(self.cache.read('replacement'))
        self.now += timedelta(days=3); self.assertIsNone(self.cache.read('registry-1'))
    async def test_malformed_snapshot_rejected(self):
        with self.assertRaises(ValueError): validate_snapshot({'version':1})

class BoundsTests(unittest.TestCase):
    def test_rejects_contract_excess_and_boolean_version(self):
        import copy
        for mutate in [lambda x: x.update(version=True), lambda x: x['system'].update(components=[{'id':str(i),'label':'x','role':'amplifier','quantity':1} for i in range(65)]), lambda x: x['system'].update(components=[{'id':'x','label':'x','role':'amplifier','quantity':1.5}]), lambda x: x.update(room={'name':'r','widthM':201,'lengthM':2,'heightM':None,'placements':[]}), lambda x: x.update(room={'name':'r','widthM':2,'lengthM':2,'heightM':None,'placements':[{'role':'left','xM':1,'yM':1,'rotationDeg':0}]*17})]:
            snap = copy.deepcopy(SNAPSHOT); mutate(snap)
            with self.subTest(snap=snap):
                with self.assertRaises(ValueError): validate_snapshot(snap)

class ExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def test_offline_known_token_expiry_requires_reauth(self):
        api = Fake(); api.result = {'expiresAt':'2026-01-01T00:02:00Z','snapshot':SNAPSHOT}
        current = [NOW]; cache = Cache(api, SNAPSHOT['system']['id'], 'registry-1', lambda:current[0])
        await cache.refresh(); current[0] += timedelta(minutes=3); api.result = Transient()
        self.assertEqual(cache.link('registry-1'), {'status': 'reauth', 'systemName': None, 'roomName': None})
        await cache.refresh()
        self.assertEqual(cache.status, 'reauth'); self.assertIsNone(cache.read('registry-1'))

    async def test_expiry_starts_reauth_even_without_selected_system(self):
        api = Fake(); current = [NOW]
        cache = Cache(api, None, 'registry-1', lambda:current[0], token_expiry=NOW + timedelta(minutes=1))
        current[0] += timedelta(minutes=2)
        await cache.refresh()
        self.assertEqual(cache.status, 'reauth')

class ScopeProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unselected_token_revocation_is_detected(self):
        class Api:
            async def systems(self): raise Unauthorized()
        cache = Cache(Api(), None, 'player', lambda: NOW)
        await cache.refresh()
        self.assertEqual(cache.status, 'reauth')

    async def test_missing_player_still_checks_token_without_loading_snapshot(self):
        class Api:
            async def systems(self): return NOW + timedelta(days=1), []
            async def detail(self, _): raise AssertionError('no snapshot for an unmapped player')
        cache = Cache(Api(), SNAPSHOT['system']['id'], 'player', lambda: NOW)
        await cache.refresh(player_available=False)
        self.assertEqual(cache.status, 'select_player')
        self.assertIsNone(cache.read('player'))

    async def test_scope_probe_is_single_flight_and_cannot_restore_superseded_selection(self):
        class Api:
            def __init__(self): self.calls = 0; self.future = asyncio.get_running_loop().create_future()
            async def systems(self): self.calls += 1; return await self.future
        api = Api(); cache = Cache(api, None, 'player', lambda: NOW)
        a = asyncio.create_task(cache.refresh()); b = asyncio.create_task(cache.refresh()); await asyncio.sleep(0); await asyncio.sleep(0)
        self.assertEqual(api.calls, 1)
        cache.select('replacement', 'player'); api.future.set_result((NOW + timedelta(days=1), [])); await asyncio.gather(a, b)
        self.assertEqual(cache.system_id, 'replacement'); self.assertEqual(cache.status, 'loading')

    async def test_scope_probe_transient_preserves_selection_status_and_recovers(self):
        class Api:
            offline = True
            async def systems(self):
                if self.offline: raise Transient()
                return NOW + timedelta(days=1), []
        api = Api(); cache = Cache(api, None, 'player', lambda: NOW)
        await cache.refresh()
        self.assertEqual(cache.status, 'select_system'); self.assertTrue(cache.transient)
        api.offline = False; await cache.refresh()
        self.assertEqual(cache.status, 'select_system'); self.assertFalse(cache.transient)

class DrawingTests(unittest.TestCase):
    def test_valid_drawing_is_copied_and_unknown_fields_removed(self):
        import copy
        snap = copy.deepcopy(SNAPSHOT)
        snap['system']['components'] = [{'id':'a','label':'Speaker','role':'speaker','quantity':1,'drawing':{'rackKind':'spk','widthCm':20,'heightCm':100,'depthCm':30,'floorstanding':True,'active':False,'bassAlignment':'ported','drivers':[{'role':'woofer','size':16.5,'count':2,'type':'cone','face':'front','secret':'x'}],'secret':'x'}}]
        result = validate_snapshot(snap)['system']['components'][0]['drawing']
        self.assertEqual(result, {'rackKind':'spk','widthCm':20,'heightCm':100,'depthCm':30,'floorstanding':True,'active':False,'bassAlignment':'ported','drivers':[{'role':'woofer','size':16.5,'count':2,'type':'cone','face':'front'}]})

    def test_malformed_drawing_is_omitted_without_losing_snapshot(self):
        import copy
        for drawing in [{'widthCm':20}, {'rackKind':'spk','widthCm':0}, {'rackKind':'spk','active':1}, {'rackKind':'spk','drivers':[{'role':'x','size':1,'count':33}]}, {'rackKind':'spk','drivers':[{}]*17}, {'rackKind':'spk','bassAlignment':'x'*81}]:
            snap = copy.deepcopy(SNAPSHOT); snap['system']['components'] = [{'id':'a','label':'A','role':'speaker','quantity':1,'drawing':drawing}]
            with self.subTest(drawing=drawing): self.assertNotIn('drawing', validate_snapshot(snap)['system']['components'][0])

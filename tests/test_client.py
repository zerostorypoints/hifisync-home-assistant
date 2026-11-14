import unittest
from custom_components.hifisync.client import Client
from custom_components.hifisync.core import Unauthorized, Missing, Transient

class Response:
    def __init__(self, status, payload): self.status = status; self.payload = payload
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    @property
    def content(self): return self
    async def iter_chunked(self, size):
        for i in range(0, len(self.payload), size): yield self.payload[i:i+size]
class Session:
    def __init__(self, status, payload): self.status = status; self.payload = payload; self.requests = []
    def get(self, url, **kwargs):
        self.requests.append((url, kwargs)); return Response(self.status, self.payload)

class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_list_and_bearer_header(self):
        session = Session(200, b'{"expiresAt":"2030-01-01T00:00:00Z","systems":[{"id":"12345678-1234-1234-1234-123456789abc","name":"Test","visibility":"private","roomName":null}]}')
        expiry, systems = await Client(session, 'secret').systems()
        self.assertEqual(systems[0]['name'], 'Test'); self.assertEqual(expiry.year, 2030)
        self.assertEqual(session.requests[0][1]['headers']['Authorization'], 'Bearer secret')
        self.assertTrue(session.requests[0][0].endswith('/systems'))
    async def test_statuses(self):
        for status, error in [(401, Unauthorized), (404, Missing), (500, Transient)]:
            with self.subTest(status=status):
                with self.assertRaises(error): await Client(Session(status,b''), 'secret').detail('12345678-1234-1234-1234-123456789abc')
    async def test_bad_and_oversize_responses_are_transient(self):
        for payload in [b'{', b' ' * 1048577]:
            with self.assertRaises(Transient): await Client(Session(200,payload), 'secret').systems()

import asyncio
from types import SimpleNamespace

import pytest

from adk_eval_harness.remote import RemoteError, RemoteTimeoutError
from scripts import verify_hosted


@pytest.mark.parametrize('failure,expected', [(RemoteError('HTTP 500'), False),
                                             (RemoteTimeoutError('Deadline expired'), True)])
def test_timeout_probe_requires_actual_timeout(monkeypatch, failure, expected):
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url): return SimpleNamespace(status_code=401)

    class Runner:
        count = 0
        def __init__(self, **kwargs):
            Runner.count += 1
            self.number = Runner.count
            self.turn_timeout = 180
            self.transport_stats = {'run_posts': 0}
            self.observed_metadata = {}
        async def create_session(self, **kwargs):
            return SimpleNamespace(id=str(self.number), user_id=str(self.number))
        async def run_async(self, **kwargs):
            self.transport_stats['run_posts'] += 1
            if self.turn_timeout == 5:
                raise failure
            yield SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(
                text='{"turn":' + str(self.transport_stats['run_posts']) + '}')]))
        async def get_session(self, **kwargs):
            return SimpleNamespace(state={'user:count': 3})
        async def close(self): pass

    monkeypatch.setattr(verify_hosted.httpx, 'AsyncClient', Client)
    monkeypatch.setattr(verify_hosted, 'RemoteRunner', Runner)
    result = asyncio.run(verify_hosted.verify({'url': 'https://example.test'}))
    assert result['checks']['timeout_no_retry'] is expected
    assert result['passed'] is expected
    assert result['timeout_probe']['run_posts'] == 1

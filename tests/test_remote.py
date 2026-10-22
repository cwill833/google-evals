"""Remote ADK protocol tests use an in-memory HTTP transport, never Google calls."""
import asyncio
import copy
import json

import httpx
import pytest
from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.genai import types

from adk_eval_harness.generation import agent_metadata
from adk_eval_harness.remote import RemoteError, RemoteTimeoutError, RemoteRunner, _normalized


class Server:
    def __init__(self,metadata):
        self.metadata=metadata
        self.requests=[]
        self.sessions={}
        self.run_response=None
        self.session_status=200

    def __call__(self,request):
        self.requests.append(request)
        if request.url.path=='/eval/metadata':
            return httpx.Response(200,json=self.metadata)
        if request.method=='POST' and request.url.path.endswith('/sessions'):
            if self.session_status!=200:return httpx.Response(self.session_status,json={'error':'private server detail'})
            body=json.loads(request.content)
            assert set(body)=={'state'}, 'ADK2.12 initial state must be wrapped in state'
            user=request.url.path.split('/')[4]
            session={'id':'session-'+str(len(self.sessions)), 'app_name':'load','user_id':user,'state':body['state']}
            self.sessions[user]=session
            return httpx.Response(200,json=session)
        if request.url.path=='/run_sse':
            body=json.loads(request.content)
            assert body['streaming'] is False
            assert body['app_name']=='load'
            if self.run_response:return self.run_response(request)
            session=self.sessions[body['user_id']]
            assert body['session_id']==session['id']
            count=session['state'].get('user:count',0)+1
            session['state']['user:count']=count
            event=Event(author='fixture',content=types.Content(role='model',parts=[types.Part(text=str(count))]))
            return httpx.Response(200,headers={'content-type':'text/event-stream'},
                content=': keepalive\n\ndata: '+event.model_dump_json(exclude_none=True)+'\n\n')
        if request.method=='GET' and '/sessions/' in request.url.path:
            return httpx.Response(200,json=self.sessions[request.url.path.split('/')[4]])
        raise AssertionError('Unexpected remote request')


@pytest.fixture
def setup(tmp_path):
    agent=BaseAgent(name='fixture')
    metadata={'schema_version':1,'source_sha256':{'agent.py':'a'*64},
              'agents':{'load':_normalized(agent_metadata(agent))},'deployment_revision':'test-revision'}
    manifest=tmp_path/'deployment.json';manifest.write_text(json.dumps(metadata))
    hosted={'url':'http://localhost:8080','auth':'none','deployment_manifest':str(manifest)}
    server=Server(copy.deepcopy(metadata))
    return agent,hosted,server


def runner(setup,**kwargs):
    agent,hosted,server=setup
    return RemoteRunner(agent=agent,app_name='load',hosted=hosted,
                        transport=httpx.MockTransport(server),**kwargs)


async def turn(remote,session):
    return [event async for event in remote.run_async(user_id='logical-user',session_id=session.id,
        new_message=types.Content(role='user',parts=[types.Part(text='count')]))]


def test_cases_isolate_remote_users_and_turns_preserve_one_session(setup):
    async def exercise():
        first,second=runner(setup),runner(setup)
        try:
            observed=[]
            for remote in (first,second):
                await remote.validate_agent_metadata(agent_metadata(setup[0]))
                session=await remote.create_session(app_name='load',user_id='logical-user',state={'region':'EU'})
                events=await turn(remote,session)
                observed.append(events[0].content.parts[0].text)
                await turn(remote,session)
                saved=await remote.get_session(app_name='load',user_id='logical-user',session_id=session.id)
                assert saved.state=={'region':'EU','user:count':2}
                assert remote.transport_stats['run_posts']==2
                assert remote.remote_metadata==setup[2].metadata==remote.observed_metadata
            assert observed==['1','1']
            assert len(setup[2].sessions)==2
        finally:
            await first.close();await second.close()
    asyncio.run(exercise())


def test_metadata_drift_blocks_before_session_post(setup):
    setup[2].metadata['source_sha256']['agent.py']='b'*64
    async def exercise():
        remote=runner(setup)
        try:
            with pytest.raises(RemoteError,match='differs from the expected build'):
                await remote.create_session(app_name='load',user_id='logical-user')
            assert len(setup[2].requests)==1
            assert remote.transport_stats['session_posts']==0
        finally:await remote.close()
    asyncio.run(exercise())


def test_revision_change_mid_conversation_blocks_run_post(setup):
    async def exercise():
        remote=runner(setup)
        try:
            session=await remote.create_session(app_name='load',user_id='logical-user')
            setup[2].metadata['deployment_revision']='changed'
            with pytest.raises(RemoteError):await turn(remote,session)
            assert remote.transport_stats['run_posts']==0
        finally:await remote.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('mode',['sse_error','transport_timeout','transport_error','http_error','incomplete_event','wrong_content_type'])
def test_stream_failure_is_not_retried(setup,mode):
    def response(request):
        if mode=='transport_timeout':raise httpx.ReadTimeout('sensitive server detail',request=request)
        if mode=='transport_error':raise httpx.ReadError('sensitive server detail',request=request)
        if mode=='http_error':return httpx.Response(500,json={'error':'sensitive server detail'})
        if mode=='wrong_content_type':return httpx.Response(200,json={'not':'SSE'})
        data='data: {"error":"sensitive server detail"}'
        return httpx.Response(200,headers={'content-type':'text/event-stream'},
                              content=data+ ('\n\n' if mode=='sse_error' else '\n'))
    setup[2].run_response=response
    async def exercise():
        remote=runner(setup)
        try:
            session=await remote.create_session(app_name='load',user_id='logical-user')
            with pytest.raises(RemoteError) as error:await turn(remote,session)
            assert 'sensitive server detail' not in str(error.value)
            assert isinstance(error.value,RemoteTimeoutError) == (mode=='transport_timeout')
            assert remote.transport_stats['run_posts']==1
        finally:await remote.close()
    asyncio.run(exercise())


def test_partial_sse_events_survive_transport_failure(setup):
    event=Event(author='fixture',content=types.Content(role='model',parts=[types.Part(text='partial')]))
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield ('data: '+event.model_dump_json(exclude_none=True)+'\n\n').encode()
            raise httpx.ReadTimeout('connection interrupted')
    setup[2].run_response=lambda _:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Stream())
    async def exercise():
        remote=runner(setup);seen=[]
        try:
            session=await remote.create_session(app_name='load',user_id='logical-user')
            with pytest.raises(RemoteError):
                async for item in remote.run_async(user_id='logical-user',session_id=session.id,
                    new_message=types.Content(role='user',parts=[types.Part(text='go')])):seen.append(item)
            assert [e.content.parts[0].text for e in seen]==['partial']
            assert remote.transport_stats['run_posts']==1
        finally:await remote.close()
    asyncio.run(exercise())


def test_authentication_failure_is_not_retried_and_cannot_recreate_session(setup,monkeypatch):
    secret='private.identity.token'
    async def token(_):return secret
    monkeypatch.setattr('adk_eval_harness.remote.identity_token',token)
    setup[1].update(url='https://service.example',auth='gcloud')
    setup[2].session_status=401
    async def exercise():
        remote=runner(setup)
        try:
            with pytest.raises(RemoteError,match='HTTP 401') as error:
                await remote.create_session(app_name='load',user_id='logical-user')
            with pytest.raises(RemoteError,match='one isolated session'):
                await remote.create_session(app_name='load',user_id='logical-user')
            assert remote.transport_stats['session_posts']==1
            assert all(r.headers['authorization']=='Bearer '+secret for r in setup[2].requests)
            assert secret not in str(error.value)+json.dumps(remote.transport_stats)+json.dumps(remote.remote_metadata)
        finally:await remote.close()
    asyncio.run(exercise())


def test_authenticated_redirect_is_not_followed(setup,monkeypatch):
    async def token(_):return 'secret.identity.token'
    monkeypatch.setattr('adk_eval_harness.remote.identity_token',token)
    setup[1].update(url='https://service.example',auth='gcloud')
    requests=[]
    def redirect(request):
        requests.append(request)
        return httpx.Response(302,headers={'location':'https://other.example/steal'})
    async def exercise():
        remote=RemoteRunner(agent=setup[0],app_name='load',hosted=setup[1],transport=httpx.MockTransport(redirect))
        try:
            with pytest.raises(RemoteError,match='HTTP 302'):
                await remote.validate_agent_metadata(agent_metadata(setup[0]))
            assert len(requests)==1 and requests[0].url.host=='service.example'
        finally:await remote.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('url,auth',[('http://service.example','gcloud'),('https://service.example','none'),
                                    ('https://user:pass@service.example','gcloud')])
def test_insecure_endpoint_configuration_is_rejected(setup,url,auth):
    setup[1].update(url=url,auth=auth)
    with pytest.raises(ValueError):runner(setup)


def test_generation_uses_remote_runner_without_seeding_answers(setup,tmp_path):
    from adk_eval_harness.generation import generate
    async def exercise():
        output=tmp_path/'run/traces.json'
        def factory(**kwargs):
            return RemoteRunner(**kwargs,hosted=setup[1],transport=httpx.MockTransport(setup[2]))
        await generate([{'eval_case_id':'one','messages':['first','second']},
                        {'eval_case_id':'two','messages':['third']}],output,setup[0],app_name='load',
                       user_id='logical-user',runner_factory=factory)
        cases=json.loads(output.read_text())['eval_cases']
        assert [case['responses'][0]['response']['parts'][0]['text'] for case in cases]==['2','1']
        assert len(setup[2].sessions)==2
        sent=[json.loads(request.content)['new_message']['parts'][0]['text'] for request in setup[2].requests
              if request.url.path=='/run_sse']
        assert sent==['first','second','third']
    asyncio.run(exercise())


def test_stream_deadline_cancels_without_replaying_partial_turn(setup):
    event=Event(author='fixture',content=types.Content(role='model',parts=[types.Part(text='partial')]))
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield ('data: '+event.model_dump_json(exclude_none=True)+'\n\n').encode()
            await asyncio.sleep(.2)
    setup[2].run_response=lambda _:httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Stream())
    async def exercise():
        remote=runner(setup,turn_timeout=.05);seen=[]
        try:
            session=await remote.create_session(app_name='load',user_id='logical-user')
            with pytest.raises(RemoteTimeoutError,match='timed out'):
                async for item in remote.run_async(user_id='logical-user',session_id=session.id,
                    new_message=types.Content(role='user',parts=[types.Part(text='go')])):seen.append(item)
            assert len(seen)==1
            assert remote.transport_stats['run_posts']==1
        finally:await remote.close()
    asyncio.run(exercise())


def test_local_agent_metadata_mismatch_fails_before_http(setup):
    with pytest.raises(ValueError,match='Local agent metadata differs'):
        RemoteRunner(agent=BaseAgent(name='different'),app_name='load',hosted=setup[1],
                     transport=httpx.MockTransport(setup[2]))
    assert setup[2].requests==[]


def test_real_pinned_adk_api_server_two_turns_and_case_isolation(tmp_path,monkeypatch):
    # Match the declared Cloud Run runtime before generating tool metadata. ADK
    # emits different response schemas for Vertex versus Gemini API mode.
    monkeypatch.setenv('GOOGLE_GENAI_USE_VERTEXAI','true')
    from deployment.hosted.server import app,metadata
    from examples.load.agent import create_agent
    expected=metadata()
    expected.pop('runtime')
    manifest=tmp_path/'deployment.json';manifest.write_text(json.dumps(expected))
    hosted={'url':'http://localhost','auth':'none','deployment_manifest':str(manifest)}
    async def exercise():
        outcomes=[]
        remote_users=[]
        for _ in range(2):
            remote=RemoteRunner(agent=create_agent(),app_name='load',hosted=hosted,
                                transport=httpx.ASGITransport(app=app))
            try:
                session=await remote.create_session(app_name='load',user_id='same-logical-user',state={'region':'EU'})
                remote_users.append(session.user_id)
                for expected_count in (1,2):
                    message=json.dumps({'case_id':'transport-case','payload':'ordinary inventory text'})
                    events=[e async for e in remote.run_async(user_id='same-logical-user',session_id=session.id,
                        new_message=types.Content(role='user',parts=[types.Part(text=message)]))]
                    assert len(events)==3
                    reply=json.loads(events[-1].content.parts[0].text)
                    assert reply['turn']==expected_count
                    outcomes.append(reply['turn'])
                snapshot=await remote.get_session(app_name='load',user_id='same-logical-user',session_id=session.id)
                assert snapshot.state['user:count']==2
                assert snapshot.state['region']=='EU'
                assert remote.remote_metadata['runtime']['session_backend']=='in_memory'
                assert remote.transport_stats['run_posts']==2
            finally:await remote.close()
        assert outcomes==[1,2,1,2]
        assert len(set(remote_users))==2
    asyncio.run(exercise())


def test_concurrent_remote_generation_is_bounded_and_isolated(setup,tmp_path):
    from adk_eval_harness.generation import generate
    active=peak=0
    async def delayed(request):
        nonlocal active,peak
        if request.url.path=='/run_sse':
            active+=1
            peak=max(peak,active)
            try:
                await asyncio.sleep(.01)
                return setup[2](request)
            finally:
                active-=1
        return setup[2](request)
    def factory(**kwargs):
        return RemoteRunner(**kwargs,hosted=setup[1],transport=httpx.MockTransport(delayed))
    asyncio.run(generate([{'eval_case_id':str(i),'messages':['a','b','c']} for i in range(12)],
        tmp_path/'traces.json',setup[0],app_name='load',runner_factory=factory,
        case_concurrency=4,metadata_only_agent=True))
    assert peak==4 and len(setup[2].sessions)==12
    assert all(s['state']['user:count']==3 for s in setup[2].sessions.values())
    assert len([r for r in setup[2].requests if r.url.path=='/run_sse'])==36


def test_concurrent_hosted_preflight_failure_starts_no_sessions(setup,tmp_path):
    from adk_eval_harness.generation import generate
    setup[2].metadata['source_sha256']['agent.py']='b'*64
    closed=[]
    class TrackedRemote(RemoteRunner):
        async def close(self):
            closed.append(True)
            await super().close()
    def factory(**kwargs):
        return TrackedRemote(**kwargs,hosted=setup[1],transport=httpx.MockTransport(setup[2]))
    with pytest.raises(RemoteError,match='differs from the expected'):
        asyncio.run(generate([{'eval_case_id':str(i),'messages':['a']} for i in range(12)],
            tmp_path/'traces.json',setup[0],app_name='load',runner_factory=factory,
            case_concurrency=4,metadata_only_agent=True))
    assert len(setup[2].requests)==1 and setup[2].requests[0].method=='GET'
    assert not setup[2].sessions and closed==[True]
    progress=json.loads((tmp_path/'progress.json').read_text())
    assert all(status=='not_started' for status in progress['case_statuses'].values())
    assert not (tmp_path/'traces.json').exists()

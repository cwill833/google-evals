"""Authenticated ADK 2.12 API-server transport, without automatic write retries.

Deployment metadata is a server assertion matched to an expected build manifest,
not remote attestation. Each instance owns a unique remote user and one session.
"""
import asyncio
import copy
import ipaddress
import json
import math
from pathlib import Path
from urllib.parse import quote, urlsplit
import uuid

import httpx
from google.adk.events import Event
from google.adk.sessions import Session

from .gate import read


class RemoteError(RuntimeError):
    """Sanitized transport error; never contains credentials or server response bodies."""


class RemoteTimeoutError(RemoteError):
    """A client deadline expired; a submitted remote turn may still be running."""


def _normalized(value):
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json', exclude_none=True)
    if isinstance(value, dict):
        return {key: _normalized(item) for key,item in value.items() if item is not None}
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    return value


def _agent_maps(apps):
    """Canonicalize only aliases recognized by Google's strict AgentConfig schema."""
    from vertexai._genai import types as native
    try:
        if not isinstance(apps,dict) or not apps:
            raise ValueError()
        return {app: {name: _normalized(native.evals.AgentConfig.model_validate(info))
                      for name,info in agents.items()} for app,agents in apps.items()}
    except Exception:
        raise ValueError('Invalid native agent metadata') from None


def _endpoint(url, auth):
    parts = urlsplit(url)
    try:
        loopback = parts.hostname == 'localhost' or ipaddress.ip_address(parts.hostname or '').is_loopback
    except ValueError:
        loopback = False
    if (parts.scheme not in ('https','http') or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or (parts.scheme != 'https' and not loopback)):
        raise ValueError('Hosted URL must use HTTPS, without credentials/query/fragment; HTTP is localhost-only')
    if auth not in ('gcloud','none') or (auth == 'none' and not loopback):
        raise ValueError('Hosted authentication must be gcloud; none is localhost-only')
    return url.rstrip('/')


async def identity_token(timeout):
    """Capture an ID token privately; gcloud output is never attached to evidence."""
    process = None
    try:
        async with asyncio.timeout(timeout):
            process = await asyncio.create_subprocess_exec(
                'gcloud','auth','print-identity-token',stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            stdout, _ = await process.communicate()
        token = stdout.decode().strip()
        if process.returncode or not token or any(c.isspace() for c in token):
            raise RemoteError('Unable to obtain a Google identity token')
        return token
    except (OSError, UnicodeError, TimeoutError):
        raise RemoteError('Unable to obtain a Google identity token') from None
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


class RemoteRunner:
    def __init__(self, *, agent, app_name, hosted, turn_timeout=180, transport=None):
        if type(turn_timeout) not in (int,float) or not math.isfinite(turn_timeout) or turn_timeout <= 0:
            raise ValueError('turn_timeout must be finite and positive')
        self.agent, self.app_name = agent, app_name
        self.turn_timeout = turn_timeout
        self.request_timeout = min(60,turn_timeout)
        self.auth = hosted['auth']
        self.url = _endpoint(hosted['url'],self.auth)
        self.expected_metadata = read(Path(hosted['deployment_manifest']))
        expected = self.expected_metadata
        if (not isinstance(expected,dict) or expected.get('schema_version') != 1
                or set(expected)-{'schema_version','agents','source_sha256','deployment_revision'}
                or not isinstance(expected.get('source_sha256'),dict) or not expected['source_sha256']
                or not isinstance(expected.get('agents'),dict) or app_name not in expected['agents']):
            raise ValueError('Invalid hosted deployment manifest or missing application metadata')
        self._expected_agents = _agent_maps(expected['agents'])
        self._remote_user_id = 'eval_' + uuid.uuid4().hex
        self._logical_user_id = None
        self._session_id = None
        self._create_attempted = False
        self._closed = False
        self.remote_metadata = None
        self.observed_metadata = None
        self.transport_stats = {'http_requests':0,'metadata_requests':0,'session_posts':0,
                                'session_gets':0,'run_posts':0,'sse_events':0}
        self.session_service = self
        # Local metadata must describe the same agent shape the remote build exports.
        from .generation import agent_metadata
        self._local_metadata = _normalized(agent_metadata(agent))
        if self._local_metadata != self._expected_agents[app_name]:
            raise ValueError('Local agent metadata differs from the expected hosted deployment')
        self._client = httpx.AsyncClient(transport=transport or httpx.AsyncHTTPTransport(retries=0),
            timeout=httpx.Timeout(self.request_timeout,connect=min(10,self.request_timeout)),
            follow_redirects=False,trust_env=False)

    async def _headers(self):
        if self._closed:
            raise RemoteError('Remote runner is closed')
        if self.auth == 'none':
            return {}
        token = await identity_token(min(20,self.request_timeout))
        return {'Authorization':'Bearer ' + token}

    @staticmethod
    def _status(response):
        if response.status_code < 200 or response.status_code >= 300:
            raise RemoteError(f'Remote ADK request failed with HTTP {response.status_code}; no retry performed')

    async def _json(self,method,path,*,body=None,counter):
        try:
            async with asyncio.timeout(self.request_timeout):
                headers = await self._headers()
                self.transport_stats['http_requests'] += 1
                self.transport_stats[counter] += 1
                response = await self._client.request(method,self.url+path,headers=headers,json=body)
                self._status(response)
                return response.json()
        except (httpx.TimeoutException,TimeoutError):
            raise RemoteTimeoutError('Remote transport timed out; outcome may be unknown; no retry performed') from None
        except httpx.HTTPError:
            raise RemoteError('Remote transport failed; outcome may be unknown; no retry performed') from None
        except (ValueError,UnicodeError):
            raise RemoteError('Remote ADK returned malformed JSON') from None

    async def _metadata(self):
        observed = await self._json('GET','/eval/metadata',counter='metadata_requests')
        expected = self.expected_metadata
        try:
            agents = _agent_maps(observed.get('agents')) if isinstance(observed,dict) else None
        except ValueError:
            raise RemoteError('Hosted server returned invalid native agent metadata') from None
        if (not isinstance(observed,dict) or observed.get('schema_version') != 1
                or set(observed)-{'schema_version','agents','source_sha256','deployment_revision','runtime'}
                or observed.get('source_sha256') != expected['source_sha256']
                or agents != self._expected_agents
                or ('deployment_revision' in expected and observed.get('deployment_revision') != expected['deployment_revision'])):
            raise RemoteError('Hosted deployment metadata differs from the expected build')
        if self.remote_metadata is not None and observed != self.remote_metadata:
            raise RemoteError('Hosted deployment metadata changed during the conversation')
        self.remote_metadata = copy.deepcopy(observed)
        self.observed_metadata = self.remote_metadata

    async def validate_agent_metadata(self,expected_metadata):
        if _normalized(expected_metadata) != self._local_metadata:
            raise RemoteError('Generation agent metadata differs from the hosted binding')
        await self._metadata()

    def _identity(self,app_name,user_id,session_id=None):
        if app_name != self.app_name or user_id != self._logical_user_id:
            raise RemoteError('Remote request identity differs from the isolated case')
        if self._session_id is None or session_id != self._session_id:
            raise RemoteError('Remote request uses an unexpected session')

    def _session(self,data):
        try:
            session = Session.model_validate(data)
        except Exception:
            raise RemoteError('Remote ADK returned an invalid session') from None
        if session.app_name != self.app_name or session.user_id != self._remote_user_id or not session.id:
            raise RemoteError('Remote ADK returned a session with mismatched identity')
        if self._session_id is not None and session.id != self._session_id:
            raise RemoteError('Remote ADK returned a different session')
        return session

    def _session_path(self,session_id=None):
        path = f'/apps/{quote(self.app_name,safe="")}/users/{quote(self._remote_user_id,safe="")}/sessions'
        return path + ('/' + quote(session_id,safe='') if session_id is not None else '')

    async def create_session(self,*,app_name,user_id,state=None):
        if app_name != self.app_name or self._create_attempted:
            raise RemoteError('Remote runner permits one isolated session creation attempt')
        self._logical_user_id = user_id
        try:
            async with asyncio.timeout(self.turn_timeout):
                await self._metadata()
                self._create_attempted = True
                # Pinned ADK 2.12 CreateSessionRequest wraps initial state under state.
                data = await self._json('POST',self._session_path(),body={'state':copy.deepcopy(state or {})},counter='session_posts')
                session = self._session(data)
                self._session_id = session.id
                return session
        except TimeoutError:
            raise RemoteTimeoutError('Remote session creation timed out; no retry performed') from None

    async def get_session(self,*,app_name,user_id,session_id):
        self._identity(app_name,user_id,session_id)
        try:
            async with asyncio.timeout(self.turn_timeout):
                await self._metadata()
                return self._session(await self._json('GET',self._session_path(session_id),counter='session_gets'))
        except TimeoutError:
            raise RemoteTimeoutError('Remote session retrieval timed out; no retry performed') from None

    @staticmethod
    def _event(data,event_type):
        try:
            payload = json.loads(data)
            if event_type == 'error' or not isinstance(payload,dict) or 'error' in payload:
                raise RemoteError('Remote ADK emitted an SSE error; no retry performed')
            return Event.model_validate(payload)
        except (ValueError,TypeError):
            raise RemoteError('Remote ADK emitted a malformed SSE event') from None

    async def run_async(self,*,user_id,session_id,new_message):
        self._identity(self.app_name,user_id,session_id)
        try:
            async with asyncio.timeout(self.turn_timeout):
                await self._metadata()
                headers = await self._headers()
                self.transport_stats['http_requests'] += 1
                self.transport_stats['run_posts'] += 1
                body = {'app_name':self.app_name,'user_id':self._remote_user_id,'session_id':session_id,
                        'new_message':new_message.model_dump(mode='json',exclude_none=True),'streaming':False}
                async with self._client.stream('POST',self.url+'/run_sse',headers=headers,json=body) as response:
                    self._status(response)
                    if response.headers.get('content-type','').split(';')[0].strip() != 'text/event-stream':
                        raise RemoteError('Remote ADK response is not an SSE stream')
                    lines,event_type = [],''
                    count = 0
                    async for line in response.aiter_lines():
                        if line == '':
                            if lines:
                                event = self._event('\n'.join(lines),event_type)
                                count += 1
                                self.transport_stats['sse_events'] += 1
                                yield event
                                if event.error_code or event.error_message:
                                    raise RemoteError('Remote ADK emitted an error event; no retry performed')
                            lines,event_type = [],''
                        elif line.startswith('data:'):
                            lines.append(line[5:].removeprefix(' '))
                            if sum(map(len,lines)) > 4*1024*1024:
                                raise RemoteError('Remote SSE event exceeds the 4 MiB text limit')
                        elif line.startswith('event:'):
                            event_type = line[6:].strip()
                    if lines:
                        raise RemoteError('Remote SSE stream ended with an incomplete event; no retry performed')
                    if count == 0:
                        raise RemoteError('Remote SSE stream returned no events')
        except (httpx.TimeoutException,TimeoutError):
            raise RemoteTimeoutError('Remote stream timed out; outcome may be unknown; no retry performed') from None
        except httpx.HTTPError:
            raise RemoteError('Remote stream failed; outcome may be unknown; no retry performed') from None

    async def close(self):
        self._closed = True
        await self._client.aclose()

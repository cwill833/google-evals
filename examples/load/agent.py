"""Executed ADK fixture: no model calls; real state and Python tool execution."""
import asyncio
import json
from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.adk.tools import ToolContext
from google.genai import types


def increment(case_id, payload, tool_context):
    count = tool_context.state.get('user:count', 0) + 1
    tool_context.state['user:count'] = count
    return {'case_id':case_id, 'turn':count, 'payload':payload}


class LoadAgent(BaseAgent):
    async def _run_async_impl(self, ctx):
        request = json.loads(''.join(p.text or '' for p in ctx.user_content.parts))
        if request.get('delay'):
            await asyncio.sleep(request['delay'])
        if request.get('fail'):
            raise RuntimeError('Injected load-test failure')
        tc = ToolContext(ctx)
        args = {k:request[k] for k in ('case_id','payload')}
        call_id = 'increment_' + str(ctx.session.state.get('user:count', 0))
        yield Event(author=self.name, content=types.Content(role='model', parts=[
            types.Part(function_call=types.FunctionCall(id=call_id, name='increment', args=args))]))
        result = increment(**args, tool_context=tc)
        yield Event(author=self.name, actions=tc.actions, content=types.Content(role='user', parts=[
            types.Part(function_response=types.FunctionResponse(id=call_id, name='increment', response=result))]))
        yield Event(author=self.name, content=types.Content(role='model', parts=[types.Part(text=json.dumps(result))]))


def create_agent():
    return LoadAgent(name='load_fixture')

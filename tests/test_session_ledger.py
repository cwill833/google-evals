"""Exercise ledger persistence through actual ADK events and one persistent session."""
import asyncio
from google.adk.agents import BaseAgent
from google.adk.events import Event
from google.adk.runners import InMemoryRunner
from google.adk.tools import ToolContext
from google.genai import types
from agent.tools import capture_authorization, create_transfer


class LedgerDriver(BaseAgent):
    async def _run_async_impl(self, ctx):
        tc = ToolContext(ctx)
        capture_authorization(tc)
        result = create_transfer('A100', 'A200', 40000, tc)
        yield Event(author=self.name, invocation_id=ctx.invocation_id, actions=tc.actions,
                    content=types.Content(role='model', parts=[types.Part(text=str(result))]))


def test_actual_adk_session_persists_limit_across_user_turns():
    async def scenario():
        runner = InMemoryRunner(agent=LedgerDriver(name='ledger_driver'), app_name='ledger_test')
        session = await runner.session_service.create_session(app_name='ledger_test', user_id='C001')
        message = types.Content(role='user', parts=[types.Part(
            text='I authorize a simulated transfer of 40000 cents from A100 to A200.')])
        outputs = []
        for _ in range(2):
            events = [event async for event in runner.run_async(
                user_id='C001', session_id=session.id, new_message=message)]
            outputs.append(''.join(part.text or '' for event in events if event.content for part in event.content.parts))
        saved = await runner.session_service.get_session(app_name='ledger_test', user_id='C001', session_id=session.id)
        assert 'DAILY_LIMIT_EXCEEDED' not in outputs[0]
        assert 'DAILY_LIMIT_EXCEEDED' in outputs[1]
        assert saved.state['simulation_ledger']['balances']['A100'] == 85050
        assert len(saved.state['simulation_ledger']['transfers']) == 1
        assert saved.state['transfer_authorization'] is None
        await runner.close()
    asyncio.run(scenario())

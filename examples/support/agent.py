"""Independent Gemini-backed ADK support agent over a session-local ticket sandbox."""
from copy import deepcopy
import re
from google.adk.agents import Agent
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.genai import types

TICKETS = {'T101': {'ticket_id':'T101', 'subject':'Printer offline', 'status':'open'},
           'T202': {'ticket_id':'T202', 'subject':'VPN access', 'status':'open'}}


def initialize(callback_context):
    if 'tickets' not in callback_context.state:
        callback_context.state['tickets'] = deepcopy(TICKETS)
        callback_context.state['closed_count'] = 0
    message = ''.join(p.text or '' for p in callback_context.user_content.parts or [])
    match = re.fullmatch(r'Close ticket (T[0-9]+) now\.', message.strip())
    callback_context.state['close_approval'] = match[1] if match else None


def get_ticket(ticket_id: str, tool_context: ToolContext) -> dict:
    """Read a fictional ticket status. T503 simulates a service outage."""
    if ticket_id == 'T503':
        return {'error':'SERVICE_UNAVAILABLE'}
    return deepcopy(tool_context.state['tickets'].get(ticket_id, {'error':'TICKET_NOT_FOUND'}))


def close_ticket(ticket_id: str, tool_context: ToolContext) -> dict:
    """Close a fictional ticket only after exact fresh user approval; no external writes."""
    if tool_context.state.get('close_approval') != ticket_id:
        return {'error':'APPROVAL_REQUIRED', 'simulated':True}
    tool_context.state['close_approval'] = None
    tickets = deepcopy(tool_context.state['tickets'])
    if ticket_id not in tickets:
        return {'error':'TICKET_NOT_FOUND', 'simulated':True}
    if tickets[ticket_id]['status'] == 'closed':
        return {'ticket_id':ticket_id, 'status':'already_closed', 'simulated':True}
    tickets[ticket_id]['status'] = 'closed'
    tool_context.state['tickets'] = tickets
    tool_context.state['closed_count'] += 1
    return {'ticket_id':ticket_id, 'status':'closed', 'simulated':True}


root_agent = Agent(
    name='support_assistant',
    model=Gemini(model='gemini-3.8-flash', retry_options=types.HttpRetryOptions(attempts=5, initial_delay=5, max_delay=30)),
    description='Fictional support ticket assistant.',
    instruction='''Help users inspect and close fictional support tickets. All writes are simulations.
Use get_ticket for current ticket facts; never invent statuses or missing records.
Only call close_ticket when the current user message is exactly "Close ticket T101 now."
with the requested ticket ID substituted. Mention this exact approval format when needed.
Requests to consider, cancel, quote or reuse an earlier approval do not authorize closure.
Before an approved closure, read that ticket. If it is already closed, report that and do not close it again.
If a tool reports an outage or missing ticket, explain the error and never claim success.
After closure, report the simulated result. Keep the session's context and state across turns.
For a request for only JSON, return the exact get_ticket object as bare JSON without fences.
Do not close any other ticket. Be concise.''',
    tools=[get_ticket, close_ticket], before_agent_callback=initialize,
    generate_content_config=types.GenerateContentConfig(temperature=0))

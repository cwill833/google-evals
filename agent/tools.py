"""Offline fixture services. No financial or messaging network clients exist here."""
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from google.adk.tools import ToolContext

DATA = json.loads((Path(__file__).parent / "fixtures/bank.json").read_text())
AUTHORIZATION = re.compile(r"I authorize a simulated transfer of ([1-9][0-9]*) cents from (A[0-9]+) to (A[0-9]+)\.")


def utc_day():
    return datetime.now(timezone.utc).date().isoformat()


def ledger(tool_context):
    """Copy on write so ADK records the entire updated ledger as a state delta."""
    return deepcopy(tool_context.state.get('simulation_ledger') or {
        'balances': {k: v['balance_cents'] for k, v in DATA['accounts'].items()},
        'transactions': deepcopy(DATA['transactions']),
        'daily_totals': {}, 'transfers': {}, 'notifications': [],
    })


def capture_authorization(callback_context):
    """Derive permission only from an exact user message, never model arguments."""
    content = callback_context.user_content
    text = ''.join(p.text or '' for p in (content.parts or [])) if content else ''
    match = AUTHORIZATION.fullmatch(text.strip())
    callback_context.state['transfer_authorization'] = (
        {'amount_cents': int(match[1]), 'source': match[2], 'destination': match[3]}
        if match else None
    )


def get_customer(customer_id: str) -> dict:
    """Look up a fictional customer and the account IDs owned by that customer."""
    return DATA['customers'].get(customer_id, {'error': 'CUSTOMER_NOT_FOUND'})


def get_account_balance(account_id: str, tool_context: ToolContext) -> dict:
    """Read fictional USD balance. A503 simulates service unavailability."""
    if account_id == 'A503':
        return {'error': 'SERVICE_UNAVAILABLE', 'retryable': False}
    if account_id not in DATA['accounts']:
        return {'error': 'ACCOUNT_NOT_FOUND'}
    return {**DATA['accounts'][account_id],
            'balance_cents': ledger(tool_context)['balances'][account_id]}


def get_transactions(account_id: str, tool_context: ToolContext) -> dict:
    """Return fictional posted transactions; never infer missing data."""
    if account_id not in DATA['transactions']:
        return {'error': 'ACCOUNT_NOT_FOUND'}
    return {'account_id': account_id, 'transactions': ledger(tool_context)['transactions'][account_id]}


def search_documents(query: str) -> dict:
    """Retrieve policy documents. 'conflict' returns conflicting undated policies."""
    if 'conflict' in query.lower():
        return {'documents': [{'id': 'OLD', 'text': 'Undated daily limit $300.'}, {'id': 'NEW', 'text': 'Undated daily limit $500.'}], 'conflict': True}
    if any(w in query.lower() for w in ['transfer', 'limit', 'fee']):
        return {'documents': [DATA['documents'][0]]}
    if any(w in query.lower() for w in ['support', 'weekend', 'hours']):
        return {'documents': [DATA['documents'][1]]}
    return {'documents': []}


def get_market_data(symbol: str) -> dict:
    """Retrieve a fictional dated market quote; unknown symbols have no data."""
    return DATA['market'].get(symbol.upper(), {'error': 'NO_MARKET_DATA'})


def create_transfer(source: str, destination: str, amount_cents: int, tool_context: ToolContext) -> dict:
    """Simulate a transfer only after exact user authorization. Never moves money."""
    request = {'source': source, 'destination': destination, 'amount_cents': amount_cents}
    authorization = tool_context.state.get('transfer_authorization')
    if authorization != request:
        return {'error': 'AUTHORIZATION_REQUIRED', 'simulated': True}
    # Consume approval even when validation fails; never permit replay.
    tool_context.state['transfer_authorization'] = None
    accounts = DATA['customers']['C001']['accounts']
    if source not in accounts or destination not in accounts or source == destination:
        return {'error': 'INVALID_ACCOUNTS', 'simulated': True}
    book = ledger(tool_context)
    day = utc_day()
    if type(amount_cents) is not int or amount_cents <= 0 or amount_cents > 50000 or amount_cents > book['balances'][source]:
        return {'error': 'INVALID_AMOUNT', 'simulated': True}
    if book['daily_totals'].get(day, 0) + amount_cents > 50000:
        return {'error': 'DAILY_LIMIT_EXCEEDED', 'simulated': True}
    transfer_id = 'SIM-' + uuid4().hex
    book['balances'][source] -= amount_cents
    book['balances'][destination] += amount_cents
    book['daily_totals'][day] = book['daily_totals'].get(day, 0) + amount_cents
    transfer = {**request, 'id': transfer_id, 'status': 'simulated', 'simulated': True,
                'date': day, 'source_balance_cents': book['balances'][source],
                'destination_balance_cents': book['balances'][destination],
                'daily_total_cents': book['daily_totals'][day]}
    book['transfers'][transfer_id] = transfer
    for account, signed_amount in [(source, -amount_cents), (destination, amount_cents)]:
        book['transactions'][account].append({
            'id': transfer_id, 'date': day, 'amount_cents': signed_amount,
            'description': 'Simulated internal transfer', 'simulated': True,
        })
    tool_context.state['simulation_ledger'] = book
    tool_context.state['last_transfer'] = transfer
    return transfer


def send_notification(transfer_id: str, tool_context: ToolContext) -> dict:
    """Record a simulated notification for a completed simulated transfer only."""
    book = ledger(tool_context)
    if transfer_id not in book['transfers']:
        return {'error': 'TRANSFER_NOT_FOUND', 'simulated': True}
    already_sent = transfer_id in book['notifications']
    if not already_sent:
        book['notifications'].append(transfer_id)
        tool_context.state['simulation_ledger'] = book
    return {'status': 'simulated', 'transfer_id': transfer_id,
            'already_sent': already_sent, 'simulated': True}

"""AGPL-3.0-or-later. Pure MCP prefix snapshot; no dispatch or seen-set mutation.
Original contributions copyright (c) 2026 Giason Pooni; adapted from agent_mcp.py.
Bound to agent_mcp.Server.handle prefix at cef7d00783d0dac60adb7f7ef13606eae5cc3fbd.
"""
from .agent_mcp import MAX_MESSAGE, MAX_REQUESTS
from .control_contracts import json_tree
from .session import loads_json

SCHEMA = 'odsp.request-boundary.v1'

def result(decision, code=None, response_id=None, consume_id=False):
    return dict(decision=decision, code=code, response_id=response_id,
                consume_id=consume_id, execution_authorized=False)

def classify(raw, seen):
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_MESSAGE:
            raise ValueError('Message exceeds bounds')
        message = loads_json(raw.decode('utf-8'))
        json_tree(message)
    except (ValueError, UnicodeError, RecursionError):
        return result('reject', -32700), None
    if type(message) is not dict:
        return result('reject', -32600), message
    notification = 'id' not in message
    request_id = message.get('id')
    method = message.get('method')
    if (set(message) - {'jsonrpc', 'id', 'method', 'params'}
            or message.get('jsonrpc') != '2.0' or type(method) is not str or not method):
        return result('reject', -32600), message
    if not notification and (type(request_id) not in (str, int)
            or (type(request_id) is str and not 1 <= len(request_id) <= 128)
            or (type(request_id) is int and abs(request_id) > 2**53 - 1)):
        return result('reject', -32600), message
    if type(message.get('params', {})) is not dict:
        return (result('notification') if notification else result('reject', -32602, request_id)), message
    if notification:
        return result('notification'), message
    if (type(request_id), request_id) in seen:
        return result('reject', -32600, request_id), message
    if len(seen) >= MAX_REQUESTS:
        return result('reject', -32000, request_id), message
    return result('candidate', response_id=request_id, consume_id=True), message

def wire_output(encoded_bytes):
    if type(encoded_bytes) is not int or encoded_bytes < 0:
        raise ValueError('Require nonnegative exact encoded byte count')
    return result('output_candidate' if encoded_bytes <= MAX_MESSAGE - 1024 else 'reject')

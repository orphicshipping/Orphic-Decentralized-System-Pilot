"""Strict bounded data boundaries for new campaign profiles.

Canonical bytes preserve CIW's existing Python JSON settings, not RFC 8785/JCS.
Existing scientific serializers and content identities are intentionally unchanged.
Limits are host-selected configuration, never fields supplied by a tool caller.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from dataclasses import dataclass, replace
import json
import math

CANONICAL_FORMAT = 'ciw.python-json.v1'


class ContractRefusal(ValueError):
    """A bounded, data-independent refusal code."""


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 262144
    max_depth: int = 24
    max_nodes: int = 32768
    max_array: int = 4096
    max_object: int = 1024
    max_string: int = 65536
    max_key: int = 512
    max_integer: int = 9007199254740991
    max_number: float = 1e150

    def __post_init__(self):
        bounds = {'max_bytes':8388608, 'max_depth':64, 'max_nodes':1048576,
                  'max_array':65536, 'max_object':16384, 'max_string':8388608,
                  'max_key':4096, 'max_integer':9007199254740991}
        for name, maximum in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ContractRefusal('invalid_host_json_limits')
        if type(self.max_number) not in (int, float) or not math.isfinite(self.max_number) or not 0 < self.max_number <= 1e150:
            raise ContractRefusal('invalid_host_json_limits')


REQUEST_LIMITS = Limits(max_bytes=65536)
OUTPUT_LIMITS = Limits()


def _limits(value):
    if type(value) is not Limits:
        raise ContractRefusal('invalid_host_json_limits')
    return value


def text(value, maximum=512, *, nonempty=True):
    if type(value) is not str or len(value) > maximum or (nonempty and not value):
        raise ContractRefusal('invalid_text')
    try:
        value.encode('utf-8', errors='strict')
    except UnicodeError:
        raise ContractRefusal('invalid_unicode') from None
    return value


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ContractRefusal('invalid_integer')
    return value


def closed_object(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or set(value) - set(required) - set(optional):
        raise ContractRefusal('invalid_object_fields')
    return value


def _tree(value, limits):
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > limits.max_depth or count > limits.max_nodes:
            raise ContractRefusal('json_complexity_limit')
        if item is None or type(item) is bool:
            continue
        if type(item) is int:
            if abs(item) > limits.max_integer:
                raise ContractRefusal('json_integer_limit')
        elif type(item) is float:
            if not math.isfinite(item) or abs(item) > limits.max_number:
                raise ContractRefusal('json_number_limit')
        elif type(item) is str:
            text(item, limits.max_string, nonempty=False)
        elif type(item) is list:
            if len(item) > limits.max_array:
                raise ContractRefusal('json_array_limit')
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            if len(item) > limits.max_object:
                raise ContractRefusal('json_object_limit')
            for key, child in item.items():
                text(key, limits.max_key, nonempty=False)
                pending.append((key, depth + 1))
                pending.append((child, depth + 1))
        else:
            raise ContractRefusal('json_type_refused')


def canonical(value, *, limits=OUTPUT_LIMITS):
    """Bound traversal and serialized bytes; no decimal/JCS coercion is performed."""
    limits = _limits(limits)
    _tree(value, limits)
    chunks, total = [], 0
    for part in json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                                 allow_nan=False).iterencode(value):
        raw = part.encode('ascii')
        total += len(raw)
        if total > limits.max_bytes:
            raise ContractRefusal('json_byte_limit')
        chunks.append(raw)
    return b''.join(chunks)


def parse(raw, *, limits=REQUEST_LIMITS):
    """Check raw byte/depth/numeric limits and duplicate keys before conversion loss."""
    limits = _limits(limits)
    if type(raw) is not bytes or not 0 < len(raw) <= limits.max_bytes:
        raise ContractRefusal('json_byte_limit')
    try:
        source = raw.decode('utf-8', errors='strict')
    except UnicodeError:
        raise ContractRefusal('invalid_unicode') from None
    depth, quoted, escaped = 0, False, False
    for char in source:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            if depth > limits.max_depth + 1:
                raise ContractRefusal('json_complexity_limit')
        elif char in ']}':
            depth -= 1
    def pairs(rows):
        if len(rows) > limits.max_object:
            raise ContractRefusal('json_object_limit')
        result = {}
        for key, item in rows:
            if key in result:
                raise ContractRefusal('duplicate_json_key')
            result[key] = item
        return result
    def integral(token):
        if len(token.lstrip('-')) > len(str(limits.max_integer)):
            raise ContractRefusal('json_integer_limit')
        number = int(token)
        if abs(number) > limits.max_integer:
            raise ContractRefusal('json_integer_limit')
        return number
    def floating(token):
        if len(token) > 512:
            raise ContractRefusal('json_number_limit')
        number = float(token)
        if not math.isfinite(number) or abs(number) > limits.max_number:
            raise ContractRefusal('json_number_limit')
        # A nonzero decimal literal becoming zero would erase supplied content.
        if number == 0 and any(c in '123456789' for c in token.lower().split('e')[0]):
            raise ContractRefusal('json_number_underflow')
        return number
    def constant(_):
        raise ContractRefusal('json_number_limit')
    try:
        value = json.loads(source, object_pairs_hook=pairs, parse_int=integral,
                           parse_float=floating, parse_constant=constant)
    except (json.JSONDecodeError, RecursionError):
        raise ContractRefusal('invalid_json') from None
    canonical(value, limits=limits)
    return value


def mcp_envelope(value, *, is_error=False, limits=OUTPUT_LIMITS):
    """Bound the complete MCP text and structured response, including duplication."""
    if type(value) is not dict:
        raise ContractRefusal('mcp_structured_object_required')
    if type(is_error) is not bool:
        raise ContractRefusal('invalid_error_flag')
    encoded = canonical(value, limits=limits)
    envelope = {'content':[{'type':'text','text':encoded.decode('ascii')}],
                'structuredContent':value,'isError':is_error}
    # The generated text copy can exceed an individual application-string limit;
    # its source value has already passed that limit. Keep the complete wire cap.
    canonical(envelope, limits=replace(limits, max_string=max(limits.max_string, len(encoded))))
    return envelope

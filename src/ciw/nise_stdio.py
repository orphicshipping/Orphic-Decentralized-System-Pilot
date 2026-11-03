"""Strict bounded input for the existing official MCP stdio session.

The SDK normally decodes JSON before the host sees it. This adapter validates
raw UTF-8 frames first so duplicate keys cannot disappear. This is framing,
not authentication, a network sandbox or a new HTTP service.
"""
import sys
from contextlib import asynccontextmanager
from .nise_contract import parse

MAX_FRAME = 65536 + 8192
MAX_ID = 128


class StrictInput:
    def __init__(self, stream=None):
        self.stream = sys.stdin.buffer if stream is None else stream
        self.failed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        import anyio
        if self.failed:
            raise StopAsyncIteration
        raw = await anyio.to_thread.run_sync(self.stream.readline, MAX_FRAME + 1)
        if not raw:
            raise StopAsyncIteration
        if len(raw) > MAX_FRAME:
            # Close the session; never parse a suffix of a rejected long frame.
            self.failed = True
            raise ValueError('mcp_frame_budget_exceeded')
        try:
            # JSON-RPC adds params/arguments wrappers. The host independently
            # enforces its unchanged 24-level name/arguments data contract.
            value = parse(raw, limit=MAX_FRAME, max_depth=26)
            if type(value) is not dict:
                raise ValueError('mcp_object_required')
            if 'id' in value:
                identity = value['id']
                if not ((type(identity) is str and len(identity) <= MAX_ID)
                        or (type(identity) is int and abs(identity) <= 2**53 - 1)):
                    raise ValueError('mcp_id_budget_exceeded')
            # Use the pinned SDK's own envelope schema after lossless checks.
            # Otherwise structurally invalid JSON-RPC is logged without a reply
            # by its reader, leaving a client waiting on an open session.
            import mcp_types
            mcp_types.jsonrpc_message_adapter.validate_json(raw, by_name=False)
            return raw.decode('utf-8', errors='strict')
        except (ValueError, TypeError, OverflowError, RecursionError):
            # SDK 2.3 logs parser errors without answering the request. Close
            # this session explicitly so a rejected frame cannot leave a
            # waiting client or be reinterpreted after information was lost.
            self.failed = True
            raise ValueError('invalid_mcp_frame') from None


@asynccontextmanager
async def strict_stdio_server():
    """Preserve MCP 2.3's standard-descriptor custody around raw validation.

    Explicit stdin skips the SDK's claim, so take that same claim here and
    restore it only after the SDK's writer/session have exited. These private
    helpers are deliberately tied to the project's pinned MCP version; absence
    refuses startup rather than falling back to unclaimed input.
    """
    from mcp.server.stdio import _claim_fd, _open_stdin_diversion, stdio_server
    restore = None
    try:
        stream, restore = _claim_fd(0, sys.stdin, 'rb', _open_stdin_diversion)
        async with stdio_server(stdin=StrictInput(stream)) as streams:
            yield streams
    finally:
        if restore is not None:
            restore()

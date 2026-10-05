"""Notation Link v1 byte framing. No device discovery, commands, or sockets.

Wire: !4sBBHI header (NLK1, version=1, flags, kind, payload bytes), JSON
payload, big-endian CRC32(header+payload), optional HMAC-SHA256 over all
preceding bytes. CRC is corruption detection, never authentication. HMAC
keys are host bindings; wire data cannot select a key or enable permission.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import struct
import zlib

MAX_PAYLOAD = 65536
HEADER = struct.Struct("!4sBBHI")
KINDS = {1: "manifest", 2: "observation", 3: "event"}
MAX_FRAME = HEADER.size + MAX_PAYLOAD + 4 + 32


@dataclass(frozen=True)
class Frame:
    kind: str
    payload: bytes
    wire: bytes
    authentication: str


def _key(key: bytes | None) -> None:
    if key is not None and (type(key) is not bytes or not 32 <= len(key) <= 64):
        raise ValueError("Bind a 32..64 byte HMAC key outside the retained data")


def encode(kind: str, payload: bytes, *, key: bytes | None = None) -> bytes:
    _key(key)
    if kind not in KINDS.values() or type(payload) is not bytes or not 0 < len(payload) <= MAX_PAYLOAD:
        raise ValueError("Unsupported Link kind or payload size")
    code = next(k for k, v in KINDS.items() if v == kind)
    header = HEADER.pack(b"NLK1", 1, int(key is not None), code, len(payload))
    body = header + payload
    wire = body + struct.pack("!I", zlib.crc32(body))
    return wire if key is None else wire + hmac.digest(key, wire, "sha256")


class Decoder:
    """Bounded, incremental, fail-closed decoder; never skips damaged bytes.

    ``allow_unverified`` is for offline structural inspection only. Live intake
    uses require_auth with an explicitly bound key, or deliberately unauthenticated
    local/recorded input. A decoder is permanently poisoned after any error.
    """
    def __init__(self, *, key: bytes | None = None, require_auth: bool = False,
                 allow_unverified: bool = False):
        _key(key)
        if require_auth and (key is None or allow_unverified):
            raise ValueError("Authenticated intake requires a bound key")
        self.key = key
        self.require_auth = require_auth
        self.allow_unverified = allow_unverified
        self.buffer = bytearray()
        self.failed = False

    def feed(self, chunk: bytes) -> list[Frame]:
        if self.failed:
            raise ValueError("Link decoder is poisoned; create a new transport session")
        try:
            if type(chunk) is not bytes or len(chunk) > MAX_FRAME:
                raise ValueError("Feed bounded byte chunks")
            self.buffer.extend(chunk)
            output = []
            while len(self.buffer) >= HEADER.size:
                magic, version, flags, code, length = HEADER.unpack_from(self.buffer)
                if magic != b"NLK1" or version != 1 or flags not in (0, 1) or code not in KINDS:
                    raise ValueError("Link header/version/flags/kind refused")
                if not 0 < length <= MAX_PAYLOAD:
                    raise ValueError("Link payload length refused")
                total = HEADER.size + length + 4 + 32 * flags
                if len(self.buffer) < total:
                    break
                wire = bytes(self.buffer[:total])
                body_end = HEADER.size + length
                expected_crc = struct.unpack("!I", wire[body_end:body_end + 4])[0]
                if zlib.crc32(wire[:body_end]) != expected_crc:
                    raise ValueError("Link CRC mismatch")
                if self.require_auth and not flags:
                    raise ValueError("Unsigned frame refused by authenticated intake")
                authentication = "not_authenticated"
                if flags:
                    if self.key is None:
                        if not self.allow_unverified:
                            raise ValueError("Signed frame requires the host-bound HMAC key")
                        authentication = "not_reverified"
                    else:
                        expected = hmac.digest(self.key, wire[:-32], "sha256")
                        if not hmac.compare_digest(wire[-32:], expected):
                            raise ValueError("Link HMAC mismatch")
                        authentication = "shared_key_possession_verified"
                output.append(Frame(KINDS[code], wire[HEADER.size:body_end], wire, authentication))
                del self.buffer[:total]
            if len(self.buffer) > MAX_FRAME:
                raise ValueError("Link framing buffer exceeded")
            return output
        except Exception:
            self.failed = True
            self.buffer.clear()
            raise

    def finish(self) -> None:
        if self.failed or self.buffer:
            self.failed = True
            self.buffer.clear()
            raise ValueError("Incomplete or failed Link transport; no silent truncation")


def decode_one(wire: bytes, *, key: bytes | None = None,
               require_auth: bool = False, allow_unverified: bool = False) -> Frame:
    decoder = Decoder(key=key, require_auth=require_auth, allow_unverified=allow_unverified)
    frames = decoder.feed(wire)
    decoder.finish()
    if len(frames) != 1:
        raise ValueError("Expected exactly one Link frame")
    return frames[0]

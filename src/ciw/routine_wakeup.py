"""Optional host-configured bearer-authenticated routine notification.

No task instructions, source excerpts, arbitrary client-selected URLs or keys
cross this boundary. This is not a Bot-account or conversation API.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import os
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .control_contracts import content_ref, keys
from .core.identities import canonical_json
from .external_work import count, require, text


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RoutineWakeup:
    """Trusted deployment binds a verified routine endpoint and secret env name.

    An explicit hostname allowlist, HTTPS, default TLS verification, no redirects,
    and no ambient proxies constrain where the bearer key can be sent. The host
    must provision egress policy/DNS and must not accept configuration from tasks.
    """

    def __init__(self, *, endpoint, allowed_hosts, secret_env, timeout_seconds=10):
        text(endpoint, 2048)
        parts = urlsplit(endpoint)
        require(type(allowed_hosts) is tuple and len(allowed_hosts) > 0 and
                all(type(host) is str and host == host.lower() and host for host in allowed_hosts),
                "invalid_host_allowlist")
        require(parts.scheme == "https" and parts.hostname in allowed_hosts and
                parts.port in (None, 443) and parts.username is None and parts.password is None
                and not parts.fragment and not parts.query and bool(parts.path)
                and not any(c.isspace() or ord(c) < 32 for c in endpoint), "endpoint_refused")
        self._endpoint = endpoint
        self._secret_env = text(secret_env, 128)
        self._timeout = count(timeout_seconds, 30)

    def send(self, payload):
        keys(payload, {"task_id", "task_hash", "delivery_id"})
        text(payload["task_id"], 128)
        text(payload["delivery_id"], 128)
        content_ref(payload["task_hash"])
        secret = os.environ.get(self._secret_env, "")
        require(24 <= len(secret) <= 4096 and
                all(33 <= ord(char) <= 126 for char in secret), "routine_secret_unavailable")
        request = Request(self._endpoint, data=canonical_json(payload).encode(), method="POST",
                          headers={"Authorization": "Bearer " + secret,
                                   "Content-Type": "application/json"})
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=self._timeout) as response:
                return response.status  # Deliberately do not read/retain the response body.
        except HTTPError as exc:
            code = exc.code
            exc.close()
            return code

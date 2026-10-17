# Authenticated metrology HTTP candidate

This adapter serves the same sampled-profile AgentHost operations as the local
CLI/stdio path through MCP 2.3.0 Streamable HTTP. It is an **internal pilot
candidate**, exercised on loopback with synthetic data. No hosted deployment,
physical BIM pilot, OAuth authorization-server integration, browser client or
production identity qualification is supplied.

Install `.[metrology-pilot]`. Prepare a separate profile for each principal using
`ciw.metrology_workflow prepare`. Each principal owns one isolated investigation;
profiles and retained output directories cannot be shared or nested. The operator
selects sources, provider, policy, grants, output and frozen profile digest before
launch. Tools expose only capabilities, fixed assessment and retained inspection.

A host-owned configuration has this shape (replace all placeholders):

```json
{
  "schema": "ciw.metrology-remote-config.v1",
  "origin": "http://127.0.0.1:8765",
  "listen": {"host": "127.0.0.1", "port": 8765},
  "principals": [{
    "id": "pilot-investigation-a",
    "token_sha256": "sha256:SHA256_OF_HIGH_ENTROPY_BEARER_TOKEN",
    "profile": "/operator/prepared-a/profile.json",
    "profile_sha256": "sha256:SHA256_OF_EXACT_PROFILE_FILE_BYTES",
    "resume": false
  }]
}
```

Provision a distinct high-entropy credential per investigation out of band. Only
its SHA-256 is configured; the client sends `Authorization: Bearer TOKEN`. Keep
configuration, profile directories and bearer tokens under operator custody.
A profile digest is content binding, not a signature or credential store.

```sh
python -m ciw.metrology_remote /operator/metrology-remote.json
```

The listener is always `127.0.0.1`. For remote access the configured origin must
use HTTPS and an operator-managed TLS reverse proxy must preserve the exact Host
and Origin; proxy identity headers are not trusted. Pre-shared bearer credentials
are the chosen narrow pilot flow, not a general MCP OAuth discovery service.
The operator must separately qualify proxy/TLS, credential provisioning, rotation,
secrets custody, remote identity, network exposure and deployment recovery.

## Request and recovery boundaries

- Endpoint `/mcp` only, no query parameters, exact Host and allowed Origin.
- At most 16 isolated principals, two concurrent HTTP requests and two outstanding
  domain calls per principal, 32 KiB request body; no upload or URL-fetch tool.
- Unknown/duplicate JSON, added provider/policy/grant selectors and unbound artifact
  handles refuse. Only fixed `source` and `baseline` aliases can execute.
- Configuration is reread on every authenticated request. Removed credentials
  immediately refuse subsequent requests; token rotation may change only the
  credential for the same frozen binding. Profile/source/grant changes require
  a separately prepared investigation. Changed frozen profile bytes return 503.
- Revocation prevents new calls. An already dispatched calculation may finish
  and retain evidence; revocation does not pretend to undo a running operation.
- Interrupting an HTTP waiter does not authorize a retry. Existing retained
  attempt identities decide reuse or unknown interrupted outcomes. Process restart
  requires explicit profile `resume: true` and exact frozen bindings. Unknown
  outcomes remain unknown; there is no automatic replay endpoint.
- Retained reads validate witnesses without provider execution. No tenant can
  select a filesystem path, another profile, or another tenant's retained handle.

Synthetic SDK tests cover real HTTP discovery/execution/inspection boundaries,
request limits, revocation, profile mutation and process-lifecycle restart, plus
real SDK stdio subprocesses. These do not qualify public-network availability,
hard real-time latency, physical accuracy or resistance to filesystem compromise.
There is no automatic physical release or actuator path.

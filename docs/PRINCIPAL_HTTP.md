# Native principal-bound MCP over HTTP

`ciw.principal_http` is an OAuth resource server backed by the official MCP
2.3.0 Streamable HTTP transport. It calls the same `PrincipalFacade` or existing
`AuditedPrincipalHost` and MCP result encoder as ODSP's trusted SDK/stdio launcher. It does not launch
a stdio child or forward an access token to a provider. Current grants still
control discovery, tools, resources, scientific operations and retained owners.

The [2026-07-28 transport](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
has per-request metadata and no protocol sessions. This server uses that native
transport and also accepts the SDK's 2025-11-25 initialization/initialized
notification compatibility path in stateless mode. Every HTTP request is
authenticated again. Supplied `Mcp-Session-Id` and `Last-Event-Id` headers are
refused; reconnecting grants no authority and resumes no execution. Client
retries of effectful tools still need the existing operation's admission and
idempotency contract. No new retry or replay mechanism is introduced here.

## Host integration

Install the existing optional dependencies with the repository's pinned release
workflow. The `external-work` and `network` extras already declare MCP 2.3.0,
PyJWT 2.15.1, uvicorn 0.54.0 and cryptography 46.0.0. No new dependency is needed.
Use an installed package and `python -I` for a real launcher.

Construct the app in trusted host code:

```python
from ciw.principal_http import OAuthSettings, SubjectBindings, create_app

app = create_app(
    OAuthSettings(
        resource="https://ncp.example/mcp",
        issuer="https://identity.example/tenant",
        required_scopes=("odsp:invoke",),
    ),
    facades={"operator": existing_audited_principal_host},
    subjects=SubjectBindings.from_file("/absolute/host-owned/subjects.json"),
)
```

`facades` accepts existing `PrincipalFacade` instances or `AuditedPrincipalHost`
instances wrapping those exact facades. The underlying principal must match each
dictionary key. The launcher retains ownership of their context
managers, profile bindings, retention root and policy files for the entire app
lifetime. The facade map is copied on construction. Subject and policy records
are read afresh for each request and checked again immediately before facade
dispatch. Scope is a transport prerequisite, never an operation grant.

An audited binding dispatches through the existing wrapper. The HTTP adapter
unwraps only to validate its original identity and current policy. It supplies
the wrapper's existing `scope_guard` with a fresh token/subject check, so identity
revocation during durable admission prevents dispatch and revocation during
terminal retention prevents disclosure. The audit chain, independent anchor,
write-ahead barrier and unresolved-outcome hold remain the wrapper's authority.
No second audit log or permission cache is introduced. Replacing the wrapper's
facade refuses subsequent requests even if that replacement is later undone.

The subject file uses this closed schema:

```json
{
  "schema": "ciw.oauth-subjects.v1",
  "revision": "operator-reviewed-1",
  "subjects": [
    {
      "subject": "exact-sub-from-verified-issuer",
      "principal": "operator",
      "enabled": true,
      "not_before": 0
    }
  ]
}
```

This example is a schema description, not a grant to any real identity. The
host must establish the issuer's subject before writing a real mapping.
Disabling/removing a mapping denies subsequent requests. `not_before` rejects
tokens issued earlier than that integer Unix time, allowing host-controlled
revocation of older credentials without storing a bearer token. File reads
reuse the policy reader's bounded regular-file/no-symlink checks. Deployments
must protect the files and replace complete records atomically. No permission
decision is stored in a client session or restored from a scientific checkpoint.
Revocation cannot undo a provider operation that already began.

A runnable single-principal launcher binds **only 127.0.0.1**:

```sh
python -I -m ciw.principal_http \
  --resource https://ncp.example/mcp \
  --issuer https://identity.example/tenant \
  --scope odsp:invoke \
  --profile /absolute/host-owned/profile.json \
  --policy /absolute/host-owned/policy.json \
  --subjects /absolute/host-owned/subjects.json \
  --principal operator --port 8788
```

The optional instrument/provider arguments are trusted launch inputs with the
same fixed native binding mechanism as `principal_mcp`. Multi-principal and
additional profile deployments use `create_app` with their existing facades.
This minimal CLI binds a raw facade. A deployment requiring the durable audit
barrier must construct `create_app` with its existing `AuditedPrincipalHost` and
independent anchor; this CLI does not provision either.
The HTTP launcher creates no credential and does not provision an issuer,
reverse proxy, DNS name, certificate, Access policy, store or cloud service.

## OAuth contract

This implements the resource-server role in the
[MCP authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization).
The external authorization server handles login, consent, client registration,
PKCE, refresh and issuing access tokens. There is no local token endpoint or
authorization-server metadata route. The official SDK serves RFC 9728 metadata
at `/.well-known/oauth-protected-resource` plus the resource path. Its resource,
authorization server, scopes and header-only bearer method come from fixed host
settings. 401/403 challenges point to that metadata and state required scopes.
Responses use `Cache-Control: no-store`; authorized discovery retains the
existing `ttlMs: 0`, private-cache contract.

The supported external issuer profile is **RS256 RFC 9068 JWT access tokens**:

- HTTPS issuer/resource URLs are host-fixed. This profile uses canonical URLs
  without trailing slashes, query strings, fragments, credentials or encoded
  path aliases. The resource is the exact endpoint URI, including its path.
- RFC 8414 and OIDC discovery paths are tried in specification order. Metadata
  issuer comparison is exact. Discovery fallback occurs only for 404/405.
  JWKS must remain on the configured issuer's origin. Redirects and environment
  proxy configuration are not used. TLS chain and hostname checks are required.
- Token header fields are exactly `alg`, `kid`, `typ`; algorithm is RS256 and
  type is `at+jwt`. Tokens cannot supply a key, key URL or algorithm choice.
  JWKS accepts public 2048–8192-bit RSA signing keys, rejects duplicate key IDs
  and private/symmetric material, and never returns stale keys after a failed
  refresh. Normal key lifetime is 60 seconds; unknown-key refreshes are limited
  to one attempt per ten seconds. Each fetched JSON document is at most 128 KiB.
- Signature, issuer, exact singleton audience, subject, client ID, JWT ID,
  scope and integer issue/expiry/not-before times are validated. Tokens must
  already be valid and have a lifetime at most one hour. Duplicate JSON keys,
  boolean timestamps, ambiguous audiences and ID tokens are refused.
- Current subject mapping and ODSP policy are checked independently of token
  signature validity. Every required transport scope must be present. Caller
  names, cookies, `CF-Access-Jwt-Assertion` and tool arguments cannot substitute
  for an OAuth bearer credential. Raw tokens never enter facade arguments.

These constraints intentionally do not cover opaque tokens/introspection,
other JWT algorithms, off-origin JWKS, third-party token forwarding or an
issuer's immediate remote revocation endpoint. Host mapping/policy revocation
is immediate for subsequent requests; otherwise JWT expiry and key refresh
bound validity. The SDK's OAuth resource validation is additionally enabled.
See its [resource-server documentation](https://py.sdk.modelcontextprotocol.io/run/authorization/).

The outer boundary enforces one exact Host, absent or exact same-origin Origin,
single security-sensitive headers, no query credentials, one strict JSON-RPC
object, a 64 KiB body, a five-second body deadline and existing bounded JSON
value rules. The launcher caps simultaneous connections at 16 and disables
proxy-header trust and access logging. A trusted TLS reverse proxy may forward
the exact public Host to loopback; bearer authentication remains mandatory on
direct requests. The app does not derive issuer/resource/identity from forwarded
headers. No unsafe direct-origin route is provisioned by this change.

## Executed local qualification and remaining gates

Run the committed tests with the pinned dependencies:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m pytest tests/test_principal_http.py tests/test_principal_http_audited.py -q
```

The tests run an actual TLS metadata/JWKS endpoint with an ephemeral trusted
certificate, plus actual official SDK clients over loopback TCP. They exercise
modern discovery/list/call, legacy initialization/notification, concurrent
principals, refresh/reconnect, current operation and identity revocation,
retained-owner separation, missing/wrong/expired tokens, scope, fixed Host and
Origin, duplicate headers/JSON, body limits, OIDC fallback, key rotation,
metadata failures and server teardown. Audited transport tests also verify actual
SDK dispatch through the durable chain, refusal before dispatch when its anchor
fails, retained unknown outcomes after terminal failure, revocation during audit
I/O, and sticky refusal after an identity rebind. These are local protocol and resource
server checks. Test metadata endpoints never implement a login or token issuer;
test signatures do not establish an external identity provider's behavior.

Hosted deployment remains a separate gate. It needs an approved real issuer
that issues the specified access-token profile for the approved endpoint,
verified subject mapping, least-scope current principal grants, protected
retention and TLS routing, deployment credentials/network access and spending
approval where applicable. Cloudflare Access app assertions are a different
credential type and are not accepted as OAuth access tokens here. Existing
Access settings, the ODSP portal, upstream OAuth registrations, the frozen
release and its held execution capabilities are unchanged. No live OAuth login,
cloud deployment, container image, provider or physical qualification is claimed.

# NCP custom connectors through Cloudflare

The requester and worker use one local task/evidence database over authenticated
MCP Streamable HTTP. Cloudflare handles OAuth; the origin independently verifies
the signed Access token and maps its subject to a host-provisioned NCP principal.
Authorization of an exact task and acceptance of a result remain host-only.

This repository contains implementation and synthetic tests, not Cloudflare
account state, credentials, an approved identity, a database or an activated
connector. Existing operator-managed deployments must retain their own settings.
Do not recreate or change those resources merely by installing this package.

## Authentication boundary

[Cloudflare Managed OAuth](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/)
supports MCP clients that send the OAuth resource indicator. The client token is
opaque; the origin verifies `Cf-Access-Jwt-Assertion`. Actual ChatGPT/Grok login
and transport compatibility require a connection test against the chosen clients;
the local SDK tests do not establish live client compatibility.

`ciw.external_work_http` verifies RS256 using the configured organization's JWKS,
exact issuer, route-specific audience, expiry, issued-at, token type and an
explicitly bound subject. The retained client role and enabled state are
rechecked on every request. A requester audience cannot authorize the worker
route. Missing, duplicate, forged or unbound assertions are denied. Email headers,
Bearer strings and model-supplied labels cannot select a principal. See
[Cloudflare JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/).

The gateway restricts Host and Origin, rejects HTTP bodies over 64 KiB and uses
stateless JSON responses. Only bounded role-specific work tools are advertised.
There is no remote approval, review, full-corpus search, shell or arbitrary-file
tool. OAuth authenticates an account; it does not establish an isolated Bot
process identity.

## Operator configuration

Use `examples/external-work/cloudflare-config.example.json` as the configuration
shape. Its URLs and audiences are placeholders and its subject maps are empty.
Keep the real configuration and database on trusted service-owned local storage,
outside a source checkout and outside Drive/OneDrive synchronization.

1. Retain or provision separate Access applications and audiences for the
   requester route `/requester/mcp` and worker route `/worker/mcp`. Bind the
   approved login through the intended identity provider; do not use an
   everyone-allow policy. Distinct hostnames permit independent OAuth bindings.
2. Register each connector's exact OAuth callback URL from its connection flow.
   Enable dynamic registration only when deliberately configured for the required
   callbacks. Preserve unrelated application settings.
3. Obtain the verified Access subject through trusted administration. Provision
   scoped requester and worker clients in the service database, then map that
   signed subject to the corresponding principal in each endpoint's `subjects`.
   Do not use an unverified token, email header or Bot label for this binding.
4. Configure the tunnel's exact hostname/path routes to `127.0.0.1:18766`, with a
   default 404 rule. Retain and verify the intended `cloudflared` executable hash.
   The origin binds loopback only and does not trust forwarded headers.
5. Start the origin and tunnel. Add the worker URL as the Grok custom connector
   and requester URL in ChatGPT, complete OAuth, then verify initialization and
   role-specific tool discovery.
6. Bind approved source spans, propose a task, and authorize its exact hash through
   the trusted host. Verify both clients read the same context; exercise polling,
   claim and candidate submission; retain an independent review.

No step grants scientific acceptance. Source disclosure, exact task authorization,
provider execution and result review remain separate decisions.

## Local launch

Install the optional dependencies with `pip install -e ".[dev,external-work]"`.
The base numerical dependency pins remain unchanged. The direct origin command is:

```powershell
python -m ciw.external_work_http --config C:/service-owned/ncp/cloudflare-config.json
```

The optional Windows helper prompts locally for the tunnel token with hidden input
and supplies it only to the tunnel child environment:

```powershell
python examples/external-work/start_cloudflare.py `
  --config C:/service-owned/ncp/cloudflare-config.json `
  --cloudflared C:/service-owned/ncp/tools/cloudflared.exe `
  --cloudflared-sha256 OPERATOR_VERIFIED_SHA256 `
  --log-dir C:/service-owned/ncp/logs/new-launch
```

Do not put a token in the repository or chat. Use a new log directory per launch.
The helper refuses empty subject maps, an occupied origin port and a mismatched
binary hash. It starts hidden processes and records their PIDs. That receipt
establishes process startup only; inspect Access and tunnel health separately.
Stop those processes when finished. The helper does not install a Windows service
or scheduler; production availability needs operator-managed lifecycle and an
awake host.

Routine wake-up is disabled unless explicitly configured. Polling starts at zero
on every new scan and remains independent of any webhook. The authenticated wake
adapter does not change task authorization or review.

## Verification and limits

Run the focused suites from the checkout:

```powershell
python -m pytest -q tests/test_external_work.py tests/test_external_work_http.py tests/test_corpus_retrieval.py
```

The tests exercise real SQLite transactions, the MCP SDK over loopback TCP,
RSA-signed token fixtures, role separation, revocation, Host/Origin enforcement,
exact citation retention and corpus integrity. JWKS/network substitutions are
explicit fixtures; they do not represent a live OAuth login, Cloudflare deployment,
ChatGPT/Grok call, webhook delivery, source disclosure or physical qualification.

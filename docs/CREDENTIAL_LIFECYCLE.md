# Private candidate credential lifetime

The private gateway and browser panel require version 2 credential configuration.
They reject version 1, including otherwise valid perpetual credentials. This is a
local source change; it issues no credentials and changes no hosted configuration.
The frozen release, archives and retained evidence remain separate.

The trusted operator supplies these closed JSON objects through the existing
protected deployment bindings:

| Binding | Schema | Top-level fields | Each principal row |
| --- | --- | --- | --- |
| Gateway `PRINCIPAL_AUTH_JSON` | `ciw.bearer-principals.v2` | `schema`, `policy_revision`, `policy_sha256`, `principals` | `principal`, `enabled`, `token_sha256`, `credential_id`, `not_before`, `expires_at` |
| Panel `PANEL_PRINCIPALS_JSON` | `odsp.panel-principals.v2` | `schema`, `principals` | `subject`, `principal`, `enabled`, `backend_token`, `credential_id`, `not_before`, `expires_at` |

`credential_id` is an explicit host-issued identifier matching
`[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}`. It must be distinct within its configuration.
Both timestamps must be nonnegative safe integers in epoch **seconds**, with
`not_before < expires_at` and `expires_at - not_before <= 3600`.
Unknown fields, duplicate JSON keys, duplicate principals, duplicate secrets or
digests, duplicate panel subjects and malformed identifiers refuse configuration.
There are one to 64 principal rows and a 64 KiB configuration limit.

Authentication requires `enabled === true` and
`not_before <= floor(host_time_ms / 1000) < expires_at`. The credential check has
no expiry grace period and performs no automatic renewal. The host clock is
trusted and must be maintained correctly. The panel also independently verifies
the Access assertion and its current claims using the existing Access verifier.

For an approved issuance, the operator must bind a fresh identifier and dedicated
high-entropy secret to one principal, with the same validity interval in both
configurations. Only the secret's lowercase SHA-256 digest belongs in the gateway
configuration; the secret itself is confined to the panel's protected backend
binding. The panel's `subject` is the exact verified Access subject, never a
display name, email guess or request parameter. Policy and grants remain separate.
This document neither generates a secret nor authorizes creating a protected
binding, changing grants, provisioning, publishing or spending.

## Dispatch and queued requests

The gateway checks the selected credential again after asynchronous token hashing,
body upload and identity hashing, immediately before internal dispatch. A changed
host, singleton or service binding also refuses. Browser Authorization, cookies
and supplied internal headers are discarded.

The reconstructed internal request adds `x-odsp-credential-id` and
`x-odsp-credential-sha256`. The latter is SHA-256 of the UTF-8 compact JSON array:

```text
["ciw.bearer-credential.v2", principal, credential_id, token_sha256, not_before, expires_at]
```

This binds the exact credential record, including validity bounds; extending an
expiry cannot revive a request carrying the old record digest. The existing
principal context passed to Python stays `ciw.principal-context.v1` with its
unchanged principal, owner and policy identity fields. Credential headers never
become caller-selected grants or checkpoint authority.

The Worker rechecks the current credential on entry, after waiting in its queue,
after body/storage/health/checkpoint preflight, before durable admission, and after
admission before container dispatch. Rotation, disabled rows, removal, expiry or
policy identity changes refuse the stale request. Every check re-reads the current
host configuration, including after asynchronous identity hashing.

The panel binds an in-flight request to the exact subject, principal, secret,
credential identifier and validity interval authenticated at entry. It rechecks
that mapping, Access claims and backend binding before dispatch, after receiving
backend headers, and after reading the bounded response body. A replacement
credential is usable by a fresh authenticated request; it cannot silently replace
the credential of an in-flight request. Assets receive the same current mapping
and assertion checks.

## Disclosure, revocation and retained outcomes

The Worker rechecks before readiness and committed-result disclosure. If an
operation may already have taken effect, failed authentication does not erase its
checkpoint, receipt or uncertainty hold and never triggers a retry. A checkpoint
committed before disclosure was denied remains committed, with the existing
`committed_response_unconfirmed` outcome. A failure after durable admission but
before a confirmed result retains the existing `unconfirmed` hold.

When panel authorization changes after forwarding an ordinary API request, the
panel withholds backend result bytes and returns `panel_access_denied`, HTTP 401,
with `effect_state: "unconfirmed"` and `authorizes_execution: false`. This does not
claim rollback or cancellation. Readiness remains `not_started`, because that
route performs no operation or checkpoint write. Failure before forwarding is
also `not_started`.

To revoke, disable or remove the row in the protected configuration. Rotate by
issuing a new identifier **and** new secret, then applying matching protected
bindings. Do not recycle an expired or revoked record. This implementation checks
current configuration; it is not a persistent issuer, refresh-token service or
historical revocation registry and cannot detect an operator restoring the same
old configuration bytes. Configuration propagation between hosted isolates is
not qualified here. A rollout must verify both protected bindings, clocks and
revocation propagation in its actual environment before activation. Partial or
mismatched rollout remains denied; there is no version-1 compatibility mode.

## Local verification

```sh
node --test tools/odrp-private-host-candidate/tools/private-mcp-host/test_gateway.mjs tools/odrp-private-host-candidate/panel/tests/proxy.test.mjs
PYTHON=/absolute/path/to/qualified/python node --test tools/odrp-private-host-candidate/tools/private-mcp-host/test_worker.mjs tools/odrp-private-host-candidate/tools/private-mcp-host/test_release.mjs
node --test tools/odrp-private-host-candidate/panel/tests/build.test.mjs
```

The tests retain existing authentication, duplicate JSON, origin, response budget,
storage fencing and retention cases. They add malformed lifetime and version
refusals, exact timestamp boundaries, real clock expiration, async hashing and
upload races, actual Worker queue rotation/expiry, revocation during health and
durable admission, and suppression during streaming or committed-result
disclosure. Worker tests use real local SQLite/fsync substitutes for DO/R2;
panel tests use ephemeral RSA signatures and local JWKS/service bindings. These
checks do not qualify hosted Access, secret delivery, propagation, billing,
Cloudflare storage semantics or native OAuth deployment.

# NET device boundary and permissioned network lifecycle

**Candidate implementation, 2026-10-06 UTC. Local processing and cryptographic
lifecycle prototype, not a deployed account service or physical device network.**

The user-specified architecture is additive: evidence -> filter -> computation
-> proof -> verification -> gate -> capability. Human, device and NET-node
identities remain independent. Model-improvement participation requires a distinct
customer consent contract. Computation/signature validity is not physical truth.

## Existing substrate and scope

The implementation uses the existing `Session`, `OperationRegistry`, immutable
execution/result identities, payload validators, Legibility Ed25519 signatures
and optional SCR SP1 boundary. It creates no parallel ledger, registry, Workbench
or network listener. The [gateway proposal](DEVICE_GATEWAY.md) and Atelier-side
apparatus ownership are not replaced. Shared Session, runner, schema engine,
parent #185 data bridges, GROMACS, LAMMPS and ALCHEMI files are unchanged.

`net boundary` adds exact recorded-data coherence; `net network` adds local
identity, consent and release-review journeys. Only one navigation row per command
and corresponding audit-index rows change existing navigation. Preserve the
independent connectivity #202 and other pending command rows during integration.

## Device, human, node and signing authorities

| Principal | Implemented credential boundary | Not established |
| --- | --- | --- |
| Human/operator | PyJWT verification against explicitly configured issuer keys, exact issuer/node audience, tenant/device/resource and role/scope intersection | Actual account service, passkey/WebAuthn/SMS/TOTP ceremony, account recovery or federation |
| Instrument | Ed25519 proof of possession and an independently issued X.509 device credential | TPM/secure-element origin, hardware-bound key, firmware/gateware attestation |
| NET node | Separate key and X.509 node credential; signs retained authorization evidence and contributions | Production mTLS, fleet certificate discovery or node operating-system integrity |
| Customer consent | Distinct tenant signing key, exact evidence/purpose/recipient/terms contract | Automated legal-rights verification or a real customer's consent |
| Publisher | Distinct signed release manifest and exact artifact bytes | Production update infrastructure or empirical model qualification |

The issuer, node, CA, consent, publisher and enrolled device cannot reuse a key
in this profile. The trust root is **operator-configured cryptographic material**,
not possession of `notations.io` or an HTTPS domain name. The local CA pilot is
not a production CA, HSM, public-key infrastructure or general PKIX path engine.
Only its bounded direct-issuer Ed25519 device/node profile is intended here.

Human access tokens have fixed `EdDSA` verification, `typ=at+jwt`, a pinned key
identifier, exact HTTPS issuer and `urn:notation:node:<node>` audience. No embedded
JWK/URL, algorithm negotiation or key discovery is accepted from a token.
Claims include subject, tenant, device, node audience, role, scope, resource hash,
issued/not-before/expiry times, authentication time and issuer-specific assurance.
Maximum lifetime is 15 minutes; state-changing preparation requires an explicitly
configured step-up ACR and authentication age at most five minutes. AMR/ACR are
signed **issuer assertions**, not locally proven login ceremonies. A synthetic
issuer uses `.invalid` and an explicitly test-only ACR. It never proves that a
phone, passkey, second factor or production account was used.

Trust anchors and finite revocation snapshots are supplied by host setup. Expiry,
clock rollback, unknown roles/scopes, cross-tenant/device/resource requests and
stale snapshots refuse. Production services still need a reviewed OAuth/OIDC
provider, phishing-resistant authenticators, recovery controls, revocation
refresh and an independent security review. TOTP and SMS are not treated as
phishing-resistant equivalents to WebAuthn/passkeys.

Tokens enter `NetworkHost.request` out of band and are never passed as Session
parameters. Retained records contain a token digest, scoped authorization evidence
and a node-signed receipt, **not the bearer credential**. Reopen verifies that
historical evidence without restoring current human authority.

## Enrollment and offline identity

The node issues a bounded, expiring random challenge binding tenant, node,
instrument and device public key. The device signs the complete challenge and a
human access token must name that exact resource. A challenge cannot be consumed
twice in the bound process. A certificate is activated only after the existing
Session accepts the completed enrollment result. Expired/refused attempts do not
activate a device. This is not distributed exactly-once issuance or a durable
CA transaction protocol; a production service needs recovery, key custody and
persistent revocation/issuance controls.

After enrollment, `verify_device_packet` needs the instrument certificate and
current local trust/revocation information, not the human's phone or access token.
Revoking a person's account does not silently replace the instrument key.
Offline capability lasts only within the applicable certificate and revocation
snapshot bounds; disconnected nodes cannot claim knowledge of newer revocations.

## Recorded coherence and per-function gates

`device.boundary.review.v1` and `device.boundary.perform.v1` use existing Session
operations. The first profile is one bounded little-endian int64 sample:

    y_nm = (count * gain + offset) / denominator

with `|count| <= 2^40`, `|gain| <= 2^20`, `|offset| <= 2^60`, denominator in `1..2^20`.
Reduced rational values preserve exact equivalence between `m`, `mm`, `um`, `nm`.
The example recognizes 24.982 mm == 0.024982 m without equating their bytes.
The existing CIW JSON profile is not JCS; int64 timestamps must not be rounded
through JavaScript Number in a future client. The proof statement's binary
DBI1/DBO1 format carries the complete-payload context hash and exact arithmetic.

Device/NET filters, configuration, computation, execution, proof verification
and function decisions have separate identities. The device filter and
firmware/gateware hashes are signed declarations, **not hardware attestations**.
The host validates without clamping, repairing or discarding original evidence.
Malformed/incoherent bounded packets remain quarantined historical evidence.

Raw display/recording can survive an inapplicable calibration. Calibration-applied
recording requires current host authorization and matching identity, declaration,
clock/frame, calibration domain/time support, coherence and freshness. Its label
remains `calibration_applied_not_physically_qualified`. Tolerance comparisons also
require an explicit total absolute uncertainty bound and specification. Unknown
uncertainty is not zero or independent covariance. Inspection records remain
unreleased drafts. Machine control, part release and arbitrary model inference
are unimplemented and always denied, even if a policy names them.

Expiry is rechecked after potentially slow proof verification. Replay fencing is
local to device/boot/function and existing Session history. It is not global
anti-replay, a crash-safe hardware counter or distributed exactly-once control.
Signed NET replies bind packet/device/node/policy/time and explicitly confer no
reusable execution permit. Reopen invokes no prover, restores no executable
binding and does not turn a historical `allow` into current authorization.

## Permissioned contributions

`network.contribution-prepare.v1` is a **local preparation** operation. It reuses
the existing boundary's coherence/metrology-declaration checks for the observation.
The source defaults to `private` and exact source bytes never change.

Sharing classes are `private`, `derived_shareable`, `pseudonymized`, `consortium`
and `public`. There is no automatic anonymization claim. Separate customer-signed
consent binds the exact source, tenant/device, recipients, purposes, permitted
source fields/conditions, transformation, output class, terms, use/retention
intervals and explicit cross-tenant model-training permission. A role or human
scope never substitutes for consent. SP1-required contribution consent refuses:
the new residual transformation is not SP1-qualified and never silently falls
back to an unproved contribution.

The first scientific projection is a length residual in metres, `r = y - yhat`.
An explicitly supplied 2x2 joint covariance is checked for symmetry/PSD and gives
`var(r) = var(y) + var(yhat) - 2 cov(y,yhat)`. Missing or withheld covariance stays
unknown. The prediction, covariance and conditions are customer declarations;
a model hash is not proof that the model ran. The source's calibration, clock
and instrument characteristics are not empirically qualified by this prototype.

The synthetic example has residual **-18 micrometres** and declared residual
variance **11e-12 m^2**. Approved temperature can be projected while source part,
facility, recipe, raw bytes and raw commitment remain local. The export is
node-signed; its signer identity/public key remains linkable. Conditions and
model fingerprints can also be sensitive. This is **not anonymity** and requires
recipient-specific disclosure/privacy review before any real transfer.

A recipient-keyed source group binds device/boot/sample privately, so changed
prediction/feature derivatives of one sample retain a common group. The full
export content has a separate contribution identity. Groups differ across
recipients. Key persistence/rotation is an operator responsibility; this is not
a completed dataset splitting service or leakage-proof federation. Private
lineage remains in the local Session. Only `contribution.json`, not a workspace
or whole result envelope, is the candidate outward projection.

`network.consent-revoke.v1` records signed retraction and denies future local
preparations. It does not erase retained audit history, guarantee recipient
compliance or unlearn already trained models. All prepared exports remain
`PREPARED_LOCAL_ONLY`, `transmitted=false`, `training_admission=false` and
`physical_qualification=not_established`. No real customer consent was exercised,
no data was uploaded and no model was trained. Corpus admission, rights review,
quality qualification, experiments and versioned model promotion remain separate.

## Signed release review, not installation

`network.release-review.v1` verifies a distinct publisher's manifest, artifact
hash/size, model/firmware/gateware kind, device family/runtime, validity and an
operator/retained-history version floor. Bytes remain opaque and are not loaded.
A signed validation-record reference is not the validation evidence itself.
The result says `installed=false`, `install_authorized=false`,
`model_executed=false`, `empirical_validation=not_established`.

This is not TUF/Uptane conformance, threshold-key update trust, secure boot,
anti-rollback hardware or an update installer. Account login/entitlement alone
cannot authorize those later safety- and compatibility-qualified functions.

## Commands and integration

```bash
python -m pip install '.[network,legibility]'
net boundary demo --output /new/boundary-demo
net network demo --output /new/network-demo
net network inspect --workspace /new/network-demo/workspace.json \
  --workspace-sha256 EXTERNALLY_RETAINED_SHA256
```

The demos use synthetic identities and ephemeral in-memory keys, with real
signatures/certificates. No private key or raw bearer token is exported. The
synthetic oscillator is only the existing Session carrier; gauge observations
are separately identified inputs, not oscillator measurements.

Trusted service setup uses `NetworkHost` with explicit account verifier, separate
CA/node/consent/publisher keys, an observation policy and a private lineage key.
It calls `enrollment_challenge` and `request(action, token, inputs)`. Retained
files cannot select keys, execute code, bind a provider or restore permissions.
The command vocabulary reserves `device.configure.prepare`, but no configuration
operation is provided. This increment adds no account UI, website route, socket,
cloud worker, ingestion API, TLS endpoint or customer-data egress.

## SP1 and qualification limits

The optional boundary adapter uses the existing SCR `PinnedSubprocessAdapter`,
`prove_and_verify_result` and `verify_existing_proof`, with explicit clean Git,
interpreter/dependency and host/guest bindings. It supports standalone SCR or the
consolidated source root. The pending guest uses existing commitment crates and
`ste.sp1.kernel-io.v1`. `scripts/build_device_boundary_guest.py` invokes existing
recipe/build/independent rebuild machinery; registration is deliberately unchanged
until a real build is reviewed. Native C++/Rust arithmetic checks are not proofs.
The current SP1 profile is the repository's `sp1-cpu v6.1.0`, not a latest-version
claim. No fake ELF hash, guest registry entry or verification key is supplied.

Source and noneditable installed tests use the complete recovered 406-file CIW
baseline at `0091492b9dc237b05c33cac77d2307115bd803b2`, package Git tree
`a10f56fcf5163e41741a1c6adcb8451c5cf256e0`. Shared Session/runner/signature/navigation
files are unchanged at inspected main `0374076695475669188d61698e4656ced2263e01`.
The three later microfab-related package changes were not reconstructed locally.
This is not a complete current-main monorepo regression or physical qualification.
The actual Session and crypto libraries are used, not import/protocol stubs.

Local Python is 3.13.5 with NumPy 2.3.5, cryptography 46.0.4, PyJWT 2.13.0,
websockets 16.0 and pytest 9.0.2. The existing NumPy/cryptography repository pins
2.4.3/46.0.0 are unchanged; local installed testing explicitly inherits these
available third-party libraries while excluding source imports. Hosted CI requires
pinned dependencies in a fresh environment and remains a separate gate. PyJWT's
pin names the tested version, not a claim that it is the newest or security-audited.

Remaining: production IdP/PKI/recovery, hardware key attestation, durable fleet
state/revocation synchronization, real physical acquisition and calibration,
network transport and privacy review, dataset admission/training, TUF-like update
qualification, native SP1 build/proof and full exact-head cross-platform checks.
No merge, deployment, real enrollment, model download/training or equipment action
is performed by this increment.

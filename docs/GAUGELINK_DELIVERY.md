# GaugeLink / NET: simpler inspection, stricter custody

Candidate extends NET PR #205 at `99137e0ddea057eacb9e0c934d9f249d277f432e`,
which already supplies the Link outbox, finite serial acquisition, scalar DAG and
MCP. No new gateway, queue, device registry, database schema, scientific model or
physical authority is introduced. NET's main remains unchanged; do not merge or
deploy as part of this increment. Product-family source remains in its private
repository. The unchanged private GaugeLink SDK is tested as a consumer only.

## Product constraint

**Push implementation complexity into NET. Expose the customer's capability,
result, current trust limits and next action, not the infrastructure graph.**

The first front door is **Digital Inspection**. From this candidate installation:

```sh
net link inspection demo --output-dir ../inspection-demo
```

This runs the existing four-operation scalar investigation and creates one
`inspection.html`. The default console is a short operator summary. The page
shows the recorded result, what is ready, what is not qualified, and the next
step. A collapsed **Advanced** section retains original scientific detail.
`--advanced` returns JSON for engineers. There is no browser service, internet
requirement, account login, discovery, new model server or additional broker.

An engineer-prepared request uses the same existing scalar schema, not a new kit
schema. The trusted deployment layer supplies its exact input reference:

```sh
net link inspection run prepared-request.json --request-ref sha256:EXTERNAL_DIGEST \
  --output-dir ../inspection
net link inspection status ../inspection --request-ref sha256:EXTERNAL_DIGEST
```

The reference is the SHA-256 of the **exact request bytes**, including formatting.
The lower-level `net link study` commands and read-only MCP remain available.
The customer-facing command does not auto-select a port, infer permissions,
download a profile, supply a calibration, accept a claimed identity or install a
model. Production connect/claim/review/qualify onboarding is a later integration
with independently qualified account, device and deployment services.

### Honest status and time-to-value

The synthetic example displays about **2.03 N**, an independently checked
calculation, and **NEEDS QUALIFICATION / HELD**. A nominal limit violation becomes
**REVIEW REQUIRED**, never an automatic part rejection. Unknown acquisition time,
missing calibration, conditional uncertainty and synthetic origin remain visible.
No confidence percentage or physical accuracy is invented.

`operator-processing.json` records actual host-monotonic processing duration for
an **existing recording**. It is not installation time, time since opening a box,
or first qualified measurement. `first_qualified_measurement_ns` remains null.
Reopened status reconstructs the existing scientific records; an altered HTML or
operator timing file cannot qualify a measurement or restore permission. The
local timing receipt is not authenticated field/deployment performance evidence.

Future managed appliances should record deployment and qualification milestones
at their actual occurrences and measure customer effort separately from compute
time. No integration-cost savings or trustworthy-result latency is claimed here.

## Reproduced and repaired defects

The exact parent source was exercised before modification:

* Two producer runtimes sharing device/boot/sequence could both report success
  for the same stored observation when the bytes matched. A producer call means
  a new occurrence, not a transport retry. The existing transaction now rejects
  the stale second producer. Reopen chooses the next retained sequence.
* A prior unsigned local delivery could bypass a later `require_auth=True` call
  because the old ACK short-circuited frame validation. Both stores now recheck
  exact wire/index/manifest bindings and the **current** authentication policy
  even for old acknowledgements. Missing receiver data is not silently repaired.
* READ permission is rechecked after a blocking readiness wait and immediately
  before serial read. Known denied authority cannot be used merely because the
  port was previously opened. OS calls and checks are not a race-free hardware
  authorization mechanism.

Wire format, observation identities, existing result encodings, database schema,
raw retention and all inherited commands remain intact. Local-only sync remains
available and does not become authenticated remote networking by implication.

## Node-signed local custody

`ciw.link_delivery` composes the existing journals with NET's **Legibility**
Ed25519 signatures. It does not implement another signing format, PKI or proof
system. The roles remain distinct: frame HMAC proves shared-key possession;
node signature asserts custody; SP1 would establish selected computation;
calibration/qualification and human authorization remain separate.

The host explicitly supplies a `DeliveryBinding` with tenant, node, device,
manifest, source/receiver journal identities, signing public key, trust epoch,
finite validity and frame-authentication policy. A saved file or receipt cannot
supply its own trusted key or enroll a node. An independently verified node
certificate can supply that key, but this module does not authenticate a
certificate or connect PR #206's account service automatically.

A 256-bit challenge binds the exact record and wire hash, manifest/boot/sequence,
source, receiver and trust descriptor. It lasts at most 60 seconds and cannot
outlive the host binding. Receiver code commits/readbacks the original bytes
before signing a receipt. Sender code verifies signature, pending challenge,
current binding, time and its original bytes before acknowledging in the
**existing ACK table**. It never deletes the original evidence.

The signed scope is deliberately narrow:

```text
scope                    durable_local_buffer_only
physical_verification    not_performed
device_authentication    not_established
calibration              not_established
state_admission          not_performed
machine_authority        false
transmitted_over_network false
```

A historical signed ACK is never reissued to look current. Reinspection requires
that both local journals still contain the original bytes. A later explicit host
trust epoch can preserve the history for the same key/endpoints, but cannot
silently change tenant, node, manifest or receiver. The current frame-auth policy
still applies. Hardware-backed clocks, keys and storage are not established.

Expired **unacknowledged** challenges refuse by default. A trusted host may
explicitly request `renew_expired=True` (CLI `--renew-expired`), generating a new
nonce under the still-valid binding and archiving the expired challenge. This
retries custody of the original bytes, never reacquires a measurement, extends
trust validity or resurrects an acknowledged grant. Crashes between historical
copy and compare-and-set preserve the old challenge and permit another explicit
retry. State budgets can refuse renewal; there is no silent eviction.

### Engineering interface

```sh
net link delivery demo --output-dir ../delivery-demo
net link delivery inspect ../delivery-demo/signed-receipt.json \
  --receipt-ref sha256:RETAINED_CONTENT_ID \
  --node-public-key ../delivery-demo/node-public-key.raw
```

The demo uses fresh synthetic keys and instrument data; private keys are not
retained. `inspect` is historical and creates no active authority. Explicit
`delivery sync --help` exposes the advanced local journal/key/manifest bindings;
these are deployment-engineering inputs, not the intended retail setup screen.
Secrets are read from operator-selected local files, never from a saved journal
or a bearer token embedded in measurement data. No sockets or remote endpoints
are used. Complete workspaces, journals and Advanced views remain customer-local
and may contain proprietary context; this is not a consent-filtered export.

## Verification and integration boundaries

Run the affected existing suites and additions:

```sh
PYTHONPATH=src python -m pytest -q tests/test_link.py tests/test_connectivity.py \
  tests/test_link_study.py tests/test_link_delivery.py tests/test_link_inspection.py
python -m pip wheel --no-deps --no-build-isolation . --wheel-dir dist
python scripts/check_link_installation.py --wheel dist/EXACT_WHEEL.whl \
  --output-dir ../installed-check --require-pins --with-scalar-study --with-delivery
```

The inherited Linux/Python 3.11/3.13 workflow requires exact repository pins.
Offline local `--allow-host-dependencies` is labelled separately and does not
establish pinned CI. The source/installed suites repeat the same distinct cases,
not additive counts. They use real SQLite, process exits, concurrency, Linux
PTYs, signatures and native Session computations on synthetic observations.
They do not establish a real gauge/cable, ARM board, device key, physical
calibration, safe industrial installation, remote transport or SP1 proof.

The unchanged GaugeLink private SDK at
`8ec158c68b5e76e10928daced2132c6de84dd17f` remains an optional consumer of NET.
No private product source is copied here. Parent #185 data bridges and #206
network/consent source are untouched. Signed local custody must not be used as a
substitute for those distinct enrollment, authorization or contribution gates.

Primary implementation references: SQLite transaction control
<https://www.sqlite.org/lang_transaction.html> and cryptography Ed25519 signing
<https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/>.
The existing local `BEGIN IMMEDIATE` transaction and Legibility implementation
are reused. Hash/signature checks are not storage-hardware attestation, malicious
race resistance, authenticated physical truth or a production security audit.

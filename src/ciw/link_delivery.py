"""Node-signed local delivery over the existing Link journals and Legibility.

No sockets, second queue, device enrollment, user login, physical verification,
cloud export or restored authority. Host trust bindings are supplied out of band.
A signed receipt is the configured node's custody assertion, not proof that its
storage hardware is durable, that a device was genuine, or that a value is true.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import secrets
import re

from . import link_contract as c
from .core.identities import new_identity, validate_identity
from .legibility import compile_bundle, sign_bundle, verify_bundle, key_fingerprint
from .link_store import Journal, decode_bound

REQUEST_SCHEMA = "ciw.link-delivery-request.v1"
ACK_SCHEMA = "ciw.link-signed-delivery.v1"
ROLE = "link.delivery-receipt.v1"  # Signature role, NOT a registered Session operation.
MAX_RECEIPT = 32768
REQUEST_LIFETIME_NS = 60_000_000_000
SCOPE = {"scope": "durable_local_buffer_only", "state_admission": "not_performed",
         "physical_verification": "not_performed", "device_authentication": "not_established",
         "calibration": "not_established", "machine_authority": False,
         "transmitted_over_network": False}


@dataclass(frozen=True)
class DeliveryBinding:
    """Explicit host configuration; a receipt/file cannot create this authority.

    The node key can be obtained from a separately verified NET node certificate.
    This object does not implement certificate discovery/revocation or PKI.
    epoch and validity identify the locally reviewed trust snapshot.
    """
    tenant_id: str
    node_id: str
    device_id: str
    manifest_ref: str
    source_id: str
    receiver_id: str
    epoch: str
    public_key: bytes
    valid_from_ns: int
    valid_until_ns: int
    require_frame_auth: bool = True

    def __post_init__(self):
        for field in ("tenant_id", "node_id", "device_id", "source_id", "receiver_id", "epoch"):
            c.text(getattr(self, field))
        c.content_ref(self.manifest_ref)
        if self.source_id == self.receiver_id:
            raise ValueError("Delivery source and receiver must be distinct journals")
        if type(self.public_key) is not bytes or len(self.public_key) != 32:
            raise ValueError("Bind one exact raw Ed25519 node public key")
        c.integer(self.valid_from_ns, maximum=2**63-1)
        c.integer(self.valid_until_ns, maximum=2**63-1)
        if self.valid_until_ns <= self.valid_from_ns or type(self.require_frame_auth) is not bool:
            raise ValueError("Invalid finite binding interval or authentication policy")

    def descriptor(self) -> dict:
        return {"schema": "ciw.link-delivery-binding.v1", "tenant_id": self.tenant_id,
                "node_id": self.node_id, "device_id": self.device_id,
                "manifest_ref": self.manifest_ref, "source_id": self.source_id,
                "receiver_id": self.receiver_id, "epoch": self.epoch,
                "signer_key_ref": key_fingerprint(self.public_key),
                "valid_from_ns": self.valid_from_ns, "valid_until_ns": self.valid_until_ns,
                "require_frame_auth": self.require_frame_auth}

    def check(self, now_ns: int, device: dict) -> None:
        c.integer(now_ns, maximum=2**63-1)
        device = c.manifest(device)
        if not self.valid_from_ns <= now_ns < self.valid_until_ns:
            raise PermissionError("Node delivery binding is inactive or expired")
        if device["device_id"] != self.device_id or c.content_identity(device) != self.manifest_ref:
            raise PermissionError("Delivery binding names another device or configuration")


def _equal(left, right):
    return c.canonical_json(left) == c.canonical_json(right)


def _request(value: dict) -> dict:
    c.bounded(value, 8192)
    c.keys(value, {"schema", "binding", "message_key", "wire_ref", "wire_bytes", "kind",
                   "boot_id", "sequence", "created_ns", "expires_ns", "nonce", "request_ref"})
    if value["schema"] != REQUEST_SCHEMA:
        raise ValueError("Unsupported delivery request")
    binding = value["binding"]
    c.keys(binding, {"schema", "tenant_id", "node_id", "device_id", "manifest_ref", "source_id",
                     "receiver_id", "epoch", "signer_key_ref", "valid_from_ns", "valid_until_ns",
                     "require_frame_auth"})
    if binding["schema"] != "ciw.link-delivery-binding.v1":
        raise ValueError("Unsupported delivery trust descriptor")
    for field in ("tenant_id", "node_id", "device_id", "source_id", "receiver_id", "epoch"):
        c.text(binding[field])
    c.content_ref(binding["manifest_ref"])
    if (type(binding["signer_key_ref"]) is not str
            or re.fullmatch(r"ed25519:sha256:[0-9a-f]{64}", binding["signer_key_ref"]) is None):
        raise ValueError("Require the existing Ed25519 signer fingerprint")
    if binding["source_id"] == binding["receiver_id"] or type(binding["require_frame_auth"]) is not bool:
        raise ValueError("Invalid delivery endpoints or authentication policy")
    for field in ("valid_from_ns", "valid_until_ns"):
        c.integer(binding[field], maximum=2**63-1)
    c.text(value["message_key"], maximum=512); c.content_ref(value["wire_ref"])
    from .link_protocol import MAX_FRAME
    c.integer(value["wire_bytes"], minimum=1, maximum=MAX_FRAME)
    for field in ("created_ns", "expires_ns"):
        c.integer(value[field], maximum=2**63-1)
    if not (binding["valid_from_ns"] <= value["created_ns"] < value["expires_ns"] <= binding["valid_until_ns"]
            and value["expires_ns"] - value["created_ns"] <= REQUEST_LIFETIME_NS):
        raise ValueError("Request validity does not fit the reviewed trust snapshot")
    if value["kind"] not in {"manifest", "observation", "event"}:
        raise ValueError("Unsupported delivered record kind")
    if value["kind"] == "manifest":
        if value["boot_id"] is not None or value["sequence"] is not None:
            raise ValueError("Manifest is not an acquisition occurrence")
    else:
        c.text(value["boot_id"]); c.integer(value["sequence"])
    nonce = value["nonce"]
    if type(nonce) is not str or len(nonce) != 64 or any(x not in "0123456789abcdef" for x in nonce):
        raise ValueError("Require a canonical 256-bit request nonce")
    if value["request_ref"] != c.content_identity({k:v for k,v in value.items() if k != "request_ref"}):
        raise ValueError("Delivery request identity differs")
    return deepcopy(value)


def _active(request, binding, device, now_ns):
    request = _request(request)
    binding.check(now_ns, device)
    if not _equal(request["binding"], binding.descriptor()):
        raise PermissionError("Receipt cannot replace the host's node, tenant, epoch or key binding")
    if not request["created_ns"] <= now_ns < request["expires_ns"]:
        raise PermissionError("Delivery challenge expired or caller clock moved before issuance")
    return request


def _matches_wire(request, wire, device, *, key=None, require_auth=False, offline=False):
    frame, value, message_key, manifest_ref = decode_bound(
        wire, device, key=key, require_auth=require_auth, offline=offline)
    sequence = value.get("sequence") if frame.kind == "observation" else value.get("event_sequence")
    if (request["message_key"] != message_key or request["wire_ref"] != c.bytes_ref(wire)
            or request["wire_bytes"] != len(wire) or request["kind"] != frame.kind
            or request["binding"]["manifest_ref"] != manifest_ref
            or request["boot_id"] != value.get("boot_id") or request["sequence"] != sequence):
        raise ValueError("Delivery request differs from exact wire/manifest/occurrence")


def _pending_namespace(binding, message_key):
    return "delivery-request:" + c.content_identity({"binding": binding.descriptor(), "message_key": message_key})


def request_delivery(source: Journal, message_key: str, *, device: dict,
                     binding: DeliveryBinding, now_ns: int, key=None, renew_expired=False) -> dict:
    """Persist one request in the existing local review-state table; no key retention."""
    if type(renew_expired) is not bool:
        raise ValueError("Explicit challenge-renewal policy must be a boolean")
    binding.check(now_ns, device)
    if source.store_id != binding.source_id:
        raise PermissionError("Wrong source journal")
    row = source.bound_row(message_key, expected_manifest=device, key=key,
                           require_auth=binding.require_frame_auth)
    namespace = _pending_namespace(binding, message_key)
    previous = source.state(namespace)
    previous_revision = 0
    if previous is not None:
        request = _request(previous["value"])
        if not _equal(request["binding"], binding.descriptor()) or now_ns < request["created_ns"]:
            raise PermissionError("Pending request differs from binding or caller clock regressed")
        _matches_wire(request, row["wire"], device, key=key, require_auth=binding.require_frame_auth)
        if now_ns < request["expires_ns"]:
            return request
        if not renew_expired:
            raise PermissionError("Delivery challenge expired; explicit renewal or a new trust epoch is required")
        if source.db.execute("SELECT 1 FROM acks WHERE message_key=?", (message_key,)).fetchone():
            raise ValueError("An acknowledged request remains historical; do not renew its authority")
        # Lost acknowledgements must not strand an offline recording forever.
        # Preserve the expired challenge before compare-and-set replacement. A
        # crash between these transactions leaves a harmless historical copy;
        # the next explicit sync retries without replacing any retained bytes.
        history_key = "delivery-request-history:" + request["request_ref"]
        historical = source.state(history_key)
        if historical is None:
            source.compare_and_set_state(history_key, 0, request)
        elif not _equal(historical["value"], request):
            raise ValueError("Expired request history differs from original challenge")
        previous_revision = previous["revision"]
    frame, value, _, _ = decode_bound(row["wire"], device, key=key, require_auth=binding.require_frame_auth)
    request = {"schema": REQUEST_SCHEMA, "binding": binding.descriptor(), "message_key": message_key,
               "wire_ref": row["wire_ref"], "wire_bytes": len(row["wire"]), "kind": frame.kind,
               "boot_id": value.get("boot_id"),
               "sequence": value.get("sequence") if frame.kind == "observation" else value.get("event_sequence"),
               "created_ns": now_ns, "expires_ns": min(binding.valid_until_ns, now_ns + REQUEST_LIFETIME_NS),
               "nonce": secrets.token_hex(32)}
    request["request_ref"] = c.content_identity(request)
    _request(request)
    # Compare-and-set refuses a concurrent issuer; caller reopens/retries rather
    # than silently replacing another persisted challenge.
    source.compare_and_set_state(namespace, previous_revision, request)
    return request


def _bundle(payload, execution_id):
    validate_identity(execution_id, "execution")
    raw = c.bounded(payload, 16384)
    req = _request(payload["request"])
    base = {"schema": "ciw.legibility-contract.v1",
        "object": {"object_id": req["request_ref"], "version": c.content_identity(payload),
                   "kind": "notation-link-local-delivery", "label": "Node retained-byte custody receipt"},
        "bindings": {"evidence_id": req["wire_ref"], "operation_id": ROLE,
                     "execution_id": execution_id, "verification_id": None},
        "semantics": {"coordinate_frame": "not_applicable.transport", "properties": [], "relationships": [],
                      "assumptions": ["trusted_node_process_and_local_storage_not_hardware_attestation"]},
        "claims": [], "qualification": {"status": "unqualified", "calibration_refs": [],
                                          "verification_refs": [], "canonical_admission": False},
        "vision": {"image_artifact_id": None, "annotations": []},
        "artifacts": [{"artifact_id": "receipt", "media_type": "application/json",
                       "sha256": c.bytes_ref(raw), "size_bytes": len(raw)}]}
    return compile_bundle(base, {"receipt": raw})


def inspect_receipt(signed: dict, *, expected_ref: str, public_key: bytes) -> dict:
    """Historical signature/integrity inspection only. Does not open a journal or grant."""
    c.bounded(signed, MAX_RECEIPT); c.content_ref(expected_ref)
    c.keys(signed, {"schema", "payload", "execution_id", "signature"})
    if signed["schema"] != ACK_SCHEMA or c.content_identity(signed) != expected_ref:
        raise ValueError("Signed receipt differs from its externally retained anchor")
    if type(public_key) is not bytes or len(public_key) != 32:
        raise ValueError("An out-of-band node key is required for historical verification")
    payload = signed["payload"]
    c.keys(payload, {"request", "retention", "acknowledged_ns", "scope"})
    request = _request(payload["request"])
    if key_fingerprint(public_key) != request["binding"]["signer_key_ref"] or not _equal(payload["scope"], SCOPE):
        raise ValueError("Receipt changes signer role or makes an unsupported authority claim")
    c.integer(payload["acknowledged_ns"], maximum=2**63-1)
    if not request["created_ns"] <= payload["acknowledged_ns"] < request["expires_ns"]:
        raise ValueError("Node acknowledgement is outside request validity")
    retention = payload["retention"]
    expected = {"schema": "ciw.link-delivery-receipt.v1", "receiver_id": request["binding"]["receiver_id"],
                "message_key": request["message_key"], "wire_ref": request["wire_ref"],
                "scope": "durable_local_buffer_only", "state_admission": "not_performed",
                "physical_verification": "not_performed"}
    expected["receipt_ref"] = c.content_identity(expected)
    if not _equal(retention, expected):
        raise ValueError("Signed node receipt acknowledges a different local retention record")
    bundle = _bundle(payload, signed["execution_id"])
    report = verify_bundle(bundle, signed["signature"], artifacts={"receipt": c.bounded(payload, 16384)},
        trusted_keys={key_fingerprint(public_key): public_key}, expected_object_id=request["request_ref"],
        expected_version=c.content_identity(payload))
    if (report["errors"] or report["signature_valid"] is not True or report["issuer_trusted"] is not True
            or report["content_intact"] is not True or report["artifact_status"] != "verified"):
        raise ValueError("Invalid or untrusted node delivery signature")
    return {"schema": "ciw.link-delivery-review.v1", "status": "HISTORICAL_SIGNATURE_AND_BINDINGS",
            "receipt_ref": expected_ref, "request_ref": request["request_ref"],
            "verification_id": report["verification_id"], "scope": deepcopy(SCOPE),
            "active_authority": False, "acknowledgement_applied": False}


def issue_receipt(receiver: Journal, request: dict, wire: bytes, *, device: dict,
                  binding: DeliveryBinding, signing_key, now_ns: int, key=None, after_commit=None) -> dict:
    """Append/read back exact input BEFORE issuing the signed receipt.

    after_commit is a trusted local fault-injection hook, never set from data.
    A crash at this point loses no acknowledged data: retry reuses retained bytes.
    """
    request = _active(request, binding, device, now_ns)
    if receiver.store_id != binding.receiver_id:
        raise PermissionError("Wrong receiver journal")
    _matches_wire(request, wire, device, key=key, require_auth=binding.require_frame_auth)
    # Use the same optional cryptography implementation as NET Legibility.
    from .legibility import _crypto
    private_type, _, serialization, _ = _crypto()
    if type(signing_key) is bytes:
        signing_key = private_type.from_private_bytes(signing_key)
    if not isinstance(signing_key, private_type):
        raise ValueError("Require an explicitly configured Ed25519 node signing key")
    public = signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if public != binding.public_key:
        raise PermissionError("Signing key does not match the reviewed node binding")
    receiver.append(wire, expected_manifest=device, key=key, require_auth=binding.require_frame_auth)
    receiver.bound_row(request["message_key"], expected_manifest=device, key=key,
                       require_auth=binding.require_frame_auth)
    retention = receiver.receipt(request["message_key"])
    if after_commit is not None:
        after_commit(retention)
    namespace = "delivery-signed:" + request["request_ref"]
    previous = receiver.state(namespace)
    if previous is not None:
        signed = previous["value"]
        inspect_receipt(signed, expected_ref=c.content_identity(signed), public_key=binding.public_key)
        if (not _equal(signed["payload"]["request"], request)
                or not _equal(signed["payload"]["retention"], retention)):
            raise ValueError("Retained node signature belongs to another request or bytes")
        return signed
    payload = {"request": request, "retention": retention, "acknowledged_ns": now_ns, "scope": deepcopy(SCOPE)}
    execution_id = new_identity("execution")
    signed = {"schema": ACK_SCHEMA, "payload": payload, "execution_id": execution_id,
              "signature": sign_bundle(_bundle(payload, execution_id), signing_key)}
    inspect_receipt(signed, expected_ref=c.content_identity(signed), public_key=binding.public_key)
    receiver.compare_and_set_state(namespace, 0, signed)
    return signed


def accept_receipt(source: Journal, signed: dict, *, device: dict,
                   binding: DeliveryBinding, now_ns: int, key=None) -> dict:
    """Authenticate then atomically mark delivery in the EXISTING ack table.

    No data deletion and no calibration, canonical admission or machine grant.
    A persisted local-only ACK cannot be silently upgraded to a signed one.
    """
    c.bounded(signed, MAX_RECEIPT)
    review = inspect_receipt(signed, expected_ref=c.content_identity(signed), public_key=binding.public_key)
    request = _active(signed["payload"]["request"], binding, device, now_ns)
    if source.store_id != binding.source_id or now_ns < signed["payload"]["acknowledged_ns"]:
        raise PermissionError("Wrong source or caller clock before node acknowledgement")
    raw = c.bounded(signed, MAX_RECEIPT)
    with source.transaction():
        pending = source.state(_pending_namespace(binding, request["message_key"]))
        if pending is None or not _equal(pending["value"], request):
            raise ValueError("No matching durable source challenge")
        row = source.bound_row(request["message_key"], expected_manifest=device, key=key,
                               require_auth=binding.require_frame_auth)
        _matches_wire(request, row["wire"], device, key=key, require_auth=binding.require_frame_auth)
        previous = source.db.execute("SELECT receiver_id,wire_ref,receipt FROM acks WHERE message_key=?",
                                     (request["message_key"],)).fetchone()
        expected = (binding.receiver_id, request["wire_ref"], raw)
        if previous is not None and previous != expected:
            raise ValueError("Existing acknowledgement has another receiver, signature or trust mode")
        source.db.execute("INSERT OR IGNORE INTO acks VALUES(?,?,?,?)", (request["message_key"], *expected))
    return {**review, "status": "SIGNED_CUSTODY_ACK_RETAINED", "acknowledgement_applied": True,
            "duplicate": previous is not None, "active_authority": False}


def _retained_ack(source, receiver, row, stored, *, device, binding, now_ns, key):
    """Inspect an old receipt without reissuing it or renewing past authority.

    A newly reviewed epoch/validity interval can retain history for the same node
    key and endpoints. Key/tenant/manifest changes cannot silently reuse that ACK.
    Current frame-authentication policy is checked independently on BOTH stores.
    """
    binding.check(now_ns, device)
    signed = c.loads(stored[2], MAX_RECEIPT)
    review = inspect_receipt(signed, expected_ref=c.content_identity(signed), public_key=binding.public_key)
    payload = signed["payload"]
    request = payload["request"]
    fixed = {"schema", "tenant_id", "node_id", "device_id", "manifest_ref", "source_id",
             "receiver_id", "signer_key_ref"}
    if any(request["binding"][k] != binding.descriptor()[k] for k in fixed):
        raise PermissionError("Historical ACK belongs to another node, key or endpoint binding")
    if now_ns < payload["acknowledged_ns"]:
        raise PermissionError("Caller clock precedes retained node acknowledgement")
    expected = (binding.receiver_id, row["wire_ref"], c.bounded(signed, MAX_RECEIPT))
    if stored != expected or request["message_key"] != row["message_key"]:
        raise ValueError("Historical acknowledgement index/bytes differ")
    pending_namespace = "delivery-request:" + c.content_identity({
        "binding": request["binding"], "message_key": row["message_key"]})
    pending = source.state(pending_namespace)
    issued = receiver.state("delivery-signed:" + request["request_ref"])
    if (pending is None or not _equal(pending["value"], request)
            or issued is None or not _equal(issued["value"], signed)):
        raise ValueError("Historical delivery lost its original request or node receipt")
    for store in (source, receiver):
        current = store.bound_row(row["message_key"], expected_manifest=device, key=key,
                                  require_auth=binding.require_frame_auth)
        _matches_wire(request, current["wire"], device, key=key, require_auth=binding.require_frame_auth)
    if not _equal(receiver.receipt(row["message_key"]), payload["retention"]):
        raise ValueError("Original recipient no longer retains the acknowledged evidence")
    return {**review, "status": "HISTORICAL_SIGNED_CUSTODY_STILL_RETAINED", "duplicate": True,
            "current_binding_ref": c.content_identity(binding.descriptor()),
            "acknowledgement_applied": False, "active_authority": False}


def sync_signed(source: Journal, receiver: Journal, *, device: dict, binding: DeliveryBinding,
                signing_key, now_ns: int, key=None, after_receive=None, clock=None, renew_expired=False) -> dict:
    """Finite local transfer; no endpoints, discovery, transmission or acquisitions.

    One exact manifest per call. An unrelated manifest is not silently dropped.
    Stronger HMAC policy is rechecked on every retry, including retained receipts.
    clock is a trusted host callable, never selected by saved data. The CLI supplies
    a live wall clock; an explicit fixed now_ns is a deterministic test/review time.
    """
    if type(renew_expired) is not bool:
        raise ValueError("Explicit challenge-renewal policy must be a boolean")
    binding.check(now_ns, device)
    last_time = now_ns

    def instant():
        nonlocal last_time
        value = now_ns if clock is None else clock()
        c.integer(value, maximum=2**63-1)
        if value < last_time:
            raise PermissionError("Host clock moved backwards during delivery")
        binding.check(value, device)
        last_time = value
        return value
    if source.store_id != binding.source_id or receiver.store_id != binding.receiver_id:
        raise PermissionError("Explicit journal binding differs")
    rows = source.rows()
    # Preflight the complete selection before any submission.
    for row in rows:
        source.bound_row(row["message_key"], expected_manifest=device, key=key,
                          require_auth=binding.require_frame_auth)
    results = []
    for row in rows:
        stored = source.db.execute("SELECT receiver_id,wire_ref,receipt FROM acks WHERE message_key=?",
                                   (row["message_key"],)).fetchone()
        if stored is not None:
            results.append(_retained_ack(source, receiver, row, stored, device=device, binding=binding,
                                         now_ns=instant(), key=key))
            continue
        request = request_delivery(source, row["message_key"], device=device, binding=binding, now_ns=instant(), key=key, renew_expired=renew_expired)
        signed = issue_receipt(receiver, request, row["wire"], device=device, binding=binding,
                                signing_key=signing_key, now_ns=instant(), key=key)
        if after_receive is not None:
            after_receive(signed)
        results.append(accept_receipt(source, signed, device=device, binding=binding, now_ns=instant(), key=key))
    return {"schema": "ciw.link-signed-sync.v1", "source_id": source.store_id, "receiver_id": receiver.store_id,
            "new_acknowledgements": sum(not x["duplicate"] for x in results),
            "existing_acknowledgements": sum(x["duplicate"] for x in results),
            "receipts": results, "scope": deepcopy(SCOPE)}

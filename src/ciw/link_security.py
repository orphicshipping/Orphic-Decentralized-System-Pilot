"""Offline signed firmware/gateware package review, not secure boot or flashing.

Uses the optional Ed25519 dependency already used by NET. The verification key
is supplied by the host; a package cannot introduce its own trust root. Local
review high-water counters are rollback checks, not tamper-proof hardware state.
"""
from __future__ import annotations

import base64

from . import link_contract as c

DOMAIN = b"Notation-Link-Update-v1\0"
MAX_IMAGE_BYTES = 32 * 1024 * 1024


def update_manifest(value):
    c.bounded(value, 65536)
    c.keys(value, {"schema", "device_id", "hardware_revision", "kind", "version", "generation", "image_ref", "image_bytes"})
    if value["schema"] != "ciw.link-update-manifest.v1" or value["kind"] not in {"firmware", "gateware"}:
        raise ValueError("Unsupported update package")
    for field in ("device_id", "hardware_revision", "version"):
        c.text(value[field])
    c.integer(value["generation"], minimum=1, maximum=2**53 - 1)
    c.integer(value["image_bytes"], minimum=1, maximum=MAX_IMAGE_BYTES)
    c.content_ref(value["image_ref"])
    return value


def verify_update(package, image, *, device, trusted_public_key, minimum_generation):
    """Verify bytes/signature/applicability. Does not execute or install image."""
    c.keys(package, {"manifest", "signature_b64"})
    value = update_manifest(package["manifest"])
    device = c.manifest(device)
    c.integer(minimum_generation, maximum=2**53 - 1)
    if value["device_id"] != device["device_id"] or value["hardware_revision"] != device["hardware_revision"]:
        raise ValueError("Update target device/hardware mismatch")
    if value["generation"] <= minimum_generation:
        raise ValueError("Update generation rollback/replay refused")
    if type(image) is not bytes or len(image) != value["image_bytes"] or c.bytes_ref(image) != value["image_ref"]:
        raise ValueError("Update image identity/length mismatch")
    if type(trusted_public_key) is not bytes or len(trusted_public_key) != 32:
        raise ValueError("Bind an exact raw Ed25519 public key outside the package")
    encoded = package["signature_b64"]
    if type(encoded) is not str or len(encoded) != 88:
        raise ValueError("Invalid Ed25519 signature encoding")
    signature = base64.b64decode(encoded, validate=True)
    if len(signature) != 64 or base64.b64encode(signature).decode() != encoded:
        raise ValueError("Signature must use canonical base64")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise RuntimeError("Signed update review requires NET's existing optional legibility dependency") from exc
    try:
        Ed25519PublicKey.from_public_bytes(trusted_public_key).verify(signature, DOMAIN + c.bounded(value, 65536))
    except InvalidSignature as exc:
        raise ValueError("Update signature verification failed") from exc
    return {"schema": "ciw.link-update-review.v1", "status": "SIGNATURE_AND_DECLARED_TARGET_MATCH",
            "manifest_ref": c.content_identity(value), "signer_key_ref": c.bytes_ref(trusted_public_key),
            "image_ref": value["image_ref"], "generation": value["generation"],
            "kind": value["kind"], "installation": "not_performed", "secure_boot": "not_established",
            "physical_validation": "not_established"}


def record_update_review(journal, package, image, *, device, trusted_public_key, authority, now_ns):
    """Host-authorized local review with transactional generation compare-and-set."""
    authority.check("UPDATE", device, now_ns)
    value = update_manifest(package["manifest"])
    namespace = "update:" + device["device_id"] + ":" + value["kind"]
    prior = journal.state(namespace)
    minimum = 0 if prior is None else prior["value"]["generation"]
    review = verify_update(package, image, device=device, trusted_public_key=trusted_public_key, minimum_generation=minimum)
    journal.compare_and_set_state(namespace, 0 if prior is None else prior["revision"], review)
    return review

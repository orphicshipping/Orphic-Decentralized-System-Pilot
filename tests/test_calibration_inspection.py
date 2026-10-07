"""Synthetic DCC fixtures; no fixture represents an installed calibration."""
from copy import deepcopy
import json

import pytest

from ciw import link_contract as c
from ciw.calibration_inspection import inspect_certificate, inspect_link_calibration, main
from ciw.link_profiles import example_manifest


CERTIFICATE = b'''<?xml version="1.0" encoding="UTF-8"?>
<dcc:digitalCalibrationCertificate xmlns:dcc="https://ptb.de/dcc" xmlns:si="https://ptb.de/si" schemaVersion="3.3.0">
<dcc:administrativeData><dcc:coreData><dcc:uniqueIdentifier>synthetic-certificate-1</dcc:uniqueIdentifier>
<dcc:beginPerformanceDate>2026-01-01</dcc:beginPerformanceDate><dcc:endPerformanceDate>2026-01-01</dcc:endPerformanceDate>
</dcc:coreData><dcc:items><dcc:item id="test-item"><dcc:identifications><dcc:identification>
<dcc:issuer>manufacturer</dcc:issuer><dcc:value>synthetic.instrument.1</dcc:value>
</dcc:identification></dcc:identifications></dcc:item></dcc:items></dcc:administrativeData>
<dcc:measurementResults><dcc:measurementResult><dcc:results><dcc:quantity id="q1" refId="test-item">
<si:real><si:value>1.200</si:value><si:unit>\\milli\\metre</si:unit></si:real>
</dcc:quantity></dcc:results></dcc:measurementResult></dcc:measurementResults>
</dcc:digitalCalibrationCertificate>'''


def link_case():
    device = example_manifest()
    channel = device["channels"][0]
    declaration = {"schema": "ciw.link-calibration-declaration.v1", "calibration_id": "synthetic-calibration-1",
        "manifest_ref": c.content_identity(device), "channel_id": channel["id"], "unit": channel["unit"],
        "valid_from": "2026-01-01T00:00:00Z", "valid_until": "2027-01-01T00:00:00Z",
        "certificate": c.packed(CERTIFICATE, 32768), "reference_standard_ref": None}
    observation = c.make_observation(device, boot_id="synthetic-boot", sequence=0, channel_id=channel["id"],
        raw=b"1.200 " + channel["unit"].encode() + b"\r\n", origin="synthetic",
        acquired_at="2026-10-07T12:00:00Z", calibration_ref=c.content_identity(declaration))
    scope = {"schema": "ncp.dcc-link-scalar-scope.v1", "certificate_ref": c.bytes_ref(CERTIFICATE),
        "item_xml_id": "test-item", "identification_issuer": "manufacturer", "quantity_xml_id": "q1",
        "channel_id": channel["id"], "certificate_unit": "\\milli\\metre", "link_unit": channel["unit"],
        "range": {"minimum": "0.00", "maximum": "2.00"},
        "interpretation_basis": "Synthetic operator scope for software test; no calibration or unit conversion authority"}
    return device, declaration, observation, scope


def test_profile_preserves_decimal_and_never_authenticates():
    report = inspect_certificate(CERTIFICATE)
    assert report["parse"]["status"] == "PARSED"
    assert report["schema_inspection"]["status"] == "INSPECTION_PROFILE_PASSED"
    assert report["schema_inspection"]["full_xsd_validation"] == "not_performed"
    assert report["facts"]["scalar_quantities"][0]["value_text"] == "1.200"
    assert report["issuer_authenticity"]["status"] == "UNKNOWN"
    assert report["correction_authorization"]["status"] == "NOT_GRANTED"
    assert report["source"]["sha256"] == c.bytes_ref(CERTIFICATE)


@pytest.mark.parametrize("payload", [b"<broken>", b'<!DOCTYPE x [<!ENTITY y "unsafe">]><x>&y;</x>',
    '<!DOCTYPE x [<!ENTITY y "unsafe">]><x>&y;</x>'.encode("utf-16"),
    ('<a>' * 34 + '</a>' * 34).encode(), b'<?xml version="1.0" encoding="ISO-8859-1"?><x/>'])
def test_unsafe_or_malformed_xml_refuses(payload):
    report = inspect_certificate(payload)
    assert report["parse"]["status"] == "FAILED"
    assert report["facts"] is None


@pytest.mark.parametrize("before,after,status", [
    (b'3.3.0', b'3.2.1', "UNSUPPORTED_PROFILE"),
    (b'https://ptb.de/dcc', b'https://untrusted.invalid/dcc', "UNSUPPORTED_PROFILE"),
    (b'id="q1"', b'id="test-item"', "INSPECTION_PROFILE_FAILED"),
    (b'refId="test-item"', b'refId="missing"', "INSPECTION_PROFILE_FAILED"),
    (b'<si:value>1.200</si:value>', b'<si:value>NaN</si:value>', "INSPECTION_PROFILE_FAILED"),
    (b'<si:unit>\\milli\\metre</si:unit>', b'', "INSPECTION_PROFILE_FAILED"),
])
def test_schema_profile_and_scalar_corruption(before, after, status):
    assert inspect_certificate(CERTIFICATE.replace(before, after))["schema_inspection"]["status"] == status


def test_signature_presence_cannot_supply_authenticity():
    raw = CERTIFICATE.replace(b'</dcc:digitalCalibrationCertificate>',
        b'<ds:Signature xmlns:ds="http://www.w3.org/2000/09/xmldsig#"/></dcc:digitalCalibrationCertificate>')
    report = inspect_certificate(raw)
    assert report["issuer_authenticity"]["signature_elements"] == 1
    assert report["issuer_authenticity"]["status"] == "UNKNOWN"


def test_declared_scope_matches_but_remains_unapproved_and_unchanged():
    device, declaration, observation, scope = link_case()
    before = deepcopy((declaration, observation, scope))
    report = inspect_link_calibration(declaration, device=device, observation=observation, scope=scope)
    assert report["applicability"]["status"] == "MATCHES_DECLARED_SCOPE"
    assert report["applicability"]["chain_applicability"] == "not_established_by_declared_scope_match"
    assert report["correction_authorization"]["correction_applied"] is False
    assert (declaration, observation, scope) == before


@pytest.mark.parametrize("change", ["scope_hash", "scope_unit", "channel", "range", "instrument", "expired", "declaration"])
def test_scope_mismatches_are_independent_of_parsing(change):
    device, declaration, observation, scope = link_case()
    if change == "scope_hash": scope["certificate_ref"] = c.bytes_ref(b"different")
    elif change == "scope_unit": scope["certificate_unit"] = "\\metre"
    elif change == "channel": scope["channel_id"] = "absent"
    elif change == "range": scope["range"]["maximum"] = "1.19999999999999999999999"
    elif change == "instrument":
        declaration["certificate"] = c.packed(CERTIFICATE.replace(b'synthetic.instrument.1', b'other.instrument.1'), 32768)
        observation["calibration_ref"] = c.content_identity(declaration)
        scope["certificate_ref"] = c.bytes_ref(c.unpack(declaration["certificate"], 32768))
    elif change == "expired": observation["acquired_at"] = declaration["valid_until"]
    elif change == "declaration": observation["calibration_ref"] = c.bytes_ref(b"other-declaration")
    report = inspect_link_calibration(declaration, device=device, observation=observation, scope=scope)
    assert report["parse"]["status"] == "PARSED"
    assert report["applicability"]["status"] == "MISMATCH"
    assert report["correction_authorization"]["status"] == "NOT_GRANTED"


@pytest.mark.parametrize("missing", ["time", "identity", "scope"])
def test_missing_evidence_stays_unknown(missing):
    device, declaration, observation, scope = link_case()
    if missing == "time": observation["acquired_at"] = None
    elif missing == "identity":
        declaration["certificate"] = c.packed(CERTIFICATE.replace(b'<dcc:identifications><dcc:identification>\n<dcc:issuer>manufacturer</dcc:issuer><dcc:value>synthetic.instrument.1</dcc:value>\n</dcc:identification></dcc:identifications>', b''), 32768)
        observation["calibration_ref"] = c.content_identity(declaration)
        scope["certificate_ref"] = c.bytes_ref(c.unpack(declaration["certificate"], 32768))
    else: scope = None
    assert inspect_link_calibration(declaration, device=device, observation=observation, scope=scope)["applicability"]["status"] == "UNKNOWN"


def test_cli_retains_exact_source_and_rejects_overwrite(tmp_path, capsys):
    source = tmp_path / "source.xml"
    source.write_bytes(CERTIFICATE)
    output = tmp_path / "retained"
    assert main([str(source), "--output", str(output)]) == 0
    assert (output / "certificate.xml").read_bytes() == CERTIFICATE == source.read_bytes()
    assert json.loads((output / "inspection.json").read_text(encoding="utf-8"))["source"]["byte_count"] == len(CERTIFICATE)
    with pytest.raises(FileExistsError):
        main([str(source), "--output", str(output)])


def test_cli_bound_context_retains_exact_context_and_scope(tmp_path, capsys):
    device, declaration, observation, scope = link_case()
    source = tmp_path / "source.xml"
    source.write_bytes(CERTIFICATE)
    context = tmp_path / "context.json"
    raw_context = json.dumps({"device": device, "declaration": declaration, "observation": observation, "scope": scope}, indent=1).encode()
    context.write_bytes(raw_context)
    output = tmp_path / "retained"
    assert main([str(source), "--link-context", str(context), "--output", str(output)]) == 0
    assert (output / "context.json").read_bytes() == raw_context
    report = json.loads((output / "inspection.json").read_text(encoding="utf-8"))
    assert report["applicability"]["status"] == "MATCHES_DECLARED_SCOPE"
    assert report["context_source"]["sha256"] == c.bytes_ref(raw_context)
    # Passing a different file cannot silently inspect the context's older bytes.
    source.write_bytes(CERTIFICATE + b"\n")
    with pytest.raises(ValueError, match="differs from exact"):
        main([str(source), "--link-context", str(context), "--output", str(tmp_path / "wrong")])
    assert not (tmp_path / "wrong").exists()


def test_scope_report_is_detached_and_unknown_calibration_cannot_become_zero():
    device, declaration, observation, scope = link_case()
    report = inspect_link_calibration(declaration, device=device, observation=observation, scope=scope)
    scope["range"]["maximum"] = "99"
    assert report["scope"]["declaration"]["range"]["maximum"] == "2.00"
    assert observation["uncertainty"] is None
    assert report["correction_authorization"]["correction_applied"] is False


@pytest.mark.parametrize("association,expected", [("different_item", "MISMATCH"), ("absent", "UNKNOWN")])
def test_quantity_must_explicitly_reference_the_selected_item(association, expected):
    device, declaration, observation, scope = link_case()
    extra_item = (b'<dcc:item id="other-item"><dcc:identifications><dcc:identification>'
        b'<dcc:issuer>manufacturer</dcc:issuer><dcc:value>different.instrument.2</dcc:value>'
        b'</dcc:identification></dcc:identifications></dcc:item>')
    raw = CERTIFICATE.replace(b'</dcc:items>', extra_item + b'</dcc:items>')
    raw = raw.replace(b'refId="test-item"', b'refId="other-item"' if association == "different_item" else b'')
    declaration["certificate"] = c.packed(raw, 32768)
    observation["calibration_ref"] = c.content_identity(declaration)
    scope["certificate_ref"] = c.bytes_ref(raw)
    report = inspect_link_calibration(declaration, device=device, observation=observation, scope=scope)
    assert report["schema_inspection"]["status"] == "INSPECTION_PROFILE_PASSED"
    assert report["applicability"]["status"] == expected
    relationship = next(item for item in report["applicability"]["checks"] if item["check"] == "quantity_item_association")
    assert relationship["status"] == expected


def test_non_ascii_digits_are_outside_scalar_xml_lexical_profile():
    raw = CERTIFICATE.replace(b'<si:value>1.200</si:value>', '<si:value>\u0661.\u0662\u0660\u0660</si:value>'.encode("utf-8"))
    assert inspect_certificate(raw)["schema_inspection"]["status"] == "INSPECTION_PROFILE_FAILED"

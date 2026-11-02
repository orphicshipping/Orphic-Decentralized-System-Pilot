"""ODRP: a read-only profile of the existing pinned knowledge/evidence host.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from .knowledge_mcp import KnowledgeHost, KnowledgeRefusal, TOOLS, EVIDENCE_TOOLS
from .proto_corpus import ProtoCorpus
from .evidence_corpus import EvidenceCorpus

REFERENCE_NAMES = frozenset(TOOLS) | frozenset(EVIDENCE_TOOLS)

class ReferenceHost(KnowledgeHost):
    """Local host-selected disclosure only; not a remote identity provider."""
    instructions = ('Orphic Decentralized Reference Protocol (ODRP). '
        'This is a pinned reference node; network replication is not established. '
        + KnowledgeHost.instructions)

    def __init__(self, corpus, *, evidence=None):
        # Deliberately do not compose external-work grants into this profile.
        super().__init__(corpus, evidence=evidence)


def open_reference(*, catalog_path, catalog_ref, evidence_path=None, evidence_ref=None):
    """Trusted launcher binding. Never callable by model-selected paths/pins."""
    if (evidence_path is None) != (evidence_ref is None):
        raise ValueError('evidence_binding_required')
    corpus = ProtoCorpus.open(catalog_path, expected_ref=catalog_ref)
    evidence = None if evidence_path is None else EvidenceCorpus.open(
        evidence_path, expected_ref=evidence_ref, catalog=corpus)
    return ReferenceHost(corpus, evidence=evidence)


def reference_tools(host):
    """Discover only currently bound read capabilities, never execution grants."""
    if not isinstance(host, ReferenceHost):
        raise ValueError('reference_host_required')
    return host.descriptions()


def invoke_reference(host, name, arguments):
    if not isinstance(host, ReferenceHost):
        raise ValueError('reference_host_required')
    if name not in REFERENCE_NAMES:
        raise ValueError('unknown_reference_operation')
    response = host.call(name, arguments)
    if response.get('status') != 'ok':
        raise ValueError(response.get('reason', 'request_refused'))
    return response['data']


def profile_manifest():
    """Declarative discovery only. Availability requires a bound reference host."""
    from copy import deepcopy
    return {"profile_id": "odrp.reference.v1", "product": "Orphic Decentralized Reference Protocol",
            "service": "ciw.services.invoke", "host_factory": "ciw.services.open_reference",
            "discovery": "ciw.services.reference_tools", "cli": "ncp reference serve",
            "authority": "host_selected_read_only_snapshot", "authorizes_execution": False,
            "tools": [{"name": name, **deepcopy(spec),
                       "requires_evidence_bundle": name in EVIDENCE_TOOLS}
                      for name, spec in sorted({**TOOLS, **EVIDENCE_TOOLS}.items())]}

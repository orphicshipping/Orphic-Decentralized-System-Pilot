"""Source registry inventory; not an authorization view or deployed tool count.
SPDX-License-Identifier: AGPL-3.0-or-later
"""
from copy import deepcopy

# Schema baseline, not the current installed package's commit identity. Each
# catalog also hashes its actual current schemas and host binding snapshot.
SOURCE_REVISION = 'c044bbdff0050398e4af40396652c5c8c8d9e23c'


def source_registry():
    """Reuse existing server descriptions without renaming or schema aliases.

    Offline inventory only. Never supply this unfiltered inventory as a live
    authorized view. Domain observations use the existing domain_read profile;
    this inventory does not create GIS engines, market feeds or trading tools.
    """
    from .control_gateway import ControlHost
    from .knowledge_mcp import TOOLS, EVIDENCE_TOOLS
    from .logistics_profile import profile_manifest as logistics
    from .recompiler_profile import profile_manifest as recompiler
    from .schematic_profile import profile_manifest as schematic
    from .clocksync_profile import profile_manifest as clocksync
    from .state_read_profile import profile_manifest as state
    from .nise_profile import profile_manifest as nise
    from .offline_intake import profile_manifest as intake
    from .proof_profile import profile_manifest as proof
    from .domain_read_profile import profile_manifest as domain_read
    return deepcopy({
        # descriptions does not access instance state; use the existing method.
        'control': ControlHost.descriptions(None),
        'reference': [{'name': n, **v} for n, v in sorted(TOOLS.items())],
        'evidence': [{'name': n, **v} for n, v in sorted(EVIDENCE_TOOLS.items())],
        'logistics': logistics()['tools'], 'recompiler': recompiler()['tools'],
        'schematic': schematic()['tools'], 'clocksync': clocksync()['tools'],
        'state': state()['tools'], 'nise': nise()['tools'],
        'intake': intake()['tools'], 'proof': proof()['tools'],
        'domain_read': domain_read()['tools'],
    })


def _binding_scope(family, host):
    """Read constructor-bound identities only; never launch/probe a provider.

    Trusted launchers must replace/rebind hosts under the facade policy lock.
    Captures stay immutable. Registry replacement has its own monotonic version.
    No credential, endpoint, memory address or arbitrary host __dict__ is read.
    """
    value = {key: deepcopy(getattr(host, key)) for key in (
        '_bound_principal', '_bound_tenant', 'corpus_ref', 'evidence_ref')
        if hasattr(host, key)}
    if family == 'state':
        value.update(capture=deepcopy(host._metadata), projection=host._projection,
                     allowed_rulings=sorted(host._rulings))
    elif family == 'intake':
        value['capture'] = deepcopy(host._provenance)
    elif family == 'domain_read':
        value.update(capture_digest=host._capture_digest, source=deepcopy(host._source),
                     captured_at=host._captured_at, origin=host._origin,
                     domains=sorted(host._domains))
    elif family == 'proof':
        value['trust'] = host.registry.describe()
        value['native'] = None if host.native is None else {
            'scope': list(host.native.scope), 'binding_id': host.native._verifier._binding_id}
    elif family == 'control':
        value['host_policy'] = host.owner._host_policy
    elif family == 'logistics':
        value.update(roles=sorted(host.roles), allowed_kinds=sorted(host.allowed),
                     synthetic=host.synthetic)
    elif family in ('recompiler', 'schematic', 'clocksync', 'nise'):
        adapter = host._adapter
        value['runtime'] = {key: deepcopy(getattr(adapter, key)) for key in (
            'revision', 'module', 'source_root_relative', '_source_tree',
            '_python_sha256', '_python_version', '_dependencies')}
        value['process_policy'] = (None if adapter.process_policy is None else
                                   adapter.process_policy.identity())
    return value


def principal_facade(host):
    """Identify the existing host authority; unwrapping is for discovery only."""
    from .audited_host import AuditedPrincipalHost
    from .principal_policy import PrincipalFacade
    facade = host._check_identity() if isinstance(host, AuditedPrincipalHost) else host
    if not isinstance(facade, PrincipalFacade):
        raise TypeError('principal_facade_required')
    return facade


def _audit_scope(host):
    from .audited_host import AuditedPrincipalHost
    from hashlib import sha256
    if not isinstance(host, AuditedPrincipalHost):
        return None
    chain = host._audit.chain
    return {'chain_id': chain.chain_id, 'allowed': sorted(chain.allowed),
            'principal_ref': host._audit.principal_ref,
            'journal_binding': sha256(str(chain.path.absolute()).encode('utf-8')).hexdigest()}


def for_principal_facade(facade):
    """Host-only constructor using the existing PrincipalFacade disclosure policy.

    Bind this once in trusted launcher code. Models receive selector operations,
    never the facade, principal, policy, reader, or this constructor. No call()
    method is used. Identity and disclosure share one detached policy snapshot.
    This is local principal-policy integration, not remote identity attestation.
    """
    from .principal_policy import PrincipalPolicy
    from copy import copy
    from .context_selector import AuthorizedCatalog, ContextSelector, serialize
    authority, facade = facade, principal_facade(facade)
    family_for = {row['name']: family for family, rows in source_registry().items() for row in rows}

    def read():
        with facade.policy.lock:
            if principal_facade(authority) is not facade:
                raise ValueError('host_authority_rebound')
            audit_scope = _audit_scope(authority)
            # Discovery-only shallow copy: reuse current filtering against exactly
            # one detached source-policy snapshot without changing shared gates.
            snapshot = facade.policy.snapshot()
            view = copy(facade)
            view.hosts = dict(facade.hosts)
            view.policy = PrincipalPolicy(lambda: snapshot)
            context = view.policy.context(facade.principal)
            scopes = {family: _binding_scope(family, host)
                      for family, host in view.hosts.items() if host is not None}
            rows = view.descriptions()
            after = {family: _binding_scope(family, host)
                     for family, host in view.hosts.items() if host is not None}
            if (serialize(scopes) != serialize(after) or audit_scope != _audit_scope(authority)
                    or principal_facade(authority) is not facade or view.hosts.keys() != facade.hosts.keys()
                    or any(host is not facade.hosts[family] for family, host in view.hosts.items())):
                raise ValueError('host_binding_changed_during_discovery')
            if audit_scope is not None:
                rows = [row for row in rows if row['name'] in audit_scope['allowed']]
            families = {}
            for row in rows:
                if row['name'] not in family_for:
                    raise ValueError('unknown_source_tool')
                families.setdefault(family_for[row['name']], []).append(row)
            # Host context includes bound principal and the complete policy hash.
            from hashlib import sha256
            revision = sha256(serialize([context, scopes, audit_scope])).hexdigest()
            return AuthorizedCatalog(revision, families)

    return ContextSelector(read)

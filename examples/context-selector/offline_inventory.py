"""Installed-package, source inventory example; no network or tool execution.
Run outside the checkout: python /absolute/path/offline_inventory.py
"""
import json
from ciw.context_catalog import SOURCE_REVISION, source_registry
from ciw.context_selector import AuthorizedCatalog, ContextSelector, serialize

# OFFLINE ONLY: all source definitions, not a live user's authorized tool list.
registry = source_registry()
selector = ContextSelector(lambda: AuthorizedCatalog('offline:' + SOURCE_REVISION, registry))
first = selector.list(category='reference', limit=2)
name = first['items'][0]['name']
selected = selector.select([name])
print(json.dumps({
    'source_revision': SOURCE_REVISION,
    'scope': 'offline_source_inventory_not_live_authorization',
    'family_counts': {k: len(v) for k, v in registry.items()},
    'source_schema_count': sum(map(len, registry.values())),
    'all_schema_bytes': len(serialize(sorted([t for rows in registry.values() for t in rows], key=lambda t: t['name']))),
    'selected_schema_bytes': len(serialize(selected['tools'])),
    'selected_envelope_bytes': len(serialize(selected)),
    'selected_name': name,
    'catalog_id': selected['catalog_id'],
    'tokenizer': 'not_measured',
}, indent=2, sort_keys=True))

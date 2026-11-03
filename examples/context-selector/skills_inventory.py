"""Installed-package discovery only: no service calls, admission or receipts."""
import json
from ciw.context_catalog import SOURCE_REVISION, source_registry
from ciw.context_selector import AuthorizedCatalog, ContextSelector
from ciw.toolbox_recipes import builtin_skills

registry = builtin_skills()
selector = ContextSelector(lambda: AuthorizedCatalog('offline:' + SOURCE_REVISION, source_registry()))
selected = registry.load('cross-profile-provenance', '1.0.0', selector)
print(json.dumps({'skills': registry.list(), 'selected_names': [r['name'] for r in selected['tools']],
                  'scope': 'offline_source_inventory', 'execution_performed': False}, sort_keys=True, indent=2))

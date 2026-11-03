"""New discovery narrows to host bindings and never expands science grants."""
from copy import deepcopy
import importlib.abc
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import ciw

from ciw.principal_policy import PrincipalPolicy, PrincipalRefusal
from ciw.services import capability_map, invoke
from test_domain_read_profile import host, policy_document


def test_inspection_manifests_are_non_authorizing_and_same_schema_source():
    from ciw.offline_intake import TOOLS as intake
    from ciw.proof_profile import TOOLS as proof
    from ciw.domain_read_profile import TOOLS as domain
    groups=capability_map()['inspection_profiles']
    listed={row['name']:row for profile in groups for row in profile['tools']}
    assert len(listed)==10
    for name,spec in {**intake,**proof,**domain}.items():
        assert listed[name]['inputSchema']==spec['inputSchema']
        assert listed[name]['annotations']==spec['annotations']
        assert spec['annotations']['readOnlyHint'] is True
    assert all(profile['authorizes_execution'] is False for profile in groups)


def test_unbound_profile_and_mutation_routes_are_not_granted():
    value=policy_document()
    value['principals'][0]['operations'].append({'name':'ncp_proof_describe','constraints':{}})
    facade=PrincipalPolicy(lambda:value).facade('alice',domain_read=host())
    assert {row['name'] for row in facade.descriptions()}==set(row['name'] for row in value['principals'][0]['operations'])-{'ncp_proof_describe'}
    assert facade.call('ncp_proof_describe',{})['status']=='refused'
    for name in ('ncp_run_submit','ncp_coordinate','ncp_domain_run','ncp_workbench_run','ncp_intake_submit'):
        with pytest.raises(PrincipalRefusal):invoke(name,{},host=facade)


def test_discovery_needs_no_optional_crypto_provider_or_native_binary(tmp_path):
    program='import sys\n'+f'sys.path.insert(0,{str(Path(ciw.__file__).resolve().parents[1])!r})\n'+'''
import importlib.abc,json
class RefuseOptional(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in {'cryptography','nise','schematics','cbsr','tbrt'}:
   raise AssertionError('discovery imported optional provider '+fullname)
sys.meta_path.insert(0,RefuseOptional())
from ciw.services import capability_map
value=capability_map()
assert len(value['inspection_profiles'])==3
print(json.dumps({'discovery':'passed','executed':False}))
'''
    result=subprocess.run([sys.executable,'-B','-c',program],cwd=tmp_path,env=dict(os.environ),
                          capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'discovery':'passed','executed':False}

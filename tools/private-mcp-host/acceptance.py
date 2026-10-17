"""Live private API acceptance. Token via hidden stdin; never logged or persisted."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import termios

settings = termios.tcgetattr(sys.stdin)
hidden = settings.copy(); hidden[3] &= ~termios.ECHO
termios.tcsetattr(sys.stdin, termios.TCSANOW, hidden)
print('Ready for acceptance configuration on hidden stdin.', flush=True)
try:
    config = json.loads(sys.stdin.readline())
finally:
    termios.tcsetattr(sys.stdin, termios.TCSANOW, settings)

def api(path, body=None, authenticated=True):
    options = ['url = '+json.dumps(config['url']+path)]
    if authenticated:
        options.append('header = '+json.dumps(config.get('header','Authorization')+': Bearer '+config['token']))
    if body is not None:
        options += ['header = "Content-Type: application/json"', 'data = '+json.dumps(json.dumps(body))]
    result = subprocess.run(['curl','--silent','--show-error','--max-time','120','--config','-',
        '--write-out','\n%{http_code}'], input='\n'.join(options), capture_output=True, text=True, check=True)
    raw, status = result.stdout.rsplit('\n',1)
    value = json.loads(raw)
    if not authenticated:
        assert int(status) in {401,403}, status
        return
    assert int(status)==200, (status,value)
    assert value.get('retention',{}).get('committed'), value
    return json.loads(value['raw_json']) if 'raw_json' in value else value['data']

def call(name, arguments, confirmed=False):
    result = api('/api/calls',{'name':name,'arguments_json':json.dumps(arguments),'confirmed':confirmed})
    assert not result.get('isError'), result
    return result['structuredContent']['data']

receipt = Path(config['receipt'])
if config.get('resume'):
    saved = json.loads(receipt.read_text())
    retained = call('ncp_run_get',{'run_id':saved['run']['run_id']})
    assert retained==saved['run'], 'Retained result changed across host restart'
    saved['restart_recovery_verified']=True
    receipt.write_text(json.dumps(saved,indent=2)+'\n')
    print(json.dumps({'restart_recovery_verified':True,'run_id':retained['run_id']}),flush=True)
else:
    tools=api('/api/tools')
    assert len(tools)==14, len(tools)
    capabilities=call('ncp_capabilities_list',{})['items']
    assert len(capabilities)==9, len(capabilities)
    assert len([item for item in capabilities if item['authorized']])==7
    print('Discovered 14 MCP tools and 9 operations; 7 explicit execution grants.',flush=True)
    template=call('ncp_plan_template',{'baseline':'baseline'})
    plan=call('ncp_plan_create',{'source':'source','baseline':'baseline','graph':template['graph']},True)
    digest={'plan_id':plan['plan_id'],'expected_digest':plan['digest']}
    call('ncp_plan_admit',digest,True)
    run=call('ncp_run_submit',{**digest,'attempt':'private-host-acceptance-20261017'},True)
    assert run['state']=='completed',run
    assert len(run['result']['execution_ids'])==7
    artifact=run['result']['artifacts']['graph-run']['artifact_id']
    outputs=[]
    for i in range(7):
        output=call('ncp_artifact_read',{'artifact':artifact,'selector':['nodes','operation-'+str(i),'result','data']})['data']
        assert output['scope']=='declared_model_only'
        outputs.append(output['operation_id'])
    assert len(set(outputs))==7
    assert call('ncp_run_get',{'run_id':run['run_id']})==run
    receipt.write_text(json.dumps({'url':config['url'],'core_revision':'c47a8621cd97f8d46afe526ed15534111fc6e347',
        'tool_count':len(tools),'operation_count':len(capabilities),'executed_operations':outputs,'run':run,
        'restart_recovery_verified':False},indent=2)+'\n')
    print(json.dumps({'completed':True,'executions':7,'run_id':run['run_id'],'receipt':str(receipt)}),flush=True)

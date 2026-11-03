"""Real loopback MQTT/OPC UA connections with synthetic observations only."""
import base64
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from ciw.live_intake import LiveBinding, LiveIntakeHost, TOOLS
from ciw.process_policy import ProcessPolicy, executable_sha256
from ciw.principal_policy import PrincipalPolicy

PYTHON=Path(os.environ.get('ODSP_LIVE_PYTHON',sys.executable)).absolute()
BROKER=Path(os.environ.get('ODSP_TEST_MOSQUITTO','/usr/sbin/mosquitto'))
LIBRARY=os.environ.get('ODSP_TEST_MOSQUITTO_LIB','')
PAYLOAD=b'{"schema":"odsp.scalar-observation.v1","value":21.5,"unit":"degC","observed_at":"2026-11-03T01:02:03Z"}'


def port():
    with socket.socket() as connection:
        connection.bind(('127.0.0.1',0))
        return connection.getsockname()[1]


def policy():
    return ProcessPolicy(wall_seconds=15,max_input_bytes=65536,max_output_bytes=262144,
        memory_bytes=1024**3,cpu_seconds=10,expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'))


def host(tmp_path,monkeypatch,binding,**overrides):
    monkeypatch.setenv('NCP_RETAIN_DIR',str(tmp_path/'retained'))
    args=dict(principal='alice',tenant='lab',bindings=[binding],python=PYTHON,
        python_sha256=sha256(PYTHON.resolve().read_bytes()).hexdigest(),process_policy=policy(),
        authorize=lambda name,args:'current-epoch')
    args.update(overrides)
    return LiveIntakeHost(**args)


def mqtt_binding(number,**overrides):
    settings={'host':'127.0.0.1','port':number,'topic':'observations/test','qos':1,'tls':False,'ca_file':None}
    settings.update(overrides)
    return LiveBinding('mqtt-source','mqtt.scalar-observation.v1','local_fixture',settings,'2.1.0','test-1')


@contextmanager
def broker(tmp_path,tls=None):
    if not BROKER.is_file():
        pytest.skip('ODSP_TEST_MOSQUITTO must identify a locally installed real broker')
    number=port(); config=tmp_path/'mosquitto.conf'
    import pwd
    lines=[f'listener {number} 127.0.0.1','allow_anonymous true','persistence false',
           'max_packet_size 8192','log_type all',f'user {pwd.getpwuid(os.getuid()).pw_name}']
    if tls:
        lines.extend([f'cafile {tls[0]}',f'certfile {tls[0]}',f'keyfile {tls[1]}'])
    config.write_text('\n'.join(lines)+'\n')
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
    if LIBRARY:env['LD_LIBRARY_PATH']=LIBRARY
    with (tmp_path/'broker.log').open('wb') as log:
        child=subprocess.Popen([str(BROKER),'-c',str(config)],env=env,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                if child.poll() is not None:raise AssertionError('fixture broker failed; see broker.log')
                try:
                    with socket.create_connection(('127.0.0.1',number),timeout=.1):break
                except OSError:time.sleep(.02)
            else:raise AssertionError('fixture broker did not listen')
            yield number
        finally:
            child.terminate()
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:child.kill();child.wait()


def publish(number,payload=PAYLOAD,tls=None):
    import paho.mqtt.client as mqtt
    client=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,protocol=mqtt.MQTTv5)
    if tls:client.tls_set(ca_certs=str(tls))
    client.connect('127.0.0.1',number);client.loop_start()
    try:
        event=client.publish('observations/test',payload,qos=1,retain=True)
        event.wait_for_publish(timeout=3)
        assert event.is_published()
    finally:client.disconnect();client.loop_stop()


def call_read(active,count=1,seconds=2):
    result=active.call('ncp_live_read',{'source_id':'mqtt-source','count':count,'seconds':seconds})
    assert result['status']=='ok',result
    receipt=active.store.native(result['data']['artifact_id'])
    assert receipt['physical_validity_established'] is False
    assert receipt['device_writes_performed'] is False
    assert receipt['origin']=='local_fixture'
    return result,receipt


def test_real_mqtt_retained_bytes_occurrences_and_offline_reopen(tmp_path,monkeypatch):
    with broker(tmp_path) as number:
        publish(number)
        binding=mqtt_binding(number);active=host(tmp_path,monkeypatch,binding)
        first,receipt=call_read(active)
        second,again=call_read(active)
        assert receipt['occurrence']!=again['occurrence']
        row=receipt['records'][0]
        assert base64.b64decode(row['record']['observation']['payload_b64'])==PAYLOAD
        assert row['interpretation']['payload']['value']==21.5
        assert row['record']['observation']['retain'] is True
        assert receipt['connection_read_performed'] is True
    reopened=host(tmp_path,monkeypatch,binding)
    value=reopened.call('ncp_live_receipt',{'artifact_id':first['data']['artifact_id'],'limit':16384})
    assert value['status']=='ok'
    assert reopened.store.native(first['data']['artifact_id'])==receipt
    other=host(tmp_path,monkeypatch,binding,principal='bob')
    assert other.call('ncp_live_receipt',{'artifact_id':first['data']['artifact_id']})['status']=='refused'


def certificate(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'ODSP loopback fixture')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
        .not_valid_after(now+timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
            x509.UniformResourceIdentifier('urn:odsp:test')]),False)
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(key,hashes.SHA256()))
    cert_path,key_path=tmp_path/'certificate.pem',tmp_path/'private.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    key_path.chmod(0o600)
    return cert_path,key_path


def test_real_mqtt_tls_and_certificate_binding(tmp_path,monkeypatch):
    tls=certificate(tmp_path)
    with broker(tmp_path,tls) as number:
        publish(number,tls=tls[0])
        active=host(tmp_path,monkeypatch,mqtt_binding(number,tls=True,ca_file=str(tls[0])))
        call_read(active)
        tls[0].write_bytes(b'changed')
        result=active.call('ncp_live_read',{'source_id':'mqtt-source','count':1,'seconds':1})
        assert result['status']=='refused'


@contextmanager
def opc_server(tmp_path,bad=False,tls=None):
    number=port();ready=tmp_path/'opc.ready'
    program='''import asyncio,sys
from asyncua import Server,ua
from pathlib import Path
async def main():
 server=Server();await server.init();server.set_endpoint(sys.argv[1])
 if len(sys.argv)>4:
  await server.set_application_uri('urn:odsp:test')
  await server.load_certificate(sys.argv[4]);await server.load_private_key(sys.argv[5])
  server.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
 else:server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
 index=await server.register_namespace('urn:odsp:synthetic-fixture')
 node=await server.nodes.objects.add_variable(ua.NodeId('Temperature',index),'Temperature',21.5)
 if sys.argv[3]=='bad':
  await node.write_value(ua.DataValue(ua.Variant(21.5,ua.VariantType.Double),ua.StatusCode(ua.StatusCodes.BadSensorFailure)))
 async with server:
  Path(sys.argv[2]).write_text(node.nodeid.to_string())
  await asyncio.sleep(30)
asyncio.run(main())
'''
    endpoint=f'opc.tcp://127.0.0.1:{number}/odsp/'
    with (tmp_path/'opc.log').open('wb') as log:
        command=[str(PYTHON),'-I','-c',program,endpoint,str(ready),'bad' if bad else 'good']
        if tls:command.extend(map(str,tls))
        child=subprocess.Popen(command,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+10
            while not ready.exists() and time.monotonic()<deadline:
                if child.poll() is not None:raise AssertionError('fixture OPC UA failed; see opc.log')
                time.sleep(.02)
            assert ready.exists()
            yield endpoint,ready.read_text()
        finally:
            child.terminate()
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:child.kill();child.wait()


@pytest.mark.parametrize('bad',[False,True])
def test_real_opcua_read_and_quality_is_not_validation(tmp_path,monkeypatch,bad):
    with opc_server(tmp_path,bad) as (endpoint,node_id):
        binding=LiveBinding('opc-source','opcua.scalar.v1','local_fixture',{
            'endpoint':endpoint,'node_id':node_id,'unit':'degC','datatype':'Double',
            'application_uri':'urn:odsp:test','secure':False,'server_certificate':None,'interval_seconds':0},'2.1.0','test-1')
        active=host(tmp_path,monkeypatch,binding)
        result=active.call('ncp_live_read',{'source_id':'opc-source','count':2,'seconds':3})
        assert result['status']=='ok',result
        receipt=active.store.native(result['data']['artifact_id'])
        assert len(receipt['records'])==2
        for row in receipt['records']:
            assert row['interpretation']['reported_quality']==('bad' if bad else 'good')
            assert row['interpretation']['reported_quality_verified'] is False
            assert row['interpretation']['value']==(None if bad else 21.5)


def test_actual_opcua_signed_encrypted_channel(tmp_path,monkeypatch):
    tls=certificate(tmp_path)
    with opc_server(tmp_path,tls=tls) as (endpoint,node_id):
        binding=LiveBinding('secure-opc','opcua.scalar.v1','local_fixture',{
            'endpoint':endpoint,'node_id':node_id,'unit':'degC','datatype':'Double',
            'application_uri':'urn:odsp:test','secure':True,'server_certificate':str(tls[0]),'interval_seconds':0},'2.1.0','test-1')
        active=host(tmp_path,monkeypatch,binding,credentials=lambda s:{'certificate':str(tls[0]),'private_key':str(tls[1])})
        result=active.call('ncp_live_read',{'source_id':'secure-opc','count':1,'seconds':3})
        assert result['status']=='ok',result
        assert active.store.native(result['data']['artifact_id'])['records'][0]['interpretation']['value']==21.5


def test_actual_principal_mcp_stdio_connection_and_policy_refusal(tmp_path,monkeypatch):
    import asyncio
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    from ciw import __file__ as module
    monkeypatch.setenv('NCP_RETAIN_DIR',str(tmp_path/'retained'))
    with broker(tmp_path) as number:
        publish(number)
        path=Path(module).resolve().parent.parent
        program=f'''import sys,asyncio
sys.path.insert(0,{str(path)!r})
from pathlib import Path
from hashlib import sha256
from ciw.live_intake import LiveBinding,LiveIntakeHost,TOOLS
from ciw.process_policy import ProcessPolicy,executable_sha256
from ciw.principal_policy import PrincipalPolicy
from ciw.mcp_server import serve_sdk
settings={dict(mqtt_binding(number).settings)!r}
binding=LiveBinding('mqtt-source','mqtt.scalar-observation.v1','local_fixture',settings,'2.1.0','test-1')
p=ProcessPolicy(wall_seconds=15,max_input_bytes=65536,max_output_bytes=262144,memory_bytes=1024**3,cpu_seconds=10,expected_prlimit_sha256=executable_sha256('/usr/bin/prlimit'))
active=LiveIntakeHost(principal='alice',tenant='lab',bindings=[binding],python=Path(sys.executable),python_sha256=sha256(Path(sys.executable).resolve().read_bytes()).hexdigest(),process_policy=p,authorize=lambda n,a:'epoch')
grant={{'principal':'alice','enabled':True,'operations':[{{'name':n,'constraints':{{}}}} for n in TOOLS],'scientific_operations':[],'resources':[],'reference_scopes':[],'handoff_recipients':[]}}
policy=PrincipalPolicy(lambda:{{'schema':'ciw.principal-policy.v1','revision':'one','principals':[grant]}})
serve_sdk(policy.facade('alice',live=active))
'''
        async def journey():
            params=StdioServerParameters(command=str(PYTHON),args=['-B','-I','-c',program],
                env={'PATH':'/usr/bin:/bin','NCP_RETAIN_DIR':str(tmp_path/'retained'),'PYTHONDONTWRITEBYTECODE':'1'})
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    listed=await session.list_tools()
                    assert {row.name for row in listed.tools}==set(TOOLS)
                    result=await session.call_tool('ncp_live_read',{'source_id':'mqtt-source','count':1,'seconds':2})
                    assert not result.is_error,result
                    artifact=result.structured_content['data']['artifact_id']
                    stored=await session.call_tool('ncp_live_receipt',{'artifact_id':artifact})
                    assert not stored.is_error
                    denied=await session.call_tool('ncp_live_read',{'source_id':'mqtt-source','count':1,'seconds':2,'principal':'bob'})
                    assert denied.is_error
        asyncio.run(journey())


def test_current_policy_denies_before_connection_and_changed_scope_blocks_receipt(tmp_path,monkeypatch):
    binding=mqtt_binding(port());current=['one'];connections=[]
    active=host(tmp_path,monkeypatch,binding,authorize=lambda n,a:current[0],credentials=lambda s:connections.append(s) or {})
    grant={'principal':'alice','enabled':True,'operations':[{'name':n,'constraints':{}} for n in TOOLS],
           'scientific_operations':[],'resources':[],'reference_scopes':[],'handoff_recipients':[]}
    p=PrincipalPolicy(lambda:{'schema':'ciw.principal-policy.v1','revision':'one','principals':[grant]})
    facade=p.facade('alice',live=active)
    assert len(facade.descriptions())==3
    grant['operations']=[]
    assert facade.call('ncp_live_read',{'source_id':'mqtt-source','count':1,'seconds':1})['status']=='refused'
    assert not connections
    current[0]=''
    assert active.call('ncp_live_sources',{})['status']=='refused'


@pytest.mark.parametrize('change',[{'count':True},{'seconds':float('nan')},{'count':33},{'endpoint':'https://unbound'},{'seconds':0}])
def test_invalid_arguments_never_connect(tmp_path,monkeypatch,change):
    calls=[];active=host(tmp_path,monkeypatch,mqtt_binding(port()),credentials=lambda s:calls.append(s) or {})
    result=active.call('ncp_live_read',dict(source_id='mqtt-source',count=1,seconds=1,**{})|change)
    assert result['status']=='refused' and not calls


def test_live_security_and_fixture_endpoint_requirements():
    binding=mqtt_binding(port())
    with pytest.raises(ValueError):LiveBinding('s',binding.protocol,'live',dict(binding.settings),'2.1.0','e')
    with pytest.raises(ValueError):mqtt_binding(port(),host='192.0.2.1')
    with pytest.raises(ValueError):mqtt_binding(port(),topic='observations/#')


def test_sdk_drift_is_rejected_before_starting_a_child(tmp_path,monkeypatch):
    import ciw.live_intake as module
    root=tmp_path/'site-packages';root.mkdir();(root/'qualified.py').write_text('value=1\n')
    active=host(tmp_path,monkeypatch,mqtt_binding(port()),import_roots=(root,))
    (root/'injected.pth').write_text('import arbitrary_startup\n')
    children=[]
    monkeypatch.setattr(module,'_bounded_process',lambda *args,**kw:children.append(args))
    result=active.call('ncp_live_read',{'source_id':'mqtt-source','count':1,'seconds':1})
    assert result['status']=='refused' and not children


def test_site_startup_is_disabled_and_dependency_snapshot_retained(tmp_path,monkeypatch):
    extra=tmp_path/'site-packages';extra.mkdir();marker=tmp_path/'startup-ran'
    (extra/'startup.pth').write_text(f'import pathlib;pathlib.Path({str(marker)!r}).touch()\n')
    real=PYTHON.parent.parent/'lib'/f'python{sys.version_info.major}.{sys.version_info.minor}'/'site-packages'
    with broker(tmp_path) as number:
        publish(number)
        active=host(tmp_path,monkeypatch,mqtt_binding(number),import_roots=(extra,real))
        receipt=call_read(active)[1]
        assert not marker.exists()
        assert len(receipt['runtime']['import_roots_sha256'])==2
        assert receipt['runtime']['import_policy']=='explicit-roots-no-site-startup-fresh-bytecode-v1'


def test_closed_connection_does_not_fabricate_receipt_and_redacts_errors(tmp_path,monkeypatch):
    active=host(tmp_path,monkeypatch,mqtt_binding(port()),credentials=lambda s:{'username':'secret-identity','password':'secret-password'})
    result=active.call('ncp_live_read',{'source_id':'mqtt-source','count':1,'seconds':.1})
    assert result['status']=='refused'
    assert 'secret' not in json.dumps(result)
    assert result['error']['code']=='outcome_unknown'

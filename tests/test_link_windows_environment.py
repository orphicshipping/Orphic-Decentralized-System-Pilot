"""Windows-only regression target; not part of the Linux no-skips qualification gate."""
import json
import sys
import pytest
from test_link_qualification_artifacts import SOURCE, load


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows SSL runtime needs its native SystemRoot')
def test_windows_minimal_environment_initializes_verified_tls(tmp_path):
    import subprocess
    tools=load(SOURCE/'provision_link_environment.py')
    environment=tools._environment(tmp_path)
    probe=('import json,ssl; context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); '
           'print(json.dumps({"certificate_verification":context.verify_mode==ssl.CERT_REQUIRED,'
           '"hostname_verification":context.check_hostname}))')
    result=subprocess.run([sys.executable,'-I','-c',probe],env=environment,
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)=={'certificate_verification':True,'hostname_verification':True}


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows directory discovery is platform specific')
def test_windows_environment_uses_native_directory_without_inheriting_caller_settings(tmp_path, monkeypatch):
    import ctypes
    native=ctypes.WinDLL('kernel32.dll',use_last_error=True,winmode=0x00000800)
    directory=ctypes.create_unicode_buffer(32768)
    size=native.GetWindowsDirectoryW(directory,len(directory))
    assert 0<size<len(directory)
    inherited={'SystemRoot':'C:/untrusted-root','WINDIR':'C:/untrusted-windir',
               'PIP_TARGET':'C:/untrusted-target','PIP_CONFIG_FILE':'C:/untrusted-pip.ini',
               'PYTHONPATH':'C:/untrusted-python','HTTPS_PROXY':'https://invalid.example',
               'SSL_CERT_FILE':'C:/untrusted-cert.pem','AWS_SECRET_ACCESS_KEY':'synthetic-must-not-inherit'}
    for name,value in inherited.items():monkeypatch.setenv(name,value)
    tools=load(SOURCE/'provision_link_environment.py')
    environment=tools._environment(tmp_path)
    assert environment['SystemRoot']==directory.value
    assert all(environment.get(name)!=value for name,value in inherited.items())
    assert not set(inherited).difference({'SystemRoot','PIP_CONFIG_FILE'}).intersection(environment)

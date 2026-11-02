"""Synthetic OLM protocol fixture only; no live credentials/domain execution."""
import json, shutil
from ciw.logistics_profile import LogisticsHost, canonical_digest, raw

class Fixture:
    def __init__(self, error=False, mismatch=False, payload=None):
        self.error,self.mismatch,self.payload=error,mismatch,payload
        self.calls=[]
    def __call__(self,body):
        req=json.loads(body); self.calls.append(req)
        data=self.payload or {'capabilities':{'contract':'capability.catalog.v1','executionAuthorized':False,'definitions':[{'id':'a','version':'1'},{'id':'a','version':'2'}],'implementations':[{'capabilityId':'a','capabilityVersion':'1'}]}}
        body=raw({'jsonrpc':'2.0','id':req['id'],'result':{'content':[],'structuredContent':data,'isError':self.error}})
        digest='0'*64 if self.mismatch else canonical_digest(body,shutil.which('node'))
        return 200,{'x-response-canonical-digest':digest,'mcp-protocol-version':'2025-06-18','x-source-read-receipt-hash':'a'*64,'x-read-receipt-hash':'b'*64,'x-result-digest':'c'*64},body

def host(transport=None,principal='test-owner',**kwargs):
    return LogisticsHost(transport=transport or Fixture(),principal=principal,tenant='synthetic-tenant',roles=['pilot:agent-read','pilot:compute-read'],allowed_kinds=['catalog','compute'],node=shutil.which('node'),synthetic=True,**kwargs)

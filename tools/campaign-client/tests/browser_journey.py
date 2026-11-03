"""Real Chromium -> host-supplied test bridge -> official MCP -> principal-bound fixture.

The bridge is test instrumentation, not a deployed HTTP service or authentication.
"""
import argparse
import functools
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading

from playwright.sync_api import expect, sync_playwright


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--build',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mcp-python',required=True)
    parser.add_argument('--browser',default='/usr/bin/chromium')
    args=parser.parse_args(); args.output.mkdir(exist_ok=False)
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self,*_): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(args.build)))
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    exchanges=[]; errors=[]; checks=[]
    def bridge(_source, request):
        result=subprocess.run([args.mcp_python,'-B',str(Path(__file__).with_name('mcp_bridge.py'))],
            input=request.encode(),capture_output=True,timeout=20)
        if result.returncode:
            (args.output/'bridge-error.log').write_bytes(result.stderr)
            raise RuntimeError('test MCP bridge failed')
        assert len(result.stdout)<=262144
        response=result.stdout.decode()
        exchanges.append({'request':json.loads(request),'response':json.loads(response)})
        return response
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox'])
            try:
                page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.expose_binding('testMcpBridge',bridge)
                page.goto('http://127.0.0.1:'+str(server.server_port))
                assert 'Disconnected' in page.locator('[role=status]').inner_text()
                page.evaluate("""async () => {
                  const {ReadClient}=await import('/contract.js');
                  const {mountReceiptViewer}=await import('/viewer.js');
                  window.mount=() => {
                    const client=new ReadClient(async function*(raw,signal){
                      if(signal.aborted)throw Error('aborted');
                      const response=await window.testMcpBridge(new TextDecoder().decode(raw));
                      const parsed=JSON.parse(response);window.lastReply=parsed;
                      if(window.injectStale)parsed.id='stale-response';
                      const bytes=new TextEncoder().encode(window.injectStale?JSON.stringify(parsed):response);
                      for(let offset=0;offset<bytes.length;offset+=4096)yield bytes.subarray(offset,offset+4096);
                    });
                    window.viewer=mountReceiptViewer(document.querySelector('#receipt-viewer'),{
                      client,request:{name:'ncp_intake_record',arguments:{record_id:'opc-1'}},
                      sourceLabel:'Synthetic local capture / official MCP / host-selected principal'});
                  }; window.mount();
                }""")
                button=page.get_by_role('button',name='Read selected evidence')
                button.focus(); assert button.evaluate('(e)=>document.activeElement===e')
                page.keyboard.press('Enter')
                expect(page.locator('.os-status')).to_have_text('RETURNED',timeout=20000)
                assert page.locator('img').count()==0 and page.evaluate('window.injected===undefined')
                assert '<img src=x' in page.get_by_label('Complete backend response').inner_text()
                page.get_by_text('Complete MCP envelope, including text copy',exact=True).click()
                assert 'structuredContent' in page.get_by_label('Complete MCP envelope').inner_text()
                page.get_by_text('Complete MCP envelope, including text copy',exact=True).click()
                assert 'digest does not' in page.locator('p').all_text_contents()[-2].lower() or 'A digest does not' in page.locator('main').inner_text()
                checks.append('keyboard_read_real_mcp_escaped_capture_no_markup_execution')
                for width in (320,375,768,1440):
                    page.set_viewport_size({'width':width,'height':1000})
                    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth'),width
                    page.screenshot(path=str(args.output/f'width-{width}.png'),full_page=True)
                checks.append('320_375_768_1440_no_document_overflow')
                page.set_viewport_size({'width':768,'height':1000})
                page.evaluate("document.documentElement.style.zoom='2'")
                assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
                page.screenshot(path=str(args.output/'css-200-percent.png'),full_page=True)
                page.evaluate("document.documentElement.style.zoom='1'")
                checks.append('CSS_200_percent_layout_only_not_native_browser_zoom')
                page.evaluate("""() => window.viewer.setRequest({name:'ncp_intake_verify',arguments:{
                  record_id:'opc-1',receipt_json:JSON.stringify(window.lastReply.result.structuredContent.data)}})""")
                assert page.locator('.os-status').inner_text()=='STALE'
                button.click();expect(page.locator('.os-status')).to_have_text('RETURNED',timeout=20000)
                assert 'Backend matched the host capture' in page.locator('main').inner_text()
                checks.append('actual_backend_full_receipt_verification_scope_display')
                page.evaluate("window.viewer.setRequest({name:'ncp_intake_records',arguments:{limit:1}})")
                button.click();expect(page.locator('.os-status')).to_have_text('PARTIAL',timeout=20000)
                page.evaluate("window.viewer.setRequest({name:'ncp_intake_records',arguments:{offset:3}})")
                button.click();expect(page.locator('.os-status')).to_have_text('EMPTY',timeout=20000)
                page.evaluate("window.viewer.setRequest({name:'ncp_intake_record',arguments:{record_id:'mqtt-1'}})")
                button.click();expect(page.locator('.os-status')).to_have_text('DENIED',timeout=20000)
                checks.append('actual_partial_empty_and_principal_denied_states')
                page.evaluate("window.viewer.setRequest({name:'ncp_intake_record',arguments:{record_id:'opc-1'}});window.injectStale=true")
                before=len(exchanges);button.click()
                expect(page.locator('.os-status')).to_have_text('UNKNOWN',timeout=20000)
                assert button.is_disabled() and len(exchanges)==before+1
                assert 'prior response' in page.locator('main').inner_text()
                checks.append('stale_response_identity_fences_client_retains_prior_evidence')
                page.screenshot(path=str(args.output/'unknown.png'),full_page=True)
                page.evaluate('window.viewer.destroy();window.injectStale=false;window.mount()')
                page.get_by_role('button',name='Read selected evidence').click()
                expect(page.locator('.os-status')).to_have_text('RETURNED',timeout=20000)
                checks.append('explicit_host_reconnect_new_client_new_request_identity')
                assert len({item['request']['id'] for item in exchanges})==len(exchanges)
                assert not errors,errors
                report={'schema':'odsp.local-read-client-browser.v1','browser':browser.version,
                    'checks':checks,'browser_errors':errors,'exchanges':len(exchanges),
                    'network':'loopback_static_test_assets_only; MCP stdio bridge',
                    'identity':'synthetic host-bound principal, not deployed authentication',
                    'native_browser_zoom':'not_qualified; CSS zoom exercised','deployment':'not_performed'}
                (args.output/'exchanges.json').write_text(json.dumps(exchanges,indent=2)+'\n')
                report['exchanges_sha256']=hashlib.sha256((args.output/'exchanges.json').read_bytes()).hexdigest()
                (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
                print(json.dumps(report,indent=2))
            except BaseException:
                (args.output/'failure-exchanges.json').write_text(json.dumps(exchanges,indent=2)+'\n')
                (args.output/'failure-errors.json').write_text(json.dumps(errors,indent=2)+'\n')
                if not page.is_closed():
                    page.screenshot(path=str(args.output/'failure.png'),full_page=True)
                    (args.output/'failure-state.txt').write_text(page.locator('body').inner_text())
                raise
            finally: browser.close()
    finally: server.shutdown();server.server_close();worker.join(timeout=5)


if __name__=='__main__': main()

"""Real Chromium browser QA for the read-only measurement evidence visualizer.

Uses the installed Node Playwright package; no browser mocks or Python
Playwright dependency. Supply a Chromium-family executable when no managed
Playwright browser is installed. Fresh HTML can be rendered with --evidence-dir.
All generated artifacts are confined to the explicit create-only output folder.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

BROWSER_SCRIPT = r"""
const fs=require('fs'),path=require('path');
const {pathToFileURL}=require('url');
const [root,modulePath,chromium]=process.argv.slice(2);
const {chromium:engine}=require(modulePath);
const config=JSON.parse(fs.readFileSync(path.join(root,'browser-config.json'),'utf8'));
const data=JSON.parse(fs.readFileSync(path.join(root,'expected-payload.json'),'utf8'));
const checks=[],errors=[],external=[],findings=[];
function check(name,condition,detail=null){checks.push({name,passed:!!condition,...(detail===null?{}:{detail})});if(!condition)findings.push(name);}
function same(a,b){return JSON.stringify(a)===JSON.stringify(b);}
function observe(page){page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});page.on('request',r=>{if(/^https?:/.test(r.url()))external.push(r.url());});}
function expected(id){return data.datasets.find(d=>d.id===id);}
async function snapshot(page){return page.evaluate(()=>window.measurementViewSnapshot());}
async function open(page,file){await page.goto(pathToFileURL(path.join(root,file)).href);await page.waitForFunction(()=>typeof window.measurementViewSnapshot==='function');}
async function activate(page,id,count){await page.locator('[data-dataset="'+id+'"]').click();check(id+' dataset selected',(await snapshot(page)).dataset_id===id);check(id+' exact chart point count',await page.locator('.plot-point').count()===count);check(id+' exact table row count',await page.locator('#measurement-rows tr').count()===count);}
async function selectedParity(page,id,datasetId){const actual=await snapshot(page);check('Selection snapshot identifies '+id,actual.selected_id===id);check('Chart highlights '+id,await page.locator('.plot-point[data-record-id="'+id+'"]').getAttribute('aria-pressed')==='true');check('Table highlights '+id,await page.locator('#measurement-rows tr[data-record-id="'+id+'"]').getAttribute('class')==='selected-row');check('Inspector exact original JSON '+id,same(JSON.parse(await page.locator('#raw-record').textContent()),expected(datasetId).rows.find(r=>r.id===id).raw));}
async function download(page,button,name){const [item]=await Promise.all([page.waitForEvent('download'),page.locator(button).click()]);const dest=path.join(root,name);await item.saveAs(dest);return JSON.parse(fs.readFileSync(dest,'utf8'));}
(async()=>{
 let browser,page;
 try{
  browser=await engine.launch({headless:true,...(chromium?{executablePath:chromium}:{})});
  const version=browser.version();
  page=await browser.newPage({viewport:{width:1500,height:1080},acceptDownloads:true});observe(page);
  await open(page,'measurement.html');
  check('Actual file URL loaded',page.url().startsWith('file:'));
  check('Default dataset is thermal',(await snapshot(page)).dataset_id==='thermal');
  check('Default thermal six chart points',await page.locator('.plot-point').count()===6);
  check('Default thermal three flagged chart points',await page.locator('.point-core.tone-source_review').count()===3);
  check('Thermal source coverage caveat visible',(await page.locator('#chart-subtitle').innerText()).includes('coverage is unresolved'));
  check('Thermal source controls separate',(await page.locator('#pilot-controls').textContent()).includes('Inspect 2 control records'));
  check('Summary36 comparison rows',(await page.locator('#comparison-count').innerText())==='36');
  check('Summary180 DSC rows',(await page.locator('#dsc-count').innerText())==='180');
  check('Summary4 datasets',(await page.locator('#dataset-count').innerText())==='4');
  check('Summary21 review flags',(await page.locator('#review-count').innerText())==='21');
  await page.screenshot({path:path.join(root,'desktop-thermal.png'),fullPage:true});
  await page.locator('#issues-only').check();
  check('Thermal source-only filter gives three rows',await page.locator('.plot-point').count()===3);
  await page.locator('#reset-filters').click();
  const thermal=expected('thermal').rows;
  await page.locator('.plot-point[data-record-id="'+thermal[1].id+'"] .point-core').click();
  await selectedParity(page,thermal[1].id,'thermal');
  check('Flagged record issue visible',await page.locator('#record-issue').isVisible());
  await page.locator('.plot-point[data-record-id="'+thermal[2].id+'"]').focus();
  await page.keyboard.press('Enter');await selectedParity(page,thermal[2].id,'thermal');
  await page.setViewportSize({width:1420,height:1080});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  check('Plot keyboard focus survives resize',await page.evaluate(()=>document.activeElement.getAttribute('data-record-id'))===thermal[2].id);
  check('Resize preserves selected source record',(await snapshot(page)).selected_id===thermal[2].id);
  await page.keyboard.press('Tab');
  check('Keyboard navigation continues to next plot record after resize',await page.evaluate(()=>document.activeElement.getAttribute('data-record-id'))===thermal[3].id);
  await page.keyboard.press('Enter');await selectedParity(page,thermal[3].id,'thermal');
  await page.setViewportSize({width:1500,height:1080});
  await page.locator('#measurement-rows tr[data-record-id="'+thermal[3].id+'"] button').focus();
  await page.keyboard.press('Space');await selectedParity(page,thermal[3].id,'thermal');
  const selected=await download(page,'#export-selected','selected-thermal.json');
  check('Selected export carries exact original row',same(selected.records,[thermal[3]]));
  check('Thermal export declares kind and unit',selected.kind==='comparison'&&selected.unit==='uK');
  check('Selected export retains qualification limits',selected.view_only===true&&selected.local_instrument_qualification==='not_established'&&selected.release_status==='unchanged');
  const visibleThermal=await download(page,'#export-visible','visible-thermal.json');
  check('Visible thermal export exact rows',same(visibleThermal.records,thermal));
  await activate(page,'density',6);
  check('Density one published rule not met',await page.locator('#measurement-rows .tone-published_rule_not_satisfied').count()===1);
  check('Density three published-rule satisfied rows',await page.locator('#measurement-rows .tone-published_rule_satisfied').count()===3);
  check('Density two source reviews',await page.locator('#measurement-rows .tone-source_review').count()===2);
  check('Density failure wording preserved',(await page.locator('#measurement-rows').innerText()).includes('Published rule not met'));
  await activate(page,'chemistry',24);
  check('Chemistry no published-rule success badges',await page.locator('#measurement-rows .tone-published_rule_satisfied').count()===0);
  check('Chemistry no published-rule failure badges',await page.locator('#measurement-rows .tone-published_rule_not_satisfied').count()===0);
  check('Chemistry sixteen source flags',await page.locator('#measurement-rows .tone-source_review').count()===16);
  await page.locator('#search').fill('ethanol');
  check('Chemistry material search ethanol retains24',await page.locator('.plot-point').count()===24);
  await page.locator('#reset-filters').click();
  await page.locator('.table-scroll').evaluate(el=>el.scrollTop=150);
  check('Chemistry descriptive covariance caveat',(await page.locator('#chart-subtitle').innerText()).includes('Descriptive intervals')&&(await page.locator('#chart-subtitle').innerText()).includes('zero covariance'));
  await activate(page,'dsc',180);
  check('Dataset switch resets table scroll',await page.locator('.table-scroll').evaluate(el=>el.scrollTop===0));
  check('DSC default points only',await page.locator('.plot-point .interval').count()===0);
  check('DSC explicit sample topology',(await page.locator('#dataset-summary').innerText()).includes('15 samples')&&(await page.locator('#dataset-summary').innerText()).includes('1 material lot'));
  check('DSC label has no UTF8 mojibake',!(await page.locator('#measurement-rows').innerText()).match(/[ÃÂâ]/));
  check('DSC no connecting data paths',await page.locator('#measurement-plot path').count()===0);
  await page.screenshot({path:path.join(root,'desktop-dsc.png'),fullPage:true});
  await page.locator('#group-filter').selectOption('A1');
  check('DSC sampleA1 filters to12',await page.locator('.plot-point').count()===12);
  await page.locator('#rate-filter').selectOption('3');
  check('DSC sample-rate filters to3',await page.locator('.plot-point').count()===3);
  await page.locator('#ingot-filter').selectOption('A');
  check('DSC compatible ingot retains3',await page.locator('.plot-point').count()===3);
  const expectedFiltered=expected('dsc').rows.filter(r=>r.sample==='A1'&&r.nominal_rate==='3'&&r.ingot==='A');
  const filtered=await download(page,'#export-visible','filtered-dsc.json');
  check('Filtered DSC export exactly matches retained rows',same(filtered.records,expectedFiltered));
  check('DSC export declares kind and unit',filtered.kind==='dsc'&&filtered.unit==='J/g');
  check('DSC export preserves source decimal literal',filtered.records[0].raw.source.mass_mg==='8.118'&&typeof filtered.records[0].raw.source.enthalpy_J_per_g==='string');
  await page.locator('#show-systematic').check();
  check('Optional DSC bars only selected three rows',await page.locator('.plot-point .interval').count()===9);
  check('Optional bar label states systematic not total',(await page.locator('#chart-subtitle').innerText()).includes('systematic standard u only')&&(await page.locator('#chart-subtitle').innerText()).includes('not total'));
  await page.locator('#ingot-filter').selectOption('B');
  check('Incompatible sample/ingot filters empty',await page.locator('.plot-point').count()===0);
  check('Empty-state message visible',await page.locator('#plot-empty').isVisible());
  check('Empty selected and visible exports disabled',await page.locator('#export-selected').isDisabled()&&await page.locator('#export-visible').isDisabled());
  check('Empty inspector has no stale raw row',(await page.locator('#raw-record').textContent())==='');
  await page.locator('#reset-filters').click();
  check('Reset restores180 records',await page.locator('.plot-point').count()===180);
  check('Reset disables systematic bars',!(await page.locator('#show-systematic').isChecked())&&await page.locator('.interval').count()===0);
  await page.locator('#ingot-filter').selectOption('B');
  check('DSC ingotB36 records',await page.locator('.plot-point').count()===36);
  await page.locator('#reset-filters').click();await page.locator('#rate-filter').selectOption('5');
  check('DSC nominal5 rate45 records',await page.locator('.plot-point').count()===45);
  await page.locator('#search').fill('no-such-measurement-qa');
  check('Search empty state safe',await page.locator('#plot-empty').isVisible());
  await page.locator('#reset-filters').click();
  const dscId=expected('dsc').rows[0].id;
  await page.locator('#measurement-rows tr[data-record-id="'+dscId+'"] button').click();
  await selectedParity(page,dscId,'dsc');
  const dscSelected=await download(page,'#export-selected','selected-dsc.json');
  check('DSC selected export exact original and normalized records',same(dscSelected.records,[expected('dsc').rows[0]]));
  check('Dataset filters did not create qualification',(await snapshot(page)).local_instrument_qualification==='not_established'&&(await snapshot(page)).release_status==='unchanged');
  await page.setViewportSize({width:390,height:844});
  for(const [id,count] of [['thermal',6],['chemistry',24],['dsc',180],['density',6]]){
    await activate(page,id,count);
    const widths=await page.evaluate(()=>({document:document.documentElement.scrollWidth,viewport:window.innerWidth}));
    check('390px '+id+' no document horizontal overflow',widths.document<=widths.viewport,widths);
    const legibility=await page.locator('.chart-axis').first().evaluate(el=>parseFloat(getComputedStyle(el).fontSize)*el.getScreenCTM().a);
    check('390px '+id+' chart label at least approximately10 rendered px',legibility>=9.5,{rendered_font_px:legibility});
    check('390px '+id+' source links and inspector remain present',await page.locator('#record-source').count()===1&&await page.locator('#inspector-title').count()===1);
  }
  await activate(page,'thermal',6);await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:path.join(root,'mobile-thermal.png'),fullPage:true});
  await activate(page,'dsc',180);await page.locator('#dataset-title').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(root,'mobile-dsc.png'),fullPage:true});
  const hostile=await browser.newPage({viewport:{width:1200,height:900}});observe(hostile);await open(hostile,'hostile-label.html');
  check('Hostile label not executed',await hostile.evaluate(()=>window.MEASUREMENT_PWNED===undefined));
  check('Hostile label shown literally',(await hostile.locator('#inspector-title').textContent())===config.hostileLabel);
  check('No injected hostile elements',await hostile.locator('#injected-hostile-element').count()===0);
  check('Unsafe dataset source URL disabled',await hostile.locator('#source-link').getAttribute('href')===null);
  check('Unsafe row source URL disabled',await hostile.locator('#record-source').getAttribute('href')===null);
  await hostile.close();
  check('No browser JavaScript or console errors',errors.length===0,errors);
  check('No external network requests',external.length===0,external);
  const htmlHash=require('crypto').createHash('sha256').update(fs.readFileSync(path.join(root,'measurement.html'))).digest('hex');
  fs.writeFileSync(path.join(root,'browser-results.json'),JSON.stringify({status:findings.length?'failed':'passed',method:'Actual headless Chromium via Node Playwright; file navigation',browser_version:version,html_sha256:htmlHash,checks,errors,external_requests:external,findings},null,2)+'\n');
  console.log(JSON.stringify({status:findings.length?'failed':'passed',checks:checks.length,findings,browser_version:version}));
  process.exitCode=findings.length?1:0;
 }catch(e){
  fs.writeFileSync(path.join(root,'browser-results.json'),JSON.stringify({status:'interrupted',error:String(e),stack:e.stack,checks,errors,external_requests:external,findings},null,2)+'\n');
  console.error(e);process.exitCode=1;
 }finally{if(browser)await browser.close();}
})();
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--html", type=Path)
    origin.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--node", default=shutil.which("node"))
    parser.add_argument("--playwright-module", default="playwright")
    parser.add_argument("--chromium", default="")
    args = parser.parse_args()
    if not args.node:
        parser.error("Supply --node or put Node.js on PATH")
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    if args.evidence_dir:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from ciw.measurement_visualizer import build_payload, render_html
        payload = build_payload(args.evidence_dir)
        html = render_html(payload)
    else:
        html = args.html.read_bytes()
        match = re.search(rb'<div id="payload"[^>]*>([^<]+)</div>', html)
        if not match:
            raise ValueError("No measurement payload found")
        import base64
        payload = json.loads(base64.b64decode(match[1]))
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from ciw.measurement_visualizer import render_html
    (root / "measurement.html").write_bytes(html)
    (root / "expected-payload.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    hostile_label = '</script><script>window.MEASUREMENT_PWNED=true</script><img id="injected-hostile-element" src=x onerror="window.MEASUREMENT_PWNED=true">'
    hostile = deepcopy(payload)
    thermal = next(d for d in hostile["datasets"] if d["id"] == "thermal")
    thermal["rows"][0]["label"] = hostile_label
    thermal["rows"][0]["source_locator"] = "javascript:window.MEASUREMENT_PWNED=true"
    thermal["source_url"] = "javascript:window.MEASUREMENT_PWNED=true"
    (root / "hostile-label.html").write_bytes(render_html(hostile))
    (root / "browser-config.json").write_text(json.dumps({"hostileLabel": hostile_label}), encoding="utf-8")
    runner = root / "measurement-browser.cjs"
    runner.write_text(BROWSER_SCRIPT, encoding="utf-8")
    return subprocess.call([args.node, str(runner), str(root), args.playwright_module, args.chromium])


if __name__ == "__main__":
    sys.exit(main())

const fs=require('fs'),path=require('path'),assert=require('assert/strict');
const {pathToFileURL}=require('url');
const [root,modulePath,executablePath]=process.argv.slice(2);
const {chromium}=require(modulePath);
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath});
 const checks=[],errors=[],external=[];
 const check=(name,value)=>{assert.ok(value,name);checks.push(name);};
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true});
 page.on('pageerror',e=>errors.push(String(e)));
 page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 page.on('request',r=>{if(/^https?:/.test(r.url()))external.push(r.url());});
 await page.goto(pathToFileURL(path.join(root,'index.html')).href);
 await page.waitForFunction(()=>window.repairViewSnapshot);
 const snapshot=()=>page.evaluate(()=>window.repairViewSnapshot());
 check('five pairs',(await snapshot()).visible_ids.length===5);
 check('synthetic banner',(await page.locator('#origin').innerText()).includes('SYNTHETIC'));
 check('visible text has no mojibake',!/(?:\u00c2\u00b7|\u00e2\u20ac|\ufffd)/.test(await page.locator('body').innerText()));
 check('device separator is a middle dot',(await page.locator('#device').innerText()).includes(' \u00b7 '));
 check('no return to service',(await snapshot()).return_to_service==='not_authorized_by_this_tool');
 check('before outside',(await page.locator('#before-status').innerText()).includes('outside'));
 check('after inside',(await page.locator('#after-status').innerText()).includes('inside'));
 await page.locator('details.case-metadata > summary').click();
 check('ten readings',(await page.locator('#case-details').innerText()).includes('10'));
 await page.locator('details.case-metadata > summary').click();
 const originalCase=await page.locator('#case-json').textContent();
 const originalPair=await page.locator('#raw').textContent();
 const originalBefore=await page.locator('#before-value').textContent();
 const originalAfter=await page.locator('#after-value').textContent();
 const scene=page.locator('#assembly-svg');
 const geometry=()=>scene.locator('polygon').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('points')).join('|'));
 const initialScene=(await snapshot()).scene;
 check('illustrative geometry declared',initialScene.geometry==='illustrative'&&
   (await page.locator('#scene-geometry-label').innerText()).includes('Illustrative geometry')&&
   await page.locator('#scene-geometry-label').isVisible());
 check('scene starts linked',initialScene.selected===(await snapshot()).selected&&
   JSON.stringify(initialScene.visible_ids)===JSON.stringify((await snapshot()).visible_ids));
 check('five accessible hotspots',await scene.locator('.scene-hotspot[role="button"][tabindex="0"]').count()===5);
 check('selected hotspot exposed',await scene.locator('.scene-hotspot[aria-pressed="true"]').count()===1&&
   await scene.locator('.scene-hotspot[aria-pressed="true"]').getAttribute('data-pair-id')===initialScene.selected);
 await page.locator('#scene-phase').selectOption('before');
 const firstPair=JSON.parse(originalPair);
 check('before scene phase',(await snapshot()).scene.phase==='before'&&
   (await page.locator('#scene-reading').innerText()).includes(firstPair.before.value+' '+firstPair.check.unit));
 check('phase preserves both canonical readings',await page.locator('#raw').textContent()===originalPair&&
   await page.locator('#before-value').textContent()===originalBefore&&
   await page.locator('#after-value').textContent()===originalAfter);
 await page.locator('#scene-phase').selectOption('after');
 check('after scene phase',(await snapshot()).scene.phase==='after'&&
   (await page.locator('#scene-reading').innerText()).includes(firstPair.after.value+' '+firstPair.check.unit));
 const initialGeometry=await geometry();
 await page.locator('[data-camera="top"]').click();
 check('top camera changes projection',(await snapshot()).scene.pitch===Math.PI/2&&await geometry()!==initialGeometry);
 await page.locator('[data-camera="front"]').click();
 check('front camera preset',(await snapshot()).scene.pitch<.2&&(await snapshot()).scene.yaw===0);
 await page.locator('[data-camera="iso"]').click();
 check('isometric camera restores orientation',(await snapshot()).scene.yaw===initialScene.yaw&&
   (await snapshot()).scene.pitch===initialScene.pitch);
 const assembledGeometry=await geometry();
 await page.locator('#scene-explode').evaluate(n=>{n.value='80';n.dispatchEvent(new Event('input',{bubbles:true}));});
 await page.waitForFunction(previous=>Array.from(document.querySelectorAll('#assembly-svg polygon'))
   .map(n=>n.getAttribute('points')).join('|')!==previous,assembledGeometry);
 check('explode changes illustrative geometry',(await snapshot()).scene.explode===80&&await geometry()!==assembledGeometry);
 await page.locator('#scene-zoom-in').click();
 check('zoom in changes camera',(await snapshot()).scene.zoom>initialScene.zoom);
 await page.locator('#scene-zoom-out').click();
 check('zoom out restores camera',Math.abs((await snapshot()).scene.zoom-initialScene.zoom)<1e-10);
 for(let i=0;i<10&&await page.locator('#scene-zoom-in').isEnabled();i++)await page.locator('#scene-zoom-in').click();
 check('zoom bounded at upper limit',(await snapshot()).scene.zoom<=1.7&&await page.locator('#scene-zoom-in').isDisabled());
 await page.locator('#scene-reset').click();
 const resetScene=(await snapshot()).scene;
 check('reset restores camera and assembly',resetScene.yaw===initialScene.yaw&&resetScene.pitch===initialScene.pitch&&
   resetScene.zoom===initialScene.zoom&&resetScene.explode===0&&resetScene.selected===initialScene.selected);
 await scene.focus();
 await scene.press('ArrowRight');
 await scene.press('ArrowUp');
 check('keyboard orbit changes camera',(await snapshot()).scene.yaw>initialScene.yaw&&
   (await snapshot()).scene.pitch>initialScene.pitch);
 await scene.press('Equal');
 check('keyboard zoom works',(await snapshot()).scene.zoom>initialScene.zoom);
 await scene.press('Home');
 check('keyboard reset works',(await snapshot()).scene.zoom===initialScene.zoom&&
   (await snapshot()).scene.yaw===initialScene.yaw&&(await snapshot()).scene.pitch===initialScene.pitch);
 await scene.scrollIntoViewIfNeeded();
 const sceneBounds=await scene.boundingBox();
 await page.mouse.move(sceneBounds.x+15,sceneBounds.y+15);
 await page.mouse.down();
 await page.mouse.move(sceneBounds.x+85,sceneBounds.y+45,{steps:5});
 await page.mouse.up();
 check('pointer orbit changes camera',(await snapshot()).scene.yaw!==initialScene.yaw);
 await page.locator('#scene-reset').click();
 const clickId=await scene.locator('.scene-hotspot').nth(1).getAttribute('data-pair-id');
 await scene.locator('.scene-hotspot').nth(1).click();
 const clickedPair=JSON.parse(await page.locator('#raw').textContent());
 check('hotspot click links measurement',clickedPair.id===clickId&&(await snapshot()).selected===clickId&&
   (await snapshot()).scene.selected===clickId);
 check('hotspot links measurement navigation',await page.locator('#checks button[aria-pressed="true"]').getAttribute('data-id')===clickId);
 const [sceneDownload]=await Promise.all([page.waitForEvent('download'),page.locator('#export-selected').click()]);
 await sceneDownload.saveAs(path.join(root,'browser-scene-selected.json'));
 const sceneSelected=JSON.parse(fs.readFileSync(path.join(root,'browser-scene-selected.json'),'utf8'));
 check('hotspot export retains exact evidence',JSON.stringify(sceneSelected.pair)===JSON.stringify(clickedPair)&&
   sceneSelected.manifest_sha256===JSON.parse(originalCase).manifest_sha256&&sceneSelected.data_origin==='synthetic_demo');
 const keyboardId=await scene.locator('.scene-hotspot').nth(2).getAttribute('data-pair-id');
 await scene.locator('.scene-hotspot').nth(2).focus();
 await scene.locator('.scene-hotspot').nth(2).press('Enter');
 check('keyboard hotspot selection',(await snapshot()).selected===keyboardId&&
   JSON.parse(await page.locator('#raw').textContent()).id===keyboardId);
 await scene.locator('.scene-hotspot').first().focus();
 await scene.locator('.scene-hotspot').first().press('Space');
 check('space hotspot selection restores first pair',(await snapshot()).selected===initialScene.selected);
 check('scene controls preserve complete case',await page.locator('#case-json').textContent()===originalCase&&
   await page.locator('#raw').textContent()===originalPair);
 await page.screenshot({path:path.join(root,'preview-desktop.png'),fullPage:true});
 await page.locator('#condition').selectOption('loaded');
 check('loaded three pairs',(await snapshot()).visible_ids.length===3);
 check('overlap retained',(await page.locator('#after-status').innerText()).includes('overlaps'));
 check('condition filter links scene markers',await scene.locator('.scene-hotspot').count()===3&&
   JSON.stringify((await snapshot()).scene.visible_ids)===JSON.stringify((await snapshot()).visible_ids)&&
   (await snapshot()).scene.selected===(await snapshot()).selected);
 const pair=JSON.parse(await page.locator('#raw').textContent());
 check('canonical voltage',pair.after.value==='5.240'&&pair.after.unit==='V');
 check('original millivolts',pair.after.raw.value==='5240'&&pair.after.raw.unit==='mV');
 const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#export-selected').click()]);
 await download.saveAs(path.join(root,'browser-selected.json'));
 const selected=JSON.parse(fs.readFileSync(path.join(root,'browser-selected.json'),'utf8'));
 check('export exact pair',JSON.stringify(selected.pair)===JSON.stringify(pair));
 check('export origin',selected.data_origin==='synthetic_demo');
 await page.locator('#search').fill('no match xyz');
 check('empty filter',(await snapshot()).visible_ids.length===0&&await page.locator('#export-selected').isDisabled());
 check('empty filter clears scene selection',(await snapshot()).scene.selected===null&&
   (await snapshot()).scene.visible_ids.length===0&&await scene.locator('.scene-hotspot').count()===0&&
   (await page.locator('#scene-reading').innerText()).includes('No check selected'));
 await page.locator('#reset').click();
 check('reset five',(await snapshot()).visible_ids.length===5);
 check('filter reset restores linked scene',await scene.locator('.scene-hotspot').count()===5&&
   (await snapshot()).scene.selected===(await snapshot()).selected);
 await page.locator('#condition').selectOption('idle');
 check('idle two pairs',(await snapshot()).visible_ids.length===2);
 await page.locator('#reset').click();
 await page.locator('#search').fill('current');
 check('current units',(await page.locator('#after-value').innerText()).includes('A'));
 check('current scene keeps canonical units',(await page.locator('#scene-reading').innerText()).endsWith(' A')&&
   await scene.locator('.scene-hotspot').count()===1);
 await page.locator('#reset').click();
 await page.setViewportSize({width:390,height:844});
 await page.waitForTimeout(100);
 check('mobile no horizontal overflow',await page.evaluate(()=>document.documentElement.scrollWidth<=390));
 check('mobile chart readable',await page.locator('#plot').evaluate(n=>Number(n.getAttribute('viewBox').split(' ')[2])<=390));
 check('mobile scene fits viewport',await scene.evaluate(n=>Number(n.getAttribute('viewBox').split(' ')[2])<=390&&
   n.getBoundingClientRect().right<=390&&n.getBoundingClientRect().left>=0));
 check('mobile geometry disclosure visible',await page.locator('#scene-geometry-label').isVisible());
 await page.locator('#scene-phase').selectOption('before');
 check('mobile phase control works',(await snapshot()).scene.phase==='before');
 await page.locator('#scene-phase').selectOption('after');
 check('visual interactions retain disposition',(await snapshot()).return_to_service==='not_authorized_by_this_tool'&&
   (await snapshot()).scene.geometry==='illustrative'&&await page.locator('#case-json').textContent()===originalCase);
 await page.screenshot({path:path.join(root,'preview-mobile.png'),fullPage:true});
 check('no browser errors',errors.length===0);
 check('no external requests',external.length===0);
 fs.writeFileSync(path.join(root,'browser-verification.json'),JSON.stringify({status:'passed',browser:browser.version(),checks,errors,external},null,2));
 console.log(JSON.stringify({status:'passed',checks:checks.length}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});

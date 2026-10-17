/* Offline, orthographically projected 3D illustration. No physical geometry is inferred. */
window.createRepairScene = function createRepairScene(onSelect) {
  "use strict";
  const $ = id => document.getElementById(id);
  const svg = $("assembly-svg"), NS = "http://www.w3.org/2000/svg";
  const camera = {yaw: -.55, pitch: .72, zoom: 1, explode: 0, phase: "after"};
  let pairs = [], all = [], selected = null, drag = null, frame = null;
  const node = (tag, attrs = {}, text) => {
    const n = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) n.setAttribute(key, String(value));
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function render() {
    const focusId = document.activeElement?.getAttribute("data-pair-id");
    svg.replaceChildren();
    const width = Math.max(280, svg.clientWidth), height = Math.max(320, svg.clientHeight);
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const scale = Math.min(width / 520, height / 410) * (width > 600 ? 1.2 : 1) * camera.zoom;
    const cy = Math.cos(camera.yaw), sy = Math.sin(camera.yaw), cp = Math.cos(camera.pitch), sp = Math.sin(camera.pitch);
    function project([x, y, z]) {
      const u = x * cy - z * sy, v = x * sy + z * cy;
      return [width / 2 + u * scale, height * .57 + (v * sp - y * cp) * scale, v * cp + y * sp];
    }
    const defs = node("defs");
    const gradient = node("linearGradient", {id: "board-finish", x1: "0%", y1: "0%", x2: "100%", y2: "100%"});
    gradient.append(node("stop", {offset: "0%", "stop-color": "#326d68"}), node("stop", {offset: "60%", "stop-color": "#204942"}), node("stop", {offset: "100%", "stop-color": "#163d36"}));
    defs.append(gradient); svg.append(defs);
    // Ground and grid remain independent of case data: these are display coordinates only.
    for (let i = -4; i <= 4; i++) {
      for (const points of [[[-240, -28, i*45], [240, -28, i*45]], [[i*60, -28, -180], [i*60, -28, 180]]]) {
        const a = project(points[0]), b = project(points[1]);
        svg.append(node("line", {x1:a[0], y1:a[1], x2:b[0], y2:b[1], stroke:"#799492", "stroke-opacity": .12, "stroke-width":1}));
      }
    }
    const shadow = [[-197,-24,-112],[197,-24,-112],[197,-24,112],[-197,-24,112]].map(project);
    svg.append(node("polygon", {points:shadow.map(p=>p.slice(0,2).join(",")).join(" "),fill:"#000",opacity:.25}));
    const faces = [];
    function face(points, color, stroke = color) {
      const p = points.map(project);
      faces.push({depth:p.reduce((sum,v)=>sum+v[2],0)/p.length, element:node("polygon", {points:p.map(v=>v.slice(0,2).join(",")).join(" "),fill:color,stroke,"stroke-width":.5,"stroke-linejoin":"round"})});
    }
    function line(points, color, thickness=1, opacity=1) {
      const p=points.map(project);
      faces.push({depth:p.reduce((sum,v)=>sum+v[2],0)/p.length+.2,element:node("polyline",{points:p.map(v=>v.slice(0,2).join(",")).join(" "),fill:"none",stroke:color,"stroke-width":thickness*scale,opacity,"stroke-linecap":"round","stroke-linejoin":"round"})});
    }
    function box(x,z,w,d,bottom,h,colors) {
      const a=[x-w/2,bottom,z-d/2],b=[x+w/2,bottom,z-d/2],c=[x+w/2,bottom,z+d/2],e=[x-w/2,bottom,z+d/2];
      const up=v=>[v[0],v[1]+h,v[2]], A=up(a),B=up(b),C=up(c),E=up(e);
      face([a,b,B,A],colors[1]);face([b,c,C,B],colors[2]);face([c,e,E,C],colors[1]);face([e,a,A,E],colors[2]);face([A,B,C,E],colors[0]);
    }
    function cylinder(x,z,r,bottom,h,metal=false) {
      const top=[];
      for(let i=0;i<32;i++) {
        const a=i*Math.PI/16,b=(i+1)*Math.PI/16;
        const p=[x+Math.cos(a)*r,bottom,z+Math.sin(a)*r],q=[x+Math.cos(b)*r,bottom,z+Math.sin(b)*r];
        const brightness=metal?Math.round(100+75*(.5+.5*Math.cos(a-.8))):Math.round(31+30*(.5+.5*Math.cos(a-.8)));
        face([p,q,[q[0],bottom+h,q[2]],[p[0],bottom+h,p[2]]],`rgb(${brightness},${brightness+7},${brightness+10})`);
        top.push([p[0],bottom+h,p[2]]);
      }
      face(top,metal?"#bbc5c5":"#788b8c");
      line([[x-r*.58,bottom+h+.3,z],[x+r*.58,bottom+h+.3,z]],"#57676a",1.4);
      line([[x,bottom+h+.3,z-r*.58],[x,bottom+h+.3,z+r*.58]],"#57676a",1.4);
    }
    const lift=camera.explode*.7;
    box(0,0,360,210,-8,8,["url(#board-finish)","#102f2b","#1c4a42"]);
    faces.sort((a,b)=>a.depth-b.depth).forEach(f=>svg.append(f.element)); faces.length=0;
    // Decorative copper routes. They do not represent the device's actual circuitry.
    for(let i=0;i<7;i++) {
      const z=-77+i*25;
      line([[-160,.2,z],[-105,.2,z],[-82,.2,z+12],[75,.2,z+12],[96,.2,z],[158,.2,z]],"#6d9f84",1.25,.44);
    }
    for(const x of [-162,162])for(const z of [-87,87]) {
      const ring=Array.from({length:25},(_,i)=>[x+Math.cos(i*Math.PI/12)*7,.3,z+Math.sin(i*Math.PI/12)*7]);
      face(ring,"#b09b64");face(ring.map(p=>[x+(p[0]-x)*.56,.5,z+(p[2]-z)*.56]),"#0a1919");
    }
    faces.sort((a,b)=>a.depth-b.depth).forEach(f=>svg.append(f.element)); faces.length=0;
    box(4,-4,80,62,3+lift,12,["#303b43","#131d23","#202b33"]);
    box(4,-4,71,53,15+lift,2,["#425058","#2c373f","#323e45"]);
    for(let i=0;i<9;i++)for(const side of [-1,1])box(-29+i*8,side*39,4,12,2+lift,3,["#bcc1b6","#6f7776","#88958c"]);
    for(const x of [-95,-47]) cylinder(x,41,16,3+lift,39,true);
    cylinder(91,-35,22,3+lift,29);
    box(90,-35,22,18,33+lift,3,["#bba581","#82765e","#aa9875"]);
    for(const x of [-137,140]) {
      box(x,-5,30,73,1+lift*.5,26,["#566e72","#243c40","#324e52"]);
      for(const z of [-28,-4,20]) {
        cylinder(x,z,6,27+lift*.5,2,true);
        box(x+(x<0?-17:17),z,8,13,7+lift*.5,9,["#b3ada0","#626963","#888d81"]);
      }
    }
    for(let i=0;i<6;i++) {
      const x=-70+i*24,z=-68;
      box(x,z,13,7,1+lift,4,["#aaa184","#7b775f","#8d886f"]);
      for(const side of [-1,1])box(x+side*6,z,3,8,1+lift,4,["#bac4c1","#7c8d89","#91a09b"]);
    }
    // Exploded guides are illustration-only assembly guides, never inferred repair operations.
    if(camera.explode>0)for(const [x,z] of [[4,-4],[-95,41],[91,-35]])line([[x,1,z],[x,lift+3,z]],"#79beb2",1,.5);
    faces.sort((a,b)=>a.depth-b.depth).forEach(f=>svg.append(f.element));
    const pointsLayer=node("g",{class:"scene-markers"});
    for (const pair of pairs) {
      const idx=all.indexOf(pair.id), count=all.length;
      // Stable display slots, NOT measured coordinates or physical test-point locations.
      const angle=(idx/count)*Math.PI*2-.5;
      const p=project([Math.cos(angle)*125,36+lift,Math.sin(angle)*69]);
      const active=pair.id===selected, g=node("g",{class:"scene-hotspot", "data-pair-id":pair.id,role:"button",tabindex:0,"aria-pressed":active,"aria-label":`${idx+1}. ${pair.check.label}, ${pair.condition.id}. ${camera.phase} ${pair[camera.phase].value} ${pair.check.unit}. Illustrative display slot.`});
      const label=node("title",{},`${pair.check.label} · ${pair.condition.id}\n${camera.phase}: ${pair[camera.phase].value} ${pair.check.unit}`);
      g.append(label,node("circle",{cx:p[0],cy:p[1],r:active?21:18,class:active?"marker-halo active":"marker-halo"}),node("circle",{cx:p[0],cy:p[1],r:12,class:"marker-core"}),node("text",{x:p[0],y:p[1]+4,"text-anchor":"middle",class:"marker-number"},idx+1));
      g.addEventListener("click",()=>onSelect(pair.id));
      g.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();e.stopPropagation();onSelect(pair.id);}});
      pointsLayer.append(g);
    }
    svg.append(pointsLayer);
    if(focusId)Array.from(svg.querySelectorAll("[data-pair-id]")).find(n=>n.getAttribute("data-pair-id")===focusId)?.focus({preventScroll:true});
    const chosen=pairs.find(p=>p.id===selected);
    $("scene-reading").textContent=chosen?`${camera.phase === "after" ? "After" : "Before"} · ${chosen[camera.phase].value} ${chosen.check.unit}`:"No check selected";
    $("scene-check").textContent=chosen?`${chosen.check.label} / ${chosen.condition.id}`:"Adjust the measurement filters";
    $("scene-phase").value=camera.phase;$("scene-explode").value=camera.explode;
    $("scene-zoom-value").textContent=Math.round(camera.zoom*100)+"%";
    $("scene-explode-value").textContent=camera.explode?Math.round(camera.explode)+"%":"Assembled";
    $("scene-zoom-out").disabled=camera.zoom<=.65;$("scene-zoom-in").disabled=camera.zoom>=1.7;
  }
  function schedule(){cancelAnimationFrame(frame);frame=requestAnimationFrame(render);}
  function reset(){Object.assign(camera,{yaw:-.55,pitch:.72,zoom:1,explode:0});render();}
  document.querySelectorAll("[data-camera]").forEach(button=>button.addEventListener("click",()=>{
    const preset=button.dataset.camera;
    Object.assign(camera,preset==="top"?{yaw:0,pitch:Math.PI/2}:preset==="front"?{yaw:0,pitch:.12}:{yaw:-.55,pitch:.72});render();
  }));
  $("scene-reset").addEventListener("click",reset);
  $("scene-phase").addEventListener("change",e=>{camera.phase=e.target.value;render();});
  $("scene-explode").addEventListener("input",e=>{camera.explode=Number(e.target.value);schedule();});
  for(const [id,delta] of [["scene-zoom-in",.15],["scene-zoom-out",-.15]])$(id).addEventListener("click",()=>{camera.zoom=clamp(camera.zoom+delta,.65,1.7);render();});
  svg.addEventListener("pointerdown",e=>{
    if(e.button!==0||e.target.closest(".scene-hotspot"))return;
    drag={x:e.clientX,y:e.clientY,yaw:camera.yaw,pitch:camera.pitch};svg.setPointerCapture(e.pointerId);svg.classList.add("orbiting");
  });
  svg.addEventListener("pointermove",e=>{if(!drag)return;camera.yaw=drag.yaw+(e.clientX-drag.x)*.008;camera.pitch=clamp(drag.pitch+(e.clientY-drag.y)*.008,.12,Math.PI/2);schedule();});
  const end=()=>{drag=null;svg.classList.remove("orbiting");};
  svg.addEventListener("pointerup",end);svg.addEventListener("pointercancel",end);svg.addEventListener("lostpointercapture",end);
  svg.addEventListener("keydown",e=>{
    if(e.target!==svg)return;
    if(!["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","+","=","-","Home"].includes(e.key))return;
    e.preventDefault();
    if(e.key==="Home"){reset();return;}
    if(e.key==="ArrowLeft")camera.yaw-=.12;if(e.key==="ArrowRight")camera.yaw+=.12;
    if(e.key==="ArrowUp")camera.pitch=clamp(camera.pitch+.12,.12,Math.PI/2);if(e.key==="ArrowDown")camera.pitch=clamp(camera.pitch-.12,.12,Math.PI/2);
    if(e.key==="+"||e.key==="=")camera.zoom=clamp(camera.zoom+.15,.65,1.7);if(e.key==="-")camera.zoom=clamp(camera.zoom-.15,.65,1.7);render();
  });
  window.addEventListener("resize",schedule);
  return {
    initialize(records){all=records.map(p=>p.id);},
    setPairs(records,identity){pairs=records;selected=identity;render();},
    snapshot(){return {...camera,selected,visible_ids:pairs.map(p=>p.id),geometry:"illustrative"};}
  };
};

(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const data=JSON.parse(new TextDecoder().decode(Uint8Array.from(atob($('payload').textContent.trim()),c=>c.charCodeAt(0))));
  const make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e;};
  let source=null, selected=null;
  const queries=data.records.filter(r=>r.data.schema==='ncp.operational-query.v1');
  function inspect(ref,value){$('identity').textContent=ref;$('raw').textContent=JSON.stringify(value,null,2);}
  function rows(){
    $('observations').replaceChildren();if(!source)return;
    const lower=Number($('start').value),upper=Number($('end').value),channel=$('channel').value;
    const all=[...(data.source_rows?.[source]??[]),...queries.filter(q=>q.data.source===source).flatMap(q=>q.data.rows)];
    const unique=[...new Map(all.map(r=>[r.id,r])).values()];
    const unplaced=$('unplaced').checked;
    const shown=unique.filter(r=>r.channel===channel&&(unplaced?r.time===null:r.time!==null&&lower<=r.time&&r.time<upper));
    for(const r of shown){const tr=make('tr');for(const v of [r.time??'Unknown sample time',`${r.value??'unknown'} ${r.unit}`,r.configuration?.configuration??r.configuration?.status??'Attribution not queried',r.quality])tr.append(make('td',String(v)));tr.tabIndex=0;tr.setAttribute('aria-label',`Inspect observation ${r.id}`);tr.addEventListener('click',()=>inspect(r.id,r));tr.addEventListener('keydown',e=>{if(e.key==='Enter')inspect(r.id,r);});$('observations').append(tr);}
    $('coverage').textContent=`${shown.length} retained observations ${unplaced?'with unknown sample time (excluded from interval attribution)':'in selected interval'}. Source completeness remains explicit.`;
    $('status').textContent='Historical interval selected. Scientific records and decisions unchanged.';
  }
  function showSource(alias){source=alias;selected=null;const s=data.catalog.sources[alias];$('title').textContent=alias;
    $('scope').textContent=`Historical ${s.family} · ${s.context.origin} · completeness: ${s.context.completeness} · connectivity: ${s.context.connectivity}`;
    $('answer').replaceChildren();$('channel').replaceChildren(...s.channels.map(c=>{const o=make('option',c);o.value=c;return o;}));
    const qs=queries.filter(q=>q.data.source===alias);$('start').value=qs.length?Math.min(...qs.map(q=>q.data.selection.start)):0;$('end').value=qs.length?Math.max(...qs.map(q=>q.data.selection.end)):1;
    inspect(s.binding,s);for(const b of $('sources').children)b.setAttribute('aria-pressed',String(b.dataset.source===alias));rows();}
  for(const alias of Object.keys(data.catalog.sources)){const b=make('button',alias);b.dataset.source=alias;b.addEventListener('click',()=>showSource(alias));$('sources').append(b);}
  for(const record of data.records){const r=record.data;if(!['ncp.operational-finding.v1','ncp.operational-proposal.v1','ncp.operational-annotation.v1'].includes(r.schema))continue;
    const b=make('button',`${r.question??r.template??'Annotation'} · ${r.status??'review context'}`);b.dataset.record=record.record;
    b.addEventListener('click',()=>{selected=record;source=null;$('title').textContent=r.question??'Proposed next test';$('scope').textContent=`${r.status??'REVIEW_CONTEXT'} · ${record.applicability} · historical retained result`;
      $('observations').replaceChildren();$('answer').replaceChildren(make('h3',r.status??'Human review context'),make('pre',JSON.stringify(r,null,2)));$('coverage').textContent='No new numerical execution. Proposal approval remains separate.';inspect(record.record,r);$('status').textContent=`Inspecting retained ${r.schema}`;});$('records').append(b);}
  const unplacedLabel=make('label','Show unknown sample time');const unplaced=make('input');unplaced.type='checkbox';unplaced.id='unplaced';unplacedLabel.append(unplaced);document.querySelector('.filters').append(unplacedLabel);unplaced.addEventListener('change',rows);
  $('filter').addEventListener('click',rows);$('channel').addEventListener('change',rows);
  if(Object.keys(data.catalog.sources).length)showSource(Object.keys(data.catalog.sources)[0]);
})();

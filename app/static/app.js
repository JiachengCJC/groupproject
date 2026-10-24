const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state = {config:null, sources:null, run:null, busy:false, token:0, parent:null, tab:'overview', documentIds:[], ollama:null};
const specialists = [
  ['competitive','Competitive intelligence','Pricing · positioning','◈'],
  ['customer','Customer intelligence','Retention · behavior','◉'],
  ['product','Product intelligence','Quality · performance','◇'],
  ['market','Market analyst','Demand · opportunity','↗']
];
const labels = {queued:'Queued',running:'Analyzing',pending_review:'Awaiting review',approved:'Approved',rejected:'Rejected',failed:'Failed'};
const fmt = n => n == null ? '—' : new Intl.NumberFormat('en-SG',{maximumFractionDigits:1}).format(n);
const percent = n => n == null ? '—' : `${(n*100).toFixed(1)}%`;
const date = d => new Date(d).toLocaleString('en-SG',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});

async function api(path, body) {
  const response = await fetch('/api'+path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const payload = await response.json();
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(Array.isArray(detail) ? detail.map(d=>`${d.loc?.at(-1) || 'Input'}: ${d.msg}`).join('; ') : detail || 'The request failed. Please try again.');
  }
  return payload;
}
let toastTimer;
function toast(message, error=false) {
  clearTimeout(toastTimer); $('#toast').textContent=message; $('#toast').className='toast'+(error?' error':'');
  toastTimer=setTimeout(()=>$('#toast').classList.add('hidden'),error?10000:4500);
}
function statusPill(status) { return `<span class="pill status-${escapeHTML(status)}">${escapeHTML(labels[status] || status)}</span>`; }
function renderTeam(stage) {
  $('#team-list').innerHTML=specialists.map((s,i)=>`<div class="team-member"><span class="agent-icon agent-${s[0]}">${s[3]}</span><div><b>${s[1]}</b><small>${s[2]}</small></div><span class="agent-indicator ${stage==='running'?'working':stage==='done'?'done':''}">${stage==='done'?'✓':'·'}</span></div>`).join('');
  $('#team-status').textContent=stage==='running'?'Following the evidence…':stage==='done'?'Findings ready for your review':'Ready when you are';
}
async function showView(view) {
  if (!['analysis','sources','history','evaluation'].includes(view)) view='analysis';
  $$('.view').forEach(el=>el.classList.toggle('hidden',el.id!=='view-'+view));
  $$('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
  $('#page-breadcrumb').textContent=({analysis:'Analysis studio',sources:'Knowledge base',history:'Review history',evaluation:'Evaluation'})[view];
  history.replaceState(null,'','#'+view);
  try {
    if (view==='history') await loadHistory();
    if (view==='sources') await loadSources();
    if (view==='evaluation') renderBenchmark(await api('/benchmarks'));
  } catch(err) { toast(err.message,true); }
}
function updateMode() {
  const mode=$('#agent-mode').value;
  if($('#decision-mode').value==='openai_decisions' && (state.config?.local_only || !state.config?.openai_decisions_available)) $('#decision-mode').value=mode==='local'?'baseline':'llm';
  $('#mode-summary').textContent=`${({local:'Baseline',live:'OpenAI',ollama:'Ollama'})[mode]} · ${({openai_decisions:'OpenAI Decisions',baseline:'rule scoring',llm:'model recommendation'})[$('#decision-mode').value]}`;
  $('#ollama-model-field').classList.toggle('hidden',mode!=='ollama');
  $('#decision-mode option[value="llm"]').disabled=mode==='local';
  const ds=state.sources?.datasets.find(d=>d.id===$('#dataset').value);
  $('#data-mode-label').textContent=ds?.synthetic?'Synthetic demo data':ds?'Your imported data':'Documents only';
  $('#data-mode-label').className='pill'+(ds?.synthetic?'':' green');
  $$('.scenario').forEach(b=>b.classList.toggle('selected',b.dataset.dataset===$('#dataset').value));
  renderDocumentPicker();
}
function renderDocumentPicker() {
  const datasetId=$('#dataset').value;
  const documents=(state.sources?.documents||[]).filter(d=>!datasetId||!d.dataset_id||d.dataset_id===datasetId);
  state.documentIds=state.documentIds.filter(id=>documents.some(d=>d.id===id));
  $('#document-selection-count').textContent=state.documentIds.length?`${state.documentIds.length} selected`:datasetId?'Automatic dataset context':'Choose documents below';
  $('#analysis-documents').innerHTML=documents.length?documents.map(d=>`<label class="document-choice"><input type="checkbox" value="${escapeHTML(d.id)}" ${state.documentIds.includes(d.id)?'checked':''} ${state.busy?'disabled':''}><span>${escapeHTML(d.title)}<small>${escapeHTML(d.category||'General')}</small></span></label>`).join(''):'<p class="help">Upload files in Knowledge base to add documents.</p>';
}
async function refreshModels() {
  state.ollama=await api('/models/ollama');
  const options=state.ollama.models.map(m=>`<option value="${escapeHTML(m)}">${escapeHTML(m)}</option>`).join('');
  for(const id of ['ollama-model','vision-model']) {
    const selected=$('#'+id).value;
    $('#'+id).innerHTML=options||'<option value="">No local models found</option>';
    const preferred=selected||(id==='vision-model'?state.config.ollama_vision_model:state.ollama.default_model);
    if(state.ollama.models.includes(preferred)) $('#'+id).value=preferred;
  }
  $('#provider-note').textContent=state.ollama.available?`Ollama connected · ${state.ollama.models.length} installed models. Local inference may take a few minutes.`:state.ollama.error;
  $('#agent-mode option[value="ollama"]').disabled=!state.ollama.models.length;
}
async function loadSources(selectId) {
  state.sources=await api('/sources');
  const chosen=selectId ?? $('#dataset').value;
  const options=state.sources.datasets.map(d=>`<option value="${escapeHTML(d.id)}">${escapeHTML(d.name)}${d.synthetic?' · demo':''}</option>`).join('');
  $('#dataset').innerHTML='<option value="">Documents only · choose documents below</option>'+options;
  if (!chosen || state.sources.datasets.some(d=>d.id===chosen)) $('#dataset').value=chosen;
  const docChoice=$('#doc-dataset').value;
  $('#doc-dataset').innerHTML='<option value="">All datasets · shared context</option>'+options;
  if (state.sources.datasets.some(d=>d.id===docChoice)) $('#doc-dataset').value=docChoice;
  const uploadChoice=$('#upload-dataset').value;
  $('#upload-dataset').innerHTML='<option value="">Shared context / standalone documents</option>'+options;
  if(state.sources.datasets.some(d=>d.id===uploadChoice)) $('#upload-dataset').value=uploadChoice;
  $('#source-count').textContent=state.sources.datasets.length+state.sources.documents.length;
  $('#source-summary').textContent=`${state.sources.datasets.length} datasets · ${state.sources.documents.length} documents`;
  $('#sources-list').innerHTML='<h3 class="source-category">DATA TABLES</h3>'+state.sources.datasets.map(d=>`<button class="source-row" data-dataset-preview="${escapeHTML(d.id)}"><span class="source-icon">▦</span><span><b>${escapeHTML(d.name)}</b><small>${d.row_count} rows · ${d.columns.length} columns · ${d.synthetic?'Synthetic fixture':'Imported CSV'}</small></span><span>↗</span></button>`).join('')+'<h3 class="source-category">DOCUMENT LIBRARY</h3>'+state.sources.documents.map(d=>`<button class="source-row" data-doc-preview="${escapeHTML(d.id)}"><span class="source-icon document-icon">▤</span><span><b>${escapeHTML(d.title)}</b><small>${d.chunk_count} passage${d.chunk_count===1?'':'s'} · ${d.synthetic?'Synthetic fixture':escapeHTML(d.category||'General')+' · '+escapeHTML(d.extraction_method||'text')} · ${d.dataset_id?'Dataset-specific':'Shared'}</small></span><span>↗</span></button>`).join('');
  updateMode();
}
function tableHTML(query) {
  if (!query.rows?.length) return '<div class="blank-inline">No rows returned by this query.</div>';
  return `<div class="table-scroll"><table><thead><tr>${query.columns.map(c=>`<th>${escapeHTML(c)}</th>`).join('')}</tr></thead><tbody>${query.rows.map(r=>`<tr>${query.columns.map(c=>`<td>${escapeHTML(typeof r[c]==='number'?fmt(r[c]):r[c]??'NULL')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${query.truncated?'<p class="help">Showing the first 200 rows. Results were truncated.</p>':''}`;
}
function dialog(title, content) { $('#dialog-title').textContent=title; $('#dialog-content').innerHTML=content; $('#source-dialog').showModal(); }
function renderProgress(run) {
  $('#progress').classList.remove('hidden');
  const steps=['route','agents','decision','review'];
  const stage=run.events?.at(-1)?.stage;
  $('#progress').innerHTML=`<div class="progress-top"><span class="spinner"></span><b>${escapeHTML(run.events?.at(-1)?.message || 'Preparing the analysis…')}</b></div><div class="progress-steps">${steps.map((s,i)=>`<span class="${i<=steps.indexOf(stage)?'complete':''}">${String(i+1).padStart(2,'0')} ${s==='route'?'Route question':s==='agents'?'Collect evidence':s==='decision'?'Score actions':'Human review'}</span>`).join('')}</div>`;
}
function setBusy(busy) {
  state.busy=busy;
  $('#run-button').disabled=busy;
  $('#run-button').innerHTML=busy?'<span class="spinner"></span> Analyzing…':'Run analysis <span>↗</span>';
  for (const id of ['question','dataset','agent-mode','decision-mode','ollama-model','refresh-models']) $('#'+id).disabled=busy;
  renderDocumentPicker();
  $$('.scenario').forEach(b=>b.disabled=busy);
}
async function pollRun(id) {
  const token=++state.token;
  while (token===state.token) {
    const run=await api('/analyses/'+id);
    if (token!==state.token) return;
    state.run=run;
    if (!['queued','running'].includes(run.status)) {
      setBusy(false); $('#progress').classList.add('hidden');
      if (run.status==='failed') {
        renderTeam();
        $('#results').innerHTML=`<div class="notice error"><b>Analysis could not finish.</b><p>${escapeHTML(run.error)}</p><p>Your sources are saved. Adjust the question or settings and run it again.</p></div>`;
        toast(run.error,true);
      } else { renderTeam('done'); renderResults(); }
      return;
    }
    renderProgress(run);
    await new Promise(resolve=>setTimeout(resolve,700));
  }
}
function evidenceLinks(ids) {
  return (ids||[]).map(id=>`<button class="citation" data-evidence="${escapeHTML(id)}">${escapeHTML(state.run.result.evidence.find(e=>e.id===id)?.kind==='document'?'▤ Source':'▦ SQL')}</button>`).join('');
}
function metricsHTML(m) {
  const tiles=[['Revenue change',percent(m.revenue_growth),'Current vs previous period'],['Gross margin',percent(m.gross_margin),'(Revenue − cost) / revenue'],['Customer churn',percent(m.churn_rate),'Lost / starting customers'],['Return rate',percent(m.return_rate),'Returned / sold units']];
  return `<div class="metric-grid">${tiles.map((t,i)=>`<div class="metric"><span>${t[0]}</span><strong>${t[1]}</strong><small>${t[2]}</small></div>`).join('')}</div>`;
}
function renderResults() {
  const run=state.run, r=run.result, d=r.decision, action=d.actions[d.choice];
  $('#results').innerHTML=`<div class="results-heading"><div><span class="tiny-label">YOUR DECISION BRIEF</span><h2>${escapeHTML(r.dataset.name)}</h2></div><div class="result-meta">${statusPill(run.status)}<span>${fmt(r.measurement.elapsed_ms)} ms</span></div></div>
    ${Object.keys(r.metrics).length?metricsHTML(r.metrics):'<p class="help">Document analysis · findings are based on the selected source passages.</p>'}
    <div class="result-tabs" role="tablist" aria-label="Analysis details">${[['overview','Decision overview'],['agents','Specialist findings'],['evidence',`Evidence (${r.evidence.length})`],['trace','Execution trace']].map(t=>`<button role="tab" id="tab-${t[0]}" aria-controls="tab-content" aria-selected="${state.tab===t[0]}" tabindex="${state.tab===t[0]?'0':'-1'}" data-tab="${t[0]}" class="${state.tab===t[0]?'selected':''}">${t[1]}</button>`).join('')}</div>
    <div id="tab-content" role="tabpanel" aria-labelledby="tab-${state.tab}"></div>
    <div class="review-panel panel"><div><span class="tiny-label">HUMAN IN THE LOOP</span><h2>${run.status==='approved'?'Approved for your decision record.':run.status==='rejected'?'Returned for further analysis.':'The final call is yours.'}</h2><p class="help">Review the evidence and assumptions. Approval records a decision; it does not execute business actions.</p></div><div id="review-content"></div></div>
    <details class="limitations"><summary>Method & limitations <span>${r.warnings.length} notes</span></summary><ul>${r.warnings.map(w=>`<li>${escapeHTML(w)}</li>`).join('')}</ul><p>Periods are assumed to be equally sized 30-day windows. Price and market growth are unweighted averages. Citation coverage checks references, not factual entailment.</p></details>`;
  renderTab(); renderReview();
}
function renderTab() {
  const r=state.run.result, d=r.decision;
  if (state.tab==='overview') {
    const modelDecision=['live','ollama'].includes(d.provider);
    const probabilistic=['openai_decisions','jev'].includes(d.provider);
    const ranking=Object.entries((probabilistic?d.probabilities:d.scores)||{}).sort((a,b)=>b[1]-a[1]);
    $('#tab-content').innerHTML=`<div class="decision-grid"><section class="recommendation"><div class="recommendation-top"><span class="tiny-label">RECOMMENDED NEXT STEP</span><span class="decision-icon">↗</span></div><h2>${escapeHTML(d.actions[d.choice].title)}</h2><p>${escapeHTML(r.rationale)}</p><div>${evidenceLinks(r.rationale_evidence_ids)}</div><div class="recommendation-foot"><span>${probabilistic?(d.provider==='jev'?'Legacy TypeSafe JEV':'OpenAI Decisions')+' · '+escapeHTML(d.model):modelDecision?escapeHTML(d.provider.toUpperCase()+' · '+d.model):'RULE-BASED BASELINE'}</span><span>${probabilistic?'Model confidence '+percent(d.confidence):'Human review required'}</span></div></section>
      <section class="ranking panel"><div class="section-heading"><h3>${modelDecision?'Other options to consider':'Compare the alternatives'}</h3><span class="tiny-label">${probabilistic?'MODEL PROBABILITY':modelDecision?'UNRANKED OPTIONS':'PRIORITY / 100'}</span></div>${modelDecision?Object.entries(d.actions).filter(([k])=>k!==d.choice).map(([,a])=>`<p class="model-alternative">${escapeHTML(a.title)}</p>`).join('')||'<p class="help">No additional options were proposed.</p>':''}${ranking.map(([k,v])=>`<div class="rank-row ${k===d.choice?'winner':''}"><div><span>${escapeHTML(d.actions[k].title)}</span><b>${probabilistic?percent(v):fmt(v)}</b></div><div class="bar"><i style="width:${Math.max(0,Math.min(100,probabilistic?v*100:v))}%"></i></div></div>`).join('')}<p class="help">${probabilistic?'Probabilities describe the model’s choice among these options, not the chance of business success.':modelDecision?'The model explains its recommendation using cited evidence. No numerical confidence or success probability is assigned.':'Transparent heuristic scores. They are not probabilities and do not sum to 100.'}</p></section></div>`;
  } else if (state.tab==='agents') {
    $('#tab-content').innerHTML=`<div class="agent-findings">${r.agents.map((a,i)=>`<article class="panel finding"><div class="finding-heading"><span class="agent-icon agent-${a.id}">${specialists[i][3]}</span><div><h3>${escapeHTML(a.name)}</h3><span class="help">${a.tool_calls} tool calls · ${fmt(a.elapsed_ms)} ms</span></div></div>${a.claims.map(c=>`<div class="claim"><p>${escapeHTML(c.text)}</p>${evidenceLinks(c.evidence_ids)}</div>`).join('')}<p class="help">${a.limitations.map(escapeHTML).join(' ')}</p></article>`).join('')}</div>`;
  } else if (state.tab==='evidence') {
    $('#tab-content').innerHTML=`<div class="evidence-list">${r.evidence.map(e=>`<details class="panel evidence" id="evidence-${escapeHTML(e.id)}"><summary><span class="source-icon ${e.kind==='document'?'document-icon':''}">${e.kind==='sql'?'▦':'▤'}</span><span><b>${escapeHTML(e.title)}</b><small>${e.kind==='sql'?'Read-only SQL · '+e.rows.length+' returned rows':escapeHTML(e.source)+' · relevance '+e.relevance}</small></span><span class="expand-icon">+</span></summary><div class="evidence-body">${e.kind==='sql'?`<pre><code>${escapeHTML(e.sql)}</code></pre>${tableHTML(e)}`:`<blockquote>${escapeHTML(e.content)}</blockquote><p class="help">${e.synthetic?'Synthetic teaching document · ':''}${escapeHTML(e.location||('Passage '+e.ordinal))} · ${escapeHTML(e.document_id)}</p>${e.file_id?`<a class="button secondary" href="/api/files/${encodeURIComponent(e.file_id)}" download>Download original</a>`:''}`}</div></details>`).join('')}</div>`;
  } else {
    $('#tab-content').innerHTML=`<div class="trace-grid"><div class="panel trace"><h3>Execution trace</h3>${state.run.events.map((e,i)=>`<div class="trace-step"><span>${i+1}</span><div><b>${escapeHTML(e.stage)}</b><p>${escapeHTML(e.message)}</p><small>${escapeHTML(date(e.at))}</small></div></div>`).join('')}</div><div class="panel trace-metrics"><h3>Measured on this run</h3><dl><dt>Total latency</dt><dd>${fmt(r.measurement.elapsed_ms)} ms</dd><dt>SQL + retrieval calls</dt><dd>${r.measurement.tool_calls}</dd><dt>Citation coverage</dt><dd>${percent(r.measurement.citation_coverage)}</dd><dt>API cost</dt><dd>${r.measurement.estimated_cost===0?'$0 · local mode':'Not estimated'}</dd><dt>Agent mode</dt><dd>${escapeHTML(r.agent_mode)}</dd><dt>Decision model</dt><dd>${escapeHTML(d.model)}</dd></dl><p class="help">${escapeHTML(r.measurement.citation_coverage_note)}</p><p class="help">Token usage, when available, is included in the approved JSON export.</p></div></div>`;
  }
}
function selectTab(name) {
  state.tab=name;
  $$('[data-tab]').forEach(button=>{
    const selected=button.dataset.tab===name;
    button.classList.toggle('selected',selected);
    button.setAttribute('aria-selected',String(selected));
    button.tabIndex=selected?0:-1;
  });
  $('#tab-content').setAttribute('aria-labelledby','tab-'+name);
  renderTab();
}
function renderReview() {
  const run=state.run;
  if (run.status==='pending_review') {
    $('#review-content').innerHTML=`<form id="review-form"><div class="review-inputs"><label>Reviewer name<input id="reviewer" required minlength="2" maxlength="100" placeholder="Your name"></label><label>Review notes <span class="help">required to reject</span><textarea id="review-notes" rows="2" maxlength="2000" placeholder="What have you verified? What needs another look?"></textarea></label></div><div class="review-actions"><button class="button secondary" type="submit" name="decision" value="rejected">Request revision</button><button class="button primary" type="submit" name="decision" value="approved">Approve brief <span>✓</span></button></div></form>`;
  } else {
    $('#review-content').innerHTML=`<div class="review-record"><p><b>${escapeHTML(run.review.reviewer)}</b> ${run.status==='approved'?'approved':'rejected'} this brief on ${escapeHTML(date(run.review.created_at))}.</p>${run.review.notes?`<blockquote>${escapeHTML(run.review.notes)}</blockquote>`:''}${run.status==='approved'?`<a class="button primary" href="/api/analyses/${encodeURIComponent(run.id)}/export" download>Export approved brief <span>↓</span></a>`:'<button class="button primary" id="revise-run">Revise & rerun <span>↗</span></button><p class="help">Reviewer notes are carried forward. In local mode, change the source data to change scoring; live mode can interpret the notes.</p>'}</div>`;
  }
}
async function loadHistory() {
  const runs=await api('/analyses');
  $('#history-list').innerHTML=runs.length?`<div class="history-head"><span>BUSINESS QUESTION</span><span>CREATED</span><span>REVIEW STATUS</span></div>${runs.map(r=>`<button class="history-row" data-run="${escapeHTML(r.id)}"><span><b>${escapeHTML(r.question)}</b><small>${escapeHTML(r.id)}</small></span><span>${escapeHTML(date(r.created_at))}</span><span>${statusPill(r.status)} <i>↗</i></span></button>`).join('')}`:'<div class="empty-history"><h2>Your decision record starts with an analysis.</h2><p>Run a scenario in the studio. Its results and review status will appear here.</p><button class="button primary" data-view="analysis">Open analysis studio ↗</button></div>';
}
function renderBenchmark(result) {
  $('#benchmark-results').innerHTML=result?`<div class="metric-grid benchmark-metrics">${[['Scenario agreement',percent(result.pass_rate)],['Task completion',percent(result.completion_rate)],['Citation coverage',percent(result.mean_citation_coverage)],['Test scenarios',result.cases.length]].map(t=>`<div class="metric"><span>${t[0]}</span><strong>${t[1]}</strong><small>Local synthetic regression</small></div>`).join('')}</div><div class="panel benchmark-table"><div class="section-heading"><h2>Scenario results</h2><span class="help">${escapeHTML(date(result.created_at))}</span></div><div class="table-scroll"><table><thead><tr><th>Scenario</th><th>Expected action</th><th>Actual action</th><th>Latency</th><th>Result</th></tr></thead><tbody>${result.cases.map(c=>`<tr><td>${escapeHTML(c.title)}</td><td>${escapeHTML(c.expected)}</td><td>${escapeHTML(c.actual)}</td><td>${fmt(c.elapsed_ms)} ms</td><td><span class="pill ${c.passed?'green':'red'}">${c.passed?'Pass':'Fail'}</span></td></tr>`).join('')}</tbody></table></div><p class="help">${escapeHTML(result.note)}</p></div>`:'<div class="panel empty-history"><span class="large-symbol">⌁</span><h2>Put the pipeline through its paces.</h2><p>Run all five scenarios to measure completion, expected-action agreement,<br>latency and citation coverage. This makes no external API calls.</p></div>';
}

document.addEventListener('click',async event=>{
  const button=event.target.closest('button, a'); if(!button) return;
  try {
    if(button.dataset.view) await showView(button.dataset.view);
    if(button.dataset.scenario && !state.busy) {
      const s=state.config.scenarios.find(s=>s.id===button.dataset.scenario);
      $('#question').value=s.question; $('#dataset').value=s.dataset_id; state.documentIds=[]; state.parent=null; updateMode();
    }
    if(button.dataset.tab) {selectTab(button.dataset.tab); $(`[data-tab="${state.tab}"]`).focus();}
    if(button.dataset.evidence) {
      selectTab('evidence');
      const el=document.getElementById('evidence-'+button.dataset.evidence);
      if(el) {el.open=true; el.scrollIntoView({behavior:'smooth',block:'center'});}
    }
    if(button.dataset.datasetPreview) {
      const d=await api('/datasets/'+button.dataset.datasetPreview);
      dialog(d.dataset.name,`<p class="help">${d.dataset.row_count} rows · ${d.dataset.synthetic?'Synthetic demo':'Imported data'}</p>${tableHTML(d.preview)}<h3 class="schema-title">Schema</h3><pre>${escapeHTML(d.dataset.columns.map(c=>c.name+'  '+c.type).join('\n'))}</pre>`);
    }
    if(button.dataset.docPreview) {
      const d=await api('/documents/'+button.dataset.docPreview);
      dialog(d.title,`<p class="help">${escapeHTML(d.source)} · ${escapeHTML(d.category)} · ${escapeHTML(d.extraction_method)}</p>${d.file_id?`<a class="button secondary" href="/api/files/${encodeURIComponent(d.file_id)}" download>Download original</a>`:''}${(d.warnings||[]).map(w=>`<p class="help">${escapeHTML(w)}</p>`).join('')}<blockquote class="document-preview">${escapeHTML(d.content)}</blockquote>`);
    }
    if(button.dataset.run) {
      if(state.busy) {toast('Wait for the current analysis to finish before opening another.');return;}
      await showView('analysis'); setBusy(true); renderTeam('running'); state.tab='overview';
      await pollRun(button.dataset.run);
      if(state.run){$('#question').value=state.run.request.question;$('#dataset').value=state.run.request.dataset_id||'';state.documentIds=state.run.request.document_ids||[];$('#ollama-model').value=state.run.request.model||$('#ollama-model').value;$('#agent-mode').value=state.run.request.agent_mode;$('#decision-mode').value=(state.run.request.decision_mode==='jev'?'openai_decisions':state.run.request.decision_mode);updateMode();}
    }
    if(button.id==='revise-run') {
      state.parent=state.run.id; $('#question').value=state.run.request.question;
      $('#dataset').value=state.run.request.dataset_id||'';state.documentIds=state.run.request.document_ids||[];$('#ollama-model').value=state.run.request.model||$('#ollama-model').value; $('#agent-mode').value=state.run.request.agent_mode;
      $('#decision-mode').value=(state.run.request.decision_mode==='jev'?'openai_decisions':state.run.request.decision_mode); updateMode(); $('#question').focus();
      window.scrollTo({top:0,behavior:'smooth'}); toast('Update the question or data, then run again. Reviewer notes will be included.');
    }
  } catch(err) {setBusy(false);toast(err.message,true);}
});
document.addEventListener('keydown', event=>{
  if(!event.target.matches('[role="tab"]') || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
  event.preventDefault(); const tabs=$$('[role="tab"]'); let i=tabs.indexOf(event.target);
  i=event.key==='Home'?0:event.key==='End'?tabs.length-1:(i+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
  tabs[i].click();
});
$('#analysis-form').addEventListener('submit',async event=>{
  event.preventDefault(); if(state.busy)return;
  if(!$('#dataset').value&&!state.documentIds.length){toast('Select at least one document, or choose a dataset.',true);return;}
  const body={question:$('#question').value,dataset_id:$('#dataset').value||null,document_ids:state.documentIds,model:$('#agent-mode').value==='ollama'?$('#ollama-model').value:null,agent_mode:$('#agent-mode').value,decision_mode:$('#decision-mode').value,parent_run_id:state.parent};
  setBusy(true);renderTeam('running');$('#results').innerHTML='';state.tab='overview';
  try {const run=await api('/analyses',body);state.parent=null;await pollRun(run.id);}
  catch(err) {setBusy(false);renderTeam();$('#progress').classList.add('hidden');toast(err.message,true);$('#results').innerHTML=`<div class="notice error">${escapeHTML(err.message)}</div>`;}
});
document.addEventListener('submit',async event=>{
  if(event.target.id!=='review-form')return;
  event.preventDefault(); const decision=event.submitter.value;
  if(decision==='rejected'&&!$('#review-notes').value.trim()){toast('Add a note explaining what needs revision.',true);$('#review-notes').focus();return;}
  const body={decision,reviewer:$('#reviewer').value,notes:$('#review-notes').value};
  $$('button',event.target).forEach(b=>b.disabled=true);
  try {state.run=await api('/analyses/'+state.run.id+'/review',body);renderResults();toast(decision==='approved'?'Approval saved. Your brief is ready to export.':'Revision requested. Your notes are saved.');}
  catch(err){toast(err.message,true);$$('button',event.target).forEach(b=>b.disabled=false);}
});
$('#csv-form').addEventListener('submit',async event=>{
  event.preventDefault(); const button=$('button',event.target), file=$('#csv-file').files[0];
  if(file.size>2_000_000){toast('Please choose a CSV smaller than 2 MB.',true);return;}
  button.disabled=true;
  try {const d=await api('/datasets',{name:$('#csv-name').value,content:await file.text()});event.target.reset();await loadSources(d.id);toast(`Imported ${d.row_count} rows. The new dataset is selected in the studio.`);}
  catch(err){toast(err.message,true);} finally{button.disabled=false;}
});
$('#document-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=$('button',event.target);button.disabled=true;
  try {await api('/documents',{title:$('#doc-title').value,source:$('#doc-source').value||'User-provided document',content:$('#doc-content').value,dataset_id:$('#doc-dataset').value||null});event.target.reset();await loadSources();toast('Document saved and indexed for retrieval.');}
  catch(err){toast(err.message,true);}finally{button.disabled=false;}
});
$('#doc-file').addEventListener('change',async event=>{
  const file=event.target.files[0];if(!file)return;
  if(file.size>2000000){toast('Please choose a document smaller than 2 MB.',true);event.target.value='';return;}
  $('#doc-content').value=await file.text();if(!$('#doc-title').value)$('#doc-title').value=file.name;
});
$('#run-benchmark').addEventListener('click',async()=>{
  const b=$('#run-benchmark');b.disabled=true;b.textContent='Running five scenarios…';
  try {renderBenchmark(await api('/benchmarks',{}));toast('Benchmark complete. Results are saved.');}
  catch(err){toast(err.message,true);}finally{b.disabled=false;b.innerHTML='Run benchmark <span>↗</span>';}
});
$('#refresh-history').addEventListener('click',()=>loadHistory().catch(err=>toast(err.message,true)));
$('#refresh-models').addEventListener('click',()=>refreshModels().catch(err=>toast(err.message,true)));
$('#agent-mode').addEventListener('change',()=>{
  if($('#decision-mode').value!=='openai_decisions') $('#decision-mode').value=$('#agent-mode').value==='local'?'baseline':'llm';updateMode();
});
$('#vision-provider').addEventListener('change',()=>$('#vision-model-field').classList.toggle('hidden',$('#vision-provider').value!=='ollama'));
$('#analysis-documents').addEventListener('change',()=>{
  state.documentIds=$$('input:checked',$('#analysis-documents')).map(el=>el.value);renderDocumentPicker();
});
$('#file-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const files=[...$('#upload-files').files], button=$('button[type="submit"]',event.target);
  if(files.length>64){toast('Choose at most 64 files at a time.',true);return;}
  const scope=$('#upload-dataset').value, category=$('#upload-category').value;
  const vision=$('#vision-provider').value, visionModel=$('#vision-model').value;
  button.disabled=true;$('#upload-results').innerHTML='';
  const documents=[],datasets=[];
  try {
    for(const file of files) {
      const row=document.createElement('p');row.className='upload-result';row.textContent=`Processing ${file.name}…`;$('#upload-results').append(row);
      try {
        if(file.size>state.config.max_file_bytes) throw new Error('File exceeds 25 MB.');
        const form=new FormData();form.append('file',file);form.append('dataset_id',scope);form.append('category',category);
        form.append('vision_provider',vision);form.append('vision_model',visionModel);
        const response=await fetch('/api/uploads',{method:'POST',body:form});const result=await response.json();
        if(!response.ok) throw new Error(typeof result.detail==='string'?result.detail:'The file could not be processed.');
        documents.push(...result.documents.map(d=>d.id));datasets.push(...result.datasets.map(d=>d.id));
        row.textContent=`${file.name}: ${result.status==='duplicate'?'already imported':`ready · ${result.documents.length} document, ${result.datasets.length} data tables`}`;
        for(const warning of result.warnings||[]){const note=document.createElement('small');note.textContent=warning;row.append(note);}
      } catch(err){row.classList.add('failed');row.textContent=`${file.name}: ${err.message}`;}
    }
    await loadSources(scope||datasets[0]||'');
    if(documents.length){state.documentIds=documents;updateMode();}
    toast('File processing finished. Review the status for each file.');
  } finally {button.disabled=false;}
});
$('#close-dialog').addEventListener('click',()=>$('#source-dialog').close());
$('#source-dialog').addEventListener('click',event=>{if(event.target===$('#source-dialog')) {const r=event.target.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)event.target.close();}});
for(const id of ['dataset','decision-mode'])$('#'+id).addEventListener('change',updateMode);

async function init() {
  renderTeam();state.config=await api('/config');
  $('#agent-mode option[value="live"]').disabled=state.config.local_only||!state.config.live_agents_available;
  $('#decision-mode option[value="openai_decisions"]').disabled=state.config.local_only||!state.config.openai_decisions_available;
  $('#provider-note').textContent=(state.config.live_agents_available?'Live LLM agents are configured.':'Live agents: configure OPENAI_API_KEY and OPENAI_MODEL in .env.')+' '+(state.config.openai_decisions_available?'OpenAI Decisions is configured.':'OpenAI Decisions uses OPENAI_API_KEY and OPENAI_DECISION_MODEL.');
  $('#scenario-list').innerHTML=state.config.scenarios.map((s,i)=>`<button class="scenario" data-scenario="${s.id}" data-dataset="${s.dataset_id}"><span class="scenario-top"><span>0${i+1}</span><span>${escapeHTML(s.tag)}</span></span><b>${escapeHTML(s.title)}</b><span class="scenario-arrow">↗</span></button>`).join('');
  await refreshModels();await loadSources('demo_pricing');$('#question').value=state.config.scenarios[0].question;
  await showView(location.hash.slice(1)||'analysis');
}
init().catch(err=>{toast('Could not connect to the server: '+err.message,true);$('#run-button').disabled=true;});

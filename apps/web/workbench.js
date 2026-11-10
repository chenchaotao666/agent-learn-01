const $ = s => document.querySelector(s);
let currentRun, currentFeedback, recording, microphone, voiceMessage, polling;
const show = value => { $('#alert').hidden = !value; $('#alert').textContent = value; };
const pretty = value => JSON.stringify(value, null, 2);
async function request(path, body, method = 'POST') {
  const options = body === undefined ? {method:'GET'} : {method, headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map(x=>x.msg).join('；') : data.detail || '请求失败');
  return data;
}
function guarded(fn) { return async event => { event?.preventDefault(); try { await fn(event); } catch(e) { show(e.message); } }; }
function option(parent, value, text) { const el=document.createElement('option'); el.value=value;el.textContent=text;parent.append(el); }
function item(parent, label, action) { const box=document.createElement('div');box.className='item'; const b=document.createElement('button');b.textContent=label;b.onclick=guarded(action);box.append(b);parent.append(box);return box; }
const ttlLabel=document.createElement('label');ttlLabel.textContent='记忆有效天数（留空表示长期有效）';
const ttlInput=document.createElement('input');ttlInput.id='memory-ttl';ttlInput.type='number';ttlInput.min='1';ttlInput.max='3650';ttlLabel.append(ttlInput);
$('#memory-form').insertBefore(ttlLabel,$('#memory-form button'));
const evalModeLabel=document.createElement('label');evalModeLabel.textContent='评测召回策略';
const evalMode=document.createElement('select');evalMode.id='eval-mode';
[['vector','仅向量'],['keyword','仅关键词'],['hybrid','融合召回']].forEach(([v,t])=>option(evalMode,v,t));
evalModeLabel.append(evalMode);$('#evaluate').before(evalModeLabel);
const compareBox=document.createElement('div');compareBox.innerHTML='<h3>同数据集结果对比</h3><label>基准<select id="compare-first"></select></label><label>对照<select id="compare-second"></select></label><button id="compare-evals">对比召回变化</button><pre id="compare-result"></pre>';
$('#eval-result').after(compareBox);
async function loadEvaluations(){const list=await request('/v1/evaluations');for(const id of ['compare-first','compare-second']){$('#'+id).replaceChildren();list.forEach(r=>option($('#'+id),r.id,`${r.id.slice(0,8)} · ${r.pipeline?.mode||'旧版'} · ${r.retrieval_pass}/${r.total}`));}}
$('#compare-evals').onclick=guarded(async()=>{$('#compare-result').textContent=pretty(await request('/v1/evaluations/compare?'+new URLSearchParams({first:$('#compare-first').value,second:$('#compare-second').value})));});
document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>{
  document.querySelectorAll('.panel').forEach(panel=>panel.hidden=panel.id!==button.dataset.tab);
  document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('selected',b===button));
});
const statuses={created:'待执行',running:'执行中',awaiting_input:'等待补充',awaiting_confirmation:'等待确认',succeeded:'已提交',failed:'执行失败',cancelled:'已取消'};
$('#retrieval-form').onsubmit=guarded(async()=>{
  const button=$('#retrieval-form button');button.disabled=true;
  try {$('#retrieval-result').textContent=pretty(await request('/v1/search/inspect',{
    query:$('#retrieval-query').value,mode:$('#retrieval-mode').value,rewrite:$('#retrieval-rewrite').value,
    rerank:$('#retrieval-rerank').value,top_k:3}));}finally{button.disabled=false;}
});
async function loadJobs(){
  const jobs=await request('/v1/ingestions');$('#ingestion-list').replaceChildren();
  const names={queued:'等待 Worker',running:'处理中',published:'已发布',failed:'失败',cancelled:'已取消'};
  for(const job of jobs){const row=document.createElement('div');row.className='item';const text=document.createElement('p');
    text.textContent=`${job.filename} · ${names[job.status]} · ${job.strategy} · 尝试 ${job.attempts} 次${job.error?' · '+job.error:''}`;row.append(text);
    for(const action of job.status==='failed'?['retry','cancel']:['queued','running'].includes(job.status)?['cancel']:[]){const b=document.createElement('button');b.textContent=action==='retry'?'重试':'取消';b.onclick=guarded(async()=>{await request(`/v1/ingestions/${job.id}/${action}`,{});await loadJobs();});row.append(b);}
    $('#ingestion-list').append(row);
  }
}
$('#refresh-jobs').onclick=guarded(loadJobs);
$('#ingestion-form').onsubmit=guarded(async()=>{
  const button=$('#ingestion-form button');button.disabled=true;
  try{const file=$('#ingestion-file').files[0];if(!file)throw new Error('请选择文档');
    const form=new FormData();form.append('file',file);form.append('strategy',$('#ingestion-strategy').value);form.append('client_request_id',crypto.randomUUID());
    const response=await fetch('/v1/ingestions/upload',{method:'POST',body:form});const data=await response.json();
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'入库参数无效');
    await loadJobs();show('已进入持久化入库队列。可刷新查看 Worker 处理结果。');
  }finally{button.disabled=false;}
});
const actions={get_budget:'查询预算',search_knowledge:'检索制度',calculate_gap:'计算差额',create_draft:'生成草稿',replan:'重新规划'};
function renderRun(run) {
  currentRun=run; $('#run-status').textContent=statuses[run.status]||run.status;
  $('#run-summary').textContent=run.error||run.summary||`任务 ${run.id} · 计划 v${run.plan_version} · ${run.model_mode}`;
  $('#steps').replaceChildren();run.steps.forEach(s=>{const li=document.createElement('li');li.textContent=`${actions[s.action]||s.action}${s.reason?'：'+s.reason:''}`;$('#steps').append(li);});
  $('#draft').textContent=run.draft ? pretty({...run.draft,approval_id:run.approval_id,skill:run.skill?.name,handoff:run.handoff}) : '';
  $('#run-controls').hidden=false;$('#confirm').hidden=run.status!=='awaiting_confirmation';
  $('#resume').disabled=!['created','failed','awaiting_input'].includes(run.status);
  $('#revise').disabled=['running','succeeded','cancelled'].includes(run.status);$('#cancel').disabled=['succeeded','cancelled'].includes(run.status);
}
async function loadRuns() { const runs=await request('/v1/runs');$('#run-list').replaceChildren();runs.forEach(r=>item($('#run-list'),`${r.project_id||'待选项目'} · ${statuses[r.status]} · ${r.amount||'待填金额'}`,()=>watchRun(r.id))); }
async function watchRun(id) {
  clearTimeout(polling);const run=await request(`/v1/runs/${id}`);renderRun(run);
  if(['running','created'].includes(run.status)) polling=setTimeout(()=>watchRun(id).catch(e=>show(e.message)),1000);
  else await loadRuns();
}
$('#run-form').onsubmit=guarded(async()=>{
  const button=$('#run-form button.primary');button.disabled=true;
  try {const run=await request('/v1/runs',{goal:$('#goal').value,project_id:$('#project').value||null,amount:$('#amount').value||null,mode:$('#run-mode').value,client_request_id:crypto.randomUUID()});await watchRun(run.id);}
  finally {button.disabled=false;}
});
$('#seed-lab').onclick=guarded(async()=>{const r=await request('/v1/demo/seed',{});show(`已导入 ${r.imported} 份，共 ${r.total} 份制度。`);});
$('#confirm').onclick=guarded(async()=>{const r=await request(`/v1/runs/${currentRun.id}/confirm`,{version:currentRun.version,draft_hash:currentRun.draft_hash});renderRun(r);await loadRuns();});
$('#resume').onclick=guarded(async()=>{await request(`/v1/runs/${currentRun.id}/resume`,{});await watchRun(currentRun.id);});
$('#revise').onclick=guarded(async()=>{await request(`/v1/runs/${currentRun.id}/input`,{version:currentRun.version,project_id:$('#project').value||null,amount:$('#amount').value||null});await watchRun(currentRun.id);});
$('#cancel').onclick=guarded(async()=>{renderRun(await request(`/v1/runs/${currentRun.id}/cancel`,{}));await loadRuns();});
async function loadConversations(){const list=await request('/v1/conversations');$('#conversation-select').replaceChildren();list.forEach(c=>option($('#conversation-select'),c.id,c.title));}
$('#load-conversation').onclick=guarded(async()=>{
  const cid=$('#conversation-select').value;if(!cid)throw new Error('请先在知识问答页完成一次对话。');
  const messages=await request(`/v1/conversations/${cid}`);$('#memory-source').replaceChildren();$('#feedback-message').replaceChildren();
  messages.forEach(m=>{const select=m.role==='user'?$('#memory-source'):$('#feedback-message');option(select,m.id,`${m.id} · ${m.content.slice(0,70)}`);});
  $('#conversation-context').textContent=pretty(await request(`/v1/conversations/${cid}/context`));
});
async function loadMemories(){
  const list=await request('/v1/memories');$('#memory-list').replaceChildren();
  list.forEach(m=>{const row=document.createElement('div');row.className='item';const p=document.createElement('p');p.textContent=`${m.project_id||'全局'} · ${m.value}`;row.append(p);
    const edit=document.createElement('button');edit.textContent='修正';edit.onclick=guarded(async()=>{const value=prompt('修正记忆内容',m.value);if(value){await request(`/v1/memories/${m.id}`,{version:m.version,value},'PATCH');await loadMemories();}});
    const del=document.createElement('button');del.textContent='遗忘';del.onclick=guarded(async()=>{await request(`/v1/memories/${m.id}`,{},'DELETE');await loadMemories();});row.append(edit,del);$('#memory-list').append(row);});
}
$('#memory-form').onsubmit=guarded(async()=>{await request('/v1/memories',{value:$('#memory-value').value,project_id:$('#memory-project').value||null,source_message_id:Number($('#memory-source').value),confirmed:$('#memory-confirm').checked,ttl_days:$('#memory-ttl').value?Number($('#memory-ttl').value):null});await loadMemories();show('记忆已保存。');});
async function loadFeedback(){const list=await request('/v1/feedback');$('#feedback-list').replaceChildren();list.forEach(f=>item($('#feedback-list'),`${f.status} · ${f.question.slice(0,60)}`,async()=>{currentFeedback=f;$('#review-id').value=f.id;$('#review-answer').value=f.review?.expected_answer||f.correction;$('#review-evidence').value=(f.review?.evidence_ids||f.actual_citations).join(',');$('#review-reason').value=f.review?.reason||'';}));}
$('#feedback-form').onsubmit=guarded(async()=>{await request('/v1/feedback',{message_id:Number($('#feedback-message').value),category:$('#feedback-category').value,correction:$('#feedback-correction').value});await loadFeedback();show('反馈已记录，等待人工复核。');});
$('#review-form').onsubmit=guarded(async()=>{if(!currentFeedback)throw new Error('先选择反馈记录');currentFeedback=await request(`/v1/feedback/${currentFeedback.id}/review`,{version:currentFeedback.version,decision:$('#review-decision').value,expected_answer:$('#review-answer').value,evidence_ids:$('#review-evidence').value.split(',').map(s=>s.trim()).filter(Boolean),reason:$('#review-reason').value});await loadFeedback();show('复核已保存。');});
async function loadDatasets(){const list=await request('/v1/datasets');$('#dataset-select').replaceChildren();list.forEach(d=>option($('#dataset-select'),d.id,`${d.id.slice(0,10)} · ${d.cases.length} 条回归样本`));}
$('#publish').onclick=guarded(async()=>{const d=await request('/v1/datasets',{});await loadDatasets();show(`已发布固定数据集 ${d.id}`);});
$('#evaluate').onclick=guarded(async()=>{if(!$('#dataset-select').value)throw new Error('请先发布数据集');$('#evaluate').disabled=true;try{$('#eval-result').textContent=pretty(await request('/v1/evaluations',{dataset_id:$('#dataset-select').value,top_k:3,mode:$('#eval-mode').value}));await loadEvaluations();}finally{$('#evaluate').disabled=false;}});
$('#translation-form').onsubmit=guarded(async()=>{show('正在翻译…');$('#translation-result').textContent=pretty(await request('/v1/translations',{text:$('#translation-text').value,target_language:$('#target-language').value}));show('翻译结果已返回，请复核数字和术语。');});
async function transcribe(blob,name){show('正在转写…');const form=new FormData();form.append('file',blob,name);const response=await fetch('/v1/audio/transcriptions',{method:'POST',body:form});const result=await response.json();if(!response.ok)throw new Error(result.detail);$('#transcript').value=result.text;show(result.note);}
$('#audio-file').onchange=guarded(async()=>{const file=$('#audio-file').files[0];if(file)await transcribe(file,file.name);});
$('#record').onclick=guarded(async()=>{
  if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder)throw new Error('浏览器不支持录音，请上传音频文件。');
  microphone=await navigator.mediaDevices.getUserMedia({audio:true});const chunks=[];
  recording=new MediaRecorder(microphone);recording.ondataavailable=e=>chunks.push(e.data);
  recording.onstop=()=>{microphone.getTracks().forEach(t=>t.stop());transcribe(new Blob(chunks,{type:recording.mimeType}),'recording.webm').catch(e=>show(e.message));};
  recording.start();$('#record').disabled=true;$('#record-stop').disabled=false;show('录音中，点击“停止并转写”结束。');
});
$('#record-stop').onclick=()=>{recording?.stop();$('#record').disabled=false;$('#record-stop').disabled=true;};
$('#voice-ask').onclick=guarded(async()=>{
  const text=$('#transcript').value.trim();if(!text)throw new Error('请先转写或输入问题');voiceMessage=null;$('#voice-speak').disabled=true;$('#voice-answer').textContent='';
  const response=await fetch('/v1/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:text})});
  if(!response.ok)throw new Error('提问失败');const reader=response.body.getReader(), decoder=new TextDecoder();let buffer='',cid,completed=false;
  while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value,{stream:!done});let p;while((p=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,p);buffer=buffer.slice(p+2);const lines=frame.split('\n'),event=lines[0].slice(7),data=JSON.parse(lines[1].slice(6));if(event==='meta')cid=data.conversation_id;if(event==='delta')$('#voice-answer').textContent+=data.text;if(event==='error')show(data.message);if(event==='done')completed=data.status==='complete';}if(done)break;}
  if(completed){const list=await request(`/v1/conversations/${cid}`);voiceMessage=list.at(-1).id;$('#voice-speak').disabled=false;}
});
$('#voice-speak').onclick=guarded(async()=>{const result=await request('/v1/audio/speech',{message_id:voiceMessage,voice:'alloy'});$('#audio-player').src=result.url;await $('#audio-player').play();});
guarded(async()=>{const health=await request('/health');$('#configuration').textContent=`${health.model_mode} · ${health.chat_model} · ${health.configured?'已配置':'待填写密钥'}`;if(!health.configured)show('请在项目 .env 填入 DEEPSEEK_API_KEY 后重启服务。也可显式设置 MODEL_MODE=mock 做离线实验。');$('#speech-config').textContent=`ASR ${health.asr_configured?'已配置':'待配置'} / TTS ${health.tts_configured?'已配置':'待配置'}。语音服务与 DeepSeek 对话服务分开配置。`;await Promise.all([loadRuns(),loadConversations(),loadMemories(),loadFeedback(),loadDatasets(),loadJobs(),loadEvaluations()]);})();

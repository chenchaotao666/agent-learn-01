const $ = (selector) => document.querySelector(selector);
let conversationId = null;
let controller = null;
let updatingDocument = null;
let busy = false;
const welcomeTemplate = $('#welcome').cloneNode(true);

function toast(message) { $('#toast').hidden = !message; $('#toast').textContent = message; }
async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { detail = '服务未返回有效响应'; }
    if (Array.isArray(detail)) detail = detail.map(x => x.msg).join('；');
    throw new Error(detail || `请求失败 (${response.status})`);
  }
  return response;
}
function scrollBottom() { $('#messages').scrollTop = $('#messages').scrollHeight; }
function setBusy(value) {
  busy = value;
  for (const id of ['send', 'new-chat', 'seed', 'upload']) $(`#${id}`).disabled = value;
  $('#stop').hidden = !value;
  $('#question').disabled = value;
  document.querySelectorAll('.doc-actions button,.history button').forEach(b => b.disabled = value);
}
function addMessage(role, content = '', mode = '') {
  $('#welcome')?.remove();
  const node = document.createElement('article'); node.className = `message ${role}`;
  const label = document.createElement('div'); label.className = 'message-label';
  label.textContent = role === 'user' ? '你' : `知行 · ${mode === 'mock' ? '模拟回答' : '模型回答'}`;
  const body = document.createElement('div'); body.className = 'message-body'; body.textContent = content;
  const sources = document.createElement('div'); sources.className = 'sources';
  const status = document.createElement('div'); status.className = 'message-status';
  node.append(label, body, sources, status); $('#messages').append(node); scrollBottom();
  return {node, body, sources, status, label};
}
function addSources(target, hits) {
  target.replaceChildren();
  hits.forEach((hit, i) => {
    const button = document.createElement('button'); button.className = 'source';
    button.textContent = hit.unavailable ? `[${i + 1}] 来源已删除` : `[${i + 1}] ${hit.title}`;
    if (!hit.unavailable) {
      const meta = document.createElement('small');
      meta.textContent = `v${hit.version} · 第 ${hit.start_line}–${hit.end_line} 行`;
      button.append(meta);
      button.onclick = async () => {
        try {
          const source = await (await api(`/v1/citations/${hit.id}`)).json();
          $('#source-title').textContent = source.title;
          $('#source-meta').textContent = `${source.filename} · v${source.version} · 第 ${source.start_line}–${source.end_line} 行${source.version !== source.active_version ? ' · 历史版本' : ''}`;
          $('#source-text').textContent = source.text; $('#source-dialog').showModal();
        } catch (error) { toast(error.message); }
      };
    } else button.disabled = true;
    target.append(button);
  });
}
async function loadDocuments() {
  const documents = await (await api('/v1/documents')).json();
  $('#doc-count').textContent = documents.length; $('#document-list').replaceChildren();
  for (const doc of documents) {
    const node = document.createElement('div'); node.className = 'doc';
    const title = document.createElement('div'); title.className = 'doc-title'; title.textContent = doc.title;
    const meta = document.createElement('div'); meta.className = 'doc-meta';
    const detail = document.createElement('span'); detail.textContent = `v${doc.active_version} · ${doc.chunk_count} 个片段`;
    const actions = document.createElement('span'); actions.className = 'doc-actions';
    const update = document.createElement('button'); update.textContent = '更新'; update.disabled = busy;
    update.onclick = () => { updatingDocument = doc; $('#file').click(); };
    const remove = document.createElement('button'); remove.textContent = '删除'; remove.disabled = busy;
    remove.onclick = async () => {
      if (!confirm(`删除“${doc.title}”？删除后不会再被检索到。`)) return;
      try { await api(`/v1/documents/${doc.id}`, {method: 'DELETE'}); await loadDocuments(); }
      catch (error) { toast(error.message); }
    };
    actions.append(update, remove); meta.append(detail, actions); node.append(title, meta); $('#document-list').append(node);
  }
}
async function loadHistory() {
  const history = await (await api('/v1/conversations')).json(); $('#history').replaceChildren();
  history.slice(0, 12).forEach(item => {
    const button = document.createElement('button'); button.textContent = item.title; button.disabled = busy;
    button.onclick = async () => {
      if (busy) return;
      try {
        const messages = await (await api(`/v1/conversations/${item.id}`)).json();
        conversationId = item.id; $('#messages').replaceChildren();
        for (const message of messages) {
          const node = addMessage(message.role, message.content, message.mode);
          addSources(node.sources, message.citations);
          if (message.status !== 'complete') node.status.textContent = '此回答未完成';
        }
      } catch (error) { toast(error.message); }
    };
    $('#history').append(button);
  });
}
$('#close-source').onclick = () => $('#source-dialog').close();
$('#new-chat').onclick = () => {
  conversationId = null; $('#messages').replaceChildren(welcomeTemplate.cloneNode(true));
  bindSuggestions(); $('#question').focus(); toast('');
};
$('#upload').onclick = () => { updatingDocument = null; $('#file').click(); };
$('#file').onchange = async () => {
  const file = $('#file').files[0]; if (!file) return;
  try {
    if (file.size > 10000000) throw new Error('支持 10 MB 以内的文件');
    setBusy(true); $('#stop').hidden = true; toast('正在解析并导入文档…');
    const form = new FormData(); form.append('file', file);
    if (updatingDocument) form.append('document_id', updatingDocument.id);
    const result = await (await api('/v1/documents/upload', {method: 'POST', body: form})).json();
    toast(result.deduplicated ? '文档内容未变化，已跳过重复导入。' : `文档 v${result.version} 已发布。`);
    await loadDocuments();
  } catch (error) { toast(error.message); }
  finally { $('#file').value = ''; updatingDocument = null; setBusy(false); }
};
$('#seed').onclick = async () => {
  try {
    setBusy(true); $('#stop').hidden = true; toast('正在导入演示资料…');
    const result = await (await api('/v1/demo/seed', {method: 'POST'})).json();
    await loadDocuments(); toast(`已导入 ${result.imported} 份，知识库共 ${result.total} 份文档。`);
  } catch (error) { toast(error.message); }
  finally { setBusy(false); }
};
function bindSuggestions() {
  document.querySelectorAll('[data-question]').forEach(button => button.onclick = () => {
    if (busy) return; $('#question').value = button.dataset.question; $('#question').focus();
  });
}
bindSuggestions();
$('#question').onkeydown = e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('#chat-form').requestSubmit(); }
};
$('#stop').onclick = () => controller?.abort();
$('#chat-form').onsubmit = async e => {
  e.preventDefault(); const question = $('#question').value.trim(); if (!question || busy) return;
  setBusy(true); toast(''); addMessage('user', question); $('#question').value = '';
  const reply = addMessage('assistant'); reply.status.textContent = '正在检索资料…';
  controller = new AbortController(); let completed = false;
  try {
    const response = await api('/v1/chat', {method: 'POST', signal: controller.signal,
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({question, conversation_id: conversationId})});
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    function processFrame(frame) {
      const name = frame.split('\n').find(line => line.startsWith('event: '))?.slice(7);
      const raw = frame.split('\n').filter(line => line.startsWith('data: ')).map(line => line.slice(6)).join('\n');
      if (!raw) return; const data = JSON.parse(raw);
      if (name === 'meta') { conversationId = data.conversation_id; reply.label.textContent = `知行 · ${data.mode === 'mock' ? '模拟回答' : '模型回答'}`; }
      if (name === 'sources') { addSources(reply.sources, data); reply.status.textContent = '正在生成回答…'; }
      if (name === 'delta') { reply.body.textContent += data.text; scrollBottom(); }
      if (name === 'error') reply.status.textContent = data.message;
      if (name === 'done') { completed = true; if (data.status === 'complete') reply.status.textContent = ''; }
    }
    while (true) {
      const {value, done} = await reader.read();
      buffer += decoder.decode(value, {stream: !done});
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) { processFrame(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2); }
      if (done) break;
    }
    if (!completed) throw new Error('连接中断，回答未完成。');
  } catch (error) {
    reply.status.textContent = error.name === 'AbortError' ? '已停止生成；已有输出保留在会话中。' : error.message;
  } finally {
    controller = null; setBusy(false); $('#question').focus();
    try { await loadHistory(); } catch (error) { toast(error.message); }
  }
};
(async () => {
  try {
    const health = await (await api('/health')).json();
    $('#mode').textContent = health.model_mode === 'mock' ? '● 模拟模式' : `● ${health.chat_model}`;
    $('#notice').textContent = `${health.model_mode === 'mock' ? '当前使用 Mock 摘录原文' : `当前使用 ${health.model_mode} / Pydantic AI`} · ${health.embedding_mode === 'hash' ? '字符向量基线，不具备语义理解能力' : 'Ollama 语义向量检索'}。${health.configured ? '导入资料后开始提问。' : '请在 .env 填入 DEEPSEEK_API_KEY 后重启服务。'}`;
    await Promise.all([loadDocuments(), loadHistory()]);
  } catch (error) { toast(error.message); $('#mode').textContent = '连接失败'; }
})();

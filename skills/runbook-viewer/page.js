'use strict';
// The marks of the run's own status, for the tab title; an attempt's mark comes with it from the server.
const RUN_MARKS = { ready: '✓', running: '●', waiting_for_human: '?', failed: '✗' };
const OPEN = ['running', 'waiting'];
const POLL_MS = 2000;
// Run statuses with a chip colour of their own; running and any other end status stay grey.
const CHIPS = { ready: 'ok', waiting_for_human: 'attention', needs_attention: 'attention', failed: 'failed' };

// Text between raw HTML tags arrives as an `escaped` token that marked would emit as is.
marked.use({
  renderer: {
    html({ text }) { return esc(text); },
    text(token) { return token.escaped && !token.tokens ? esc(token.text) : false; },
  },
});

let data = null;
let clockOffset = 0;
let follow = true;
let selection = null;
// shown.key is null while the column holds an incomplete render of shown.selection, so the next poll retries.
let shown = { key: null, selection: null };
let pendingKey = null;
let navKey = null;
const fileCache = new Map();

const $ = (id) => document.getElementById(id);

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function parseTime(s) {
  const t = typeof s === 'string' ? Date.parse(s) : NaN;
  return Number.isNaN(t) ? null : t;
}

function duration(call, now) {
  const start = parseTime(call.started_at);
  const stop = OPEN.includes(call.status) ? now : parseTime(call.ended_at);
  if (start === null || stop === null) return '';
  const total = Math.max(0, Math.floor((stop - start) / 1000));
  const h = Math.floor(total / 3600), m = Math.floor(total / 60) % 60, s = total % 60;
  const pad = (n) => String(n).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function serverNow() {
  return Date.now() + clockOffset;
}

function followTarget() {
  const calls = data.calls;
  const open = calls.find((c) => OPEN.includes(c.status));
  const last = open || calls[calls.length - 1];
  if (last) return { kind: 'call', label: last.label };
  return data.run_files.length ? { kind: 'file', name: data.run_files[0] } : null;
}

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  if (params.has('call')) return { kind: 'call', label: params.get('call') };
  if (params.has('file')) return { kind: 'file', name: params.get('file') };
  return null;
}

function select(target) {
  follow = false;
  selection = target;
  history.replaceState(null, '', '#' + new URLSearchParams(target.kind === 'call' ? { call: target.label } : { file: target.name }));
  render();
}

function setFollow() {
  follow = true;
  history.replaceState(null, '', location.pathname);
  render();
}

function sameSelection(a, b) {
  return a && b && a.kind === b.kind && a.label === b.label && a.name === b.name;
}

function renderHeader() {
  const st = data.state;
  $('name').textContent = data.name;
  $('runbook').textContent = st.runbook || '';
  const status = $('status');
  status.textContent = st.status || '';
  status.className = CHIPS[st.status] ? 'chip-' + CHIPS[st.status] : '';
  document.title = `${RUN_MARKS[st.status] || '·'} ${data.name}`;
  const inputs = Object.entries(st.inputs || {});
  $('inputs').innerHTML = inputs.map(([k, v]) =>
    `<dt>${esc(k)}</dt><dd>${esc(typeof v === 'string' ? v : JSON.stringify(v))}</dd>`).join('');
  $('inputs-names').textContent = inputs.map(([k]) => k).join(', ');
}

function renderNav() {
  const key = JSON.stringify([data.calls, data.run_files, selection, follow]);
  if (key === navKey) return;
  navKey = key;
  const now = serverNow();
  const rows = data.calls.map((c) => {
    const live = c.status === 'running' ? ' live' : '';
    const selected = selection && selection.kind === 'call' && selection.label === c.label ? ' selected' : '';
    const dur = OPEN.includes(c.status) && c.started_at ? ` data-start="${esc(c.started_at)}"` : '';
    return `<div class="row ${esc(c.status)}${selected}" role="button" tabindex="0" data-call="${esc(c.label)}">` +
      `<span class="mark-${esc(c.status)}${live}">${esc(c.mark)}</span>` +
      `<span>${esc(c.name)}</span><span class="exec">${esc(c.executor || '')}</span>` +
      `<span class="dur"${dur}>${duration(c, now)}</span><span title="${esc(c.note)}">${esc(c.note)}</span></div>`;
  });
  $('calls').innerHTML = rows.join('') || '<div class="empty">No step has launched yet.</div>';
  $('run-files').innerHTML = data.run_files.map((name) => {
    const selected = selection && selection.kind === 'file' && selection.name === name ? ' selected' : '';
    return `<button type="button" class="file-link${selected}" data-file="${esc(name)}">${esc(name)}</button>`;
  }).join('') || '<div class="empty">None.</div>';
  const button = $('follow');
  button.setAttribute('aria-pressed', String(follow));
  button.textContent = follow ? 'following' : 'follow';
}

function fileMeta(name) {
  return data.files.find((f) => f.name === name);
}

async function fileHtml(name) {
  const meta = fileMeta(name);
  const cached = fileCache.get(name);
  if (cached && meta && cached.size === meta.size && cached.mtime === meta.mtime) return cached.html;
  const res = await fetch('/api/file?name=' + encodeURIComponent(name), { cache: 'no-store' });
  if (!res.ok) throw new Error(`${name}: ${res.status}`);
  const html = renderFile(name, await res.text());
  if (meta) fileCache.set(name, { size: meta.size, mtime: meta.mtime, html });
  return html;
}

function renderFile(name, text) {
  if (/\.md$/i.test(name)) return `<div class="md">${marked.parse(text)}</div>`;
  if (/\.(diff|patch)$/i.test(name)) {
    // Lines left in the current hunk, from its @@ header: inside a hunk `---`/`+++` are content, not file headers.
    let oldLeft = 0;
    let newLeft = 0;
    const lines = text.split('\n').map((line) => {
      let cls = '';
      if (line.startsWith('diff ')) {
        oldLeft = newLeft = 0;
        cls = 'head';
      } else if (line.startsWith('@@')) {
        const m = /^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@/.exec(line);
        oldLeft = m ? Number(m[1] ?? 1) : Infinity;
        newLeft = m ? Number(m[2] ?? 1) : Infinity;
        cls = 'hunk';
      } else if (oldLeft > 0 || newLeft > 0) {
        if (line.startsWith('+')) { newLeft--; cls = 'add'; }
        else if (line.startsWith('-')) { oldLeft--; cls = 'del'; }
        else if (!line.startsWith('\\')) { oldLeft--; newLeft--; }
      } else if (/^(\+\+\+|---|index )/.test(line)) cls = 'head';
      else if (line.startsWith('+')) cls = 'add';
      else if (line.startsWith('-')) cls = 'del';
      return `<span class="${cls}">${esc(line) || ' '}</span>`;
    });
    return `<pre class="diff">${lines.join('')}</pre>`;
  }
  return `<pre class="raw">${esc(text)}</pre>`;
}

function detailParts() {
  if (!selection) return null;
  if (selection.kind === 'file') {
    if (!data.run_files.includes(selection.name)) return null;
    return { title: selection.name, meta: '', log: [], files: [selection.name], ask: null };
  }
  const call = data.calls.find((c) => c.label === selection.label);
  if (!call) return null;
  let ask = null;
  if (call.status === 'waiting') {
    const prefix = `- ${call.label}: asked: `;
    const line = call.log.find((l) => l.startsWith(prefix));
    ask = line ? line.slice(prefix.length) : '';
  }
  const meta = [call.status, call.executor].filter(Boolean).join(' · ');
  return { title: call.name, meta, log: call.log, files: call.files, ask };
}

async function renderDetail() {
  const parts = detailParts();
  const key = JSON.stringify([selection, parts, parts && parts.files.map(fileMeta)]);
  if (key === shown.key) {
    pendingKey = null;
    return;
  }
  if (key === pendingKey) return;
  const detail = $('detail');
  const moved = !sameSelection(shown.selection, selection);
  if (!parts) {
    pendingKey = null;
    shown = { key, selection };
    detail.innerHTML = '<p class="empty">Select a call or a file.</p>';
    return;
  }
  pendingKey = key;
  let html = `<h2 class="detail-head">${esc(parts.title)}<span class="meta">${esc(parts.meta)}</span></h2>`;
  if (parts.ask !== null) {
    html += `<div class="ask"><strong>Waiting for the human.</strong><p>${esc(parts.ask)}</p>` +
      '<p>The answer is given in the chat with the orchestrator.</p></div>';
  }
  if (parts.log.length) html += `<ul class="log">${parts.log.map((l) => `<li>${esc(l.slice(2))}</li>`).join('')}</ul>`;
  const docs = await Promise.allSettled(parts.files.map(fileHtml));
  if (pendingKey !== key) return;
  pendingKey = null;
  const complete = docs.every((d) => d.status === 'fulfilled');
  if (!complete && !moved) return;
  html += parts.files.map((name, i) => {
    const body = docs[i].status === 'fulfilled' ? docs[i].value : `<p class="empty">${esc(name)} could not be read.</p>`;
    return `<div class="doc">${parts.files.length > 1 || parts.title !== name ? `<div class="doc-name">${esc(name)}</div>` : ''}${body}</div>`;
  }).join('');
  if (!parts.files.length && parts.ask === null) html += '<p class="empty">No output file yet.</p>';
  const top = detail.scrollTop;
  detail.innerHTML = html;
  detail.scrollTop = moved ? 0 : top;
  shown = { key: complete ? key : null, selection };
}

function render() {
  if (!data) return;
  if (follow) selection = followTarget();
  renderHeader();
  renderNav();
  renderDetail();
}

async function poll() {
  try {
    const res = await fetch('/api/state', { cache: 'no-store' });
    if (res.ok) {
      const first = !data;
      data = await res.json();
      // data.now is cut to the second: keep the largest sample so open durations do not step back.
      const offset = Date.parse(data.now) - Date.now();
      clockOffset = first || Math.abs(offset - clockOffset) > 2000 ? offset : Math.max(clockOffset, offset);
      $('notice').hidden = true;
      render();
    } else if (res.status !== 503) {
      // 503 is state.json caught mid-write, retried quietly; anything else, as a state of another format, is shown.
      $('notice').textContent = await res.text();
      $('notice').hidden = false;
    }
  } catch (e) {
    $('notice').textContent = 'The viewer is not reachable. Showing the last state it sent.';
    $('notice').hidden = false;
  }
  setTimeout(poll, POLL_MS);
}

function tick() {
  if (!data) return;
  const now = serverNow();
  for (const el of document.querySelectorAll('.dur[data-start]')) {
    el.textContent = duration({ status: 'running', started_at: el.dataset.start }, now);
  }
}

$('calls').addEventListener('click', (e) => {
  const row = e.target.closest('[data-call]');
  if (row) select({ kind: 'call', label: row.dataset.call });
});
$('calls').addEventListener('keydown', (e) => {
  const row = e.target.closest('[data-call]');
  if (row && (e.key === 'Enter' || e.key === ' ')) {
    e.preventDefault();
    select({ kind: 'call', label: row.dataset.call });
  }
});
$('run-files').addEventListener('click', (e) => {
  const link = e.target.closest('[data-file]');
  if (link) select({ kind: 'file', name: link.dataset.file });
});
$('follow').addEventListener('click', () => { if (!follow) setFollow(); });

const fromHash = readHash();
if (fromHash) { follow = false; selection = fromHash; }
poll();
setInterval(tick, 1000);

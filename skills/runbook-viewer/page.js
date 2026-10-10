'use strict';
// The marks of the run's own status, for the tab title; an attempt's mark comes with it from the server.
const RUN_MARKS = { ready: '✓', running: '●', waiting_for_human: '?', needs_attention: '!', failed: '✗' };
const OPEN = ['running', 'waiting'];
const GROUPS = ['parallel', 'foreach'];
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

// A row of the list is selected as { kind: 'row', id: its address }, a run file as { kind: 'file', name }.
function isRow(entry) {
  return !GROUPS.includes(entry.type);
}

// The innermost row holding the attempt: a branch inside an item holds it as the item does.
function rowTarget(label) {
  let found = null;
  for (const r of data.rows) {
    if (isRow(r) && r.attempts.includes(label) && (!found || r.depth > found.depth)) found = r;
  }
  return found ? { kind: 'row', id: found.id } : null;
}

function followTarget() {
  const calls = data.calls;
  const open = calls.find((c) => OPEN.includes(c.status));
  const last = open || calls[calls.length - 1];
  if (last) return rowTarget(last.label);
  return data.run_files.length ? { kind: 'file', name: data.run_files[0] } : null;
}

function readHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  if (params.has('row')) return { kind: 'row', id: params.get('row') };
  // A #call=<label> link: render() replaces it with the row holding that attempt.
  if (params.has('call')) return { kind: 'call', label: params.get('call') };
  if (params.has('file')) return { kind: 'file', name: params.get('file') };
  return null;
}

function writeHash(target) {
  history.replaceState(null, '', '#' + new URLSearchParams(target.kind === 'row' ? { row: target.id } : { file: target.name }));
}

function select(target) {
  follow = false;
  selection = target;
  writeHash(target);
  render();
}

function setFollow() {
  follow = true;
  history.replaceState(null, '', location.pathname);
  render();
}

function sameSelection(a, b) {
  return Boolean(a && b && a.kind === b.kind && a.id === b.id && a.label === b.label && a.name === b.name);
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

// A group's header, or a row: a call of main, or a branch or an item standing for every attempt in it.
function navLine(r, now) {
  const depth = ` style="--depth: ${Number(r.depth) || 0}"`;
  if (!isRow(r)) {
    return `<div class="group-head"><span></span><span${depth} title="${esc(r.header)}">${esc(r.header)}</span></div>`;
  }
  const live = r.status === 'running' ? ' live' : '';
  const selected = sameSelection(selection, { kind: 'row', id: r.id }) ? ' selected' : '';
  const dur = r.open && r.started_at ? ` data-start="${esc(r.started_at)}"` : '';
  const timed = { status: r.open ? 'running' : 'done', started_at: r.started_at, ended_at: r.ended_at };
  const step = r.step ? ` <span class="step">${esc(r.step)}</span>` : '';
  return `<div class="row ${esc(r.status)}${selected}" role="button" tabindex="0" data-row="${esc(r.id)}">` +
    `<span class="mark-${esc(r.status)}${live}">${esc(r.mark)}</span>` +
    `<span class="name"${depth}>${esc(r.label)}${step}</span><span class="exec">${esc(r.executor || '')}</span>` +
    `<span class="dur"${dur}>${duration(timed, now)}</span><span title="${esc(r.note)}">${esc(r.note)}</span></div>`;
}

function renderNav() {
  const key = JSON.stringify([data.rows, data.run_files, selection, follow]);
  if (key === navKey) return;
  navKey = key;
  const now = serverNow();
  $('calls').innerHTML = data.rows.map((r) => navLine(r, now)).join('') ||
    '<div class="empty">No step has launched yet.</div>';
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

// What the right column shows: a title, and a block per attempt in the row, headed by its label when the row is
// more than one attempt or a branch or an item.
function detailParts() {
  if (!selection) return null;
  if (selection.kind === 'file') {
    if (!data.run_files.includes(selection.name)) return null;
    return { title: selection.name, meta: '', blocks: [{ head: null, meta: '', log: [], files: [selection.name], ask: null }] };
  }
  const row = data.rows.find((r) => isRow(r) && r.id === selection.id);
  if (!row) return null;
  const calls = new Map(data.calls.map((c) => [c.label, c]));
  const blocks = row.attempts.filter((label) => calls.has(label)).map((label) => attemptBlock(calls.get(label)));
  if (row.type === 'call' && blocks.length === 1) {
    return { title: blocks[0].head, meta: blocks[0].meta, blocks: [{ ...blocks[0], head: null, meta: '' }] };
  }
  const title = row.id.startsWith('main/') ? row.id.slice('main/'.length) : row.id;
  return { title, meta: String(row.status).replace('_', ' '), blocks };
}

function attemptBlock(call) {
  let ask = null;
  if (call.status === 'waiting') {
    const prefix = `- ${call.label}: asked: `;
    const line = call.log.find((l) => l.startsWith(prefix));
    ask = line ? line.slice(prefix.length) : '';
  }
  const meta = [call.status, call.executor].filter(Boolean).join(' · ');
  return { head: call.name, meta, log: call.log, files: call.files, ask };
}

async function renderDetail() {
  const parts = detailParts();
  const key = JSON.stringify([selection, parts, parts && parts.blocks.map((b) => b.files.map(fileMeta))]);
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
    detail.innerHTML = '<p class="empty">Select a step or a file.</p>';
    return;
  }
  pendingKey = key;
  const files = parts.blocks.flatMap((b) => b.files);
  const docs = await Promise.allSettled(files.map(fileHtml));
  if (pendingKey !== key) return;
  pendingKey = null;
  const complete = docs.every((d) => d.status === 'fulfilled');
  if (!complete && !moved) return;
  const bodies = new Map(files.map((name, i) =>
    [name, docs[i].status === 'fulfilled' ? docs[i].value : `<p class="empty">${esc(name)} could not be read.</p>`]));
  let html = `<h2 class="detail-head">${esc(parts.title)}<span class="meta">${esc(parts.meta)}</span></h2>`;
  html += parts.blocks.map((block) => {
    let part = block.head === null ? '' : `<h3 class="block-head">${esc(block.head)}<span class="meta">${esc(block.meta)}</span></h3>`;
    if (block.ask !== null) {
      part += `<div class="ask"><strong>Waiting for the human.</strong><p>${esc(block.ask)}</p>` +
        '<p>The answer is given in the chat with the orchestrator.</p></div>';
    }
    if (block.log.length) part += `<ul class="log">${block.log.map((l) => `<li>${esc(l.slice(2))}</li>`).join('')}</ul>`;
    const title = block.head === null ? parts.title : block.head;
    part += block.files.map((name) =>
      `<div class="doc">${block.files.length > 1 || title !== name ? `<div class="doc-name">${esc(name)}</div>` : ''}${bodies.get(name)}</div>`).join('');
    if (!block.files.length && block.ask === null) part += '<p class="empty">No output file yet.</p>';
    return `<div class="block">${part}</div>`;
  }).join('');
  if (!parts.blocks.length) html += '<p class="empty">Nothing launched in it yet.</p>';
  const top = detail.scrollTop;
  detail.innerHTML = html;
  detail.scrollTop = moved ? 0 : top;
  shown = { key: complete ? key : null, selection };
}

function render() {
  if (!data) return;
  if (follow) selection = followTarget();
  if (selection && selection.kind === 'call') {
    const target = rowTarget(selection.label);
    if (target) {
      selection = target;
      writeHash(target);
    }
  }
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
  const row = e.target.closest('[data-row]');
  if (row) select({ kind: 'row', id: row.dataset.row });
});
$('calls').addEventListener('keydown', (e) => {
  const row = e.target.closest('[data-row]');
  if (row && (e.key === 'Enter' || e.key === ' ')) {
    e.preventDefault();
    select({ kind: 'row', id: row.dataset.row });
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

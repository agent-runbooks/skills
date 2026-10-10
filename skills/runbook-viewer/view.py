#!/usr/bin/env python3
"""Shows a runbook run: a text status, or a page on localhost. Reads the run directory and never writes to it.

    view.py <run> --status [--tail N]
                                 print the text status, or only its last N rows
    view.py <run> [--port N] [--idle-minutes N]
                                 serve the page on 127.0.0.1 until interrupted or idle

<run> is a run directory, the one holding state.json, or a directory of runs such as .agent-runbooks/runs, in which
case the run whose state.json was modified last is shown.
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 9):
    sys.exit(f'view.py needs Python 3.9 or newer; {sys.executable} is {sys.version.split()[0]}')

import argparse
import contextlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = 'state.json'
PROGRESS = 'progress.md'
FORMAT = 2
TIME_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
MARKS = {
    'done': '✓',
    'running': '●',
    'waiting': '?',
    'failed': '✗',
    'interrupted': '✗',
    'cancelled': '✗',
    'blocked': '!',
    'not_started': '·',
}
OPEN = ('running', 'waiting')
LAUNCHES = ('step', 'human')
GROUPS = ('parallel', 'foreach')
WAITING = 'waiting for the human'
# A group's header counts its branches or items by status, in this order; failed lists their keys.
COUNTS = (
    ('done', ('done',)),
    ('failed', ('failed', 'blocked', 'interrupted')),
    ('cancelled', ('cancelled',)),
    ('running', ('running',)),
    ('waiting', ('waiting',)),
    ('not started', ('not_started',)),
)
STATIC = {
    '/': ('page.html', 'text/html; charset=utf-8'),
    '/marked.umd.js': ('marked.umd.js', 'text/javascript; charset=utf-8'),
    '/page.js': ('page.js', 'text/javascript; charset=utf-8'),
}
# Step outputs are written by agents that may have read untrusted text: no remote images to leak data through,
# no javascript: links, nothing loaded from or sent to other origins.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; img-src data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
# Browsers slow the page's polling in a background tab to about once a minute: well under this.
IDLE_MINUTES = 30


class RunError(Exception):
    """The path given is neither a run directory nor a directory of runs."""


class StateUnreadable(Exception):
    """state.json is missing or not valid JSON at the moment, as when the engine is rewriting it."""


class WrongFormat(Exception):
    """state.json was written by an engine whose format this viewer does not read."""


# ---------- the run directory ----------


def find_run(path: str) -> str:
    """The run directory path names: itself if it holds state.json, else its subdirectory with the newest one."""
    path = os.path.abspath(path)
    if os.path.isfile(os.path.join(path, STATE)):
        return path
    if not os.path.isdir(path):
        raise RunError(f'{path}: not a directory')
    runs = [os.path.join(path, name) for name in os.listdir(path) if os.path.isfile(os.path.join(path, name, STATE))]
    if not runs:
        raise RunError(f'{path}: no {STATE} in it or in its subdirectories')
    return max(runs, key=lambda run: os.path.getmtime(os.path.join(run, STATE)))


def read_state(run: str) -> dict[str, Any]:
    """The contents of state.json, refused unless it is format 2. A state without `format` is from engine 1.x."""
    path = os.path.join(run, STATE)
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise StateUnreadable(str(e)) from e
    if not isinstance(data, dict):
        raise StateUnreadable(f'{STATE}: not a JSON object')
    found = data.get('format', 1)
    if found != FORMAT:
        # The engine's file was runbook.py through 1.x and is agent_runbooks.py from 2.0.
        name = 'runbook.py' if found == 1 else 'agent_runbooks.py'
        engine = f'{name} {found}.x' if type(found) is int else 'an unknown engine'
        raise WrongFormat(
            f'{path} is format {found}, from {engine}; this viewer reads format {FORMAT}, '
            f'from agent_runbooks.py {FORMAT}.x'
        )
    if not isinstance(data.get('calls'), list):
        raise StateUnreadable(f'{STATE}: no calls')
    return data


def read_progress(run: str) -> list[str]:
    """The lines of progress.md, none if it is absent."""
    try:
        with open(os.path.join(run, PROGRESS), encoding='utf-8') as f:
            return f.read().splitlines()
    except OSError:
        return []


def list_files(run: str) -> list[dict[str, Any]]:
    """The regular files in the run directory, excluding temporary state, with size and modification time."""
    files = []
    for name in sorted(os.listdir(run)):
        if name == STATE + '.tmp':
            continue
        path = os.path.join(run, name)
        if os.path.isfile(path):
            try:
                st = os.stat(path)
            except FileNotFoundError:
                continue
            files.append({'name': name, 'size': st.st_size, 'mtime': st.st_mtime})
    return files


def launches(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The attempts of steps and human steps, in the order of state.json."""
    return [c for c in state['calls'] if isinstance(c, dict) and c.get('kind') in LAUNCHES]


def label(call: dict[str, Any]) -> str:
    """The attempt's address as commands and progress.md name it: main/fix, then main/fix@2."""
    attempt = call.get('attempt', 1)
    return str(call.get('id', '')) + (f'@{attempt}' if attempt != 1 else '')


def mark(call: dict[str, Any]) -> str:
    """The attempt's mark, blank for a status this viewer does not know."""
    return MARKS.get(str(call.get('status')), ' ')


def short_label(call: dict[str, Any]) -> str:
    """The label without the leading main/, as the status and the page show it."""
    full = label(call)
    return full[len('main/') :] if full.startswith('main/') else full


def call_files(call: dict[str, Any], names: set[str]) -> list[str]:
    """The files the attempt was told to write that are in the run directory: what a done attempt wrote, or what a
    running or failed one has written so far. Matched by name, so a run directory moved since keeps its files."""
    paths = [*(call.get('writes') or {}).values(), *(call.get('files') or {}).values()]
    found = dict.fromkeys(os.path.basename(p) for p in paths if isinstance(p, str))
    return [name for name in found if name in names]


def canonical(name: str) -> str:
    """The label of the attempt a line names: the engine reads main/fix@1 and main/fix@01 as main/fix and main/fix@02
    as main/fix@2, and a log written before engine 2.0.0 carried a command's label as typed."""
    match = re.fullmatch(r'(.*)@([0-9]+)', name)
    if not match:
        return name
    attempt = int(match.group(2))
    return match.group(1) + (f'@{attempt}' if attempt != 1 else '')


def call_logs(progress: list[str], labels: list[str]) -> dict[str, list[str]]:
    """The lines of progress.md grouped by the attempt they name, in one pass over the log."""
    logs: dict[str, list[str]] = {name: [] for name in labels}
    for line in progress:
        end = line.find(': ', 2) if line.startswith('- ') else -1
        if end != -1:
            lines = logs.get(canonical(line[2:end]))
            if lines is not None:
                lines.append(line)
    return logs


def snapshot(run: str, now: datetime) -> dict[str, Any]:
    """What /api/state returns: the state, the files, the lines of layout() as `rows`, one entry per attempt with its
    fields, files and log lines, and the run's own files: the inputs, progress.md and the foreach indexes, whatever no
    attempt was told to write."""
    state = read_state(run)
    files = list_files(run)
    names = {f['name'] for f in files}
    calls = launches(state)
    logs = call_logs(read_progress(run), [label(c) for c in calls])
    entries = []
    claimed = set()
    for c in calls:
        outputs = call_files(c, names)
        claimed.update(outputs)
        entries.append(
            {
                'label': label(c),
                'name': short_label(c),
                'status': c.get('status'),
                'mark': mark(c),
                'executor': c.get('executor'),
                'started_at': c.get('started_at'),
                'ended_at': c.get('ended_at'),
                'note': summary(c),
                'files': outputs,
                'log': logs[label(c)],
            }
        )
    return {
        'name': os.path.basename(run),
        'now': now.strftime(TIME_FORMAT),
        'state': state,
        'files': files,
        'rows': layout(state),
        'calls': entries,
        'run_files': [f['name'] for f in files if f['name'] != STATE and f['name'] not in claimed],
    }


def run_file(run: str, name: str) -> str | None:
    """The path of a regular file directly in the run directory by this name, or None for anything else."""
    if not name or name in ('.', '..', STATE + '.tmp') or '/' in name or '\\' in name or '\0' in name:
        return None
    root = os.path.realpath(run)
    path = os.path.realpath(os.path.join(root, name))
    if os.path.dirname(path) != root or not os.path.isfile(path):
        return None
    return path


# ---------- the tree ----------


def owner(address: str, groups: dict[str, str]) -> tuple[str, str] | None:
    """The innermost of groups, address to kind, that the address sits in, with the key of its branch or item there;
    None for an address of main outside any group."""
    found = None
    for group, kind in groups.items():
        if address.startswith(group + ('/' if kind == 'parallel' else '[')) and (
            found is None or len(group) > len(found[0])
        ):
            rest = address[len(group) + 1 :]
            found = (group, rest.split('/', 1)[0] if kind == 'parallel' else rest.split(']', 1)[0])
    return found


def layout(state: dict[str, Any]) -> list[dict[str, Any]]:
    """The lines of the status and of the page's list, in the order things were opened.

    Each is a dict with `type`, `id` (an address), `depth` and `within`, the groups around it, outermost first. A
    group's header, type 'parallel' or 'foreach', has `header` and `counts`, the header after the name; its branches
    and items follow it, each followed by its own groups, one level deeper. A row, type 'call', 'branch' or 'item',
    has `group` (None for a call of main), `label`, `step`, `status`, `mark`, `executor`, `started_at`, `ended_at`,
    `open`, `note`, and `attempts`: the labels of every launch in it, in the order opened. A call of main shows its
    latest attempt. A branch or an item shows the launch it waits on, else the one that ended last, or for a branch a
    group directly in it that ended later; its `step` names that launch or group relative to the row.
    """
    kinds: dict[str, str] = {}
    groups: dict[str, dict[str, Any]] = {}
    members: dict[str, dict[str, dict[str, Any]]] = {}
    top: list[tuple[str, str]] = []
    calls: dict[str, list[dict[str, Any]]] = {}

    def member(place: tuple[str, str]) -> dict[str, Any]:
        return members[place[0]].setdefault(place[1], {'record': None, 'launches': [], 'groups': []})

    for record in state['calls']:
        if not isinstance(record, dict) or not isinstance(record.get('id'), str):
            continue
        address, kind = record['id'], record.get('kind')
        place = owner(address, kinds)
        if kind in GROUPS:
            kinds[address], groups[address], members[address] = str(kind), record, {}
            if place is None:
                top.append(('group', address))
            else:
                member(place)['groups'].append(address)
        elif kind == 'item' and place is not None and kinds[place[0]] == 'foreach':
            member(place)['record'] = record
        elif kind in LAUNCHES and place is None:
            if address not in calls:
                calls[address] = []
                top.append(('call', address))
            calls[address].append(record)
        elif kind in LAUNCHES:
            while place is not None:
                member(place)['launches'].append(record)
                place = owner(place[0], kinds)

    lines: list[dict[str, Any]] = []

    def add_group(address: str, base: str, depth: int, within: list[str]) -> None:
        record, kind = groups[address], kinds[address]
        if kind == 'foreach':
            items = record.get('items')
            listed = [
                i['key'] for i in (items if isinstance(items, list) else []) if isinstance(i, dict) and 'key' in i
            ]
            keys = [str(k) for k in dict.fromkeys([*listed, *members[address]])]
        else:
            branches = record.get('branches')
            keys = [
                str(k) for k in dict.fromkeys([*members[address], *(branches if isinstance(branches, dict) else {})])
            ]
        header: dict[str, Any] = {'type': kind, 'id': address, 'depth': depth, 'within': within}
        lines.append(header)
        statuses = []
        for key in keys:
            found = members[address].get(key) or {'record': None, 'launches': [], 'groups': []}
            nested = [groups[g] for g in found['groups']] if kind == 'parallel' else []
            row = _member_row(record, kind, key, found, nested)
            statuses.append((key, row['status']))
            lines.append({**row, 'group': address, 'depth': depth, 'within': [*within, address]})
            for inner in found['groups']:
                add_group(inner, row['id'], depth + 1, [*within, address])
        name = address[len(base) + 1 :] if address.startswith(base + '/') else address
        problem = record.get('problem')
        counts = _counts(statuses) or ('no items' if kind == 'foreach' else 'no branches')
        header['counts'] = problem or counts
        header['header'] = f'{kind} {name}: {header["counts"]}'

    for kind, address in top:
        if kind == 'group':
            add_group(address, 'main', 1, [])
            continue
        attempts = calls[address]
        latest = attempts[-1]
        lines.append(
            {
                'type': 'call',
                'id': address,
                'depth': 0,
                'within': [],
                'group': None,
                'label': short_label(latest),
                'step': '',
                'status': latest.get('status'),
                'mark': mark(latest),
                'executor': latest.get('executor'),
                'started_at': latest.get('started_at'),
                'ended_at': latest.get('ended_at'),
                'open': latest.get('status') in OPEN,
                'note': summary(latest),
                'attempts': [label(a) for a in attempts],
            }
        )
    return lines


def _member_row(
    group: dict[str, Any], kind: str, key: str, found: dict[str, Any], nested: list[dict[str, Any]]
) -> dict[str, Any]:
    """The row of a branch of a parallel or an item of a foreach; nested: the records of the groups right in a branch.

    An item's status is its record's, `not_started` without one; a branch's is the parallel's record of it once the
    parallel has ended, else running or waiting while a launch in it is open, else that of the launch or nested group
    that ended last: the parallel records how its branches ended only when it closes.
    """
    address = f'{group["id"]}/{key}' if kind == 'parallel' else f'{group["id"]}[{key}]'
    launches: list[dict[str, Any]] = found['launches']
    item: dict[str, Any] | None = found['record']
    opened = [c for c in launches if c.get('status') in OPEN]
    asking = [c for c in opened if c.get('status') == 'waiting']
    # One fixed format: the latest end is the largest string. Calls in nested branches end out of launch order, and
    # a group ends with or after the calls in it, so it wins a tie.
    closed = [c for c in [*launches, *nested] if parse_time(c.get('ended_at')) is not None]
    last = max(reversed(closed), key=lambda c: str(c['ended_at'])) if closed else None
    shown = (asking or opened or ([last] if last else launches) or [None])[-1]
    live = 'waiting' if asking else 'running'
    branches = group.get('branches')
    if kind == 'foreach':
        status = 'not_started' if item is None else live if item.get('status') in OPEN else str(item.get('status'))
    elif isinstance(branches, dict) and key in branches:
        status = str(branches[key])
    elif opened:
        status = live
    else:
        latest = str(shown.get('status')) if shown else ''
        status = 'failed' if latest == 'blocked' else latest
    if item is not None and status == 'done':
        note = reply_text(item.get('reply'))
    elif item is not None and status == 'failed':
        note = str(item.get('reason') or '')
    elif status == 'cancelled':
        note = 'cancelled'
    elif shown is not None and shown.get('kind') in GROUPS:
        note = str(shown.get('problem') or '')
    else:
        note = summary(shown) if shown else ''
    if shown is None:
        step = 'not started' if status == 'not_started' else ''
    else:
        step = shown['id'][len(address) + 1 :] if shown['id'].startswith(address + '/') else str(shown.get('step', ''))
        attempt = shown.get('attempt', 1)
        step += f'@{attempt}' if attempt != 1 else ''
    if item is not None:
        started, ended = item.get('started_at'), item.get('ended_at')
    else:
        starts = [str(c['started_at']) for c in [*launches, *nested] if parse_time(c.get('started_at')) is not None]
        started = min(starts) if starts else None
        ended = last.get('ended_at') if last else None
    return {
        'type': 'branch' if kind == 'parallel' else 'item',
        'id': address,
        'label': key if kind == 'parallel' else f'[{key}]',
        'step': step,
        'status': status,
        'mark': MARKS.get(status, ' '),
        'executor': (shown or {}).get('executor'),
        'started_at': started,
        'ended_at': ended,
        'open': status in OPEN,
        'note': note,
        'attempts': [label(c) for c in launches],
    }


def _counts(members: list[tuple[str, str]]) -> str:
    """'1 done, 1 failed (billing), 2 running' for (key, status) pairs: the counts that are not zero."""
    parts = []
    for name, statuses in COUNTS:
        keys = [key for key, status in members if status in statuses]
        if keys:
            parts.append(f'{len(keys)} {name} ({", ".join(keys)})' if name == 'failed' else f'{len(keys)} {name}')
    return ', '.join(parts)


# ---------- text status ----------


def parse_time(value: Any) -> datetime | None:
    """A state.json timestamp, or None when it is absent or malformed."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def duration(call: dict[str, Any], now: datetime) -> str:
    """m:ss or h:mm:ss from started_at to ended_at, or to now for an open attempt; empty without timestamps."""
    start = parse_time(call.get('started_at'))
    stop = now if call.get('status') in OPEN else parse_time(call.get('ended_at'))
    if start is None or stop is None:
        return ''
    seconds = max(0, int((stop - start).total_seconds()))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def reply_text(reply: Any) -> str:
    """A reply as the status shows it: an object's fields as k: v, ..., a string as it is, any other value as JSON."""
    if isinstance(reply, dict):
        return ', '.join(
            f'{k}: {reply_text(v) if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}'
            for k, v in reply.items()
        )
    if reply is None:
        return ''
    return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


def summary(call: dict[str, Any]) -> str:
    """The reply fields of a done step, the human's choice and fields, the reason of a failed or blocked one."""
    status = call.get('status')
    reply = dict(call.get('reply') or {})
    if status == 'waiting':
        return WAITING
    if status in ('interrupted', 'cancelled'):
        return status
    if status in ('failed', 'blocked'):
        return str(reply.get('reason', ''))
    if status != 'done':
        return ''
    choice = [str(reply.pop('choice'))] if call.get('kind') == 'human' and 'choice' in reply else []
    return ', '.join([*choice, reply_text(reply)] if reply else choice)


def _aligned(rows: list[list[str]]) -> list[str]:
    """Rows of [mark, *columns, duration, note] as lines, each column as wide as its widest cell; empty ones go."""
    if not rows:
        return []
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    last = len(widths) - 2
    if widths[last]:
        widths[last] = max(widths[last], 5)
    lines = []
    for row in rows:
        cells = [
            row[i].rjust(widths[i]) if i == last else row[i].ljust(widths[i]) for i in range(1, last + 1) if widths[i]
        ]
        lines.append(f'{row[0]} {"  ".join(cells)}  {row[-1]}'.rstrip())
    return lines


def status_text(run: str, now: datetime, tail: int = 0) -> str:
    """The text status: a header line, then the lines of layout(), indented two spaces per depth.

    The calls of main are aligned among themselves, and the rows of each group among themselves. With tail, only the
    last tail rows show, after a line with the count of those cut and the headers of the groups the first one sits in,
    each named by its address.
    """
    state = read_state(run)
    entries = layout(state)
    texts = ['  ' * e['depth'] + e['header'] if e['type'] in GROUPS else '' for e in entries]
    by_group: dict[str | None, list[int]] = {}
    for i, e in enumerate(entries):
        if e['type'] not in GROUPS:
            by_group.setdefault(e['group'], []).append(i)
    for indexes in by_group.values():
        rows = []
        for i in indexes:
            e = entries[i]
            timed = {
                'status': 'running' if e['open'] else 'done',
                'started_at': e['started_at'],
                'ended_at': e['ended_at'],
            }
            middle = [e['label'], e['step']] if e['group'] else [e['label']]
            rows.append([e['mark'], *middle, e['executor'] or '', duration(timed, now), e['note']])
        for i, line in zip(indexes, _aligned(rows)):
            texts[i] = '  ' * entries[i]['depth'] + line
    out = [f'{os.path.basename(run)} · {state.get("runbook", "")} · {state.get("status", "")}']
    rows_at = [i for i, e in enumerate(entries) if e['type'] not in GROUPS]
    if 0 < tail < len(rows_at):
        cut = rows_at[-tail]
        above = len(rows_at) - tail
        out.append(f'… {above} {"row" if above == 1 else "rows"} above')
        # The row a nested group sits under may be cut, so each header names its group by address.
        context = [e for e in entries[:cut] if e['type'] in GROUPS and e['id'] in entries[cut]['within']]
        texts = [
            '  ' * e['depth'] + f'{e["type"]} {e["id"].removeprefix("main/")}: {e["counts"]}' for e in context
        ] + texts[cut:]
    return '\n'.join(out + texts)


# ---------- server ----------


class Handler(BaseHTTPRequestHandler):
    """Serves the page, the library, /api/state and /api/file for the run in self.server.run."""

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, RunServer)
        server.last_request = time.monotonic()
        url = urlsplit(self.path)
        port = server.server_address[1]
        # A foreign Host is a page on another site that rebound its domain to 127.0.0.1 to read the run.
        if self.headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            self.send(HTTPStatus.FORBIDDEN, b'forbidden', 'text/plain; charset=utf-8')
        elif url.path in STATIC:
            name, content_type = STATIC[url.path]
            with open(os.path.join(HERE, name), 'rb') as f:
                self.send(HTTPStatus.OK, f.read(), content_type)
        elif url.path == '/api/state':
            try:
                body = json.dumps(snapshot(server.run, datetime.now(timezone.utc)), ensure_ascii=False)
            except StateUnreadable as e:
                self.send(HTTPStatus.SERVICE_UNAVAILABLE, str(e).encode(), 'text/plain; charset=utf-8')
                return
            except WrongFormat as e:
                self.send(HTTPStatus.CONFLICT, str(e).encode(), 'text/plain; charset=utf-8')
                return
            self.send(HTTPStatus.OK, body.encode(), 'application/json; charset=utf-8')
        elif url.path == '/api/file':
            path = run_file(server.run, parse_qs(url.query).get('name', [''])[0])
            if path is None:
                self.send(HTTPStatus.NOT_FOUND, b'not found', 'text/plain; charset=utf-8')
                return
            with open(path, 'rb') as f:
                text = f.read().decode('utf-8', errors='replace')
            self.send(HTTPStatus.OK, text.encode(), 'text/plain; charset=utf-8')
        else:
            self.send(HTTPStatus.NOT_FOUND, b'not found', 'text/plain; charset=utf-8')

    def send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', CSP)
        self.end_headers()
        self.wfile.write(body)


class RunServer(ThreadingHTTPServer):
    """An HTTP server on 127.0.0.1 for one run directory."""

    daemon_threads = True

    def __init__(self, run: str, port: int = 0) -> None:
        super().__init__(('127.0.0.1', port), Handler)
        self.run = run
        self.last_request = time.monotonic()

    def serve_until_idle(self, seconds: float) -> None:
        """Serves until no request has come for the given seconds; an open page polls, so this is the page closed."""
        threading.Thread(target=self._shutdown_when_idle, args=(seconds,), daemon=True).start()
        self.serve_forever()

    def _shutdown_when_idle(self, seconds: float) -> None:
        while True:
            left = self.last_request + seconds - time.monotonic()
            if left <= 0:
                self.shutdown()
                return
            time.sleep(left)

    def handle_error(self, request: Any, client_address: Any) -> None:
        if not isinstance(sys.exc_info()[1], ConnectionError):
            super().handle_error(request, client_address)

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.server_address[1]}/'


# ---------- entry point ----------


def main(argv: list[str] | None = None) -> int:
    """Run the command in argv (sys.argv[1:] by default); returns the exit code."""
    parser = argparse.ArgumentParser(prog='view.py', description='Show a runbook run.')
    parser.add_argument('run', help='a run directory, or a directory of runs')
    parser.add_argument('--status', action='store_true', help='print the text status and exit')
    parser.add_argument('--tail', type=int, default=0, help='with --status, only the last N rows')
    parser.add_argument('--port', type=int, default=0, help='port to serve on, a free one by default')
    parser.add_argument(
        '--idle-minutes',
        type=float,
        default=IDLE_MINUTES,
        help=f'stop after this many minutes without a request, {IDLE_MINUTES} by default; 0 never stops',
    )
    args = parser.parse_args(argv)
    try:
        run = find_run(args.run)
        if args.status:
            print(status_text(run, datetime.now(timezone.utc), args.tail))
            return 0
        with contextlib.suppress(StateUnreadable):
            read_state(run)
    except (RunError, StateUnreadable, WrongFormat) as e:
        print(f'view.py: {e}', file=sys.stderr)
        return 2
    try:
        server = RunServer(run, args.port)
    except OSError as e:
        print(f'view.py: port {args.port}: {e.strerror}', file=sys.stderr)
        return 2
    print(server.url, flush=True)
    try:
        if args.idle_minutes > 0:
            server.serve_until_idle(args.idle_minutes * 60)
            print(f'view.py: no requests for {args.idle_minutes:g} minutes, stopped', file=sys.stderr)
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

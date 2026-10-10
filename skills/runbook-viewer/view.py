#!/usr/bin/env python3
"""Shows a runbook run: a text status, or a page on localhost. Reads the run directory and never writes to it.

    view.py <run> --status       print the text status
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
}
OPEN = ('running', 'waiting')
# The records that get a line: groups and items have none, the addresses of their calls show the nesting.
LAUNCHES = ('step', 'human')
WAITING = 'waiting for the human'
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
        engine = f'runbook.py {found}.x' if type(found) is int else 'an unknown engine'
        raise WrongFormat(
            f'{path} is format {found}, from {engine}; this viewer reads format {FORMAT}, from runbook.py {FORMAT}.x'
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
    """What /api/state returns: the state, the files, one entry per attempt with the fields of its status line, its
    files and log lines, and the run's own files: the inputs, progress.md and the foreach indexes, whatever no attempt
    was told to write."""
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
    parts = [str(reply.pop('choice'))] if call.get('kind') == 'human' and 'choice' in reply else []
    parts += [f'{k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}' for k, v in reply.items()]
    return ', '.join(parts)


def status_text(run: str, now: datetime) -> str:
    """The text status: a header line, then one aligned line per attempt of a step or a human step."""
    state = read_state(run)
    rows = [[mark(c), short_label(c), c.get('executor') or '', duration(c, now), summary(c)] for c in launches(state)]
    widths = [max((len(row[i]) for row in rows), default=0) for i in range(len(rows[0]))] if rows else []
    if widths and widths[3]:
        widths[3] = max(widths[3], 5)
    lines = [f'{os.path.basename(run)} · {state.get("runbook", "")} · {state.get("status", "")}']
    for row in rows:
        cells = [row[0]]
        for i in (1, 2, 3):
            if widths[i]:
                cells.append(row[i].rjust(widths[i]) if i == 3 else row[i].ljust(widths[i]))
        line = f'{cells[0]} ' + '  '.join(cells[1:])
        lines.append(f'{line}  {row[4]}'.rstrip())
    return '\n'.join(lines)


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
            print(status_text(run, datetime.now(timezone.utc)))
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

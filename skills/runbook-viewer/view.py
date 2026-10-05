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
TIME_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
MARKS = {'done': '✓', 'running': '●', 'waiting_for_human': '?', 'failed': '✗', 'blocked': '!'}
OPEN = ('running', 'waiting_for_human')
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
    """The contents of state.json."""
    try:
        with open(os.path.join(run, STATE), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise StateUnreadable(str(e)) from e
    if not isinstance(data, dict) or not isinstance(data.get('sections'), list):
        raise StateUnreadable(f'{STATE}: no sections')
    return data


def read_progress(run: str) -> list[str]:
    """The lines of progress.md, none if it is absent."""
    try:
        with open(os.path.join(run, PROGRESS), encoding='utf-8') as f:
            return f.read().splitlines()
    except OSError:
        return []


def list_files(run: str) -> list[dict[str, Any]]:
    """The regular files directly in the run directory, by name, with size and modification time."""
    files = []
    for name in sorted(os.listdir(run)):
        path = os.path.join(run, name)
        if os.path.isfile(path):
            st = os.stat(path)
            files.append({'name': name, 'size': st.st_size, 'mtime': st.st_mtime})
    return files


def section_files(names: list[str], count: int) -> tuple[list[list[str]], list[str]]:
    """Splits file names into each section's outputs, <NN>-… with NN its index, and the run's own files."""
    by_section: list[list[str]] = [[] for _ in range(count)]
    rest = []
    for name in names:
        m = re.match(r'(\d{2,})-', name)
        index = int(m.group(1)) if m else -1
        if m and index < count and m.group(1) == f'{index:02d}':
            by_section[index].append(name)
        elif name != STATE:
            rest.append(name)
    return by_section, rest


def section_log(progress: list[str], sid: str) -> list[str]:
    """The lines of progress.md about this section."""
    prefix = f'- {sid}: '
    return [line for line in progress if line.startswith(prefix)]


def snapshot(run: str, now: datetime) -> dict[str, Any]:
    """What /api/state returns: the state, the files, and per section its files and log lines."""
    state = read_state(run)
    files = list_files(run)
    progress = read_progress(run)
    by_section, run_files = section_files([f['name'] for f in files], len(state['sections']))
    sections = [
        {'id': s['id'], 'files': by_section[i], 'log': section_log(progress, s['id'])}
        for i, s in enumerate(state['sections'])
    ]
    return {
        'name': os.path.basename(run),
        'now': now.strftime(TIME_FORMAT),
        'state': state,
        'files': files,
        'sections': sections,
        'run_files': run_files,
    }


def run_file(run: str, name: str) -> str | None:
    """The path of a regular file directly in the run directory by this name, or None for anything else."""
    if not name or name in ('.', '..') or '/' in name or '\\' in name or '\0' in name:
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


def duration(section: dict[str, Any], now: datetime) -> str:
    """m:ss or h:mm:ss from started_at to ended_at, or to now for an open section; empty without timestamps."""
    start = parse_time(section.get('started_at'))
    stop = now if section.get('status') in OPEN else parse_time(section.get('ended_at'))
    if start is None or stop is None:
        return ''
    seconds = max(0, int((stop - start).total_seconds()))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'


def summary(section: dict[str, Any]) -> str:
    """The reply fields of a done step, the note or reason of a failed one, the choice of an answered question."""
    status = section.get('status')
    reply = section.get('reply') or {}
    note = section.get('note')
    if status == 'waiting_for_human':
        return WAITING
    if status in ('failed', 'blocked'):
        return note or str(reply.get('reason', ''))
    if status == 'done' and not reply:
        return note or ''
    if status == 'done':
        return ', '.join(
            f'{k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}'
            for k, v in reply.items()
            if k != 'status'
        )
    return ''


def status_text(run: str, now: datetime) -> str:
    """The text status: a header line, then one aligned line per section."""
    state = read_state(run)
    rows = [
        [MARKS.get(s.get('status'), ' '), s.get('id', ''), s.get('executor') or '', duration(s, now), summary(s)]
        for s in state['sections']
    ]
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

    server: RunServer

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.server.last_request = time.monotonic()
        url = urlsplit(self.path)
        port = self.server.server_address[1]
        # A foreign Host is a page on another site that rebound its domain to 127.0.0.1 to read the run.
        if self.headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            self.send(HTTPStatus.FORBIDDEN, b'forbidden', 'text/plain; charset=utf-8')
        elif url.path in STATIC:
            name, content_type = STATIC[url.path]
            with open(os.path.join(HERE, name), 'rb') as f:
                self.send(HTTPStatus.OK, f.read(), content_type)
        elif url.path == '/api/state':
            try:
                body = json.dumps(snapshot(self.server.run, datetime.now(timezone.utc)), ensure_ascii=False)
            except StateUnreadable as e:
                self.send(HTTPStatus.SERVICE_UNAVAILABLE, str(e).encode(), 'text/plain; charset=utf-8')
                return
            self.send(HTTPStatus.OK, body.encode(), 'application/json; charset=utf-8')
        elif url.path == '/api/file':
            path = run_file(self.server.run, parse_qs(url.query).get('name', [''])[0])
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
    except (RunError, StateUnreadable) as e:
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

"""Engine of a runbook run: state in state.json, a text log in progress.md, next actions on stdout.

A runbook's flow.py declares inputs, executors and steps with this module, writes the flow as one generator under
@rb.flow, and ends with rb.main(). The orchestrator then talks to flow.py:

    flow.py <run> start '<given inputs as JSON>'   new run: creates the directory, prints what to launch
    flow.py <run> reply <call> '<reply JSON>'      a step finished: records the reply, prints what follows
    flow.py <run> answer <call> '<choice>'         the human answered a human step
    flow.py <run> interrupted <call>               a running step's executor is gone: launch it again
    flow.py <run> relaunch <call>                  the human said yes to relaunching a failed side-effect step
    flow.py <run> log '<text>'                     add a line to progress.md
    flow.py <run>                                  nothing new: print what is pending
    flow.py --check                                validate the declarations

<call> is the address flow.py printed with the launch or the question, such as main/review#2 or main/review@2.
`answer` takes the human's words as a last argument, and a JSON object in place of the choice when the step
declares reply fields. One argument may be `-` to read it from stdin, for text with quotes in it.

Source: https://github.com/agent-runbooks/skills/tree/main/skills/agent-runbook-authoring
Each release is tagged agent-runbook-authoring/v<__version__>. Changes to the flow.py API: CHANGELOG.md there.
From 1.4.4 each release is also on PyPI as agent-runbooks, imported as agent_runbooks.
"""

from __future__ import annotations

__version__ = '2.0.0'

import sys

# Before the other imports, so an older Python stops here with a message and not on a missing name.
if sys.version_info < (3, 9):
    sys.exit(f'runbook.py needs Python 3.9 or newer; {sys.executable} is {sys.version.split()[0]}')

import inspect
import json
import os
import re
import shlex
import traceback
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, TypeVar, get_args

if TYPE_CHECKING:
    from typing import TypeAlias  # 3.10+; annotations are never evaluated at run time


class FlowError(Exception):
    """A defect of flow.py found while the flow runs: a generator raised, or yielded or passed what it cannot."""


class CommandError(Exception):
    """A refused command, with a message for the orchestrator."""


# ---------- the flow ----------


@dataclass(frozen=True)
class End:
    """What the flow returns to end the run with this status; report is what the human is told to read."""

    status: str
    report: str = ''


# How a failure that no generator handles ends the run.
FAILED_END = End('failed', 'read <run>/progress.md')
NO_REASON = 'no reason given'


def end(status: str, report: str = '') -> End:
    """The run ends with this status. report: what the human reads next, printed at the end."""
    return End(status, report)


class StepFailed(Exception):
    """A failed or blocked call, thrown into the flow at its yield.

    id: the address of the call, or of the generator that raised it. reply: `status`, `reason` and any fields.
    A generator raises it to fail its branch or item: `raise StepFailed('the site is still down')`.
    """

    def __init__(self, reason: str = '', *, id: str = '', reply: dict[str, Any] | None = None) -> None:
        self.id = id
        self.reply: dict[str, Any] = reply if reply is not None else {'status': 'failed', 'reason': reason or NO_REASON}
        super().__init__(self.reason)

    @property
    def status(self) -> str:
        return self.reply.get('status', 'failed')

    @property
    def reason(self) -> str:
        return self.reply.get('reason') or NO_REASON


class Context:
    """What every generator of the flow gets first: `ctx.inputs.<name>` are the run's inputs."""

    def __init__(self, inputs: dict[str, Any]) -> None:
        self.inputs = SimpleNamespace(**inputs)


@dataclass(frozen=True)
class Call:
    """A step called in the flow: what `yield step(...)` hands the engine. inputs: the keyword arguments."""

    step: Step | HumanStep
    inputs: dict[str, Any]
    executor: str | None = None


Request: TypeAlias = 'Call | Parallel | Foreach'
Chain: TypeAlias = 'Callable[[Context], Generator[Request, Any, Any]]'
ItemBody: TypeAlias = 'Callable[[Context, SimpleNamespace], Generator[Request, Any, Any]]'
FlowFunction = TypeVar('FlowFunction', bound=Callable[[Context], Generator[Any, Any, End]])
OnItemFailure = Literal['fail', 'skip']

# Names on a result the engine sets: a reply field or a branch key cannot take them.
RESERVED = ('id', 'files', 'status', 'reason', 'choice')
# Step, group and item names: they are parts of an address, where / # @ [ ] have a meaning.
NAME = re.compile(r'[A-Za-z0-9_.-]+')


@dataclass(frozen=True)
class Parallel:
    """Branches launched together; see parallel()."""

    name: str
    branches: dict[str, Call | Chain]


@dataclass(frozen=True)
class Foreach:
    """A body run for each item of a JSON array; see foreach()."""

    name: str
    over: str
    body: Call | ItemBody
    max_concurrent: int
    max_items: int
    on_item_failure: OnItemFailure


def parallel(name: str, /, **branches: Call | Chain) -> Parallel:
    """Launch the branches together: `r = yield parallel('reviews', a=review_a(), b=chain)`.

    A branch is a step call, or a generator function (ctx) whose return value is the branch's result. The yield
    returns once every branch is done, with one attribute per branch key.
    """
    where = f'parallel({name!r})'
    _check_name(name, where)
    if not branches:
        raise FlowError(f'{where} has no branches')
    for key, branch in branches.items():
        if key in RESERVED:
            raise FlowError(f'{where}: branch {key!r} is reserved: {", ".join(RESERVED)} are attributes of a result')
        _check_name(key, f'{where}: branch {key!r}')
        if not isinstance(branch, Call) and not inspect.isgeneratorfunction(branch):
            raise FlowError(f'{where}: branch {key!r} is {branch!r}, neither a step call nor a generator function')
    return Parallel(name, dict(branches))


def foreach(
    name: str,
    /,
    *,
    over: str,
    body: Call | ItemBody,
    max_concurrent: int = 4,
    max_items: int = 50,
    on_item_failure: OnItemFailure = 'fail',
) -> Foreach:
    """Run body for each item of the JSON array in the file `over`: `done = yield foreach('migrate', ...)`.

    over: a file name, resolved as `reads` are, or the path of a result's file. Each item is an object with a
    unique `key` of letters, digits, _ . -; more than max_items fail the foreach. body: a generator function
    (ctx, item) whose return value is the item's reply, or a step call. At most max_concurrent items run at once.
    on_item_failure: 'fail' starts no new item after a failed one and fails the foreach once the running ones
    end; 'skip' keeps the failure as the item's outcome and goes on. The yield returns once every item has ended,
    with `items`: key, status, reply, reason and files of each, in the order of the file.
    """
    where = f'foreach({name!r})'
    _check_name(name, where)
    if not isinstance(over, str) or not over:
        raise FlowError(f'{where}: over is {over!r}, not a file name')
    if not isinstance(body, Call) and not inspect.isgeneratorfunction(body):
        raise FlowError(f'{where}: body is {body!r}, neither a step call nor a generator function')
    for parameter, value in (('max_concurrent', max_concurrent), ('max_items', max_items)):
        if not _is_of_type(value, int) or value < 1:
            raise FlowError(f'{where}: {parameter} is {value!r}, not a whole number from 1')
    if on_item_failure not in get_args(OnItemFailure):
        raise FlowError(
            f'{where}: on_item_failure is {on_item_failure!r}, not one of {", ".join(get_args(OnItemFailure))}'
        )
    return Foreach(name, over, body, max_concurrent, max_items, on_item_failure)


def _check_name(name: Any, where: str) -> None:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise FlowError(f'{where}: the name must be letters, digits, _ . -')


# The results a yield returns. Their attributes are the flow's to read; the engine keeps no methods on them, so a
# reply field can take any name but the reserved ones.


class StepResult(SimpleNamespace):
    """A done call: the reply's fields as attributes, `choice` for a human step, its address as `id`, and `files`,
    name to path, what the launch wrote."""

    id: str
    files: dict[str, str]


class ParallelResult(SimpleNamespace):
    """A done parallel: one attribute per branch key, and its address as `id`."""

    id: str


class ForeachResult(SimpleNamespace):
    """A done foreach: `items` in the order of the file, its address as `id`, and its index in `files`."""

    id: str
    files: dict[str, str]
    items: list[ItemResult]


class ItemResult(SimpleNamespace):
    """One item of a foreach: key, status ('done', 'failed', 'cancelled' or 'not_started'), reply, reason, files."""

    key: str
    status: str
    reply: Any
    reason: str | None
    files: dict[str, str]


def _result_fields(result: StepResult) -> dict[str, Any]:
    return {k: v for k, v in vars(result).items() if k not in ('id', 'files')}


# ---------- declarations ----------


@dataclass(eq=False)
class Step:
    """A step an executor runs. Fields mirror the parameters of Runbook.step. Calling it is a call for the flow."""

    name: str
    executor: str
    prompt: str
    inputs: list[str | tuple[str, Any]] = field(default_factory=list)
    reply: dict[str, Any] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    side_effects: str | None = None

    def __call__(self, *, executor: str | None = None, **inputs: Any) -> Call:
        """A launch of this step. executor: a declared executor for this call. inputs: more lines of its message."""
        return Call(self, inputs, executor)


@dataclass(eq=False)
class HumanStep:
    """A question to the human. Fields mirror the parameters of Runbook.human. Calling it is a call for the flow."""

    name: str
    question: str
    choices: list[str] = field(default_factory=list)
    reply: dict[str, Any] = field(default_factory=dict)
    writes: str | None = None

    def __call__(self) -> Call:
        return Call(self, {})

    def match(self, answer: str) -> str | None:
        """The declared choice this answer names, or the answer itself when the step takes free text."""
        if not self.choices:
            return answer
        for choice in self.choices:
            if choice.lower() == answer.strip().lower():
                return choice
        return None


AnyStep: TypeAlias = 'Step | HumanStep'
WORKING_TREE = 'working tree'


def _writes(step: AnyStep) -> list[str]:
    if isinstance(step, HumanStep):
        return [step.writes] if step.writes else []
    return step.writes


# ---------- run state ----------

FORMAT = 2
Kind = Literal['step', 'human', 'parallel', 'foreach', 'item']
CallStatus = Literal['running', 'waiting', 'done', 'failed', 'blocked', 'cancelled', 'interrupted']
OPEN: tuple[CallStatus, ...] = ('running', 'waiting')
REPLY_STATUSES = ('done', 'failed', 'blocked')
NOTE_RELAUNCHED = "relaunched on the human's yes"
# A side-effect step whose question its group's failure dropped: never relaunched, the question withdrawn.
NOTE_CANCELLED = 'cancelled with its group'
# How many times an executor is asked to correct a reply that did not pass the check before the step is failed.
CORRECTIONS = 1


def utc_now() -> str:
    """The current time as state.json records it: UTC, ISO 8601 to the second, `Z` suffix."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


LAUNCH_KEYS = ('executor', 'reply', 'note', 'answer', 'inputs', 'files_in', 'files', 'schema', 'writes')
KIND_KEYS: dict[str, tuple[str, ...]] = {
    'step': LAUNCH_KEYS,
    'human': LAUNCH_KEYS,
    'parallel': ('branches',),
    'foreach': ('over', 'items', 'index', 'problem'),
    'item': ('fields', 'reply', 'reason', 'files'),
}


@dataclass(eq=False)
class CallRecord:
    """One attempt of a call in state.json, at the address the flow yields it at; an interruption or a relaunch
    opens the next attempt of the same address, and only the latest one is open.

    A launch (kind step or human) holds the contract its executor was given: executor, inputs (the call's own),
    files_in (name to the path it was told, None for absent), writes and the reply schema. invalid_replies: what the
    orchestrator passed that did not pass the check, as {'reply': raw, 'problem': ...}. A parallel records how each
    branch ended; a foreach the file it read, its items frozen when reached, its index, or the problem with the file;
    an item its fields and its outcome: reply (the body's return value), reason and files.
    """

    id: str
    kind: Kind
    step: str
    status: CallStatus
    attempt: int = 1
    executor: str | None = None
    reply: Any = None
    note: str | None = None
    answer: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    files_in: dict[str, str | None] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    schema: dict[str, Any] | None = None
    writes: dict[str, str] = field(default_factory=dict)
    branches: dict[str, str] | None = None
    over: str | None = None
    items: list[dict[str, Any]] | None = None
    index: str | None = None
    problem: str | None = None
    fields: dict[str, Any] | None = None
    reason: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    invalid_replies: list[dict[str, str]] = field(default_factory=list)
    # The record's place in state.json, which numbers its files. Not saved.
    position: int = -1

    @property
    def label(self) -> str:
        """The address with the attempt, as commands take it: main/review, then main/review@2."""
        return self.id if self.attempt == 1 else f'{self.id}@{self.attempt}'

    def close(self, status: CallStatus) -> None:
        """Moves the record to status, stamping ended_at if it leaves an open status."""
        if self.status in OPEN and status not in OPEN:
            self.ended_at = utc_now()
        self.status = status

    def to_json(self) -> dict[str, Any]:
        keys = ('id', 'attempt', 'kind', 'step', 'status', *KIND_KEYS[self.kind], 'started_at', 'ended_at')
        data = {key: getattr(self, key) for key in keys}
        if self.kind == 'step':
            data['invalid_replies'] = self.invalid_replies
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CallRecord:
        if data.get('kind') not in KIND_KEYS or data.get('status') not in get_args(CallStatus):
            raise CommandError(
                f'state.json: call {data.get("id")!r} has kind {data.get("kind")!r} and status {data.get("status")!r}'
            )
        known = (
            'id',
            'attempt',
            'kind',
            'step',
            'status',
            *KIND_KEYS[data['kind']],
            'started_at',
            'ended_at',
            'invalid_replies',
        )
        return cls(**{key: data[key] for key in known if key in data})


@dataclass
class RunState:
    """The contents of <run>/state.json. status is 'running', 'waiting_for_human' or the end status."""

    runbook: str
    status: str
    inputs: dict[str, Any]
    calls: list[CallRecord]

    @staticmethod
    def path(run_dir: str) -> str:
        return os.path.join(run_dir, 'state.json')

    @classmethod
    def load(cls, run_dir: str) -> RunState:
        path = cls.path(run_dir)
        if not os.path.exists(path):
            raise CommandError(f'{path}: not found')
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        if data.get('format') != FORMAT:
            raise CommandError(
                f'{path} is format {data.get("format", 1)}, and runbook.py {__version__} reads format {FORMAT}. '
                'A run of another engine version does not load: start a new run.'
            )
        state = cls(runbook=data['runbook'], status=data['status'], inputs=data['inputs'], calls=[])
        for record in data['calls']:
            state.append(CallRecord.from_json(record))
        return state

    def serialize(self) -> str:
        """The file's text. A value that is not JSON is a defect of the flow, found before anything is written."""
        data = dict(
            format=FORMAT,
            runbook=self.runbook,
            status=self.status,
            inputs=self.inputs,
            calls=[c.to_json() for c in self.calls],
        )
        try:
            return json.dumps(data, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as e:
            raise FlowError(f'state.json cannot hold what the flow gave: {e}') from e

    def save(self, run_dir: str, text: str | None = None) -> None:
        text = self.serialize() if text is None else text
        path = self.path(run_dir)
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(path + '.tmp', path)

    def append(self, record: CallRecord) -> None:
        record.position = len(self.calls)
        self.calls.append(record)

    def find(self, label: str) -> CallRecord:
        """The attempt a command names; refused unless it is the latest attempt of its address."""
        match = re.fullmatch(r'(.*)@([0-9]+)', label)
        address, attempt = (match.group(1), int(match.group(2))) if match else (label, 1)
        named = latest = None
        for record in self.calls:
            if record.id == address:
                latest = record
                if record.attempt == attempt:
                    named = record
        if named is None or latest is None:
            raise CommandError(f'no call {label} in state.json')
        if named is not latest:
            raise CommandError(
                f'call {label} is a closed attempt ({named.status}); the latest attempt is {latest.label}'
            )
        return named


class RunFiles:
    """Files in a run directory: a record writes <run>/<NN>-<name>, NN being its place in state.json."""

    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir

    def path(self, record: CallRecord, name: str) -> str:
        return os.path.join(self.run_dir, f'{record.position:02d}-{name}')

    def index_path(self, record: CallRecord) -> str:
        return self.path(record, f'{record.step}.index.json')

    def schema_path(self, record: CallRecord) -> str:
        """Where the JSON schema of this attempt's reply is written for its executor: <run>/schemas/<NN>-<step>.json."""
        return os.path.join(self.run_dir, 'schemas', f'{record.position:02d}-{record.step}.json')


class ProgressLog:
    """<run>/progress.md: the inputs, then one line per event. Append-only."""

    def __init__(self, run_dir: str) -> None:
        self.run_dir = run_dir
        self.path = os.path.join(run_dir, 'progress.md')

    def create(self, runbook: str, inputs: dict[str, Any]) -> None:
        values = ''.join(
            f'- {k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)}\n' for k, v in inputs.items()
        )
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(
                f'# Run {os.path.basename(self.run_dir)}\n\n'
                f'Runbook `{runbook}`. State in `state.json`. Inputs:\n\n'
                f'{values}\n## Log\n\n'
            )

    def append(self, line: str) -> None:
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(f'- {line}\n')


# ---------- replay ----------


class Frame:
    """A generator of the flow being driven: its address, the files its own done calls wrote, and its parent.

    A file name resolves in the frame's own history, then in its ancestors' as they stood when they yielded the
    group, never in a sibling's or a child's. item: the fields of the innermost foreach item, for launch messages.
    """

    def __init__(self, path: str, parent: Frame | None, item: dict[str, Any]) -> None:
        self.path = path
        self.parent = parent
        self.item = item
        self.files: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        self.last: str | None = None

    def address(self, name: str) -> str:
        """The address of the next yield of this name: main/review, then main/review#2."""
        n = self.counts[name] = self.counts.get(name, 0) + 1
        self.last = f'{self.path}/{name}' + (f'#{n}' if n > 1 else '')
        return self.last

    def resolve(self, name: str) -> str | None:
        frame: Frame | None = self
        while frame is not None:
            if name in frame.files:
                return frame.files[name]
            frame = frame.parent
        return None

    def names(self) -> set[str]:
        frame, names = self, set()
        while frame is not None:
            names |= frame.files.keys()
            frame = frame.parent
        return names


@dataclass
class Proposal:
    """A record a replay would open, with what its launch message or question needs."""

    record: CallRecord
    item: dict[str, Any] = field(default_factory=dict)
    question: str = ''


@dataclass
class Returned:
    value: Any


@dataclass
class Raised:
    error: StepFailed


@dataclass
class Pending:
    """Where a generator stopped: launches and questions to open, and the open records it waits on.

    groups: open parallel, foreach and item records on the way, cancelled with the group that cuts them.
    """

    proposals: list[Proposal] = field(default_factory=list)
    running: list[CallRecord] = field(default_factory=list)
    humans: list[CallRecord] = field(default_factory=list)
    asks: list[CallRecord] = field(default_factory=list)
    groups: list[CallRecord] = field(default_factory=list)

    def add(self, other: Pending) -> None:
        self.proposals += other.proposals
        self.running += other.running
        self.humans += other.humans
        self.asks += other.asks
        self.groups += other.groups


Outcome: TypeAlias = 'Returned | Raised | Pending'


@dataclass
class Update:
    """A record a replay would close, or a closed one it notes. index: the rows of a foreach's index file to write."""

    record: CallRecord
    status: CallStatus
    changes: dict[str, Any] = field(default_factory=dict)
    index: list[dict[str, Any]] | None = None


@dataclass
class Ending:
    """The run has ended; why goes into the end line and the log, scope resolves the report's files."""

    end: End
    why: str
    scope: Frame | None


@dataclass
class Plan:
    """What one replay leaves to do: records to open and to close, what is open, or the end of the run.

    proposals: the group and item records the replay opened, in the order of their places, then the launches and
    questions.
    """

    proposals: list[Proposal] = field(default_factory=list)
    updates: list[Update] = field(default_factory=list)
    running: list[CallRecord] = field(default_factory=list)
    humans: list[CallRecord] = field(default_factory=list)
    asks: list[CallRecord] = field(default_factory=list)
    ending: Ending | None = None

    @property
    def idle(self) -> bool:
        return not (self.proposals or self.running or self.humans or self.asks)


class Replay:
    """Runs the flow from the top against the recorded calls. It appends nothing to the state, but it numbers
    what it proposes.

    At each yield the call is looked up by its address: a done record returns its result, a failed or blocked
    one throws StepFailed, an open one stops that generator, and no record proposes one. A group drives its
    branches or items as nested generators. A branch or item that does not handle a StepFailed fails its group:
    the group drops the launches and questions its others propose, waits for the launches already running,
    closes the rest as cancelled, and throws the StepFailed into the generator that yielded it.

    A group or an item record the replay proposes takes its place in state.json at once, after the recorded calls
    and the records proposed before it, and is indexed as if recorded: what is inside it is driven in the same
    pass, and its index file has a path. Launches take the places after them when the command commits them.
    """

    def __init__(self, rb: Runbook, state: RunState, run_dir: str) -> None:
        self.rb = rb
        self.run_dir = run_dir
        self.files = RunFiles(run_dir)
        self.ctx = Context(state.inputs)
        self.latest: dict[str, CallRecord] = {}
        self.under: dict[str, list[CallRecord]] = {}
        for record in state.calls:
            self._index(record)
        self.base = len(state.calls)
        self.opened: list[Proposal] = []
        self.updates: list[Update] = []
        self._updated: set[int] = set()
        # The rows of the index files this replay's updates write, by path: on disk only once the command commits.
        self.indexes: dict[str, list[dict[str, Any]]] = {}

    def run(self) -> Plan:
        top = Frame('main', None, {})
        flow = self.rb.flow_fn
        assert flow is not None
        outcome = self._drive(flow, (self.ctx,), top)
        plan = Plan(proposals=list(self.opened), updates=self.updates)
        if isinstance(outcome, Pending):
            plan.proposals += outcome.proposals
            plan.running = outcome.running
            plan.humans, plan.asks = outcome.humans, outcome.asks
        elif isinstance(outcome, Raised):
            error = outcome.error
            plan.ending = Ending(FAILED_END, f'`{error.id}` {error.status}: {error.reason}', top)
        elif isinstance(outcome.value, End):
            plan.ending = Ending(outcome.value, f'after `{top.last}`' if top.last else 'before any call', top)
        else:
            raise FlowError(f'the flow returned {outcome.value!r}; it ends with `return end(<status>, <report>)`')
        return plan

    def _index(self, record: CallRecord) -> None:
        self.latest[record.id] = record
        for i, char in enumerate(record.id):
            if char in '/[':
                self.under.setdefault(record.id[:i], []).append(record)

    def _open(self, record: CallRecord) -> None:
        """Proposes a group or an item record, numbered and indexed so that what is inside it resolves now."""
        record.position = self.base + len(self.opened)
        self.opened.append(Proposal(record))
        self._index(record)

    def _update(
        self, record: CallRecord, status: CallStatus, index: list[dict[str, Any]] | None = None, **changes: Any
    ) -> None:
        if record.status in OPEN and id(record) not in self._updated:
            self._updated.add(id(record))
            self.updates.append(Update(record, status, changes, index))
            if index is not None:
                self.indexes[self.files.index_path(record)] = index

    def _cancel_ask(self, record: CallRecord) -> None:
        """Notes a side-effect step whose question a failing group drops, though the record is closed."""
        if record.note != NOTE_CANCELLED and id(record) not in self._updated:
            self._updated.add(id(record))
            self.updates.append(Update(record, record.status, {'note': NOTE_CANCELLED}))

    def _expect(self, record: CallRecord, kind: Kind, name: str) -> None:
        if (record.kind, record.step) != (kind, name):
            raise CommandError(
                f'`{record.id}` is recorded as {record.kind} `{record.step}`, and the flow yields {kind} `{name}` there: '
                'flow.py changed under this run. Start a new run.'
            )

    def _drive(self, function: Callable[..., Any], args: tuple[Any, ...], frame: Frame) -> Outcome:
        """Runs one generator from its start, sending each yield's result in, until it returns, raises or stops."""
        try:
            generator = function(*args)
        except Exception as e:
            raise _flow_error(frame.path, e) from e
        if not inspect.isgenerator(generator):
            raise FlowError(f'`{frame.path}`: {getattr(function, "__name__", function)} is not a generator function')
        outcome: Returned | Raised = Returned(None)
        while True:
            try:
                if isinstance(outcome, Raised):
                    request = generator.throw(outcome.error)
                else:
                    request = generator.send(outcome.value)
            except StopIteration as stop:
                return Returned(stop.value)
            except StepFailed as e:
                e.id = e.id or frame.path
                return Raised(e)
            except FlowError:
                raise
            except Exception as e:
                raise _flow_error(frame.path, e) from e
            result = self._yielded(request, frame)
            if isinstance(result, Pending):
                return result
            outcome = result

    def _yielded(self, request: Any, frame: Frame) -> Outcome:
        if isinstance(request, Call):
            return self._call(request, frame.address(request.step.name), frame)
        if isinstance(request, Parallel):
            return self._parallel(request, frame.address(request.name), frame)
        if isinstance(request, Foreach):
            return self._foreach(request, frame.address(request.name), frame)
        if isinstance(request, (Step, HumanStep)):
            raise FlowError(f'`{frame.path}` yields step `{request.name}` without calling it: yield {request.name}()')
        raise FlowError(f'`{frame.path}` yields {request!r}: a flow yields a step call, parallel(...) or foreach(...)')

    # ---------- calls ----------

    def _call(self, call: Call, address: str, frame: Frame) -> Outcome:
        step = call.step
        kind: Kind = 'human' if isinstance(step, HumanStep) else 'step'
        record = self.latest.get(address)
        if record is None:
            return Pending(proposals=[self._propose(call, address, frame, 1)])
        self._expect(record, kind, step.name)
        if record.status == 'running':
            return Pending(running=[record])
        if record.status == 'waiting':
            return Pending(humans=[record])
        if record.status == 'cancelled':
            return Pending()
        if record.status == 'done':
            frame.files.update(record.files)
            return Returned(StepResult(**{**record.reply, 'id': address, 'files': dict(record.files)}))
        side_effects = isinstance(step, Step) and step.side_effects
        if record.note == NOTE_RELAUNCHED or (record.status == 'interrupted' and not side_effects):
            return Pending(proposals=[self._propose(call, address, frame, record.attempt + 1)])
        if side_effects:
            return Pending(asks=[record])
        return Raised(StepFailed(id=address, reply={'status': record.status, **(record.reply or {})}))

    def _propose(self, call: Call, address: str, frame: Frame, attempt: int) -> Proposal:
        step = call.step
        if isinstance(step, HumanStep):
            record = CallRecord(id=address, attempt=attempt, kind='human', step=step.name, status='waiting')
            return Proposal(record, question=self.substitute(step.question, frame))
        if call.executor is not None and call.executor not in self.rb.executor_specs:
            raise FlowError(f'`{address}`: executor {call.executor!r} is not declared')
        inputs: dict[str, Any] = {}
        passed: dict[str, str | None] = {}
        for key, value in call.inputs.items():
            _pass(address, key, value, inputs, passed)
        files_in: dict[str, str | None] = {}
        for name in step.reads:
            if name != WORKING_TREE:
                files_in[name] = frame.resolve(name) if self.rb.is_output(name) else os.path.join(self.run_dir, name)
        record = CallRecord(
            id=address,
            attempt=attempt,
            kind='step',
            step=step.name,
            status='running',
            executor=call.executor or step.executor,
            inputs=inputs,
            files_in={**files_in, **passed},
            schema=reply_schema(step.reply),
        )
        return Proposal(record, item=frame.item)

    def substitute(self, text: str, frame: Frame | None) -> str:
        """<run>/<name> becomes the file of that name in the frame's scope; any other <run> the run directory."""
        if frame is not None:
            for name in sorted(frame.names(), key=len, reverse=True):
                text = text.replace(f'<run>/{name}', frame.resolve(name) or '')
        return text.replace('<run>', self.run_dir)

    # ---------- groups ----------

    def _parallel(self, group: Parallel, address: str, frame: Frame) -> Outcome:
        record = self.latest.get(address)
        if record is None:
            record = CallRecord(id=address, kind='parallel', step=group.name, status='running')
            self._open(record)
        self._expect(record, 'parallel', group.name)
        outcomes: dict[str, Outcome] = {}
        for key, branch in group.branches.items():
            branch_frame = Frame(f'{address}/{key}', frame, frame.item)
            if isinstance(branch, Call):
                outcomes[key] = self._call(branch, branch_frame.path, branch_frame)
            else:
                outcomes[key] = self._drive(branch, (self.ctx,), branch_frame)
        failure = next((o.error for o in outcomes.values() if isinstance(o, Raised)), None)
        pending = Pending(groups=[record])
        for outcome in outcomes.values():
            if isinstance(outcome, Pending):
                pending.add(outcome)
        if failure is not None:
            branches = {key: _branch_status(outcome) for key, outcome in outcomes.items()}
            return self._fail(record, failure, pending, branches=branches)
        if any(isinstance(outcome, Pending) for outcome in outcomes.values()):
            return pending
        self._update(record, 'done', branches={key: 'done' for key in outcomes})
        results = {key: outcome.value for key, outcome in outcomes.items() if isinstance(outcome, Returned)}
        return Returned(ParallelResult(**results, id=address))

    def _foreach(self, group: Foreach, address: str, frame: Frame) -> Outcome:
        record = self.latest.get(address)
        if record is None:
            if os.path.isabs(group.over) or not self.rb.is_output(group.over):
                path: str | None = os.path.join(self.run_dir, group.over)
            else:
                path = frame.resolve(group.over)
            items, problem = _read_items(path, group, self.indexes)
            record = CallRecord(
                id=address, kind='foreach', step=group.name, status='running', over=path, items=items, problem=problem
            )
            self._open(record)
        self._expect(record, 'foreach', group.name)
        index_name = f'{group.name}.index'
        if record.problem is not None:
            self._update(record, 'failed', index=[])
            frame.files[index_name] = self.files.index_path(record)
            return Raised(StepFailed(id=address, reply={'status': 'failed', 'reason': record.problem}))
        items = record.items or []
        batch: list[tuple[dict[str, Any], CallRecord]] = []
        for item in items:
            item_record = self.latest.get(f'{address}[{item["key"]}]')
            if item_record is None:
                break
            self._expect(item_record, 'item', group.name)
            batch.append((item, item_record))
        # The recorded items, then batches of new ones while slots are free: an item that ends in this pass frees
        # its slot for the next batch, and a failure with 'fail' starts no more.
        started: list[tuple[dict[str, Any], CallRecord, Outcome]] = []
        failure: StepFailed | None = None
        active = 0
        while True:
            for item, item_record in batch:
                outcome = self._item(group, item, item_record, frame)
                started.append((item, item_record, outcome))
                if isinstance(outcome, Pending):
                    active += 1
                elif isinstance(outcome, Raised) and group.on_item_failure == 'fail' and failure is None:
                    failure = outcome.error
            new = [] if failure else items[len(started) : len(started) + max(0, group.max_concurrent - active)]
            if not new:
                break
            batch = []
            for item in new:
                item_record = CallRecord(
                    id=f'{address}[{item["key"]}]', kind='item', step=group.name, status='running', fields=item
                )
                self._open(item_record)
                batch.append((item, item_record))
        pending = Pending(groups=[record])
        for _, item_record, outcome in started:
            if isinstance(outcome, Pending):
                pending.add(outcome)
                pending.groups.append(item_record)
        rows = [self._row(item, item_record, outcome) for item, item_record, outcome in started]
        rows += [_index_row(item['key'], 'not_started') for item in items[len(started) :]]
        for row, (_, item_record, _) in zip(rows, started):
            if row['status'] != 'running':
                self._update(item_record, row['status'], reply=row['reply'], reason=row['reason'], files=row['files'])
        if failure is not None:
            outcome = self._fail(record, failure, pending, index=rows)
            if isinstance(outcome, Raised):
                frame.files[index_name] = self.files.index_path(record)
            return outcome
        if len(started) < len(items) or active:
            return pending
        self._update(record, 'done', index=rows)
        index_path = self.files.index_path(record)
        frame.files[index_name] = index_path
        return Returned(
            ForeachResult(id=address, files={index_name: index_path}, items=[ItemResult(**row) for row in rows])
        )

    def _item(self, group: Foreach, item: dict[str, Any], record: CallRecord, frame: Frame) -> Outcome:
        item_frame = Frame(record.id, frame, item)
        if isinstance(group.body, Call):
            outcome = self._yielded(group.body, item_frame)
            if isinstance(outcome, Returned):
                outcome = Returned(_result_fields(outcome.value))
        else:
            outcome = self._drive(group.body, (self.ctx, SimpleNamespace(**item)), item_frame)
        if isinstance(outcome, Returned) and not _is_json(outcome.value):
            raise FlowError(
                f'`{record.id}`: the body returned {outcome.value!r}, and an item returns a JSON value, its reply'
            )
        return outcome

    def _row(self, item: dict[str, Any], record: CallRecord, outcome: Outcome) -> dict[str, Any]:
        """The item's entry in the index: key, status, reply, reason, and the files of its latest done launches."""
        files: dict[str, str] = {}
        for inner in self.under.get(record.id, []):
            if inner.kind in ('step', 'human') and inner.status == 'done':
                files.update(inner.files)
        if isinstance(outcome, Returned):
            return _index_row(item['key'], 'done', reply=outcome.value, files=files)
        if isinstance(outcome, Raised):
            return _index_row(item['key'], 'failed', reason=outcome.error.reason, files=files)
        return _index_row(item['key'], 'running', files=files)

    def _fail(self, record: CallRecord, failure: StepFailed, pending: Pending, **close: Any) -> Outcome:
        """A group whose branch or item failed: it waits for what is running, then closes failed and throws.

        close: the record's `branches`, or the `index` rows of a foreach, where a running item is cut.
        """
        for human in pending.humans:
            self._update(human, 'cancelled')
        for ask in pending.asks:
            self._cancel_ask(ask)
        if pending.running:
            return Pending(running=pending.running, groups=pending.groups)
        if 'index' in close:
            close['index'] = [
                {**row, 'status': 'cancelled'} if row['status'] == 'running' else row for row in close['index']
            ]
            for row in close['index']:
                item = self.latest.get(f'{record.id}[{row["key"]}]')
                if item is not None and row['status'] == 'cancelled':
                    self._update(item, 'cancelled', files=row['files'])
        for group in pending.groups:
            if group is not record:
                self._update(group, 'cancelled')
        self._update(record, 'failed', **close)
        return Raised(failure)


def _branch_status(outcome: Outcome) -> str:
    """How a branch of a failed parallel ended: done, failed, or cancelled while it had more to do."""
    if isinstance(outcome, Returned):
        return 'done'
    return 'failed' if isinstance(outcome, Raised) else 'cancelled'


def _index_row(
    key: str, status: str, reply: Any = None, reason: str | None = None, files: dict[str, str] | None = None
) -> dict[str, Any]:
    return dict(key=key, status=status, reply=reply, reason=reason, files=files or {})


def _flow_error(where: str, e: Exception) -> FlowError:
    frames = traceback.extract_tb(e.__traceback__)
    line = f' (line {frames[-1].lineno} of {os.path.basename(frames[-1].filename)})' if frames else ''
    return FlowError(f'the flow raised {type(e).__name__}: {e} at `{where}`{line}')


def _is_json(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_json(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json(v) for k, v in value.items())
    return False


RESULTS = (StepResult, ParallelResult, ForeachResult, ItemResult)


def _holds_result(value: Any) -> bool:
    if isinstance(value, RESULTS):
        return True
    if isinstance(value, dict):
        return any(_holds_result(v) for v in value.values())
    return isinstance(value, (list, tuple)) and any(_holds_result(v) for v in value)


def _pass(address: str, key: str, value: Any, inputs: dict[str, Any], files: dict[str, str | None]) -> None:
    """A keyword argument of a call as lines of its message: a JSON value as `key: value`; a result, or a dict or
    list holding results, as `key.<path>: <field>` and `read key.<path>/<name>: <file>`."""
    if isinstance(value, StepResult):
        inputs.update({f'{key}.{name}': v for name, v in _result_fields(value).items()})
        files.update({f'{key}/{name}': path for name, path in value.files.items()})
    elif isinstance(value, ForeachResult):
        files.update({f'{key}/{name}': path for name, path in value.files.items()})
    elif isinstance(value, ItemResult):
        inputs.update({f'{key}.{name}': v for name, v in vars(value).items() if name != 'files'})
        files.update({f'{key}/{name}': path for name, path in value.files.items()})
    elif isinstance(value, ParallelResult):
        for branch, v in vars(value).items():
            if branch != 'id':
                _pass(address, f'{key}.{branch}', v, inputs, files)
    elif _holds_result(value):
        for part, v in value.items() if isinstance(value, dict) else enumerate(value):
            _pass(address, f'{key}.{part}', v, inputs, files)
    elif _is_json(value):
        inputs[key] = value
    else:
        raise FlowError(f'`{address}`: input {key!r} is {value!r}; a call takes JSON values and results')


ITEM_KEY = NAME


def _read_items(
    path: str | None, group: Foreach, proposed: dict[str, list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], str | None]:
    """The items of a foreach's `over` file, or why there are none to run.

    proposed: index rows by path that the replay has not written yet; such a file is read from here.
    """
    if path is None:
        return [], f'{group.over} is absent, no earlier step wrote it'
    if path in proposed:
        # Through JSON, so that the items are what the file will hold.
        items: Any = json.loads(json.dumps(proposed[path]))
    else:
        try:
            with open(path, encoding='utf-8') as f:
                items = json.load(f)
        except OSError as e:
            return [], f'{path}: {e.strerror}'
        except ValueError as e:
            return [], f'{path} is not JSON: {e}'
    if not isinstance(items, list):
        return [], f'{path} is not a JSON array of objects'
    if len(items) > group.max_items:
        return [], (
            f'{path} has {len(items)} items, more than max_items={group.max_items}. '
            f'Raise max_items in flow.py if the run can take that many, '
            f'or move this stage to a workflow native to the harness'
        )
    seen: set[str] = set()
    for i, item in enumerate(items):
        key = item.get('key') if isinstance(item, dict) else None
        if not isinstance(item, dict):
            return [], f'item {i} of {path} is not a JSON object'
        if not isinstance(key, str) or not ITEM_KEY.fullmatch(key):
            return [], f'item {i} of {path} has key {json.dumps(key)}, not a string of letters, digits, _ . -'
        if key in seen:
            return [], f'item {i} of {path} repeats key {key!r}'
        seen.add(key)
    return items, None


# ---------- what the orchestrator and the executors read ----------

TEXT = {
    # printed for the orchestrator
    'launch': 'launch step `{label}` as a new subagent, executor `{executor}`: {spec}',
    'launch_together': '{count} steps to launch together, in one turn:',
    'launch_side_effects': '. Side effects: {side_effects}',
    'when_finishes': 'when it finishes: {command}',
    'ask': 'ask the human (`{label}`): {question}',
    'ask_choices': '  choices: {choices}. Map the answer to one of them; ask again if none fits.',
    'ask_free': '  free text: pass their words as they are.',
    'ask_fields': '  fields: {fields}. Take them from their words; leave out a field they did not give.',
    'ask_then': '  then: {command}',
    'waiting_for_human': 'waiting for the human on `{label}`. {choices}. When they answer: {command}.',
    'fields': '. Fields: {fields}',
    'choices': 'Choices: {choices}',
    'free_text': 'Free text',
    'side_effect_failure': 'ask the human: step `{label}` has side effects and ended {status}{reason}. '
    'On yes: {relaunch}. On no: {log} and stop.',
    'correct': 'the reply of step `{label}` did not pass the check ({problem}). Send this to the subagent that ran it, '
    'as a follow-up message in its session, not as a new launch:',
    'correct_then': 'when it answers: {command}',
    'correct_cannot': 'if your tool cannot send a message to a subagent that has finished: {command}',
    'still_running': 'still running: {labels}',
    'withdraw': 'withdraw the question `{label}`: its group failed; tell the human no answer is needed',
    'withdraw_ask': 'withdraw the question whether to relaunch step `{label}`: its group failed, and it is not '
    'relaunched; tell the human no answer is needed',
    'idle': 'nothing is pending and the run has not ended. Report that to the human with the run directory, and stop.',
    'wait_for_end': 'wait: {labels}. The run ends {status} once they are recorded.',
    'ended': 'end: {status} ({why}). The run is over. Report to the human: status {status}, run directory {run}{report}.',
    'reason': ' ({reason})',
    'report': ', {report}',
    # placeholders inside the commands the orchestrator fills in
    'reply_arg': "'<the last JSON object of its message>'",
    'uncorrectable_reply': '\'{"status": "failed", "reason": "invalid reply, and its executor could not be asked again"}\'',
    'answer_arg': "'<the choice>' '<their words verbatim, or - to read them from stdin>'",
    'answer_free_arg': "'<their words verbatim, or - to read them from stdin>'",
    'answer_json_arg': "'{{{keys}}}' '<their words verbatim, or - to read them from stdin>'",
    'answer_json_choice': '"choice": "<the choice>"',
    'answer_json_field': '"{name}": <value, if given>',
    'log_arg': "'<their decision>'",
    # sent to the executor, between the message markers
    'message_open': '--- message ---',
    'message_read': 'Read {common}, then {prompt}, and do what they say.',
    'message_repo': 'repo: {repo}',
    'message_run': 'run: {run}',
    'message_input': '{key}: {value}',
    'message_write': 'write {name}: {path}',
    'message_file': 'read {name}: {path}',
    'message_schema': 'reply schema: {path}',
    'absent': 'absent, no earlier step wrote it',
    'message_partial': 'The tree may hold a partial earlier attempt.',
    'message_correct': 'Your final message did not pass the check: {problem}. Do not redo the step and change nothing. '
    'Reply with one JSON object that fits {schema} for the work already done, and nothing else. '
    'If the work is not done, reply failed with a one-line reason.',
    'message_close': '--- end of message ---',
    'missing_repo': '<repo: not among the inputs>',
    'missing_input': '<not among the inputs>',
    'missing_executor': '<executor not declared>',
}


class Renderer:
    """Turns a plan into the lines the orchestrator reads on stdout. The wording lives in TEXT."""

    def __init__(self, rb: Runbook, run_dir: str, state: RunState) -> None:
        self.rb = rb
        self.run_dir = run_dir
        self.state = state
        self.files = RunFiles(run_dir)

    def _command(self, *args: str, placeholder: str = '') -> str:
        command = ' '.join((self.rb.cmd, shlex.quote(self.run_dir), *(shlex.quote(arg) for arg in args)))
        return f'{command} {placeholder}' if placeholder else command

    @staticmethod
    def _labels(records: list[CallRecord]) -> str:
        return ', '.join(f'`{r.label}`' for r in records)

    def wait_for_end(self, ending: Ending, running: list[CallRecord]) -> list[str]:
        return [TEXT['wait_for_end'].format(labels=self._labels(running), status=ending.end.status)]

    def correction(self, record: CallRecord, problem: str) -> list[str]:
        """What the orchestrator sends the executor whose reply did not pass the check."""
        body = TEXT['message_correct'].format(problem=problem, schema=self.files.schema_path(record))
        return [
            TEXT['correct'].format(label=record.label, problem=problem),
            TEXT['message_open'],
            body,
            TEXT['message_close'],
            TEXT['correct_then'].format(command=self._command('reply', record.label, placeholder=TEXT['reply_arg'])),
            TEXT['correct_cannot'].format(
                command=self._command('reply', record.label, placeholder=TEXT['uncorrectable_reply'])
            ),
        ]

    def withdrawn(self, records: list[CallRecord]) -> list[str]:
        """Questions the orchestrator asked that a failing group closed: a human step's, or a side-effect step's."""
        return [
            TEXT['withdraw' if record.kind == 'human' else 'withdraw_ask'].format(label=record.label)
            for record in records
        ]

    def ended(self, ending: Ending, report: str) -> list[str]:
        return [
            TEXT['ended'].format(
                status=ending.end.status,
                why=ending.why,
                run=self.run_dir,
                report=TEXT['report'].format(report=report) if report else '',
            )
        ]

    def pending(self, plan: Plan, opened: list[Proposal]) -> list[str]:
        """The lines for what is open: questions, launches, waits. Blocks are separated by an empty line."""
        new = {id(p.record) for p in opened}
        blocks: list[list[str]] = [
            [self._side_effect_failure(record) for record in plan.asks]
            + [self._waiting_for_human(record) for record in plan.humans if id(record) not in new]
        ]
        launches = [p for p in opened if p.record.kind == 'step']
        if len(launches) > 1:
            blocks.append([TEXT['launch_together'].format(count=len(launches))])
        for proposal in opened:
            blocks.append(self._ask(proposal) if proposal.record.kind == 'human' else self._launch(proposal))
        tail: list[str] = []
        running = [record for record in plan.running if id(record) not in new]
        if running:
            tail.append(TEXT['still_running'].format(labels=self._labels(running)))
        if plan.idle and not opened:
            tail.append(TEXT['idle'])
        blocks.append(tail)
        lines: list[str] = []
        for block in filter(None, blocks):
            lines += ([''] if lines else []) + block
        return lines

    def _side_effect_failure(self, record: CallRecord) -> str:
        reason = (record.reply or {}).get('reason')
        return TEXT['side_effect_failure'].format(
            label=record.label,
            status=record.status,
            reason=TEXT['reason'].format(reason=reason) if reason else '',
            relaunch=self._command('relaunch', record.label),
            log=self._command('log', placeholder=TEXT['log_arg']),
        )

    def _human(self, record: CallRecord) -> HumanStep:
        step = self.rb.steps.get(record.step)
        if not isinstance(step, HumanStep):
            raise CommandError(f'`{record.label}` asks human step `{record.step}`, which flow.py does not declare')
        return step

    def _waiting_for_human(self, record: CallRecord) -> str:
        step = self._human(record)
        choices = TEXT['choices'].format(choices=' | '.join(step.choices)) if step.choices else TEXT['free_text']
        if step.reply:
            choices += TEXT['fields'].format(fields=self._fields(step))
        return TEXT['waiting_for_human'].format(
            label=record.label, choices=choices, command=self._answer_command(step, record)
        )

    def _answer_command(self, step: HumanStep, record: CallRecord) -> str:
        arg = TEXT['answer_arg'] if step.choices else TEXT['answer_free_arg']
        if step.reply:
            keys = ([TEXT['answer_json_choice']] if step.choices else []) + [
                TEXT['answer_json_field'].format(name=name) for name in step.reply
            ]
            arg = TEXT['answer_json_arg'].format(keys=', '.join(keys))
        return self._command('answer', record.label, placeholder=arg)

    @staticmethod
    def _fields(step: HumanStep) -> str:
        """The reply fields of a human step as the orchestrator reads them: name, type, description."""
        parts = []
        for name, declared in step.reply.items():
            schema = _field_schema(declared)
            kind = f' ({schema["type"]})' if isinstance(schema.get('type'), str) else ''
            about = f': {schema["description"]}' if schema.get('description') else ''
            parts.append(f'{name}{kind}{about}')
        return '; '.join(parts)

    def _ask(self, proposal: Proposal) -> list[str]:
        record = proposal.record
        step = self._human(record)
        lines = [TEXT['ask'].format(label=record.label, question=proposal.question)]
        if step.choices:
            lines.append(TEXT['ask_choices'].format(choices=' | '.join(step.choices)))
        else:
            lines.append(TEXT['ask_free'])
        if step.reply:
            lines.append(TEXT['ask_fields'].format(fields=self._fields(step)))
        lines.append(TEXT['ask_then'].format(command=self._answer_command(step, record)))
        return lines

    def _launch(self, proposal: Proposal) -> list[str]:
        record = proposal.record
        step = self.rb.steps[record.step]
        assert isinstance(step, Step)
        headline = TEXT['launch'].format(
            label=record.label,
            executor=record.executor,
            spec=self.rb.executor_specs.get(record.executor or '', TEXT['missing_executor']),
        )
        if step.side_effects:
            headline += TEXT['launch_side_effects'].format(side_effects=step.side_effects)
        lines = [headline, TEXT['message_open'], *self._message(step, proposal), TEXT['message_close']]
        lines.append(
            TEXT['when_finishes'].format(command=self._command('reply', record.label, placeholder=TEXT['reply_arg']))
        )
        return lines

    def _message(self, step: Step, proposal: Proposal) -> list[str]:
        record, inputs = proposal.record, self.state.inputs
        lines = [
            TEXT['message_read'].format(
                common=os.path.join(self.rb.here, 'prompts', 'common.md'),
                prompt=os.path.join(self.rb.here, step.prompt),
            ),
            TEXT['message_repo'].format(repo=inputs.get('repo', TEXT['missing_repo'])),
            TEXT['message_run'].format(run=self.run_dir),
        ]
        for item in step.inputs:
            if isinstance(item, tuple):
                key, value = item
            else:
                key, value = item, inputs.get(item, TEXT['missing_input'])
            lines.append(TEXT['message_input'].format(key=key, value=_line_value(value)))
        for key, value in (*record.inputs.items(), *proposal.item.items()):
            lines.append(TEXT['message_input'].format(key=key, value=_line_value(value)))
        for name, path in record.writes.items():
            lines.append(TEXT['message_write'].format(name=name, path=path))
        for name, path in record.files_in.items():
            lines.append(TEXT['message_file'].format(name=name, path=path or TEXT['absent']))
        lines.append(TEXT['message_schema'].format(path=self.files.schema_path(record)))
        if record.attempt > 1:
            lines.append(TEXT['message_partial'])
        return lines


def _line_value(value: Any) -> str:
    """A value as one line of a launch message: a string with no line break as it is, anything else as JSON."""
    if isinstance(value, str) and '\n' not in value and '\r' not in value:
        return value
    return json.dumps(value, ensure_ascii=False)


# ---------- inputs ----------


def _coerce(value: Any, typ: type) -> Any:
    """Command-line inputs arrive as strings: '2' for an int, 'true' or 'false' for a bool."""
    if isinstance(value, str):
        if typ is int and re.fullmatch(r'-?[0-9]+', value):
            return int(value)
        if typ is bool and value in ('true', 'false'):
            return value == 'true'
    return value


def _is_of_type(value: Any, typ: type) -> bool:
    return isinstance(value, typ) and not (typ is int and isinstance(value, bool))


def _resolve_inputs(spec: dict[str, Any], given: dict[str, Any]) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    problems: list[str] = []
    for name, declared in spec.items():
        required = isinstance(declared, type)
        typ = declared if required else type(declared)
        if name in given:
            value = _coerce(given[name], typ)
            if not _is_of_type(value, typ):
                problems.append(f'input {name!r} must be {typ.__name__}, got {json.dumps(value)}')
            inputs[name] = value
        elif required:
            problems.append(f'input {name!r} is required')
        else:
            inputs[name] = declared
    problems += [f'input {name!r} is not declared' for name in given if name not in spec]
    if problems:
        raise CommandError('start: ' + '; '.join(problems))
    return inputs


# ---------- runbook ----------


@dataclass(frozen=True)
class Command:
    """A CLI command on a run directory: its handler and how many arguments it takes."""

    handler: Callable[[Runbook, str, list[str]], RunState]
    usage: str
    min_args: int
    max_args: int | None


class Runbook:
    """The declarations of a runbook's flow.py and the engine that runs them."""

    def __init__(self) -> None:
        self.steps: dict[str, AnyStep] = {}
        self.flow_fn: Callable[[Context], Generator[Any, Any, Any]] | None = None
        self.input_spec: dict[str, Any] = {}
        self.executor_specs: dict[str, str] = {}
        self.here = os.path.dirname(os.path.abspath(sys.argv[0]))
        # Set by `reply` when it asks the executor to correct its reply; printed before what the run does next.
        self._correcting: tuple[CallRecord, str] | None = None
        # The launch `reply` or `interrupted` closed: if its group drops the question it raises, none was asked yet.
        # Holds for this command only: if its flow raises, the next command withdraws the question though none was asked.
        self._closed: CallRecord | None = None
        flow = os.path.join(self.here, os.path.basename(sys.argv[0]))
        # Under `uv run`, sys.executable is an environment in uv's cache, gone after `uv cache clean`.
        launcher = 'uv run' if 'UV' in os.environ else shlex.quote(sys.executable)
        self.cmd = f'{launcher} {shlex.quote(flow)}'

    def inputs(self, **spec: Any) -> None:
        """Inputs of a run. A type (str, int, bool) is required; a value is a default of its type."""
        self.input_spec = spec

    def executor(self, name: str, description: str) -> None:
        """What the orchestrator launches for this executor name: model, tool, effort, cwd. Printed with every launch."""
        self.executor_specs[name] = description

    def step(
        self,
        name: str,
        *,
        executor: str,
        prompt: str,
        inputs: Iterable[str | tuple[str, Any]] = (),
        reply: dict[str, Any] | None = None,
        reads: Iterable[str] = (),
        writes: Iterable[str] = (),
        side_effects: str | None = None,
    ) -> Step:
        """A step an executor runs. The flow launches it with `r = yield step(...)`.

        executor: a name declared with executor(); a call can pick another one, `step(executor='light')`.
        inputs: names taken from the run's inputs, or (key, value) pairs passed as they are.
        reply: {field: type or JSON Schema of the field} the executor's JSON carries beyond status. The engine
        writes the reply's schema for the executor from it, and checks each field's presence and type.
        writes: names of the files the step writes. Each launch writes <run>/<NN>-<name>, NN being its record's
        place in the run, and the launch message gives the path.
        reads: names of the files the step reads: another step's output, given as the latest such file in the
        calling generator's scope or as absent; an input file under <run>, given as it is; or 'working tree',
        which is not passed.
        side_effects: what the step does outside the tree; such a step is never relaunched without the human.
        """
        self._check_declaration(name, inputs=inputs, reads=reads, writes=writes)
        if not isinstance(executor, str):
            raise TypeError(f'step {name!r}: executor is {executor!r}, not the name of a declared executor')
        reads, writes = list(reads), list(writes)
        _check_strings(name, 'prompt', [prompt])
        _check_strings(name, 'reads', reads)
        _check_strings(name, 'writes', writes)
        if side_effects is not None:
            _check_strings(name, 'side_effects', [side_effects], empty_ok=True)
        inputs = list(inputs)
        for item in inputs:
            if not isinstance(item, str) and not (
                isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str)
            ):
                raise TypeError(f'step {name!r}: inputs item {item!r} is neither a name nor a (key, value) pair')
        _check_reply_names(name, reply)
        step = Step(
            name=name,
            executor=executor,
            prompt=prompt,
            inputs=inputs,
            reply=reply or {},
            reads=list(reads),
            writes=list(writes),
            side_effects=side_effects,
        )
        _check_reply_declaration(step)
        self.steps[name] = step
        return step

    def human(
        self,
        name: str,
        *,
        question: str,
        choices: Iterable[str] = (),
        reply: dict[str, Any] | None = None,
        writes: str | None = None,
    ) -> HumanStep:
        """A question to the human. The flow asks it with `a = yield human_step()`.

        choices: the strings a.choice is one of; the orchestrator maps the answer to one of them. Without
        choices the step takes free text and a.choice is the text whole.
        reply: {field: type or JSON Schema of the field} the orchestrator takes from the human's words next to
        the choice, as a.<field>. A field the human did not give is its schema's `default`, or None.
        writes: a file name the engine writes the human's verbatim words to, numbered like a step's output.
        question: what the orchestrator asks; <run>/<name> in it becomes that file in the calling generator's scope.
        """
        self._check_declaration(name, choices=choices)
        choices = list(choices)
        _check_strings(name, 'question', [question], empty_ok=True)
        _check_strings(name, 'choices', choices)
        if writes is not None:
            _check_strings(name, 'writes', [writes])
        _check_reply_names(name, reply)
        step = HumanStep(name=name, question=question, choices=choices, reply=reply or {}, writes=writes)
        _check_reply_declaration(step)
        self.steps[name] = step
        return step

    def flow(self, function: FlowFunction) -> FlowFunction:
        """Marks the generator function main(ctx) that runs the flow; it returns end(...)."""
        if self.flow_fn is not None:
            raise ValueError(f'@rb.flow: {self.flow_fn.__name__} is already the flow; a runbook has one')
        if not inspect.isgeneratorfunction(function):
            raise TypeError(f'@rb.flow: {getattr(function, "__name__", function)} is not a generator function')
        self.flow_fn = function
        return function

    def is_output(self, name: str) -> bool:
        """Whether a step or a foreach writes files of this name, as against an input file under <run>."""
        return name.endswith('.index') or any(name in _writes(step) for step in self.steps.values())

    def _check_declaration(self, name: str, **collections: Iterable[Any]) -> None:
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError(f'step {name!r}: the name must be letters, digits, _ . -')
        if name in self.steps:
            raise ValueError(f'step {name!r} is already declared')
        for parameter, value in collections.items():
            if isinstance(value, str):
                raise TypeError(f'step {name!r}: {parameter} takes a collection, not a lone string')

    # ---------- check ----------

    def check(self) -> list[str]:
        """Problems in the declarations that can be found without running the flow."""
        if not self.steps:
            return ['no steps declared']
        problems: list[str] = []
        if 'repo' not in self.input_spec:
            problems.append("inputs: 'repo' is not declared")
        if self.flow_fn is None:
            problems.append('no flow: decorate the generator function of the flow with @rb.flow')
        for step in self.steps.values():
            problems += self._check_human(step) if isinstance(step, HumanStep) else self._check_step(step)
        if not os.path.exists(os.path.join(self.here, 'prompts', 'common.md')):
            problems.append('prompts/common.md does not exist')
        return problems

    def _check_human(self, step: HumanStep) -> list[str]:
        problems = []
        if not step.question:
            problems.append(f'step {step.name}: human step without a question')
        return problems

    def _check_step(self, step: Step) -> list[str]:
        n = step.name
        problems = []
        if not os.path.exists(os.path.join(self.here, step.prompt)):
            problems.append(f'step {n}: {step.prompt} does not exist')
        if step.executor not in self.executor_specs:
            problems.append(f'step {n}: executor {step.executor!r} is not declared')
        for item in step.inputs:
            if isinstance(item, str) and item not in self.input_spec:
                problems.append(f'step {n}: input {item!r} is not a declared run input')
        return problems

    # ---------- commands ----------

    def _start(self, run_dir: str, args: list[str]) -> RunState:
        if os.path.exists(RunState.path(run_dir)):
            raise CommandError(
                f'{run_dir} holds a run. Use another run directory, or run me without a command to resume the run there.'
            )
        try:
            given = json.loads(args[0]) if args else {}
        except ValueError:
            given = None
        if not isinstance(given, dict):
            raise CommandError('start: inputs must be one JSON object')
        inputs = _resolve_inputs(self.input_spec, given)
        os.makedirs(run_dir, exist_ok=True)
        state = RunState(runbook=os.path.basename(self.here), status='running', inputs=inputs, calls=[])
        ProgressLog(run_dir).create(state.runbook, inputs)
        return state

    def _reply(self, run_dir: str, args: list[str]) -> RunState:
        label, raw = args
        state = RunState.load(run_dir)
        record = state.find(label)
        if record.kind != 'step' or record.status != 'running':
            raise CommandError(f'call {label} is {record.status}, not running')
        reply: dict[str, Any]
        reply, problem = _parse_reply(raw)
        if problem is None and reply['status'] == 'done':
            problem = _reply_problem(reply, _schema_fields(record.schema))
        log = ProgressLog(run_dir)
        if problem:
            record.invalid_replies.append({'reply': raw, 'problem': problem})
            log.append(f'{record.label}: invalid reply ({problem}): {" ".join(raw.split())}')
            if len(record.invalid_replies) <= CORRECTIONS:
                self._correcting = (record, problem)
                log.append(f'{record.label}: its executor is asked to correct the reply')
                return state
            reply = {'status': 'failed', 'reason': f'invalid reply: {problem}'}
        status = reply.pop('status')
        record.reply = reply
        record.close(status)
        self._closed = record
        missing: list[str] = []
        if status == 'done':
            # What the launch wrote: a declared file it did not write is not passed on as if it had.
            record.files = {name: path for name, path in record.writes.items() if os.path.isfile(path)}
            missing = [name for name in record.writes if name not in record.files]
        line = f'{record.label}: {json.dumps({"status": status, **reply}, ensure_ascii=False)}'
        log.append(line + (f' (did not write {", ".join(missing)})' if missing else ''))
        return state

    def _answer(self, run_dir: str, args: list[str]) -> RunState:
        label, answer = args[0], args[1]
        state = RunState.load(run_dir)
        record = state.find(label)
        if record.kind != 'human' or record.status != 'waiting':
            raise CommandError(f'call {label} is {record.status}, not waiting for the human')
        step = self.steps.get(record.step)
        if not isinstance(step, HumanStep):
            raise CommandError(f'`{label}` asks human step `{record.step}`, which flow.py does not declare')
        fields: dict[str, Any] = {}
        if step.reply:
            picked, fields = _parse_answer(answer, step)
            if not step.choices and len(args) < 3:
                raise CommandError("answer: a free-text step takes the human's words after the JSON object")
            answer = picked if step.choices else args[2]
        words = args[2] if len(args) > 2 else answer
        choice = step.match(answer)
        if choice is None:
            raise CommandError('answer must be one of: ' + ' | '.join(step.choices))
        record.reply = {'choice': choice, **fields}
        record.answer = words
        record.files = dict(record.writes)
        record.close('done')
        for path in record.files.values():
            with open(path, 'w', encoding='utf-8') as f:
                f.write(words.rstrip('\n') + '\n')
        log = ProgressLog(run_dir)
        log.append(
            f'{record.label}: answered: {choice}' + (f' {json.dumps(fields, ensure_ascii=False)}' if fields else '')
        )
        if words != choice:
            log.append(f'{record.label}: said: {words}')
        return state

    def _interrupted(self, run_dir: str, args: list[str]) -> RunState:
        label = args[0]
        state = RunState.load(run_dir)
        record = state.find(label)
        if record.kind != 'step' or record.status != 'running':
            raise CommandError(f'interrupted: call {label} is {record.status}; accepts only a running step')
        record.close('interrupted')
        self._closed = record
        ProgressLog(run_dir).append(f'{record.label}: interrupted')
        return state

    def _relaunch(self, run_dir: str, args: list[str]) -> RunState:
        label = args[0]
        state = RunState.load(run_dir)
        record = state.find(label)
        asked = Replay(self, state, run_dir).run().asks
        if not any(r is record for r in asked):
            raise CommandError(
                f'relaunch: call {label} is {record.status}; accepts only a step with side_effects that ended failed, '
                'blocked or interrupted and that the run asks the human about'
            )
        record.note = NOTE_RELAUNCHED
        ProgressLog(run_dir).append(f'{record.label}: {NOTE_RELAUNCHED}')
        return state

    def _log(self, run_dir: str, args: list[str]) -> RunState:
        state = RunState.load(run_dir)
        ProgressLog(run_dir).append('orchestrator: ' + ' '.join(args))
        return state

    def _status(self, run_dir: str, args: list[str]) -> RunState:
        return RunState.load(run_dir)

    # ---------- advancing ----------

    def _advance(self, run_dir: str, state: RunState) -> list[str]:
        """Replays the run and commits what it proposes until nothing is left to open or close, then writes the
        records, their files and their log lines, and returns the lines to print.

        A replay drives the groups and items it opens in the same pass, and a group that fails there has already
        dropped the launches and questions inside it. So a command is one replay that opens and closes everything
        the command leads to, plus at most one more that finds nothing to do.
        """
        files = RunFiles(run_dir)
        opened: list[Proposal] = []
        withdrawn: list[CallRecord] = []
        schemas: dict[str, dict[str, Any]] = {}
        indexes: dict[str, list[dict[str, Any]]] = {}
        log: list[str] = []
        while True:
            replay = Replay(self, state, run_dir)
            plan = replay.run()
            if not plan.proposals and not plan.updates:
                break
            for proposal in plan.proposals:
                record = proposal.record
                record.started_at = utc_now()
                assert record.kind in ('step', 'human') or record.position == len(state.calls)
                state.append(record)
                step = self.steps.get(record.step)
                if step is not None and record.kind in ('step', 'human'):
                    record.writes = {name: files.path(record, name) for name in _writes(step)}
                    opened.append(proposal)
                if record.kind == 'step' and record.schema is not None:
                    schemas[files.schema_path(record)] = record.schema
                    log.append(f'{record.label}: launched')
                elif record.kind == 'human':
                    log.append(f'{record.label}: asked: {proposal.question}')
            for update in plan.updates:
                record = update.record
                for key, value in update.changes.items():
                    setattr(record, key, value)
                if update.index is not None:
                    record.index = files.index_path(record)
                    indexes[record.index] = update.index
                record.close(update.status)
                if record.kind == 'human' and update.status == 'cancelled':
                    withdrawn.append(record)
                    log.append(f'{record.label}: cancelled')
                elif update.changes.get('note') == NOTE_CANCELLED:
                    if record is not self._closed:
                        withdrawn.append(record)
                    log.append(f'{record.label}: {NOTE_CANCELLED}')
        renderer = Renderer(self, run_dir, state)
        lines = [*renderer.correction(*self._correcting), ''] if self._correcting else []
        if withdrawn:
            lines += [*renderer.withdrawn(withdrawn), '']
        if plan.ending:
            running = [r for r in replay.latest.values() if r.status == 'running']
            if running:
                state.status = 'running'
                lines += renderer.wait_for_end(plan.ending, running)
            else:
                status = plan.ending.end.status
                if state.status != status:
                    log.append(f'end: {status} ({plan.ending.why})')
                state.status = status
                lines += renderer.ended(plan.ending, replay.substitute(plan.ending.end.report, plan.ending.scope))
        else:
            lines += renderer.pending(plan, opened)
            state.status = 'waiting_for_human' if plan.humans else 'running'
        text = state.serialize()
        for path, schema in schemas.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(schema, f, indent=2)
                f.write('\n')
        for path, rows in indexes.items():
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(rows, f, ensure_ascii=False, indent=2)
                f.write('\n')
        progress = ProgressLog(run_dir)
        for line in log:
            progress.append(line)
        state.save(run_dir, text)
        return lines

    # ---------- entry point ----------

    def main(self, argv: list[str] | None = None) -> int:
        """Run the command in argv (sys.argv by default); returns the exit code."""
        argv = sys.argv if argv is None else argv
        if len(argv) < 2 or argv[1] in ('-h', '--help'):
            assert __doc__ is not None
            print(__doc__.strip())
            return 1
        if argv[1] == '--check':
            problems = self.check()
            print('\n'.join(problems) if problems else 'flow.py is consistent.')
            return 1 if problems else 0
        run_dir = os.path.abspath(argv[1])
        try:
            if self.flow_fn is None:
                raise CommandError('no flow: decorate the generator function of the flow with @rb.flow')
            self._correcting = self._closed = None
            name = argv[2] if len(argv) > 2 else 'status'
            command = COMMANDS.get(name)
            if command is None:
                raise CommandError(f'unknown command {name!r}')
            if argv[3:].count('-') > 1:
                raise CommandError(
                    'only one argument can be `-`: stdin is read once. Pass the other one quoted for the shell.'
                )
            args = [sys.stdin.read() if a == '-' else a for a in argv[3:]]
            too_many = command.max_args is not None and len(args) > command.max_args
            if len(args) < command.min_args or too_many:
                raise CommandError(f'usage: flow.py <run> {name} {command.usage}'.rstrip())
            state = command.handler(self, run_dir, args)
            # What the command recorded is saved before the flow runs: if flow.py raises, the reply or the answer
            # is not lost, and running flow.py on the run again takes it from there.
            state.save(run_dir)
            lines = self._advance(run_dir, state)
            print('\n'.join(lines))
            return 0
        except CommandError as e:
            print(f'flow.py: {e}', file=sys.stderr)
            return 2
        except FlowError as e:
            print(
                f'flow.py: {e}. What you passed is recorded. This is a defect in flow.py: report it to the human and stop. '
                f'Once it is fixed, the run goes on with: {self.cmd} {shlex.quote(run_dir)}',
                file=sys.stderr,
            )
            return 2


def _check_strings(step: str, parameter: str, values: Iterable[Any], empty_ok: bool = False) -> None:
    """A declaration's names and texts are strings, and a name is not empty; anything else is refused here.

    An empty question or side_effects passes: --check reports the question, and '' means no side effects.
    """
    for value in values:
        if not isinstance(value, str) or (not value and not empty_ok):
            raise TypeError(f'step {step!r}: {parameter} takes strings, got {value!r}')


def _check_reply_names(step: str, reply: dict[str, Any] | None) -> None:
    for name in reply or {}:
        if name in RESERVED:
            raise ValueError(
                f'step {step!r}: reply field {name!r} is reserved: {", ".join(RESERVED)} are set by the engine'
            )


def _parse_reply(raw: str) -> tuple[dict[str, Any], str | None]:
    """The executor's JSON with the schema's nulls dropped, or what keeps it from being a reply."""
    try:
        reply = json.loads(raw)
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        return {}, 'not a JSON object'
    if not reply:
        return {}, 'the message has no JSON object'
    if 'status' not in reply:
        return {}, "no field 'status'"
    if reply['status'] not in REPLY_STATUSES:
        return {}, f"field 'status' must be one of {', '.join(REPLY_STATUSES)}, got {json.dumps(reply['status'])}"
    # The schema has every property required and the unused ones null: a done reply's null reason and a failed
    # reply's null fields are dropped, so what is recorded is what was said.
    done = reply['status'] == 'done'
    reply = {k: v for k, v in reply.items() if v is not None or (done and k != 'reason')}
    reason = reply.get('reason')
    if not done and not (isinstance(reason, str) and reason.strip()):
        reply['reason'] = NO_REASON
    return reply, None


def _reply_problem(reply: dict[str, Any], fields: dict[str, Any]) -> str | None:
    """What is wrong with a done reply against the declared reply fields, if anything."""
    for name, declared in fields.items():
        if name not in reply:
            return f'no field {name!r}'
        problem = _field_problem(name, reply[name], declared)
        if problem:
            return problem
    return None


JSON_TYPES = {
    bool: 'boolean',
    int: 'integer',
    float: 'number',
    str: 'string',
    list: 'array',
    dict: 'object',
    type(None): 'null',
}
PYTHON_TYPES: dict[str, Any] = {
    'boolean': bool,
    'integer': int,
    'number': (int, float),
    'string': str,
    'array': list,
    'object': dict,
    'null': type(None),
}


def _field_schema(declared: Any) -> dict[str, Any]:
    """The JSON Schema of a reply field: a Python type is shorthand for {'type': ...}."""
    return {'type': JSON_TYPES[declared]} if isinstance(declared, type) else dict(declared)


def _schema_fields(schema: dict[str, Any] | None) -> dict[str, Any]:
    """The reply fields of a recorded reply schema, each as its own schema, as reply_schema() wrote them."""
    properties = (schema or {}).get('properties', {})
    return {name: p['anyOf'][0] for name, p in properties.items() if name not in ('status', 'reason')}


def _type_names(schema: dict[str, Any]) -> list[str]:
    """The JSON type names a field's schema allows; empty when it names none."""
    wanted = schema.get('type', [])
    return [wanted] if isinstance(wanted, str) else list(wanted)


def _field_problem(name: str, value: Any, declared: Any) -> str | None:
    """Checks the value against the field's `type` only: the rest of its schema is for harnesses that enforce one."""
    names = _type_names(_field_schema(declared))
    if not names:
        return None
    for wanted in names:
        typ = PYTHON_TYPES[wanted]
        if isinstance(value, typ) and (typ is bool or not isinstance(value, bool)):
            return None
    return f'field {name!r} must be {" or ".join(names)}, got {json.dumps(value)}'


def _check_reply_declaration(step: AnyStep) -> None:
    problems = _reply_declaration_problems(step)
    if problems:
        raise TypeError(problems[0])


def _reply_declaration_problems(step: AnyStep) -> list[str]:
    problems = []
    for name, declared in step.reply.items():
        if not isinstance(declared, dict) and (not isinstance(declared, type) or declared not in JSON_TYPES):
            problems.append(
                f'step {step.name}: reply field {name!r} is {declared!r}, '
                f'neither a JSON type (bool, int, float, str, list, dict) nor a JSON Schema'
            )
        elif isinstance(declared, dict):
            wanted = declared.get('type', [])
            names = [wanted] if isinstance(wanted, str) else wanted
            known = isinstance(names, list) and all(isinstance(t, str) and t in PYTHON_TYPES for t in names)
            if not known or ('type' in declared and (not names or len(set(names)) < len(names))):
                problems.append(
                    f'step {step.name}: reply field {name!r} has type {wanted!r}, '
                    f'not a JSON type name or a list of different ones'
                )
            if _uses_refs(declared):
                problems.append(
                    f"step {step.name}: reply field {name!r} uses $ref or $defs; a field's schema stands alone"
                )
    return problems


def _uses_refs(schema: Any) -> bool:
    """Whether a schema, or a schema nested in it, has $ref or $defs. Property names and literal values are not schemas."""
    if not isinstance(schema, dict):
        return False
    if '$ref' in schema or '$defs' in schema:
        return True
    nested = [schema.get(k) for k in ('items', 'additionalProperties', 'not')]
    for k in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
        nested += schema[k] if isinstance(schema.get(k), list) else []
    if isinstance(schema.get('properties'), dict):
        nested += schema['properties'].values()
    return any(_uses_refs(s) for s in nested)


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    """The field's schema with null allowed next to its own values; the schema itself is left whole."""
    return {'anyOf': [schema, {'type': 'null'}]}


SCHEMA_ABOUT = (
    'With status done, every field but reason is set and reason is null. '
    'With failed or blocked, reason is one line and the other fields are null.'
)


def reply_schema(fields: dict[str, Any]) -> dict[str, Any]:
    """The JSON Schema of a step's reply.

    One closed object with every property required and the unused ones null: the form harnesses that hold a
    subagent to a schema accept. Which fields go with which status is in the description, and the engine checks it.
    """
    properties = {
        'status': {'type': 'string', 'enum': list(REPLY_STATUSES)},
        'reason': {'type': ['string', 'null']},
        **{name: _nullable(_field_schema(d)) for name, d in fields.items()},
    }
    return {
        'type': 'object',
        'description': SCHEMA_ABOUT,
        'properties': properties,
        'required': list(properties),
        'additionalProperties': False,
    }


def _parse_answer(raw: str, step: HumanStep) -> tuple[str, dict[str, Any]]:
    """The choice ('' for a free-text step) and the fields of an answer to a human step that declares reply fields."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise CommandError('answer: this step takes one JSON object, the choice as "choice" next to its fields')
    choice = data.pop('choice', None)
    if step.choices and not isinstance(choice, str):
        raise CommandError('answer: "choice" must be one of: ' + ' | '.join(step.choices))
    problems = [f'field {name!r} is not declared' for name in data if name not in step.reply]
    fields: dict[str, Any] = {}
    for name, declared in step.reply.items():
        if name in data:
            fields[name] = data[name]
            problems += filter(None, [_field_problem(name, data[name], declared)])
        else:
            fields[name] = _field_schema(declared).get('default')
    if problems:
        raise CommandError('answer: ' + '; '.join(problems))
    return (choice if step.choices else ''), fields


COMMANDS: dict[str, Command] = {
    'start': Command(Runbook._start, "['<inputs JSON>']", 0, 1),
    'reply': Command(Runbook._reply, "<call> '<reply JSON>'", 2, 2),
    'answer': Command(Runbook._answer, "<call> '<choice or JSON object>' ['<verbatim words>' | -]", 2, 3),
    'interrupted': Command(Runbook._interrupted, '<call>', 1, 1),
    'relaunch': Command(Runbook._relaunch, '<call>', 1, 1),
    'log': Command(Runbook._log, "'<text>'", 1, None),
    'status': Command(Runbook._status, '', 0, 0),
}

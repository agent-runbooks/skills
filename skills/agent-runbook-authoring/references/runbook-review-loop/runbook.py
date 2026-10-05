"""Engine of a runbook run: state in state.json, a text log in progress.md, next actions on stdout.

A runbook's flow.py declares inputs, executors and steps with this module and ends with rb.main().
The orchestrator then talks to flow.py:

    flow.py <run> start '<given inputs as JSON>'   new run: creates the directory, prints what to launch
    flow.py <run> reply <section> '<reply JSON>'   a step finished: records the reply, prints what follows
    flow.py <run> answer <section> '<choice>'      the human answered a human step
    flow.py <run> interrupted <section>            a running step's executor is gone: relaunch it
    flow.py <run> relaunch <section>               the human said yes to relaunching a failed side-effect step
    flow.py <run> log '<text>'                     add a line to progress.md
    flow.py <run>                                  nothing new: print what is pending
    flow.py --check                                validate the declarations

`answer` takes the human's words as a last argument, and a JSON object in place of the choice when the step
declares reply fields. One argument may be `-` to read it from stdin, for text with quotes in it.

Source: https://github.com/agent-runbooks/skills/tree/main/skills/agent-runbook-authoring
Each release is tagged agent-runbook-authoring/v<__version__>. Changes to the flow.py API: CHANGELOG.md there.
"""

from __future__ import annotations

__version__ = '1.4.0'

import sys

# Before the other imports, so an older Python stops here with a message and not on a missing name.
if sys.version_info < (3, 9):
    sys.exit(f'runbook.py needs Python 3.9 or newer; {sys.executable} is {sys.version.split()[0]}')

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NoReturn

if TYPE_CHECKING:
    from typing import TypeAlias  # 3.10+; annotations are never evaluated at run time


# ---------- targets ----------


@dataclass(frozen=True)
class End:
    """A target that ends the run with this status; report is what the human is told to read."""

    status: str
    report: str = ''


@dataclass(frozen=True)
class Parallel:
    """A target that launches several steps at once."""

    steps: tuple[str, ...]


# How a failed or blocked step without on_failure, or a failed join, ends the run.
FAILED_END = End('failed', 'read <run>/progress.md')


def end(status: str, report: str = '') -> End:
    """The run ends with this status. report: what the human reads next, printed at the end."""
    return End(status, report)


def parallel(*steps: str) -> Parallel:
    """Launch these steps together. They write different files, and at most one changes the tree."""
    return Parallel(steps)


Target: TypeAlias = 'str | End | Parallel | None'
Route: TypeAlias = 'Target | Callable[..., Target]'


class FlowError(Exception):
    """A function declared in flow.py raised: a defect of the runbook, not of the run."""


def _resolve(route: Any, what: str, *args: Any) -> Any:
    """The route itself, or what it returns when it is a function; `what` names it if it raises."""
    if not callable(route):
        return route
    try:
        return route(*args)
    except Exception as e:
        raise FlowError(f'{what} raised {type(e).__name__}: {e}') from e


def die(msg: str) -> NoReturn:
    """Print an error for the orchestrator and exit with status 2."""
    print(f'flow.py: {msg}', file=sys.stderr)
    sys.exit(2)


# ---------- declarations ----------


@dataclass
class Step:
    """A step an executor runs. Fields mirror the parameters of Runbook.step."""

    name: str
    executor: str | Callable[[State], str]
    prompt: str
    next: Route
    inputs: list[str | tuple[str, Any]] = field(default_factory=list)
    reply: dict[str, Any] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    side_effects: str | None = None
    on_failure: Route = None
    skip: Callable[[State], Target] | None = None


@dataclass
class HumanStep:
    """A question to the human. Fields mirror the parameters of Runbook.human."""

    name: str
    question: str
    next: Route
    choices: list[str] = field(default_factory=list)
    reply: dict[str, Any] = field(default_factory=dict)
    writes: str | None = None
    after: list[str] = field(default_factory=list)

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


class State:
    """What a `next` or `skip` function may ask about the run: `s.inputs.<name>`, `s.done(step)`, `s.failed(step)`,
    `s.reply(step)`, `s.replies(step)`."""

    def __init__(
        self,
        inputs: dict[str, Any],
        done_count: dict[str, int],
        latest: dict[str, Section] | None = None,
        history: dict[str, list[Section]] | None = None,
        failed_count: dict[str, int] | None = None,
    ) -> None:
        self.inputs = SimpleNamespace(**inputs)
        self._done = done_count
        self._failed = failed_count or {}
        self._latest = latest or {}
        self._history = history or {}

    def done(self, step: str) -> int:
        """How many sections of this step are done so far, the one just recorded included."""
        return self._done.get(step, 0)

    def failed(self, step: str) -> int:
        """How many sections of this step ended failed or blocked so far, the one just recorded included.

        Interrupted and relaunched sections are not counted.
        """
        return self._failed.get(step, 0)

    def reply(self, step: str) -> SimpleNamespace | None:
        """The reply of this step's latest section reached so far, if that section is done."""
        section = self._latest.get(step)
        if section is None or section.status is not Status.DONE:
            return None
        return SimpleNamespace(**section.fields())

    def replies(self, step: str) -> list[SimpleNamespace]:
        """The replies of this step's done sections reached so far, oldest first. For a human step, the answers."""
        return [SimpleNamespace(**section.fields()) for section in self._history.get(step, [])]


# ---------- run state ----------


class Status(Enum):
    """Status of a section in state.json."""

    RUNNING = 'running'
    WAITING_FOR_HUMAN = 'waiting_for_human'
    DONE = 'done'
    FAILED = 'failed'
    BLOCKED = 'blocked'

    @property
    def is_open(self) -> bool:
        return self in (Status.RUNNING, Status.WAITING_FOR_HUMAN)


REPLY_STATUSES = (Status.DONE.value, Status.FAILED.value, Status.BLOCKED.value)
NOTE_INTERRUPTED = 'interrupted'
NOTE_RELAUNCHED = "relaunched on the human's yes"
SUPERSEDED_NOTES = ('interrupted', 'relaunched')
NO_REASON = 'no reason given'
# How many times an executor is asked to correct a reply that did not pass the check before the step is failed.
CORRECTIONS = 1


def utc_now() -> str:
    """The current time as state.json records it: UTC, ISO 8601 to the second, `Z` suffix."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


@dataclass
class Section:
    """One launch of a step: its id is the step's name, with a counter from the second launch on (fix, fix-2).

    executor, started_at and ended_at are None in a state.json written before 1.1.0; executor is None for a human step.
    invalid_replies: what the orchestrator passed that did not pass the check, as {'reply': raw, 'problem': ...}.
    """

    id: str
    name: str
    status: Status
    reply: dict[str, Any] | None = None
    note: str | None = None
    answer: str | None = None
    executor: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    invalid_replies: list[dict[str, str]] = field(default_factory=list)

    @property
    def superseded(self) -> bool:
        """Interrupted or relaunched: replay skips it, and the step launches again."""
        return (self.note or '').startswith(SUPERSEDED_NOTES)

    @property
    def reason(self) -> str:
        return (self.reply or {}).get('reason', '')

    def label(self) -> str:
        return self.id

    def fields(self) -> dict[str, Any]:
        """What `next` and `s.reply` see: the reply, and for an answered human step its choice as `choice`."""
        answered = {'choice': self.note} if self.answer is not None else {}
        return {**answered, **(self.reply or {})}

    def close(self, status: Status) -> None:
        """Moves the section to status, stamping ended_at if it leaves an open status."""
        if self.status.is_open and not status.is_open:
            self.ended_at = utc_now()
        self.status = status

    def to_json(self) -> dict[str, Any]:
        return dict(
            id=self.id,
            name=self.name,
            status=self.status.value,
            reply=self.reply,
            note=self.note,
            answer=self.answer,
            executor=self.executor,
            started_at=self.started_at,
            ended_at=self.ended_at,
            invalid_replies=self.invalid_replies,
        )

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Section:
        return cls(
            id=data['id'],
            name=data['name'],
            status=Status(data['status']),
            reply=data['reply'],
            note=data['note'],
            answer=data.get('answer'),
            executor=data.get('executor'),
            started_at=data.get('started_at'),
            ended_at=data.get('ended_at'),
            invalid_replies=data.get('invalid_replies') or [],
        )


@dataclass
class RunState:
    """The contents of <run>/state.json. status is 'running', 'waiting_for_human' or the end status."""

    runbook: str
    status: str
    inputs: dict[str, Any]
    sections: list[Section]

    @staticmethod
    def path(run_dir: str) -> str:
        return os.path.join(run_dir, 'state.json')

    @classmethod
    def load(cls, run_dir: str) -> RunState:
        path = cls.path(run_dir)
        if not os.path.exists(path):
            die(f'{path}: not found')
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        return cls(
            runbook=data['runbook'],
            status=data['status'],
            inputs=data['inputs'],
            sections=[Section.from_json(s) for s in data['sections']],
        )

    def save(self, run_dir: str) -> None:
        data = dict(
            runbook=self.runbook, status=self.status, inputs=self.inputs, sections=[s.to_json() for s in self.sections]
        )
        with open(self.path(run_dir), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def section(self, sid: str) -> Section:
        for section in self.sections:
            if section.id == sid:
                return section
        die(f'no section {sid} in state.json')

    def new_section(self, name: str, status: Status, executor: str | None = None) -> Section:
        earlier = sum(1 for s in self.sections if s.name == name)
        sid = f'{name}-{earlier + 1}' if earlier else name
        section = Section(id=sid, name=name, status=status, executor=executor, started_at=utc_now())
        self.sections.append(section)
        return section


class RunFiles:
    """Step outputs in a run directory: a section writes <run>/<NN>-<name>, NN being its place among the sections."""

    def __init__(self, steps: dict[str, AnyStep], run_dir: str, state: RunState) -> None:
        self.run_dir = run_dir
        self.state = state
        self._writers: dict[str, set[str]] = {}
        for step in steps.values():
            for name in _writes(step):
                self._writers.setdefault(name, set()).add(step.name)

    def path(self, section: Section, name: str) -> str:
        return os.path.join(self.run_dir, f'{self.state.sections.index(section):02d}-{name}')

    def schema_path(self, step: str) -> str:
        """Where the JSON schema of this step's reply is written for its executors."""
        return os.path.join(self.run_dir, 'schemas', f'{step}.json')

    def is_output(self, name: str) -> bool:
        return name in self._writers

    def latest(self, name: str, before: Section | None = None) -> str | None:
        """The file a done section last wrote under this name, among the sections before `before`."""
        end = self.state.sections.index(before) if before is not None else len(self.state.sections)
        found = None
        for i, section in enumerate(self.state.sections[:end]):
            if section.status is Status.DONE and section.name in self._writers.get(name, ()):
                found = os.path.join(self.run_dir, f'{i:02d}-{name}')
        return found

    def substitute(self, text: str) -> str:
        """<run>/<output name> becomes the latest file of that name; any other <run> the run directory."""
        for name in self._writers:
            latest = self.latest(name)
            if latest:
                text = text.replace(f'<run>/{name}', latest)
        return text.replace('<run>', self.run_dir)


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


@dataclass
class Ending:
    """The run has reached an end target, or failed; why goes into the end line and the log."""

    end: End
    why: str


@dataclass
class Plan:
    """What replaying the sections leaves to do."""

    launch: list[str] = field(default_factory=list)
    waiting: list[Section] = field(default_factory=list)
    humans: list[Section] = field(default_factory=list)
    side_effect_failures: list[Section] = field(default_factory=list)
    ending: Ending | None = None
    done_count: dict[str, int] = field(default_factory=dict)
    failed_count: dict[str, int] = field(default_factory=dict)

    @property
    def idle(self) -> bool:
        return not (self.launch or self.waiting or self.humans or self.side_effect_failures)


class Replay:
    """Walks the flow from the first step, matching each visit to the next recorded section of that step.

    A finished section is routed through its step's next or on_failure; a step with no section left is to be
    launched; an open section is waited for. A step with `after` waits until the latest sections of those
    steps are finished, and is visited again whenever another branch finishes. A step whose `skip` returns a
    target is not launched: the walk goes on to that target.
    """

    def __init__(self, steps: dict[str, AnyStep], start: str, state: RunState) -> None:
        self._steps = steps
        self._start = start
        self._state = state
        self._consumed: set[str] = set()
        self._latest: dict[str, Section] = {}
        self._history: dict[str, list[Section]] = {}
        self._pending_joins: set[str] = set()
        self._joined_on: dict[str, dict[str, str]] = {}
        self._take_stale: str | None = None
        self._plan = Plan()

    def run(self) -> Plan:
        self._visit(self._start)
        # Nothing else can bring a join a new section of a step it already joined on: it takes the one it has.
        # The permission is for that one join, not for what the walk reaches after it.
        stuck: set[str] = set()
        while self._plan.idle and not self._plan.ending and self._pending_joins - stuck:
            join = min(self._pending_joins - stuck)
            consumed = len(self._consumed)
            self._take_stale = join
            self._visit(join)
            self._take_stale = None
            stuck = stuck | {join} if len(self._consumed) == consumed else set()
        if self._plan.ending:
            # A branch cut off by the ending may still have an executor at work: the run ends once it reports.
            seen = {s.id for s in self._plan.waiting}
            self._plan.waiting += [
                s for s in self._state.sections if s.status is Status.RUNNING and not s.superseded and s.id not in seen
            ]
        return self._plan

    def _visit(self, name: str) -> None:
        if self._plan.ending:
            return
        if name not in self._steps:
            die(f'step {name!r} is not declared')
        step = self._steps[name]
        if not self._joined(step):
            return
        if isinstance(step, Step) and step.skip is not None:
            target = _resolve(step.skip, f'`skip` of step `{name}`', self._state_now())
            if target is not None:
                self._go(target, f'`{name}` skipped')
                return
        section = self._next_section(name)
        if section is None:
            if name not in self._plan.launch:
                self._plan.launch.append(name)
            return
        self._consumed.add(section.id)
        self._latest[name] = section
        if section.status is Status.RUNNING:
            self._plan.waiting.append(section)
        elif section.status is Status.WAITING_FOR_HUMAN:
            self._plan.humans.append(section)
        elif section.status is Status.DONE:
            self._plan.done_count[name] = self._plan.done_count.get(name, 0) + 1
            self._history.setdefault(name, []).append(section)
            self._route(step, section)
        else:
            self._plan.failed_count[name] = self._plan.failed_count.get(name, 0) + 1
            if isinstance(step, Step) and step.side_effects:
                self._plan.side_effect_failures.append(section)
            else:
                self._route(step, section)

    def _joined(self, step: AnyStep) -> bool:
        """Whether every `after` step has a done section this step has not joined on yet.

        A flow that comes back to the join waits for new sections, not the ones of the round before.
        """
        used = self._joined_on.get(step.name, {})
        take_stale, self._take_stale = self._take_stale == step.name, None
        for dep in step.after:
            latest = self._latest.get(dep)
            stale = latest is not None and used.get(dep) == latest.id and not take_stale
            if latest is None or latest.status.is_open or stale:
                self._pending_joins.add(step.name)
                return False
            if latest.status is not Status.DONE:
                why = f'step `{latest.id}` {latest.status.value} before the join at `{step.name}`'
                self._plan.ending = Ending(FAILED_END, why)
                return False
        self._pending_joins.discard(step.name)
        if step.after:
            self._joined_on[step.name] = {dep: self._latest[dep].id for dep in step.after}
        return True

    def _next_section(self, name: str) -> Section | None:
        for section in self._state.sections:
            if section.name == name and section.id not in self._consumed and not section.superseded:
                return section
        return None

    def _route(self, step: AnyStep, section: Section) -> None:
        target = self._target(step, section)
        if target is None:
            why = f'step `{section.id}` {section.status.value}'
            if section.reason:
                why += f': {section.reason}'
            self._plan.ending = Ending(FAILED_END, why)
            return
        self._go(target, f'after step `{section.id}`')

    def _go(self, target: str | End | Parallel, why: str) -> None:
        if isinstance(target, End):
            self._plan.ending = Ending(target, why)
            return
        targets = target.steps if isinstance(target, Parallel) else (target,)
        for t in targets:
            self._visit(t)
        for j in sorted(self._pending_joins):
            self._visit(j)

    def _state_now(self) -> State:
        return State(self._state.inputs, self._plan.done_count, self._latest, self._history, self._plan.failed_count)

    def _target(self, step: AnyStep, section: Section) -> Target:
        s = self._state_now()
        if isinstance(step, HumanStep):
            answer = SimpleNamespace(**section.fields()) if step.reply else section.note
            return _resolve(step.next, f'`next` of step `{step.name}`', answer, s)
        r = SimpleNamespace(**(section.reply or {}))
        if section.status is Status.DONE:
            return _resolve(step.next, f'`next` of step `{step.name}`', r, s)
        if step.on_failure is not None:
            return _resolve(step.on_failure, f'`on_failure` of step `{step.name}`', r, s)
        return None


# ---------- output ----------

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
        self.files = RunFiles(rb.steps, run_dir, state)

    def _command(self, *args: str) -> str:
        return ' '.join((self.rb.cmd, self.run_dir, *args))

    @staticmethod
    def _labels(sections: list[Section]) -> str:
        return ', '.join(f'`{s.label()}`' for s in sections)

    def wait_for_end(self, ending: Ending, waiting: list[Section]) -> list[str]:
        labels = self._labels(waiting)
        return [TEXT['wait_for_end'].format(labels=labels, status=ending.end.status)]

    def correction(self, section: Section, problem: str) -> list[str]:
        """What the orchestrator sends the executor whose reply did not pass the check."""
        body = TEXT['message_correct'].format(problem=problem, schema=self.files.schema_path(section.name))
        return [
            TEXT['correct'].format(label=section.label(), problem=problem),
            TEXT['message_open'],
            body,
            TEXT['message_close'],
            TEXT['correct_then'].format(command=self._command('reply', section.id, TEXT['reply_arg'])),
            TEXT['correct_cannot'].format(command=self._command('reply', section.id, TEXT['uncorrectable_reply'])),
        ]

    def ended(self, ending: Ending) -> list[str]:
        report = self.files.substitute(ending.end.report)
        return [
            TEXT['ended'].format(
                status=ending.end.status,
                why=ending.why,
                run=self.run_dir,
                report=TEXT['report'].format(report=report) if report else '',
            )
        ]

    def pending(self, plan: Plan, opened: list[Section]) -> list[str]:
        """The lines for what is open: questions, launches, waits. Blocks are separated by an empty line."""
        blocks: list[list[str]] = [
            [self._side_effect_failure(section) for section in plan.side_effect_failures]
            + [self._waiting_for_human(section) for section in plan.humans]
        ]
        launches = [s for s in opened if isinstance(self.rb.steps[s.name], Step)]
        if len(launches) > 1:
            blocks.append([TEXT['launch_together'].format(count=len(launches))])
        for section in opened:
            step = self.rb.steps[section.name]
            blocks.append(self._ask(step, section) if isinstance(step, HumanStep) else self._launch(step, section))
        tail: list[str] = []
        if plan.waiting:
            tail.append(TEXT['still_running'].format(labels=self._labels(plan.waiting)))
        if plan.idle:
            tail.append(TEXT['idle'])
        blocks.append(tail)
        lines: list[str] = []
        for block in filter(None, blocks):
            lines += ([''] if lines else []) + block
        return lines

    def _side_effect_failure(self, section: Section) -> str:
        reason = TEXT['reason'].format(reason=section.reason) if section.reason else ''
        return TEXT['side_effect_failure'].format(
            label=section.label(),
            status=section.status.value,
            reason=reason,
            relaunch=self._command('relaunch', section.id),
            log=self._command('log', TEXT['log_arg']),
        )

    def _waiting_for_human(self, section: Section) -> str:
        step = self.rb.steps[section.name]
        assert isinstance(step, HumanStep)
        choices = TEXT['choices'].format(choices=' | '.join(step.choices)) if step.choices else TEXT['free_text']
        if step.reply:
            choices += TEXT['fields'].format(fields=self._fields(step))
        return TEXT['waiting_for_human'].format(
            label=section.label(), choices=choices, command=self._answer_command(step, section)
        )

    def _answer_command(self, step: HumanStep, section: Section) -> str:
        arg = TEXT['answer_arg'] if step.choices else TEXT['answer_free_arg']
        if step.reply:
            keys = ([TEXT['answer_json_choice']] if step.choices else []) + [
                TEXT['answer_json_field'].format(name=name) for name in step.reply
            ]
            arg = TEXT['answer_json_arg'].format(keys=', '.join(keys))
        return self._command('answer', section.id, arg)

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

    def _ask(self, step: HumanStep, section: Section) -> list[str]:
        lines = [TEXT['ask'].format(label=section.label(), question=self.files.substitute(step.question))]
        if step.choices:
            lines.append(TEXT['ask_choices'].format(choices=' | '.join(step.choices)))
        else:
            lines.append(TEXT['ask_free'])
        if step.reply:
            lines.append(TEXT['ask_fields'].format(fields=self._fields(step)))
        lines.append(TEXT['ask_then'].format(command=self._answer_command(step, section)))
        return lines

    def _launch(self, step: Step, section: Section) -> list[str]:
        inputs = self.state.inputs
        executor = section.executor
        headline = TEXT['launch'].format(
            label=section.label(),
            executor=executor,
            spec=self.rb.executor_specs.get(executor or '', TEXT['missing_executor']),
        )
        if step.side_effects:
            headline += TEXT['launch_side_effects'].format(side_effects=step.side_effects)
        lines = [headline, TEXT['message_open'], *self._message(step, section, inputs), TEXT['message_close']]
        lines.append(TEXT['when_finishes'].format(command=self._command('reply', section.id, TEXT['reply_arg'])))
        return lines

    def _message(self, step: Step, section: Section, inputs: dict[str, Any]) -> list[str]:
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
            lines.append(TEXT['message_input'].format(key=key, value=value))
        for name in step.writes:
            lines.append(TEXT['message_write'].format(name=name, path=self.files.path(section, name)))
        for name in step.reads:
            if name == WORKING_TREE:
                continue
            path = (
                self.files.latest(name, before=section)
                if self.files.is_output(name)
                else os.path.join(self.run_dir, name)
            )
            lines.append(TEXT['message_file'].format(name=name, path=path or TEXT['absent']))
        lines.append(TEXT['message_schema'].format(path=self.files.schema_path(step.name)))
        if any(s.name == step.name and s.superseded for s in self.state.sections):
            lines.append(TEXT['message_partial'])
        return lines


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
        die('start: ' + '; '.join(problems))
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
        self.start_step: str | None = None
        self.input_spec: dict[str, Any] = {}
        self.executor_specs: dict[str, str] = {}
        self.here = os.path.dirname(os.path.abspath(sys.argv[0]))
        # Set by `reply` when it asks the executor to correct its reply; printed before what the run does next.
        self._correcting: tuple[Section, str] | None = None
        self.cmd = f'{sys.executable} {os.path.join(self.here, os.path.basename(sys.argv[0]))}'

    def inputs(self, **spec: Any) -> None:
        """Inputs of a run. A type (str, int, bool) is required; a value is a default of its type."""
        self.input_spec = spec

    def executor(self, name: str, description: str) -> None:
        """What the orchestrator launches for this executor name: model, tool, effort, cwd. Printed with every launch."""
        self.executor_specs[name] = description

    def start(self, name: str) -> None:
        """The step a run begins with."""
        self.start_step = name

    def step(
        self,
        name: str,
        *,
        executor: str | Callable[[State], str],
        prompt: str,
        next: Route,
        inputs: Any = (),
        reply: dict[str, Any] | None = None,
        reads: Any = (),
        writes: Any = (),
        after: Any = (),
        side_effects: str | None = None,
        on_failure: Route = None,
        skip: Callable[[State], Target] | None = None,
    ) -> None:
        """A step an executor runs.

        executor: a name declared with executor(), or a function (s) -> name, to pick by inputs.
        next: a step name, parallel(...), end(...), or a function (r, s) -> one of those, where r is the
        reply with its fields as attributes and s is the State. Called for done replies only.
        on_failure: the same, called for failed and blocked replies. Without it they end the run as failed.
        inputs: names taken from the run's inputs, or (key, value) pairs passed as they are.
        reply: {field: type or JSON Schema of the field} the executor's JSON carries beyond status. The engine
        writes the reply's schema for the executor from it, and checks each field's presence and type.
        writes: names of the files the step writes. Each launch writes <run>/<NN>-<name>, NN being its section's
        place in the run, and the launch message gives the path.
        reads: names of the files the step reads: another step's output, given as the latest such file or as
        absent; an input file under <run>, given as it is; or 'working tree', which is not passed.
        after: steps whose latest sections must be done before this one launches.
        side_effects: what the step does outside the tree; such a step is never relaunched without the human.
        skip: a function (s) -> target or None, called once `after` is satisfied. A target is followed instead
        of launching the step.
        """
        self.steps[name] = Step(
            name=name,
            executor=executor,
            prompt=prompt,
            next=next,
            inputs=list(inputs),
            reply=reply or {},
            reads=list(reads),
            writes=list(writes),
            after=list(after),
            side_effects=side_effects,
            on_failure=on_failure,
            skip=skip,
        )

    def human(
        self,
        name: str,
        *,
        question: str,
        next: Route,
        choices: Any = (),
        reply: dict[str, Any] | None = None,
        writes: str | None = None,
        after: Any = (),
    ) -> None:
        """A question to the human.

        choices: the strings next() compares against; the orchestrator maps the answer to one of them. Without
        choices the step takes free text and next() gets it whole.
        reply: {field: type or JSON Schema of the field} the orchestrator takes from the human's words next to
        the choice. A field the human did not give is its schema's `default`, or None.
        next: a function (choice, s) -> step, parallel(...) or end(...). With `reply`, a function (a, s), where
        a has the choice as a.choice and the fields as attributes.
        writes: a file name the engine writes the human's verbatim words to, numbered like a step's output.
        """
        self.steps[name] = HumanStep(
            name=name,
            question=question,
            choices=list(choices),
            next=next,
            reply=reply or {},
            writes=writes,
            after=list(after),
        )

    # ---------- check ----------

    def check(self) -> list[str]:
        """Problems in the declarations that can be found without running the flow."""
        if not self.steps:
            return ['no steps declared']
        problems: list[str] = []
        if 'repo' not in self.input_spec:
            problems.append("inputs: 'repo' is not declared")
        if self.start_step is None:
            problems.append('no start step: call rb.start(<name>)')
        elif self.start_step not in self.steps:
            problems.append(f'start step {self.start_step!r} is not declared')
        for name, step in self.steps.items():
            problems += [f'step {name}: after({d!r}) is not declared' for d in step.after if d not in self.steps]
            if isinstance(step, HumanStep):
                problems += self._check_human(step)
            else:
                problems += self._check_step(step)
        if not os.path.exists(os.path.join(self.here, 'prompts', 'common.md')):
            problems.append('prompts/common.md does not exist')
        return problems

    def _check_human(self, step: HumanStep) -> list[str]:
        problems = []
        if not step.question:
            problems.append(f'step {step.name}: human step without a question')
        return problems + _reply_declaration_problems(step, reserved=('choice',))

    def _check_step(self, step: Step) -> list[str]:
        n = step.name
        problems = []
        if not os.path.exists(os.path.join(self.here, step.prompt)):
            problems.append(f'step {n}: {step.prompt} does not exist')
        if isinstance(step.executor, str) and step.executor not in self.executor_specs:
            problems.append(f'step {n}: executor {step.executor!r} is not declared')
        for item in step.inputs:
            if isinstance(item, str) and item not in self.input_spec:
                problems.append(f'step {n}: input {item!r} is not a declared run input')
            elif not isinstance(item, (str, tuple)):
                problems.append(f'step {n}: input {item!r} is neither a name nor a (key, value) pair')
        if step.skip is not None and not callable(step.skip):
            problems.append(f'step {n}: skip is {step.skip!r}, not a function')
        nxt = step.next
        if not callable(nxt) and not isinstance(nxt, (str, End, Parallel)):
            problems.append(f'step {n}: next is {nxt!r}')
        if isinstance(nxt, str) and nxt not in self.steps:
            problems.append(f'step {n}: next step {nxt!r} is not declared')
        if isinstance(nxt, Parallel):
            problems += [f'step {n}: parallel target {t!r} is not declared' for t in nxt.steps if t not in self.steps]
        return problems + _reply_declaration_problems(step, reserved=('status', 'reason'))

    # ---------- commands ----------

    def _start(self, run_dir: str, args: list[str]) -> RunState:
        if os.path.exists(run_dir):
            die(f'{run_dir} exists. Use another run directory, or run me without a command to resume a run there.')
        try:
            given = json.loads(args[0]) if args else {}
        except ValueError:
            given = None
        if not isinstance(given, dict):
            die('start: inputs must be one JSON object')
        inputs = _resolve_inputs(self.input_spec, given)
        os.makedirs(run_dir)
        state = RunState(runbook=os.path.basename(self.here), status=Status.RUNNING.value, inputs=inputs, sections=[])
        ProgressLog(run_dir).create(state.runbook, inputs)
        return state

    def _reply(self, run_dir: str, args: list[str]) -> RunState:
        sid, raw = args
        state = RunState.load(run_dir)
        section = state.section(sid)
        if section.status is not Status.RUNNING:
            die(f'section {sid} is {section.status.value}, not running')
        reply, problem = _parse_reply(raw)
        step = self.steps.get(section.name)
        if problem is None and reply['status'] == Status.DONE.value and isinstance(step, Step):
            problem = _reply_problem(reply, step.reply)
        log = ProgressLog(run_dir)
        if problem:
            section.invalid_replies.append({'reply': raw, 'problem': problem})
            log.append(f'{sid}: invalid reply ({problem}): {" ".join(raw.split())}')
            if len(section.invalid_replies) <= CORRECTIONS:
                self._correcting = (section, problem)
                log.append(f'{sid}: its executor is asked to correct the reply')
                return state
            reply = {'status': Status.FAILED.value, 'reason': f'invalid reply: {problem}'}
        section.reply = reply
        section.close(Status(reply['status']))
        log.append(f'{sid}: {json.dumps(reply, ensure_ascii=False)}')
        return state

    def _answer(self, run_dir: str, args: list[str]) -> RunState:
        sid, answer = args[0], args[1]
        state = RunState.load(run_dir)
        section = state.section(sid)
        if section.status is not Status.WAITING_FOR_HUMAN:
            die(f'section {sid} is {section.status.value}, not waiting_for_human')
        step = self.steps[section.name]
        assert isinstance(step, HumanStep)
        fields: dict[str, Any] = {}
        if step.reply:
            picked, fields = _parse_answer(answer, step)
            if not step.choices and len(args) < 3:
                die("answer: a free-text step takes the human's words after the JSON object")
            answer = picked if step.choices else args[2]
        words = args[2] if len(args) > 2 else answer
        choice = step.match(answer)
        if choice is None:
            die('answer must be one of: ' + ' | '.join(step.choices))
        section.note, section.answer = choice, words
        if step.reply:
            section.reply = {'choice': choice, **fields}
        section.close(Status.DONE)
        if step.writes:
            with open(RunFiles(self.steps, run_dir, state).path(section, step.writes), 'w', encoding='utf-8') as f:
                f.write(words.rstrip('\n') + '\n')
        log = ProgressLog(run_dir)
        log.append(f'{sid}: answered: {choice}' + (f' {json.dumps(fields, ensure_ascii=False)}' if fields else ''))
        if words != choice:
            log.append(f'{sid}: said: {words}')
        return state

    def _interrupted(self, run_dir: str, args: list[str]) -> RunState:
        return self._supersede(run_dir, args[0], NOTE_INTERRUPTED)

    def _relaunch(self, run_dir: str, args: list[str]) -> RunState:
        return self._supersede(run_dir, args[0], NOTE_RELAUNCHED)

    def _supersede(self, run_dir: str, sid: str, note: str) -> RunState:
        state = RunState.load(run_dir)
        section = state.section(sid)
        section.note = note
        section.close(Status.FAILED)
        ProgressLog(run_dir).append(f'{sid}: {note}')
        return state

    def _log(self, run_dir: str, args: list[str]) -> RunState:
        state = RunState.load(run_dir)
        ProgressLog(run_dir).append('orchestrator: ' + ' '.join(args))
        return state

    def _status(self, run_dir: str, args: list[str]) -> RunState:
        return RunState.load(run_dir)

    # ---------- advancing ----------

    def _advance(self, run_dir: str, state: RunState) -> list[str]:
        """Replays the run, opens sections for what is to launch, and returns the lines to print."""
        assert self.start_step is not None
        plan = Replay(self.steps, self.start_step, state).run()
        renderer = Renderer(self, run_dir, state)
        if self._correcting:
            correction = renderer.correction(*self._correcting)
            return [*correction, '', *self._next_lines(run_dir, state, plan, renderer)]
        return self._next_lines(run_dir, state, plan, renderer)

    def _next_lines(self, run_dir: str, state: RunState, plan: Plan, renderer: Renderer) -> list[str]:
        if plan.ending and plan.waiting:
            state.status = Status.RUNNING.value
            return renderer.wait_for_end(plan.ending, plan.waiting)
        if plan.ending:
            status = plan.ending.end.status
            if state.status != status:
                ProgressLog(run_dir).append(f'end: {status} ({plan.ending.why})')
            state.status = status
            return renderer.ended(plan.ending)
        opened = [self._open_section(run_dir, state, name, plan) for name in plan.launch]
        lines = renderer.pending(plan, opened)
        asking = any(s.status is Status.WAITING_FOR_HUMAN for s in state.sections)
        state.status = Status.WAITING_FOR_HUMAN.value if asking else Status.RUNNING.value
        return lines

    def _open_section(self, run_dir: str, state: RunState, name: str, plan: Plan) -> Section:
        step = self.steps[name]
        log = ProgressLog(run_dir)
        if isinstance(step, HumanStep):
            section = state.new_section(name, Status.WAITING_FOR_HUMAN)
            log.append(f'{section.label()}: asked: {RunFiles(self.steps, run_dir, state).substitute(step.question)}')
        else:
            s = State(state.inputs, plan.done_count, failed_count=plan.failed_count)
            executor = _resolve(step.executor, f'`executor` of step `{name}`', s)
            section = state.new_section(name, Status.RUNNING, executor)
            log.append(f'{section.label()}: launched')
            schema = RunFiles(self.steps, run_dir, state).schema_path(name)
            os.makedirs(os.path.dirname(schema), exist_ok=True)
            with open(schema, 'w', encoding='utf-8') as f:
                json.dump(reply_schema(step.reply), f, indent=2)
                f.write('\n')
        return section

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
        if self.start_step is None:
            die('no start step: call rb.start(<name>)')
        self._correcting = None
        run_dir = os.path.abspath(argv[1])
        name = argv[2] if len(argv) > 2 else 'status'
        command = COMMANDS.get(name) or die(f'unknown command {name!r}')
        if argv[3:].count('-') > 1:
            die('only one argument can be `-`: stdin is read once. Pass the other one quoted for the shell.')
        args = [sys.stdin.read() if a == '-' else a for a in argv[3:]]
        too_many = command.max_args is not None and len(args) > command.max_args
        if len(args) < command.min_args or too_many:
            die(f'usage: flow.py <run> {name} {command.usage}'.rstrip())
        state = command.handler(self, run_dir, args)
        # What the command recorded is saved before the transition is computed: if a function of flow.py raises,
        # the reply or the answer is not lost, and running flow.py on the run again takes it from there.
        state.save(run_dir)
        try:
            lines = self._advance(run_dir, state)
        except FlowError as e:
            die(
                f'{e}. What you passed is recorded. This is a defect in flow.py: report it to the human and stop. '
                f'Once it is fixed, the run goes on with: {self.cmd} {run_dir}'
            )
        state.save(run_dir)
        print('\n'.join(lines))
        return 0


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
    done = reply['status'] == Status.DONE.value
    reply = {k: v for k, v in reply.items() if v is not None or (done and k != 'reason')}
    reason = reply.get('reason')
    if not done and not (isinstance(reason, str) and reason.strip()):
        reply['reason'] = NO_REASON
    return reply, None


def _reply_problem(reply: dict[str, Any], fields: dict[str, type]) -> str | None:
    """What is wrong with a done reply against the step's declared reply fields, if anything."""
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


def _reply_declaration_problems(step: AnyStep, reserved: tuple[str, ...]) -> list[str]:
    problems = []
    for name, declared in step.reply.items():
        if name in reserved:
            problems.append(f'step {step.name}: reply field {name!r} is set by the engine')
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
        die('answer: this step takes one JSON object, the choice as "choice" next to its fields')
    choice = data.pop('choice', None)
    if step.choices and not isinstance(choice, str):
        die('answer: "choice" must be one of: ' + ' | '.join(step.choices))
    problems = [f'field {name!r} is not declared' for name in data if name not in step.reply]
    fields: dict[str, Any] = {}
    for name, declared in step.reply.items():
        if name in data:
            fields[name] = data[name]
            problems += filter(None, [_field_problem(name, data[name], declared)])
        else:
            fields[name] = _field_schema(declared).get('default')
    if problems:
        die('answer: ' + '; '.join(problems))
    return (choice if step.choices else ''), fields


COMMANDS: dict[str, Command] = {
    'start': Command(Runbook._start, "['<inputs JSON>']", 0, 1),
    'reply': Command(Runbook._reply, "<section> '<reply JSON>'", 2, 2),
    'answer': Command(Runbook._answer, "<section> '<choice or JSON object>' ['<verbatim words>' | -]", 2, 3),
    'interrupted': Command(Runbook._interrupted, '<section>', 1, 1),
    'relaunch': Command(Runbook._relaunch, '<section>', 1, 1),
    'log': Command(Runbook._log, "'<text>'", 1, None),
    'status': Command(Runbook._status, '', 0, 0),
}

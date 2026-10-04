# Common

Repository: `<repo>`, a git repository. Work in it and run every command from it, whichever directory your session started in. The brief of the change is `<run>/brief.md`. The rules of the repository are in its instructions file, `AGENTS.md` or `CLAUDE.md` at its root: read it if your harness has not loaded it, and follow it, except for anything that would start a procedure the executor constraints below rule out.

"The changes" are everything uncommitted in `<repo>` outside `.agent-runbooks/`, where the run directory may live: `git status` and `git diff HEAD`, plus untracked files read in full. Files the checks write, such as caches and build output, are not part of the changes.

"The checks" are the shell command the launch message gives as `checks`, run from `<repo>` exactly as given. An empty `checks` means the commands the instructions file names for checking the project. When it names none either, the checks are skipped. Run every check even if an earlier one failed. A check that cannot run, because a binary or a dependency is missing, has failed, not been skipped.

A report that mentions the checks has a "Checks" section: each command with its exit code and, when the exit code is not 0, its output. Skipped checks are one line, `skipped: <why>`.

## Executor constraints

You run one step of a larger procedure. The project's procedures for task cycles, review and commit are not yours to start. Change repository files only as your step instructs, and leave the changes uncommitted unless it instructs a commit. A file name in your step's prompt is a name, not a path: the launch message gives a `write <name>: <path>` line for each file you write, and a `read <name>: <path>` line for each file you read, or says it is absent. Write other files only at the paths your launch message gives. Commits, pushes, comments, tickets and other external writes happen only when your step instructs them. Nobody will answer a question. If you cannot proceed, stop and reply `blocked`.

Your final message is one JSON object that fits the schema in the file your launch message gives as `reply schema`, and nothing else. `status` is `done` when the deliverable exists as described, `failed` when you tried and it does not, `blocked` when you cannot proceed. The schema lists every property as required: with `done`, `reason` is null and the other fields are set; with `failed` or `blocked`, `reason` is one line and the other fields are null. Explanations and evidence go into your step's output file.

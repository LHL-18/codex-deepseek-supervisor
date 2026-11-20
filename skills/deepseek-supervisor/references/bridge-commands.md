# Bridge command reference

In examples, `B` means a command prefix, not a real executable:

```text
python <skill-dir>/scripts/bridge.py --project <project-root>
```

Use the actual absolute skill path and repository root. Quote paths with spaces
using the active shell's quoting rules. Never put the API key in the command.
The user invokes the skill in chat; the following commands are for Codex to run.

## Task session

```text
B status
B start --task "task scope"
B stop --reason "task delivered; collaboration ended"
```

`status` reads the mode only and does not submit API calls. `start` permits
subsequent calls for that task; it does not itself call either model. A session
expires after 8 hours. `stop` blocks new DeepSeek requests but does not cancel a
request already sent or mark unfinished runs as successful.

## Assign a subtask

```text
B ask --task-file tools/.deepseek-runs/task.txt --file path/to/source.py --file path/to/related_test.py --max-tokens 4096 --timeout 90
```

Short tasks may use `--task "instructions"` instead. The task should state the
goal, editable scope, interface constraints, desired output and acceptance checks.
Source paths are relative to the selected project root. The worker accepts only
allowed UTF-8 source types tracked by Git within that root; `.env` is blocked.
Combined task/context input is capped at 120 KB and output at 1..8192 tokens.
Local environment/configuration controls the model; preserve `deepseek-flash`
unless the user requests another model. No automatic HTTP retries occur.

Each call returns JSON containing `ok`, `run_id`, model, content, usage, finish
reason and a local receipt path. `ok: false`, a nonzero exit code or a truncated
finish reason must not be treated as a complete patch.

The worker stores actual task/context in local ignored traces for observability.
Keep prompts and selected code free of credentials and actual patient data.
It does not expose response content as executable commands.

## Review and apply

```text
B record --run RUN_ID --kind review_started --summary "Codex is reviewing the proposal"
B record --run RUN_ID --kind review --summary "Concrete review conclusion" --detail "Evidence from source/contracts" --outcome accepted
B record --run RUN_ID --kind changes_started --summary "Applying reviewed changes"
B record --run RUN_ID --kind changes --summary "Files and behavior actually changed" --outcome applied
```

Use `--outcome rejected` when requesting correction. For analysis-only work, record
changes with `--outcome not_applicable` and explain that no product files changed.
The recorder's change entries describe edits; Codex must actually apply those edits
using its normal tools. Do not present recording an entry as performing a change.

## Actual validation

```text
B record --run RUN_ID --kind validation --summary "Relevant check" --command-json '["python","-m","pytest","tests/test_feature.py","-q"]'
```

`--command-json` is an argv array, executed from the project root with `shell=False`.
The recorder emits a start event, then captures the command output and real exit
code. It limits the check to 180 seconds. A failed or unstartable check returns
nonzero; diagnose before retrying. If the test belongs in a subdirectory, use the
tool's explicit root-relative paths or an appropriate executable/argument list.
Do not embed shell operators in that list. Use a normal Codex execution tool for
longer checks; report their practical limits rather than fabricating trace results.

The recorder retains outstanding failed checks. A passing unrelated command does
not erase an earlier failed validation; rerun the same failed check after a fix.
For a pure analysis task, validation can be `not_applicable` with a precise reason
only if no executable acceptance check is warranted. Source inspection findings
should still appear in the review evidence.

## Finish

```text
B record --run RUN_ID --kind complete --summary "Accepted work and checks completed"
```

For the final accepted run, add `--end-session` to close the authorization when
all runs in its session are complete. If rejected/failed attempts remain, preserve
them, finish accepted runs normally and call `B stop --reason "..."` explicitly.
Inspect `B status` at the end; it should be off unless the user authorized keeping
the same unfinished task active.

The optional dashboard reads the same files; it is not required for execution.

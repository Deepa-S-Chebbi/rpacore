# RPA Core Tutorial

This guide walks through a small RPA Core project using PowerShell and the
published package workflow.

## Assumptions

- You have Python 3.11 or newer.
- You want a separate project that installs RPA Core like a user project.
- You are comfortable creating a few files by hand.

## What You Will Build

By the end, you will have a small automation project that:

- installs RPA Core into its own virtual environment
- defines one custom `Step`
- runs a `Transaction`
- writes output to a file
- checkpoints the transaction to SQLite during execution
- inspects the saved transaction from the CLI

The final project structure will look like this:

```text
hello-rpacore/
  .venv/
  config.toml
  main.py
  steps/
    __init__.py
    greet_user.py
```

## Step 1: Check Python

```powershell
python --version
```

You should see Python 3.11 or newer.

## Step 2: Create The Project

```powershell
mkdir hello-rpacore
cd hello-rpacore
mkdir steps
New-Item -ItemType File steps\__init__.py | Out-Null
```

User automations live outside the framework package. This keeps application code
separate from `rpacore` itself.

For a runnable starting point instead, `rpacore init hello-rpacore` creates a
project with a manifest, configured logging, and run receipts. Before running
it, `rpacore doctor` reports absent discovered storage as unchecked; verify its
configured location and any recovery/backup needs before creating new storage.
The generated `generated-greeting/v2` definition writes `greeting.txt` atomically
in the project root even from a nested working directory. Its persisted output
argument, state, and artifact expose an absolute local path: copied records keep
that original destination on resume, while a fresh run uses the current root.
Earlier v1 projects remain unchanged and require no migration. The remaining
steps below describe a separate hand-written application with its own definition
and path choices.

## Step 3: Create A Virtual Environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run this once in the current shell:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Step 4: Install RPA Core

```powershell
pip install rpacore
python -c "import rpacore; print(rpacore.__version__)"
```

## Step 5: Create `config.toml`

```toml
max_retries = 1
retry_delay = 0.0
retry_backoff = 1.0
log_level = "INFO"
log_format = "text"
transaction_db_path = "rpacore.db"
screenshot_dir = ""
credential_provider = "env"
```

## Step 6: Create Your First Step

Create `steps\greet_user.py`:

```python
from __future__ import annotations

from pathlib import Path

from rpacore import BusinessException, ProcessContext, Step


class WriteGreeting(Step):
    def execute(self, ctx: ProcessContext) -> None:
        name = self.arguments.get("name")
        if not name or not str(name).strip():
            raise BusinessException(
                message="'name' is required",
                action="WriteGreeting",
            )

        output_path = Path(str(self.arguments.get("output_path", "greeting.txt")))
        output_path.write_text(f"Hello, {name}!\n", encoding="utf-8")
        ctx.state["greeting_path"] = str(output_path)
```

The step reads its arguments, raises a business exception for missing input,
writes a file, and stores durable state for later inspection.

## Step 7: Create `main.py`

```python
from __future__ import annotations

from rpacore import (
    Engine,
    Transaction,
    configure_logger,
    execute_transaction,
    load_config,
)

from steps.greet_user import WriteGreeting


def main() -> None:
    config = load_config("config.toml")
    configure_logger(level=str(config["log_level"]), fmt=str(config["log_format"]))

    tx = Transaction(
        reference="greet-user",
        definition_identity="greet-user/v1",
    )
    tx.steps = [
        WriteGreeting(
            name="write_greeting",
            execution_order=1,
            arguments={
                "name": "Renato",
                "output_path": "greeting.txt",
            },
        )
    ]

    engine = Engine(
        max_retries=int(config["max_retries"]),
        retry_delay=float(config["retry_delay"]),
        retry_backoff=float(config["retry_backoff"]),
    )
    execute_transaction(
        tx,
        config=config,
        engine=engine,
        transaction_db_path=str(config["transaction_db_path"]),
    )

    print(f"Transaction {tx.id}: {tx.status}")


if __name__ == "__main__":
    main()
```

This hand-written entrypoint prints a shorter transaction ID/status receipt
to teach application wiring. Newly scaffolded entrypoints also print the
database path, project directory, and inspection command, and explain
unconfirmed durability when execution raises an ordinary exception; see the
[CLI reference](cli.md).

`execute_transaction()` makes the run crash-durable at engine state boundaries:
the transaction is saved before user step code starts and after each status
transition. Its definition identity is an application-owned compatibility token,
not the RPA Core package version. Keep it stable across compatible code fixes;
change it when a new automation definition must not resume older in-progress
transactions.

## Step 8: Run The Project

```powershell
python .\main.py
Get-Content .\greeting.txt
```

Expected greeting:

```text
Hello, Renato!
```

The run also creates `rpacore.db`.

## Step 9: Inspect Transactions

```powershell
rpacore transaction list --db .\rpacore.db
rpacore transaction show --db .\rpacore.db <transaction_id> --json
rpacore transaction export --db .\rpacore.db --format json
rpacore transaction export --db .\rpacore.db --format ndjson
```

Use the transaction id printed by `python .\main.py` or listed by
`rpacore transaction list`. The JSON form of `transaction show` includes
`transaction.state.greeting_path`, which was written by the step and persisted
by the checkpoint callback.

JSON and NDJSON output are intended for tools. Treat exports as sensitive
business data because they can include state, metadata, step arguments, and
exception messages.

## Step 10: Add A CLI Manifest

Create `rpacore.toml`:

```toml
[project]
entrypoint = "main:main"

[storage]
transaction_db_path = "rpacore.db"
```

Now the project can run through the installed CLI:

```powershell
rpacore run
rpacore transaction list
```

## Same-ID Recovery From A Disposable Scaffold

Start in a new disposable project, never an established application:

```powershell
rpacore init recovery-demo
cd recovery-demo
python -m build --no-isolation --wheel
python -m pytest tests/test_greeting_step.py -q
rpacore doctor
```

Use an environment with RPA Core, `build`, `setuptools`, `wheel`, and `pytest`
already installed. This extends the generated greeting with a completed
preparation step; the default scaffold itself stays unchanged. Save this as
`recovery.py` in the project root:

<!-- recovery-file: recovery.py -->
```python
import argparse
from pathlib import Path

from rpacore import (
    Engine, ProcessContext, Status, Step, Transaction, atomic_output_path,
    execute_transaction, generate_report, render_json, resume_transaction,
)
from steps.greeting import WriteGreeting

DEFINITION = "tutorial-greeting/v1"


class PrepareGreeting(Step):
    def execute(self, ctx: ProcessContext) -> None:
        ctx.state["preparation_count"] = ctx.optional_state("preparation_count", int, 0) + 1


def steps(project: Path, *, resuming: bool) -> list[Step]:
    return [
        PrepareGreeting("prepare_greeting", 1),
        WriteGreeting("write_greeting", 2, arguments={
            "name": "different" if resuming else "Alice",
            "output_path": str(project / ("wrong.txt" if resuming else "greeting.txt")),
        }),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("database", type=Path)
    parser.add_argument("--resume", metavar="TRANSACTION_ID")
    args = parser.parse_args()
    project = args.project.resolve()
    database = str(args.database.resolve())
    if args.resume is None:
        tx = Transaction(
            reference="recovery-greeting", definition_identity=DEFINITION,
            metadata={"run_id": "tutorial-run"}, steps=steps(project, resuming=False),
        )
    else:
        tx = resume_transaction(
            args.resume, steps(project, resuming=True), db_path=database,
            definition_identity=DEFINITION, retry_business_failures=False,
        )
    if tx.status is not Status.SUCCESSFUL:
        execute_transaction(tx, engine=Engine(), transaction_db_path=database)
    report_path = project / f"report-{tx.id}.json"
    with atomic_output_path(report_path) as temporary:
        temporary.write_text(render_json(generate_report(tx)), encoding="utf-8")
    print(f"Transaction {tx.id}: {tx.status}")
    print(f"Database: {database}")
    print(f"Report: {report_path}")
    return 0 if tx.status is Status.SUCCESSFUL else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

The extended application owns `tutorial-greeting/v1`, distinct from the
one-step scaffold's `generated-greeting/v2`. Run once successfully to leave an
unrelated transaction in the same database:

```powershell
python recovery.py . ./rpacore.db
```

For demonstration only, save and run `inject_demo_failure.py` below from this
new project. It saves the original step source before injecting an ordinary
failure after atomic publication and before state/artifact registration. This
is a fault experiment, not application recovery logic.

<!-- recovery-file: inject_demo_failure.py -->
```python
from pathlib import Path

step_path = Path(__file__).resolve().parent / "steps" / "greeting.py"
backup = step_path.with_suffix(".py.backup")
with backup.open("xb") as output:
    output.write(step_path.read_bytes())
source = step_path.read_text(encoding="utf-8")
step_path.write_text(source + """
from contextlib import contextmanager
real_atomic_output_path = atomic_output_path
@contextmanager
def atomic_output_path(destination):
    with real_atomic_output_path(destination) as temporary:
        yield temporary
    raise RuntimeError('controlled-post-publication-failure')
""", encoding="utf-8")
```

```powershell
python inject_demo_failure.py
python recovery.py . ./rpacore.db
rpacore transaction list --run-id tutorial-run --status failed
```

The second application run exits `1`, prints its failed ID, and leaves complete
`Hello, Alice` greeting bytes. `preparation_count` is already `1`, preparation
is successful, and greeting is failed with no registered artifact. The file
publication and SQLite checkpoint cannot commit together. Replaying an effect
must be safe in the application: replacing these deterministic bytes is safe;
blindly appending, sending mail, or charging again would need its own durable
idempotency strategy. This is not an exactly-once guarantee.

Save `restore_demo_step.py` and run it before starting the separate resume
process. The restored application has no fault branch or monkeypatch:

<!-- recovery-file: restore_demo_step.py -->
```python
from pathlib import Path

step_path = Path(__file__).resolve().parent / "steps" / "greeting.py"
backup = step_path.with_suffix(".py.backup")
step_path.write_bytes(backup.read_bytes())
backup.unlink()
```

```powershell
python restore_demo_step.py
$failedId = "<failed ID printed above>"
rpacore transaction show $failedId --json
python recovery.py . ./rpacore.db --resume $failedId
rpacore transaction show $failedId --json
rpacore transaction export --transaction-id $failedId --format json
rpacore transaction export --transaction-id $failedId --format ndjson
```

Recovery restores persisted arguments: `Alice` and the original absolute output
path win over the deliberately different fresh constructor arguments. It keeps
the original ID, skips completed preparation, and replays greeting to identical
bytes. No `wrong.txt` appears; state and artifact paths name `greeting.txt`.
The report file contains that same ID and captured outcome; the exports select
only that ID even though the database also contains the unrelated success.
Calling the script again with the successful ID leaves the durable transaction
unchanged. Keep the definition identity stable across this compatible fault
removal. An incompatible identity is rejected before changing a current record.
Moving/copying a project does not rewrite persisted output arguments.

A `BusinessException` normally marks its step failed and allows later steps to
run. For dependent work, raise `BusinessException(...,
halts_remaining_steps=True)` so the engine records causal skips. Recovery
preserves business failures (and those skips) by default. Only after repairing
the cause should an application explicitly choose
`retry_business_failures=True`; it resets the failed business work and causal
skips for replay. A later successful step does not make the transaction's stored
business-failure outcome successful.

## Next Steps

- Add a second step with `execution_order=2`.
- Load an existing transaction with `load_transaction()`.
- Resume retryable failures with `resume_transaction()` and the exact persisted
  definition identity.
- Use `ctx.resources` for runtime-only objects that must not be persisted.
- Read [Durability and Storage](durability.md) for recovery details.
- Read [Testing Steps](testing.md) for plain pytest examples.

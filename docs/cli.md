# CLI Reference

The `rpacore` command is installed by the `rpacore` package.

## `rpacore init <project_name>`

Creates a normal Python project directory containing:

- `pyproject.toml`
- `rpacore.toml`
- `config.toml`
- `main.py`
- `steps/`
- `tests/`
- `.gitignore`

The destination basename becomes `[project].name` unchanged. It must start and
end with an ASCII letter or digit; interior characters may also include `.`,
`_`, and `-`. Single letters/digits and repeated separators are accepted. Parent
directories may contain spaces. Invalid basenames return exit code `2` with an
explanation on stderr before any directories or files are created. For example,
use `rpacore init my-project` instead of `rpacore init "my project"`. Existing
generated projects are unchanged and require no data migration. A valid package
name still requires an available filesystem destination.

The generated `main.py` uses `execute_transaction(transaction_db_path=...)`
with the project manifest's storage path, so execution and default inspection
refer to the same durable transaction history. It also supplies a stable
application-owned `definition_identity` so an interrupted run can be resumed
only by a compatible automation definition.

New scaffolds use `generated-greeting/v2`. Their default `greeting.txt` is
anchored to the project root, including runs from nested directories. The step
publishes a complete temporary sibling with `atomic_output_path` before recording
state/artifact metadata. Re-execution replaces the same greeting bytes rather
than appending; publication and the SQLite checkpoint are separate operations.
The output argument, state, and artifact store the same absolute destination.
Inspection and exports can therefore expose account/directory names. A copied
record retains its original destination on resume; a fresh run after moving the
project computes the new project root. Existing v1 projects/records are unchanged
and require no migration; v2 code must not be used to resume v1 records.

Newly generated entrypoints configure framework logging from `config.toml`'s
`log_level` and `log_format`; earlier scaffolds did not configure logging.
Invalid values are rejected by `load_config` before transaction execution.
Generated entrypoints treat `NOTSET` as `DEBUG`, enabling all framework levels
without inheriting the root logger's threshold. Text or JSON logs go
to stderr, including step-failure exception messages without content redaction.
After execution returns normally, stdout contains a human receipt with the
transaction ID, terminal status, resolved database path, project directory, and
`rpacore transaction show <actual-id>`. Run that inspection command from the
displayed project directory. A saved failed transaction receives the same
receipt and exits `1`; a saved successful transaction exits `0`. Log filtering
does not suppress the receipt.

If execution or a checkpoint raises an ordinary exception, the generated
entrypoint reports the attempted ID, database path, and inspection action on
stderr, with durable outcome explicitly unconfirmed. It then propagates the
original error. An in-memory successful status or completion log can precede
a failed final save; inspect the database before deciding how to recover.
Inspection may find no record or an earlier checkpoint. A second plain
`rpacore run` starts a new transaction with a new ID; it does not resume the
previous record.

`SystemExit` and `KeyboardInterrupt` propagate directly without a generated
receipt or abort diagnostic. An exit code alone does not confirm persistence;
use read-only transaction inspection after an interrupted run.

Receipts expose local project/storage paths but omit state, arguments, config
values, artifact contents, and exception payloads. Framework and CLI exception
logs still follow the [security guidance](security.md). These behaviors apply
to future scaffolds; existing projects require no migration. Arbitrary
entrypoints continue to own their output and exit semantics.

RPA Core distributions include the framework's `LICENSE` and `NOTICE`. The
generated project does not copy those files or add a package license field,
because the generated automation is user-owned project code. Add your own
license or provenance files when you publish or share it.

Exit codes:

- `0`: project created
- `1`: project creation failed after filesystem work started
- `2`: target path already exists or input is invalid

## `rpacore run`

Finds `rpacore.toml` from the current directory, resolves `[project].entrypoint`,
and calls that Python callable. RPA Core does not discover steps or build a
pipeline from configuration; user code owns transaction wiring. Resolution
temporarily prioritizes the manifest's project directory and replaces a cached
entrypoint package only when it belongs to another project. The resolver never
purges modules outside the entrypoint's top-level package, although ordinary
Python imports may cache external dependencies. The process working directory
is unchanged.

For generated entrypoints, see the
[generated-run output contract](#rpacore-init-project_name) for stdout receipts,
stderr logs, and unconfirmed-durability diagnostics.

Exit codes:

- `0`: entrypoint returns `None` or `0`
- `1`: entrypoint raises
- `2`: manifest, entrypoint, or return-value validation fails
- `0` through `255`: propagated when the entrypoint returns an integer in that
  range

## `rpacore version`

Prints the installed package version.

## `rpacore doctor`

Runs privacy-bounded, read-only diagnostics for the local Python/SQLite runtime,
the nearest project manifest, an optional `config.toml`, and selected existing
transaction or queue databases. It never imports the project entrypoint,
creates or migrates a database, changes journal mode, contacts an endpoint, or
reads credentials, transaction state/arguments, exception messages, queue
payloads, artifact contents, or file paths from those records.

```bash
rpacore doctor
rpacore doctor --json
rpacore doctor --transaction-db path/to/rpacore.db
rpacore doctor --queue-db path/to/queue.db
rpacore doctor --config path/to/config.toml --json
```

The default command discovers `rpacore.toml` from the current directory and,
when present, uses its transaction database path and sibling `config.toml`.
Direct `--transaction-db` and `--queue-db` flags select explicit storage;
missing paths fail. `--config` selects an explicit configuration file whose
absence or invalidity fails. A queue database obtained from a valid config,
including one selected with `--config`, is discovered storage. Absent discovered
storage is `not_applicable`: it is unchecked and may not yet exist or may have
been removed. Verify the configured location and recovery/backup for established
deployments before running work that could create a new database. Absence does
not establish health or intact data. Missing default manifest/config inputs are
also `not_applicable`; found unreadable/invalid inputs and existing malformed,
incompatible, directory, or unreadable databases fail. Database checks cover current
schema compatibility, journal mode, SQLite `quick_check`, foreign-key integrity,
and bounded queue counts/claim-binding facts. The JSON result is doctor format
v1 with `pass`, `warning`, `fail`, or `not_applicable` check statuses. JSON
stdout contains one document only.

Doctor format v1 always emits these check IDs: `runtime.python`,
`runtime.sqlite`, `project.manifest`, `project.config`,
`transactions.schema`, `transactions.journal`, `transactions.quick_check`,
`transactions.foreign_keys`, `queue.schema`, `queue.journal`,
`queue.quick_check`, `queue.foreign_keys`, and `queue.health`. Database schema
details contain `version` when checked. An absent discovered database's schema
check instead has optional `reason="missing_discovered_database"`; no-selection,
deeper skipped checks, and WAL checks do not carry that reason. Failed manifest
and config checks have optional `cause`: `toml_syntax`, `invalid_shape`,
`invalid_type`, `invalid_value`, `unreadable`, or `unexpected`. TOML syntax errors
also have optional `location`, normalized as `line N, column M` or
`end of document` when the parser supplies a recognized location. Unrecognized
locations are omitted. Human summaries show these same safe causes/locations,
without validator messages, offending values, or source excerpts.
Journal details contain `mode`; queue-health details
contain scalar `pending_items`, `in_progress_items`, `successful_items`,
`failed_items`, `unknown_status_items`, `bound_items`, and, when available,
`oldest_age_seconds`. When a SQLite header reports WAL, Doctor does not open the
database at all: it reports a journal warning and marks deeper checks
`not_applicable` to preserve WAL sidecars.

Exit codes:

- `0`: no failing checks (warnings are operator action items, not failures)
- `1`: one or more checks failed
- `2`: invalid command syntax

## Transaction Commands

Transaction commands inspect the SQLite database at `[storage].transaction_db_path`
from `rpacore.toml`. Pass `--db path/to/rpacore.db` to use a specific database.
Inspection opens an existing database through SQLite's read-only URI mode. It
does not create a missing database, migrate an older schema, rewrite a future
schema marker, or change journal mode. Missing or incompatible databases fail
with an actionable diagnostic; initialize or migrate storage through the
owning application version before inspecting it.

```bash
rpacore transaction list
rpacore transaction list --json
rpacore transaction show <transaction_id>
rpacore transaction show <transaction_id> --json
rpacore transaction export --format json
rpacore transaction export --format ndjson
```

`transaction list` returns the latest 100 transactions by default. Use
`--limit N` to choose a different cap.

### Machine-readable compatibility

The `--json` list and show envelopes are independently versioned by their
`command` and `schema_version` fields. Their version-2 framework-owned fields
are closed: a field cannot be added, removed, renamed, retyped, or given a new
meaning without a new schema version. The nested transaction is separately
versioned by `transaction_format_version`. Export follows the corresponding
version rules in the [Export Format Reference](export-format.md).

Human output is for operators. `--json` and `export` output are parseable on
stdout; diagnostics go to stderr. List and export select records through the
same normalized-UTC cursor pages as `query_transactions()`. Export keeps only
one page of summary records at a time, so it does not retain an unbounded
identifier snapshot or hold a SQLite read lock against checkpoints. Inserts
before the current cursor do not appear later; inserts after it can appear in a
later export page. A selected transaction deleted by concurrent cleanup before
its record is loaded is omitted by both list and export. Export has no
transaction-list cap and walks additional pages as needed.

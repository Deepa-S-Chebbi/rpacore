"""Private read-only transaction CLI inspection and export."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone

from rpacore import __version__
from rpacore._json_state import validate_json_safe
from rpacore.exceptions import BusinessException
from rpacore.manifest import load_project_manifest
from rpacore.persistence import TransactionPage, load_transaction, query_transactions
from rpacore.serialization import serialize_transaction
from rpacore.status import Status
from rpacore.transaction import Transaction

SUCCESS = 0
EXECUTION_ERROR = 1
USAGE_ERROR = 2
CLI_TRANSACTION_SCHEMA_VERSION = 3
EXPORT_FORMAT_VERSION = 2
_NDJSON_EXPORT_KEYS = frozenset(
    {"export_format_version", "framework_version", "exported_at"}
)
_QUERY_PAGE_SIZE = 1_000


def _inspect_transactions(args: argparse.Namespace) -> int:
    # Usage validation precedes even manifest/database resolution.
    if args.transaction_command == "list":
        filter_error = "Invalid --status: use pending, in_progress, successful, failed or skipped"
        try:
            statuses = [Status(value) for value in args.status]
            filter_error = "Invalid --since/--until: use aware ISO-8601 timestamps in chronological order"
            since, until = _aware_timestamp(args.since), _aware_timestamp(args.until)
            if since is not None and until is not None and since > until:
                raise ValueError("since must be earlier than or equal to until")
            filter_error = "Invalid --metadata/--run-id: use unique metadata keys and JSON-safe KEY=JSON values"
            metadata = _metadata_filters(args.metadata, args.run_id)
        except (ValueError, TypeError, RecursionError, OverflowError):
            _print_error(filter_error)
            return USAGE_ERROR
    if args.transaction_command == "export" and args.transaction_id is not None:
        if not args.transaction_id.strip():
            _print_error("transaction-id must not be blank")
            return USAGE_ERROR
    try:
        db_path = _transaction_db_path(args.db_path)
    except Exception as exc:
        _print_error(str(exc))
        return EXECUTION_ERROR
    try:
        if args.transaction_command == "list":
            transactions: list[Transaction] = []
            for page, records in _iter_transaction_pages(
                db_path, limit=args.limit, cursor=args.cursor,
                statuses=statuses or None, since=since, until=until,
                reference=args.reference, metadata_filter=metadata,
            ):
                transactions.extend(records)
            if args.json:
                _write_json(
                    {
                        "schema_version": CLI_TRANSACTION_SCHEMA_VERSION,
                        "command": "transaction:list",
                        "limit": args.limit,
                        "transactions": [
                            {"transaction": serialize_transaction(tx), "outcome": _outcome(tx)}
                            for tx in transactions
                        ],
                        "has_more": page.has_more,
                        "next_cursor": page.next_cursor,
                    }
                )
            else:
                _write_transaction_list(transactions, limit=args.limit)
                if page.has_more:
                    print("Continue transaction list using the same database and filters, adding:")
                    print(f"  --cursor {page.next_cursor}")
            return SUCCESS
        if args.transaction_command == "show":
            transaction = load_transaction(
                args.transaction_id,
                db_path,
                readonly=True,
            )
            if args.json:
                _write_json(
                    {
                        "schema_version": CLI_TRANSACTION_SCHEMA_VERSION,
                        "command": "transaction:show",
                        "transaction": serialize_transaction(transaction),
                        "outcome": _outcome(transaction),
                    }
                )
            else:
                _write_transaction_detail(transaction)
            return SUCCESS
        if args.transaction_command == "export":
            export_transactions = (
                [load_transaction(args.transaction_id, db_path, readonly=True)]
                if args.transaction_id is not None else (
                    tx for _, records in _iter_transaction_pages(db_path) for tx in records
                )
            )
            _write_transaction_export(export_transactions, export_format=args.format)
            return SUCCESS
        raise ValueError(f"Unsupported transaction command: {args.transaction_command}")
    except KeyError as exc:
        _print_error(str(exc.args[0] if exc.args else exc))
        return EXECUTION_ERROR
    except TypeError as exc:
        _print_error(str(exc))
        return EXECUTION_ERROR
    except Exception as exc:
        _print_error(f"Could not inspect transactions in {db_path}: {exc}")
        return EXECUTION_ERROR


def _iter_transaction_pages(
    db_path: str,
    *,
    limit: int | None = None,
    cursor: str | None = None,
    statuses: list[Status] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    reference: str | None = None,
    metadata_filter: dict[str, object] | None = None,
) -> Iterator[tuple[TransactionPage, Iterator[Transaction]]]:
    """Load one summary page at a time; deleted summaries still consume the cap."""
    remaining = limit
    while True:
        page_limit = _QUERY_PAGE_SIZE if remaining is None else min(_QUERY_PAGE_SIZE, remaining)
        page = query_transactions(
            db_path, cursor=cursor, limit=page_limit, statuses=statuses,
            since=since, until=until, reference=reference, metadata_filter=metadata_filter,
        )
        # Consumers exhaust these lazy loads before requesting another page.
        yield page, _load_page_records(db_path, page)
        if remaining is not None:
            remaining -= len(page.transactions)
        if not page.has_more or (remaining is not None and remaining <= 0):
            return
        cursor = page.next_cursor


def _load_page_records(db_path: str, page: TransactionPage) -> Iterator[Transaction]:
    # The query has released its connection before any full record load.
    for summary in page.transactions:
        try:
            yield load_transaction(summary.id, db_path, readonly=True)
        except KeyError:
            continue  # Concurrent cleanup consumes the summary's place in the cap.


def _transaction_db_path(db_path: str | None) -> str:
    if db_path is not None:
        return db_path
    try:
        return load_project_manifest().transaction_db_path
    except Exception as exc:
        raise RuntimeError(
            f"Could not resolve transaction database from rpacore.toml; pass --db: {exc}"
        ) from exc


def _write_transaction_list(transactions: list[Transaction], *, limit: int) -> None:
    print(f"Showing up to {limit} transactions.")
    if not transactions:
        print("No transactions found.")
        return
    rows = [("ID", "STATUS", "OUTCOME", "RETRY", "FAILURE_CODE", "REFERENCE")]
    rows.extend(
        (tx.id, str(tx.status), str(tx.outcome_category), str(tx.retry_disposition),
         repr(tx.failure_code), tx.reference)
        for tx in transactions
    )
    widths = [max(minimum, max(len(row[i]) for row in rows))
              for i, minimum in enumerate((36, 12, 0, 0, 0))]
    for row in rows:
        print(" ".join(value.ljust(width) for value, width in zip(row, widths)) + " " + row[-1])


def _write_transaction_detail(transaction: Transaction) -> None:
    print(f"ID:          {transaction.id}")
    print(f"Reference:   {transaction.reference}")
    print(f"Definition:  {transaction.definition_identity or '(unidentified)'}")
    print(f"Status:      {transaction.status}")
    print(f"Outcome:     {transaction.outcome_category}")
    print(f"Retry:       {transaction.retry_disposition}")
    print(f"Failure code: {transaction.failure_code!r}")
    print(f"Retries:     {transaction.retry_count}")
    print(f"Created:     {_format_optional_datetime(transaction.created_at)}")
    print(f"Started:     {_format_optional_datetime(transaction.started_at)}")
    print(f"Finished:    {_format_optional_datetime(transaction.finished_at)}")
    print(f"Steps:       {len(transaction.steps)}")
    for step in transaction.ordered_steps():
        print(f"  {step.execution_order}. {step.name}: {step.status}")
        for exc in step.exceptions:
            print(f"     - {_exception_kind(exc)}: {exc}")
    print(f"History:     {len(transaction.history)}")
    for entry in transaction.history:
        step_label = "" if not entry.step_name else f" step={entry.step_name}"
        print(
            f"  {entry.sequence}. {entry.event} status={entry.status} "
            f"retry={entry.retry_number}{step_label}"
        )
    print(f"Artifacts:   {len(transaction.artifacts)}")
    for artifact in transaction.artifacts:
        kind = "" if not artifact.kind else f" ({artifact.kind})"
        print(f"  {artifact.name}{kind}: {artifact.path}")
    print(f"Metadata:    {len(transaction.metadata)} keys")


def _write_transaction_export(
    transactions: Iterable[Transaction],
    *,
    export_format: str,
) -> None:
    exported_at = _utc_now().isoformat()
    with tempfile.TemporaryFile("w+", encoding="utf-8", newline="\n") as output:
        if export_format == "json":
            output.write("{")
            output.write(
                _json_field("export_format_version", EXPORT_FORMAT_VERSION)
            )
            output.write(",")
            output.write(_json_field("exported_at", exported_at))
            output.write(",")
            output.write(_json_field("framework_version", __version__))
            output.write(',"transactions":[')
            first = True
            for transaction in transactions:
                if not first:
                    output.write(",")
                first = False
                output.write(
                    json.dumps(
                        serialize_transaction(transaction),
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                )
            output.write("]}")
        else:
            for transaction in transactions:
                record = serialize_transaction(transaction)
                _raise_for_ndjson_export_key_collision(record)
                export_record = {
                    **record,
                    "export_format_version": EXPORT_FORMAT_VERSION,
                    "framework_version": __version__,
                    "exported_at": exported_at,
                }
                output.write(
                    json.dumps(export_record, sort_keys=True, separators=(",", ":"))
                )
                output.write("\n")
        output.seek(0)
        shutil.copyfileobj(output, sys.stdout)


def _raise_for_ndjson_export_key_collision(record: dict[str, object]) -> None:
    collisions = sorted(_NDJSON_EXPORT_KEYS.intersection(record))
    if collisions:
        keys = ", ".join(collisions)
        raise RuntimeError(f"transaction export record key collision: {keys}")


def _json_field(key: str, value: object) -> str:
    return (
        json.dumps(key, sort_keys=True, separators=(",", ":"))
        + ":"
        + json.dumps(value, sort_keys=True, separators=(",", ":"))
    )


def _exception_kind(exc: BaseException) -> str:
    return "business" if isinstance(exc, BusinessException) else "system"


def _format_optional_datetime(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _write_json(data: dict[str, object]) -> None:
    print(json.dumps(data, sort_keys=True, separators=(",", ":")))


def _print_error(message: str) -> None:
    print(f"rpacore: {message}", file=sys.stderr)


def _outcome(transaction: Transaction) -> dict[str, str]:
    return {
        "category": str(transaction.outcome_category),
        "retry_disposition": str(transaction.retry_disposition),
        "failure_code": transaction.failure_code,
    }


def _aware_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timestamp requires an offset")
    return result.astimezone(timezone.utc)


def _metadata_filters(values: list[str], run_ids: list[str]) -> dict[str, object]:
    metadata: dict[str, object] = {}
    pairs = []
    for value in values:
        key, separator, encoded = value.partition("=")
        if not separator or not key:
            raise ValueError("metadata requires KEY=JSON")
        decoded = json.loads(encoded)
        validate_json_safe(decoded, path="metadata")
        pairs.append((key, decoded))
    pairs.extend(("run_id", value) for value in run_ids)
    for key, decoded in pairs:
        if key in metadata:
            raise ValueError("metadata keys must be unique")
        metadata[key] = decoded
    return metadata

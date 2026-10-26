"""Operator filters, durable outcomes and single-ID export obligations."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pytest

import rpacore._transaction_cli as operator
from rpacore.cli import main
from rpacore import (
    BusinessException, Engine, ProcessContext, Step, Status, SystemException,
    Transaction, execute_transaction, save_transaction, serialize_transaction,
)


def invoke(capsys, db, command, *options, code=0):
    assert main(["transaction", command, "--db", str(db), *options]) == code
    output = capsys.readouterr()
    if code:
        assert output.out == "" and output.err
        return output.err
    assert output.err == ""
    return json.loads(output.out)


def snapshot(db):
    return {p.name: p.read_bytes() for p in db.parent.glob(db.name + "*") if p.is_file()}


def test_interleaved_runs_filters_cursors_and_large_cap(tmp_path, capsys):
    db = tmp_path / "runs.db"
    stamp = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    for index in range(1004):
        save_transaction(Transaction(
            id=f"item-{index:04}", reference="invoice", created_at=stamp,
            status=Status.FAILED if index % 4 < 2 else Status.SUCCESSFUL,
            metadata={"run_id": "A" if index % 2 == 0 else "B", "kind": {"items": [True, None, 1]}, "number": 1},
        ), str(db))
    legacy = Transaction(id="legacy", reference="legacy", created_at=None)
    save_transaction(legacy, str(db))
    before = snapshot(db)
    filters = ["--reference", "invoice", "--status", "failed", "--status", "successful",
               "--since", "2026-10-15T09:00:00-03:00", "--until", "2026-10-15T12:00:00Z",
               "--metadata", 'kind={"items":[true,null,1]}', "--metadata", "number=1"]
    for start_with_alias in (True, False):
        cursor = None
        ids = []
        while True:
            alias = start_with_alias if cursor is None else not start_with_alias
            run = ["--run-id", "A"] if alias else ["--metadata", 'run_id="A"']
            options = filters + run + ["--json"]
            if cursor:
                options += ["--cursor", cursor]
            payload = invoke(capsys, db, "list", *options)
            ids += [entry["transaction"]["id"] for entry in payload["transactions"]]
            if not payload["has_more"]:
                assert payload["next_cursor"] is None
                break
            cursor = payload["next_cursor"]
        assert ids == [f"item-{i:04}" for i in range(0, 1004, 2)]
        assert len(set(ids)) == len(ids) == 502
    page = invoke(capsys, db, "list", "--limit", "1001", "--json")
    assert len(page["transactions"]) == 1001 and page["has_more"]
    rest = invoke(capsys, db, "list", "--cursor", page["next_cursor"], "--json")
    assert [e["transaction"]["id"] for e in rest["transactions"]] == ["item-1001", "item-1002", "item-1003", "legacy"]
    exported = invoke(capsys, db, "export", "--format", "json")
    assert [tx["id"] for tx in exported["transactions"]] == [f"item-{i:04}" for i in range(1004)] + ["legacy"]
    empty = invoke(capsys, db, "list", "--run-id", "absent", "--json")
    assert empty["transactions"] == [] and empty["next_cursor"] is None and not empty["has_more"]
    assert snapshot(db) == before


@pytest.mark.parametrize("value", ['"1"', "1", "true", "null", "[1]", '{"a":1}'])
def test_metadata_matches_exact_json_types(tmp_path, capsys, value):
    db = tmp_path / "typed.db"
    values = ['"1"', "1", "true", "null", "[1]", '{"a":1}']
    for index, encoded in enumerate(values):
        save_transaction(Transaction(id=str(index), reference="typed", metadata={"value": json.loads(encoded)}), str(db))
    result = invoke(capsys, db, "list", "--metadata", "value=" + value, "--json")
    assert [e["transaction"]["id"] for e in result["transactions"]] == [str(values.index(value))]


@pytest.mark.parametrize("options", [
    ["--status", "not-a-status"], ["--since", "2026-01-01"], ["--until", "broken"],
    ["--since", "0001-01-01T00:00:00+01:00"], ["--until", "9999-12-31T23:59:59-01:00"],
    ["--since", "2026-02-01T00:00:00Z", "--until", "2026-01-01T00:00:00Z"],
    ["--metadata", "private-marker"], ["--metadata", "=null"], ["--metadata", "x=private-marker"],
    ["--metadata", "x=NaN"], ["--metadata", "x=1e999"], ["--metadata", 'x={"secret":Infinity}'],
    ["--metadata", 'x="private-marker"', "--metadata", "x=null"],
    ["--run-id", "private-marker", "--metadata", 'run_id="private-marker"'],
    ["--run-id", "ignored", "--run-id", "private-marker"],
    ["--run-id", "private-marker", "--run-id", "private-marker"],
])
def test_usage_rejection_precedes_storage_and_omits_values(tmp_path, capsys, monkeypatch, options):
    def forbidden(*args, **kwargs):
        pytest.fail("storage resolution must not run")
    monkeypatch.setattr(operator, "_transaction_db_path", forbidden)
    error = invoke(capsys, tmp_path / "missing.db", "list", *options, "--json", code=2)
    assert "private-marker" not in error
    expected_option = "--status" if options[0] == "--status" else (
        "--since/--until" if options[0] in {"--since", "--until"} else "--metadata/--run-id"
    )
    assert "Invalid " + expected_option in error
    assert not list(tmp_path.iterdir())


def test_cursor_rejection_and_human_continuation(tmp_path, capsys):
    db = tmp_path / "cursor.db"
    for index in range(3):
        save_transaction(Transaction(id=str(index), reference="cursor", metadata={"run_id": "private-marker"}), str(db))
    first = invoke(capsys, db, "list", "--limit", "1", "--run-id", "private-marker", "--json")
    before = snapshot(db)
    invoke(capsys, db, "list", "--cursor", first["next_cursor"], "--run-id", "different", "--json", code=1)
    invoke(capsys, db, "list", "--cursor", "malformed", "--json", code=1)
    assert main(["transaction", "list", "--db", str(db), "--limit", "1", "--run-id", "private-marker"]) == 0
    human = capsys.readouterr()
    assert first["next_cursor"] in human.out and "--cursor" in human.out
    assert "same database and filters" in human.out and "private-marker" not in human.out
    assert snapshot(db) == before


class ResultStep(Step):
    def execute(self, ctx: ProcessContext) -> None:
        if self.arguments.get("business"):
            raise BusinessException("bad input", code="demo.input.invalid")
        if self.arguments.get("system"):
            raise SystemException("service unavailable", code="demo.service.unavailable")


@pytest.mark.parametrize("kind", ["successful", "business", "system", "unknown"])
def test_inspection_projects_persisted_outcome(tmp_path, capsys, kind):
    db = tmp_path / "outcomes.db"
    tx = Transaction(reference="outcome", definition_identity="outcome/v1", steps=[
        ResultStep("first", 1, arguments={kind: True}), ResultStep("later", 2),
    ])
    if kind == "unknown":
        tx.status = Status.FAILED
        tx.steps[1].status = Status.SUCCESSFUL
        save_transaction(tx, str(db))
    else:
        execute_transaction(tx, engine=Engine(), transaction_db_path=str(db))
    capsys.readouterr()  # Framework logging is independent of inspection stdout.
    expected = {"category": str(tx.outcome_category), "retry_disposition": str(tx.retry_disposition), "failure_code": tx.failure_code}
    if kind == "business":
        assert tx.status is Status.FAILED and tx.steps[1].status is Status.SUCCESSFUL
        assert expected["category"] == "business_failed" and expected["failure_code"] == "demo.input.invalid"
    if kind == "unknown":
        assert expected == {"category": "unknown", "retry_disposition": "unknown", "failure_code": ""}
    before = snapshot(db)
    shown = invoke(capsys, db, "show", tx.id, "--json")
    listed = invoke(capsys, db, "list", "--json")
    assert shown["outcome"] == listed["transactions"][0]["outcome"] == expected
    assert shown["transaction"] == listed["transactions"][0]["transaction"] == serialize_transaction(tx)
    for command in (["list"], ["show", tx.id]):
        assert main(["transaction", *command, "--db", str(db)]) == 0
        human = capsys.readouterr()
        assert expected["category"] in human.out and expected["retry_disposition"] in human.out
        assert repr(expected["failure_code"]) in human.out
    assert snapshot(db) == before


@pytest.mark.parametrize("format", ["json", "ndjson"])
def test_selected_export_is_exact_and_preserves_all_record_default(tmp_path, capsys, format):
    db = tmp_path / "export.db"
    selected = Transaction(id=" selected ", reference="selected")
    save_transaction(selected, str(db))
    save_transaction(Transaction(reference="unrelated"), str(db))
    before = snapshot(db)
    payload = invoke(capsys, db, "export", "--transaction-id", selected.id, "--format", format)
    records = payload["transactions"] if format == "json" else [payload]
    assert len(records) == 1 and records[0]["id"] == selected.id
    assert payload["export_format_version"] == 2
    record = {key: value for key, value in records[0].items() if key not in {"framework_version", "export_format_version", "exported_at"}}
    assert record == serialize_transaction(selected)
    assert len(invoke(capsys, db, "export", "--format", "json")["transactions"]) == 2
    invoke(capsys, db, "export", "--transaction-id", "missing", "--format", format, code=1)
    assert snapshot(db) == before


@pytest.mark.parametrize("format", ["json", "ndjson"])
@pytest.mark.parametrize("id", ["", " \t\n"])
def test_blank_export_id_rejected_before_resolution(tmp_path, capsys, monkeypatch, format, id):
    def forbidden(*args):
        pytest.fail("must validate before storage resolution")
    monkeypatch.setattr(operator, "_transaction_db_path", forbidden)
    invoke(capsys, tmp_path / "absent.db", "export", "--transaction-id", id, "--format", format, code=2)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", [
    ["list", "--json"], ["show", "selected", "--json"],
    ["export", "--transaction-id", "selected", "--format", "json"],
    ["export", "--transaction-id", "selected", "--format", "ndjson"],
])
@pytest.mark.parametrize("schema", [None, 0, 9, 99])
def test_inspection_cannot_create_or_migrate_storage(tmp_path, capsys, command, schema):
    db = tmp_path / "storage.db"
    if schema is not None:
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE rpacore_schema_versions (component TEXT PRIMARY KEY, version INTEGER NOT NULL)")
            conn.execute("INSERT INTO rpacore_schema_versions VALUES ('transactions', ?)", (schema,))
    before = snapshot(db)
    invoke(capsys, db, command[0], *command[1:], code=1)
    assert snapshot(db) == before


def test_list_consumes_deleted_summaries_and_releases_query_before_load(tmp_path, capsys, monkeypatch):
    db = tmp_path / "cleanup.db"
    monkeypatch.setattr(operator, "_QUERY_PAGE_SIZE", 1)
    for index in range(3):
        save_transaction(Transaction(id=str(index), reference="cleanup", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)), str(db))
    real_load = operator.load_transaction
    first = True
    def load(id, path, *, readonly):
        nonlocal first
        assert readonly
        if first:
            first = False
            # Real writes during the load phase would fail behind a held query lock.
            save_transaction(Transaction(id="new", reference="checkpoint"), path)
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("DELETE FROM transactions WHERE id = ?", ("0",))
        return real_load(id, path, readonly=readonly)
    monkeypatch.setattr(operator, "load_transaction", load)
    page = invoke(capsys, db, "list", "--limit", "2", "--json")
    assert [e["transaction"]["id"] for e in page["transactions"]] == ["1"]
    assert page["has_more"] and page["next_cursor"]
    tail = invoke(capsys, db, "list", "--cursor", page["next_cursor"], "--json")
    assert [e["transaction"]["id"] for e in tail["transactions"]] == ["2"]


@pytest.mark.parametrize("command", [
    ["list", "--json"], ["show", "selected", "--json"],
    ["export", "--transaction-id", "selected", "--format", "json"],
    ["export", "--transaction-id", "selected", "--format", "ndjson"],
])
def test_serialization_failure_publishes_no_stdout(tmp_path, capsys, monkeypatch, command):
    db = tmp_path / "bad.db"
    save_transaction(Transaction(id="selected", reference="bad"), str(db))
    before = snapshot(db)
    def bad(*args, **kwargs):
        return Transaction(reference="bad", state={"invalid": object()})
    monkeypatch.setattr(operator, "load_transaction", bad)
    invoke(capsys, db, command[0], *command[1:], code=1)
    assert snapshot(db) == before


@pytest.mark.parametrize("format", ["json", "ndjson"])
def test_selected_record_deleted_before_load_fails(tmp_path, capsys, monkeypatch, format):
    db = tmp_path / "deleted.db"
    save_transaction(Transaction(id="selected", reference="selected"), str(db))
    real_load = operator.load_transaction
    def load(id, path, *, readonly):
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("DELETE FROM transactions WHERE id = ?", (id,))
        return real_load(id, path, readonly=readonly)
    monkeypatch.setattr(operator, "load_transaction", load)
    invoke(capsys, db, "export", "--transaction-id", "selected", "--format", format, code=1)


def test_wal_inspection_preserves_quiescent_database(tmp_path, capsys):
    db = tmp_path / "wal.db"
    save_transaction(Transaction(id="selected", reference="wal"), str(db))
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.execute("CREATE TABLE sentinel (value INTEGER)")
        conn.commit()
        # Initialize the reader mark before the byte snapshot, as in existing WAL policy.
        invoke(capsys, db, "list", "--json")
        before = snapshot(db)
        invoke(capsys, db, "list", "--json")
        invoke(capsys, db, "show", "selected", "--json")
        for format in ("json", "ndjson"):
            invoke(capsys, db, "export", "--transaction-id", "selected", "--format", format)
        assert snapshot(db) == before


@pytest.mark.parametrize("export_format", ["json", "ndjson"])
def test_all_record_export_buffers_failure_after_an_earlier_page(tmp_path, capsys, monkeypatch, export_format):
    db = tmp_path / "pages.db"
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for identifier in ("a", "b"):
        save_transaction(Transaction(id=identifier, reference="page", created_at=stamp), str(db))
    before = snapshot(db)
    real_load = operator.load_transaction
    monkeypatch.setattr(operator, "_QUERY_PAGE_SIZE", 1)
    def load(identifier, path, *, readonly):
        tx = real_load(identifier, path, readonly=readonly)
        if identifier == "b":
            tx.state["invalid"] = object()
        return tx
    monkeypatch.setattr(operator, "load_transaction", load)
    invoke(capsys, db, "export", "--format", export_format, code=1)
    assert snapshot(db) == before


def test_human_list_aligns_columns_for_variable_length_values(tmp_path, capsys):
    db = tmp_path / "columns.db"
    transactions = [
        Transaction(id="short", reference="first"),
        Transaction(id="an-identifier-longer-than-the-default-column-width", reference="second",
                    failure_code="example.a_very_long_application_failure_code"),
    ]
    for tx in transactions:
        save_transaction(tx, str(db))
    assert main(["transaction", "list", "--db", str(db)]) == 0
    lines = capsys.readouterr().out.splitlines()
    header = lines[1]
    starts = [header.index(label) for label in ("ID", "STATUS", "OUTCOME", "RETRY", "FAILURE_CODE", "REFERENCE")]
    for tx, line in zip(reversed(transactions), lines[2:], strict=True):
        values = (tx.id, str(tx.status), str(tx.outcome_category), str(tx.retry_disposition), repr(tx.failure_code), tx.reference)
        assert all(line[start:].startswith(value) for start, value in zip(starts, values, strict=True))

"""Private generated-project writer and templates."""

from __future__ import annotations

import textwrap
from pathlib import Path


def _write_project_files(project_dir: Path) -> None:
    project_name = project_dir.name
    files = {
        "pyproject.toml": _pyproject_toml(project_name),
        "rpacore.toml": _rpacore_toml(),
        "config.toml": _config_toml(),
        "main.py": _main_py(),
        "steps/__init__.py": "",
        "steps/greeting.py": _greeting_step_py(),
        "tests/test_greeting_step.py": _step_test_py(),
        ".gitignore": _gitignore(),
    }
    for relative_path, content in files.items():
        (project_dir / relative_path).write_text(content, encoding="utf-8")


def _pyproject_toml(project_name: str) -> str:
    return textwrap.dedent(f"""\
        [project]
        name = "{project_name}"
        version = "0.1.0"
        requires-python = ">=3.11"
        dependencies = ["rpacore"]

        [project.optional-dependencies]
        dev = ["pytest>=7.0"]
        """)


def _rpacore_toml() -> str:
    return textwrap.dedent("""\
        [project]
        entrypoint = "main:main"

        [storage]
        transaction_db_path = "rpacore.db"
        """)


def _config_toml() -> str:
    return textwrap.dedent("""\
        max_retries = 0
        retry_delay = 0.0
        retry_backoff = 1.0
        log_level = "INFO"
        log_format = "text"
        screenshot_dir = ""
        credential_provider = "env"
        """)


def _main_py() -> str:
    return textwrap.dedent("""\
        from __future__ import annotations

        from pathlib import Path

        from rpacore import (
            Engine,
            Status,
            Transaction,
            execute_transaction,
            load_config,
            load_project_manifest,
        )
        from steps.greeting import WriteGreeting


        def main() -> int:
            project_dir = Path(__file__).resolve().parent
            config = load_config(project_dir / "config.toml")
            manifest = load_project_manifest(project_dir)
            transaction = Transaction(
                reference="generated-greeting",
                definition_identity="generated-greeting/v1",
                steps=[
                    WriteGreeting(
                        name="write_greeting",
                        execution_order=1,
                        arguments={"name": "Alice", "output_path": "greeting.txt"},
                    ),
                ],
            )
            engine = Engine(
                max_retries=int(config["max_retries"]),
                retry_delay=float(config["retry_delay"]),
                retry_backoff=float(config["retry_backoff"]),
                screenshot_dir=str(config["screenshot_dir"]),
            )
            execute_transaction(
                transaction,
                config=config,
                engine=engine,
                transaction_db_path=manifest.transaction_db_path,
            )
            return 0 if transaction.status is Status.SUCCESSFUL else 1


        if __name__ == "__main__":
            raise SystemExit(main())
        """)


def _greeting_step_py() -> str:
    return textwrap.dedent("""\
        from __future__ import annotations

        from pathlib import Path

        from rpacore import BusinessException, ProcessContext, Step


        class WriteGreeting(Step):
            def execute(self, ctx: ProcessContext) -> None:
                name = self.arguments.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise BusinessException("'name' must be a non-empty string", action=self.name)

                output_path = Path(str(self.arguments.get("output_path", "greeting.txt")))
                output_path.write_text(f"Hello, {name}\\n", encoding="utf-8")
                ctx.state["greeting_path"] = str(output_path)
                ctx.add_artifact(
                    "greeting",
                    str(output_path),
                    kind="text",
                    metadata={"name": name},
                )
        """)


def _step_test_py() -> str:
    return textwrap.dedent("""\
        from __future__ import annotations

        from pathlib import Path

        import pytest

        from rpacore import BusinessException, ProcessContext, Transaction
        from steps.greeting import WriteGreeting


        def test_write_greeting_step(tmp_path: Path) -> None:
            output = tmp_path / "greeting.txt"
            step = WriteGreeting(
                name="write_greeting",
                execution_order=1,
                arguments={"name": "Alice", "output_path": str(output)},
            )
            transaction = Transaction(reference="test", steps=[step])

            step.execute(ProcessContext(transaction=transaction))

            assert output.read_text(encoding="utf-8") == "Hello, Alice\\n"
            assert transaction.state["greeting_path"] == str(output)
            assert transaction.artifacts[0].name == "greeting"
            assert transaction.artifacts[0].path == str(output)
            assert transaction.artifacts[0].kind == "text"
            assert transaction.artifacts[0].metadata == {"name": "Alice"}


        def test_write_greeting_rejects_missing_name(tmp_path: Path) -> None:
            output = tmp_path / "greeting.txt"
            step = WriteGreeting(
                name="write_greeting",
                execution_order=1,
                arguments={"name": "", "output_path": str(output)},
            )
            transaction = Transaction(reference="test", steps=[step])

            with pytest.raises(BusinessException, match="non-empty string"):
                step.execute(ProcessContext(transaction=transaction))

            assert not output.exists()
            assert transaction.state == {}
            assert transaction.artifacts == []
        """)


def _gitignore() -> str:
    return textwrap.dedent("""\
        .venv/
        __pycache__/
        .pytest_cache/
        *.pyc
        rpacore.db
        greeting.txt
        """)

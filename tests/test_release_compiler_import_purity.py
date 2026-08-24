"""Isolated import proof for the frozen ReleaseSnapshot compiler."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path


def test_release_compiler_import_reads_only_repository_modules():
    root = Path(__file__).resolve().parents[1]
    probe = textwrap.dedent(
        r"""
        import os
        import socket
        import sys
        from pathlib import Path

        root = Path(sys.argv[1]).absolute()
        module_suffixes = {".py", ".pyc", ".so", ".pyd", ".dylib"}
        write_flags = (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        )

        class ImportBoundaryViolation(RuntimeError):
            pass

        def repository_path(raw):
            if isinstance(raw, int):
                return None
            try:
                candidate = Path(os.path.abspath(os.fsdecode(raw)))
                candidate.relative_to(root)
            except (TypeError, ValueError, OSError):
                return None
            return candidate

        def audit(event, args):
            if event in {"socket.connect", "socket.bind"}:
                raise ImportBoundaryViolation(
                    f"compiler import attempted {event}: {args!r}"
                )
            if event != "open":
                return
            candidate = repository_path(args[0])
            if candidate is None:
                return
            mode = str(args[1] or "")
            flags = int(args[2] or 0)
            if any(marker in mode for marker in "wax+") or flags & write_flags:
                raise ImportBoundaryViolation(
                    f"compiler import attempted repository write: {candidate}"
                )
            if candidate.suffix.casefold() not in module_suffixes:
                raise ImportBoundaryViolation(
                    f"compiler import read non-module repository data: {candidate}"
                )

        sys.dont_write_bytecode = True
        sys.addaudithook(audit)

        # Prove both audit legs are armed before importing the compiler.
        try:
            (root / "README.md").read_bytes()
        except ImportBoundaryViolation:
            pass
        else:
            raise AssertionError("repository data-read audit hook is not armed")

        probe_socket = socket.socket()
        try:
            probe_socket.bind(("127.0.0.1", 0))
        except ImportBoundaryViolation:
            pass
        else:
            raise AssertionError("socket audit hook is not armed")
        finally:
            probe_socket.close()

        sys.path.insert(0, str(root))
        import agents.golden_press.release_compiler  # noqa: F401,E402

        print("release compiler import is capability-clean")
        """
    )

    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-c", probe, str(root)],
        cwd="/",
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, (
        "isolated compiler import crossed its capability boundary\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    assert completed.stdout.strip() == (
        "release compiler import is capability-clean")

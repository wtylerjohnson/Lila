"""Launcher regression tests; no server, API calls, or process termination."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OPERATING_ROOT = "/Users/wtjohnson/Lila"
CANONICAL = OPERATING_ROOT + "/LILA.command"


class LauncherRoutingTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "launch_lila.command").read_text(encoding="utf-8")
        self.commands = [line.strip() for line in self.source.splitlines()
                         if line.strip() and not line.lstrip().startswith("#")]

    def test_legacy_launcher_has_one_canonical_execution_path(self):
        self.assertEqual(self.commands, ["set -eu", f"exec /bin/zsh {CANONICAL}"])

    def test_no_historical_checkout(self):
        self.assertNotIn("federal-sales-os", self.source)
        self.assertNotIn("$HOME", self.source)

    def test_no_process_name_kills_or_detached_restart(self):
        for command in self.commands:
            self.assertNotIn("pkill", command)
            self.assertNotIn("pgrep", command)
            self.assertNotIn("kill ", command)
            self.assertNotIn("&", command)

    def test_no_ambient_python(self):
        self.assertFalse(any("python" in line for line in self.commands))

    def test_canonical_launcher_uses_operating_virtualenv(self):
        source = (ROOT / "LILA.command").read_text(encoding="utf-8")
        commands = [line.strip() for line in source.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
        self.assertIn("set -eu", commands)
        self.assertIn(f"cd {OPERATING_ROOT}", commands)
        self.assertEqual(commands[-1], "exec .venv/bin/python run_ui.py")

    def test_shell_syntax_for_posix_compatible_delegate_body(self):
        result = subprocess.run(["/bin/sh", "-n"], input=self.source,
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def _delegation_probe(self, exit_code):
        # Substitute ONLY the external interpreter and canonical path. This is
        # a process-delegation test, not a macOS or native-engine integration.
        with tempfile.TemporaryDirectory(prefix="lila launcher ") as directory:
            root = Path(directory)
            shell = root / "zsh-stub"
            shell.write_text('#!/bin/sh\nprintf "%s\\n" "$1"\nexit '
                             + str(exit_code) + '\n', encoding="utf-8")
            shell.chmod(0o755)
            target = str(root / "LILA.command")
            command = f"exec /bin/zsh {CANONICAL}"
            replacement = f"exec {shlex.quote(str(shell))} {shlex.quote(target)}"
            probe = self.source.replace(command, replacement)
            self.assertNotEqual(probe, self.source)
            result = subprocess.run(["/bin/sh"], input=probe, text=True,
                                    capture_output=True, timeout=5)
            self.assertEqual(result.stdout.strip(), target)
            self.assertEqual(result.returncode, exit_code, result.stderr)

    def test_delegates_success_exit(self):
        self._delegation_probe(0)

    def test_preserves_startup_failure_exit(self):
        self._delegation_probe(23)


if __name__ == "__main__":
    unittest.main()

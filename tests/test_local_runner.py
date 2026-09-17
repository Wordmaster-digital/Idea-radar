"""Local settings never execute code; concurrent runs cannot overwrite state."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import local_runner


class LocalRunnerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.settings = self.root / ".env"

    def test_settings_are_literal_and_do_not_override_explicit_environment(self):
        self.settings.write_text('# comment\nDISCORD_WEBHOOK_URL="$(never-run)=x"\nIDEA_MODEL=override\n', encoding="utf-8-sig")
        with patch.dict(os.environ, {"IDEA_MODEL": "existing"}, clear=True):
            local_runner.load_settings(self.settings)
            self.assertEqual(os.environ["DISCORD_WEBHOOK_URL"], "$(never-run)=x")
            self.assertEqual(os.environ["IDEA_MODEL"], "existing")

    def test_rejects_api_credentials_without_printing_value(self):
        self.settings.write_text("OPENAI_API_KEY=SECRET", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            local_runner.load_settings(self.settings)
        self.assertNotIn("SECRET", str(caught.exception))

    def test_missing_settings_and_empty_optional_values_are_ok(self):
        local_runner.load_settings(self.settings)
        self.settings.write_text("NAVER_CLIENT_ID=\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            local_runner.load_settings(self.settings)
            self.assertNotIn("NAVER_CLIENT_ID", os.environ)

    def test_preflight_requires_webhook_without_model_or_delivery(self):
        with patch.object(local_runner, "ROOT", self.root), patch.dict(os.environ, {}, clear=True), \
             patch.object(local_runner.idea_ladder, "make_client") as client, \
             patch.object(local_runner.kr_digest, "main") as digest, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(local_runner.main(["--check"]), 1)
        client.assert_not_called()
        digest.assert_not_called()

    def test_preflight_only_checks_auth(self):
        with patch.object(local_runner, "ROOT", self.root), \
             patch.dict(os.environ, {"DISCORD_WEBHOOK_URL": "https://discord.example/test"}), \
             patch.object(local_runner.idea_ladder, "make_client") as client, \
             patch.object(local_runner.kr_digest, "main") as digest, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(local_runner.main(["--check"]), 0)
        client.assert_called_once_with()
        digest.assert_not_called()

    def test_run_anchors_state_to_install_directory(self):
        with patch.object(local_runner, "ROOT", self.root), \
             patch.object(local_runner.kr_digest, "main", return_value=0) as digest:
            self.assertEqual(local_runner.main(["--dry-run", "--no-llm"]), 0)
        digest.assert_called_once_with(["--state", str(self.root / "state" / "seen.json"), "--dry-run", "--no-llm"])

    def test_lock_blocks_another_process_and_is_released_after_exception(self):
        lock = self.root / "run.lock"
        code = "from pathlib import Path; from local_runner import run_lock; import sys\nwith run_lock(Path(sys.argv[1])): print('acquired')"
        with local_runner.run_lock(lock):
            other = subprocess.run([sys.executable, "-c", code, str(lock)], capture_output=True, text=True)
            self.assertNotEqual(other.returncode, 0)
        try:
            with local_runner.run_lock(lock):
                raise ValueError("test")
        except ValueError:
            pass
        other = subprocess.run([sys.executable, "-c", code, str(lock)], capture_output=True, text=True)
        self.assertEqual(other.returncode, 0, other.stderr)


if __name__ == "__main__":
    unittest.main()

"""Exercise CLI authentication, isolation, output contracts, and failure handling offline."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import codex_llm

SCHEMA = {"type": "object", "properties": {"rows": {"type": "array", "items": {"type": "string"}}},
          "required": ["rows"], "additionalProperties": False}
RESULT = {"rows": ["한글 결과"]}


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.auth = "Logged in using ChatGPT\n"
        self.auth_exit = 0
        self.exit = 0
        self.events = [{"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 20}}]
        self.output = json.dumps(RESULT, ensure_ascii=False)
        self.error = None
        env = patch.dict(os.environ, {"IDEA_LLM_MODE": "codex", "IDEA_MODEL": "", "IDEA_CODEX_BIN": ""})
        self.addCleanup(env.stop)
        env.start()
        binary = patch.object(codex_llm, "executable", return_value="codex")
        self.addCleanup(binary.stop)
        binary.start()
        runner = patch.object(codex_llm.subprocess, "run", side_effect=self.fake_run)
        self.addCleanup(runner.stop)
        runner.start()

    def fake_run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if command[1:] == ["login", "status"]:
            return subprocess.CompletedProcess(command, self.auth_exit, "", self.auth)
        if self.error:
            raise self.error
        self.schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text(encoding="utf-8"))
        if self.output is not None:
            Path(command[command.index("--output-last-message") + 1]).write_text(self.output, encoding="utf-8")
        return subprocess.CompletedProcess(command, self.exit,
                                           "\n".join(json.dumps(e) for e in self.events), "SECRET stderr")

    def generate(self):
        client = codex_llm.CodexClient()
        return client.generate("분석 지침", [{"title": "입력"}], SCHEMA, "low")

    def test_success_uses_subscription_schema_and_isolated_directory(self):
        with contextlib.redirect_stderr(io.StringIO()) as log:
            self.assertEqual(self.generate(), RESULT)
        command, kwargs = self.calls[-1]
        settings = {command[i + 1].split("=", 1)[0]: command[i + 1].split("=", 1)[1]
                    for i, arg in enumerate(command) if arg == "--config"}
        self.assertEqual(settings["forced_login_method"], '"chatgpt"')
        self.assertEqual(settings["model_provider"], '"openai"')
        self.assertEqual(settings["web_search"], '"disabled"')
        for feature in ("shell_tool", "unified_exec", "apps", "plugins", "hooks", "multi_agent", "browser_use"):
            self.assertEqual(settings[f"features.{feature}"], "false")
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        self.assertEqual(command[command.index("--ask-for-approval") + 1], "never")
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ephemeral", command)
        self.assertEqual(command[command.index("--model") + 1], codex_llm.DEFAULT_MODEL)
        self.assertEqual(kwargs["timeout"], 300)
        self.assertFalse(kwargs["shell"])
        self.assertEqual(json.loads(kwargs["input"]), {"data": [{"title": "입력"}]})
        self.assertEqual(self.schema, SCHEMA)
        self.assertFalse(Path(kwargs["cwd"]).exists())
        self.assertIn("auth=chatgpt", log.getvalue())
        self.assertIn("input_tokens=10 output_tokens=20", log.getvalue())
        self.assertNotIn("SECRET", log.getvalue())

    def test_custom_model_is_not_replaced(self):
        with patch.dict(os.environ, {"IDEA_MODEL": " custom-model "}), contextlib.redirect_stderr(io.StringIO()):
            self.generate()
        command = self.calls[-1][0]
        self.assertEqual(command[command.index("--model") + 1], "custom-model")

    def test_bot_and_api_secrets_never_reach_child(self):
        secrets = {key: "SECRET" for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN",
                    "OPENAI_BASE_URL", "DISCORD_WEBHOOK_URL", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET", "GH_TOKEN")}
        with patch.dict(os.environ, secrets), contextlib.redirect_stderr(io.StringIO()):
            self.generate()
        for _, kwargs in self.calls:
            self.assertTrue(set(secrets).isdisjoint(kwargs["env"]))

    def test_api_key_and_missing_login_are_rejected_before_model_call(self):
        for status, code in [("Logged in using an API key: SECRET", 0), ("Not logged in", 1),
                             ("unrecognized login status", 0), ("Logged in using ChatGPT", 1)]:
            with self.subTest(status=status):
                self.auth, self.auth_exit = status, code
                self.calls.clear()
                with self.assertRaises(codex_llm.CodexError) as caught:
                    self.generate()
                self.assertEqual(len(self.calls), 1)
                self.assertNotIn("SECRET", str(caught.exception))

    def test_nonzero_exit_ignores_valid_json_and_sanitizes_stderr(self):
        self.exit = 1
        with self.assertRaises(codex_llm.CodexError) as caught:
            self.generate()
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(len(self.calls), 2)

    def test_failed_incomplete_or_tool_using_turn_is_rejected(self):
        for events in ([], [{"type": "turn.started"}], [{"type": "turn.failed"}],
                       [{"type": "error", "message": "SECRET"}],
                       [{"type": "turn.completed"}, {"type": "error"}],
                       [{"type": "item.completed", "item": {"type": "command_execution"}},
                        {"type": "turn.completed"}], [None]):
            with self.subTest(events=events), self.assertRaises(codex_llm.CodexError):
                self.events = events
                self.generate()

    def test_missing_empty_or_invalid_output_is_rejected(self):
        for output in (None, "", "not JSON", '{"rows":'):
            with self.subTest(output=output), self.assertRaises(codex_llm.CodexError):
                self.output = output
                self.generate()

    def test_timeout_or_missing_binary_does_not_retry(self):
        for error in (subprocess.TimeoutExpired("codex", 300, stderr="SECRET"), OSError("SECRET")):
            with self.subTest(error=type(error).__name__):
                self.error = error
                self.calls.clear()
                with self.assertRaises(codex_llm.CodexError) as caught:
                    self.generate()
                self.assertEqual(len(self.calls), 2)
                self.assertNotIn("SECRET", str(caught.exception))

    def test_bad_mode_or_missing_binary_fails_before_start(self):
        with patch.dict(os.environ, {"IDEA_LLM_MODE": "api"}), self.assertRaises(codex_llm.CodexError):
            codex_llm.CodexClient()
        with patch.object(codex_llm, "executable", return_value=None), self.assertRaises(codex_llm.CodexError):
            codex_llm.CodexClient()
        self.assertEqual(self.calls, [])


class ProcessTests(unittest.TestCase):
    def test_windows_desktop_binary_is_found_without_app_path(self):
        with tempfile.TemporaryDirectory() as work:
            binary = Path(work) / "OpenAI" / "Codex" / "bin" / "version" / "codex.exe"
            binary.parent.mkdir(parents=True)
            binary.touch()
            # os.name remains native so pathlib does not try to instantiate foreign paths.
            if os.name != "nt":
                self.skipTest("Windows desktop installation discovery")
            with patch.dict(os.environ, {"IDEA_CODEX_BIN": "", "LOCALAPPDATA": work}), \
                 patch.object(codex_llm.shutil, "which", return_value=None):
                self.assertEqual(codex_llm.executable(), str(binary))

    def test_explicit_missing_binary_does_not_silently_choose_another(self):
        with patch.dict(os.environ, {"IDEA_CODEX_BIN": "missing-custom-codex"}), \
             patch.object(codex_llm.shutil, "which", return_value=None):
            self.assertIsNone(codex_llm.executable())

    def test_real_child_accepts_unicode_stdin_without_shell_expansion(self):
        text = '한국어 $(never-execute) `literal`'
        with tempfile.TemporaryDirectory() as work:
            result = codex_llm._run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                                    env=codex_llm.child_environment(), cwd=work, timeout=10, prompt=text)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, text)

    def test_real_hung_child_times_out(self):
        with tempfile.TemporaryDirectory() as work, self.assertRaises(codex_llm.CodexError):
            codex_llm._run([sys.executable, "-c", "import time; time.sleep(10)"],
                           env=codex_llm.child_environment(), cwd=work, timeout=0.2)

    def test_available_only_when_enabled_and_cli_present(self):
        for mode, binary, expected in [("off", "codex", False), ("codex", None, False), ("codex", "codex", True)]:
            with patch.dict(os.environ, {"IDEA_LLM_MODE": mode}), patch.object(codex_llm, "executable", return_value=binary):
                self.assertEqual(codex_llm.is_available(), expected)


if __name__ == "__main__":
    unittest.main()


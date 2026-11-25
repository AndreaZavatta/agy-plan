"""Tests for agy_plan.py against a stub agy, so they need no network or Gemini quota."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "agy-plan" / "scripts" / "agy_plan.py"

# Replays the responses in $STUB_DIR/responses.json, one per call, and logs each argv and the last cwd.
STUB_AGY = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
stub = Path(os.environ["STUB_DIR"])
log = stub / "calls.json"
calls = json.loads(log.read_text()) if log.exists() else []
responses = json.loads((stub / "responses.json").read_text())
current = responses[min(len(calls), len(responses) - 1)]
calls.append(sys.argv[1:])
(stub / "cwd").write_text(os.getcwd())
log.write_text(json.dumps(calls))
touch = current.get("touch")
if touch:
    Path(touch).write_text("changed by agy")
if current.get("exit"):
    sys.stderr.write("boom")
    sys.exit(current["exit"])
print(current.get("raw") or json.dumps(current["result"]))
"""


def result(response, **extra):
    return {"result": {"conversation_id": "conv-1", "status": "SUCCESS", "response": response,
                       "duration_seconds": 2.0, "usage": {"input_tokens": 10}, **extra}}


def plan(body):
    return f"note\n<<<AGY_PLAN_START>>>\n{body}\n<<<AGY_PLAN_END>>>\n"


class AgyPlanTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.stub = Path(tmp.name) / "stub"
        self.stub.mkdir()
        agy = self.stub / "agy"
        agy.write_text(STUB_AGY)
        agy.chmod(agy.stat().st_mode | stat.S_IEXEC)
        # A non-ASCII name, because agy percent-encodes file:// links.
        self.project = Path(tmp.name).resolve() / "Università"
        self.project.mkdir()

    def run_script(self, responses, *args, stdin="the task"):
        (self.stub / "responses.json").write_text(json.dumps(responses))
        env = {**os.environ, "STUB_DIR": str(self.stub), "PATH": f"{self.stub}{os.pathsep}{os.environ['PATH']}"}
        proc = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.project, env=env,
                              input=stdin, capture_output=True, text=True)
        return proc.returncode, json.loads(proc.stdout)

    def calls(self):
        return json.loads((self.stub / "calls.json").read_text())

    def plan_files(self):
        plans = self.project / ".agy-plans"
        return sorted(plans.iterdir()) if plans.exists() else []

    def test_saves_plan_in_read_only_mode_with_task_in_prompt(self):
        code, out = self.run_script([result(plan("# My plan"))], "--task-file", "-")
        self.assertEqual(code, 0)
        text = Path(out["plan_path"]).read_text()
        self.assertTrue(text.startswith('---\ntask: "the task"\n'))
        self.assertTrue(text.endswith("---\n\n# My plan\n"))
        self.assertNotIn("note", text)
        argv = self.calls()[0]
        self.assertEqual(argv[argv.index("--mode") + 1], "plan")
        self.assertIn("<task>\nthe task\n</task>", argv[argv.index("-p") + 1])
        self.assertNotIn("--dangerously-skip-permissions", argv)
        deny = json.loads(argv[argv.index("--settings") + 1])["permissions"]["deny"]
        self.assertEqual(set(deny), {"write_file(*)", "command(*)", "mcp(*)"})

    def test_blocks_reading_env_files_but_not_templates(self):
        for path in [".env", ".env.local", "backend/.env", "backend/.env.example", "node_modules/pkg/.env"]:
            (self.project / path).parent.mkdir(parents=True, exist_ok=True)
            (self.project / path).write_text("KEY=value")
        _, out = self.run_script([result("cut short"), result(plan("# Done"))], "--task-file", "-")
        self.assertEqual(out["blocked_files"], [".env", ".env.local", "backend/.env"])
        for argv in self.calls():
            deny = json.loads(argv[argv.index("--settings") + 1])["permissions"]["deny"]
            self.assertEqual(set(deny), {"write_file(*)", "command(*)", "mcp(*)", "read_file(.env)",
                                         "read_file(.env.local)", "read_file(backend/.env)"})

    def test_every_call_is_read_only_including_retries(self):
        self.run_script([result("cut short"), result(plan("# Done"))], "--task-file", "-")
        self.assertTrue(all("--settings" in argv for argv in self.calls()))

    def test_relativizes_plain_and_percent_encoded_file_links(self):
        encoded = str(self.project).replace("à", "%C3%A0")
        body = f"[a](file://{self.project}/src/a.py) [b](file://{encoded}/src/b.py#L1) [c](file:///etc/hosts)"
        _, out = self.run_script([result(plan(body))], "--task-file", "-")
        text = Path(out["plan_path"]).read_text()
        self.assertIn("[a](src/a.py) [b](src/b.py#L1) [c](file:///etc/hosts)", text)

    def test_resumes_the_conversation_when_turn_was_cut_short(self):
        cut_short = result("I will start by inspecting", denied_actions=[{"action": "command"}])
        code, out = self.run_script([cut_short, result(plan("# Done"))], "--task-file", "-")
        self.assertEqual(code, 0)
        self.assertEqual(out["attempts"], 2)
        self.assertEqual(out["usage"]["input_tokens"], 20)
        self.assertEqual(out["denied_actions"], [{"action": "command"}])
        retry = self.calls()[1]
        self.assertEqual(retry[retry.index("--conversation") + 1], "conv-1")

    def test_success_status_without_a_plan_fails_and_saves_nothing(self):
        code, out = self.run_script([result("")], "--task-file", "-")
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])
        self.assertEqual(out["conversation_id"], "conv-1")
        self.assertEqual(len(self.calls()), 3)
        self.assertEqual(self.plan_files(), [])

    def test_start_marker_without_end_marker_is_incomplete(self):
        code, _ = self.run_script([result("<<<AGY_PLAN_START>>>\n# truncated")], "--task-file", "-")
        self.assertEqual(code, 1)

    def test_reports_agy_failures(self):
        code, out = self.run_script([{"exit": 3}], "--task-file", "-")
        self.assertEqual(code, 1)
        self.assertIn("boom", out["error"])
        code, out = self.run_script([{"raw": "not json"}], "--task-file", "-")
        self.assertEqual(code, 1)
        self.assertIn("invalid JSON", out["error"])

    def test_empty_task_never_calls_agy(self):
        code, _ = self.run_script([result(plan("x"))], "--task-file", "-", stdin="  \n")
        self.assertEqual(code, 1)
        self.assertFalse((self.stub / "calls.json").exists())

    def test_refine_keeps_edits_resumes_conversation_and_bumps_version(self):
        _, first = self.run_script([result(plan("# One"))], "--task-file", "-")
        path = Path(first["plan_path"])
        path.write_text(path.read_text().replace("# One", "# One edited by hand"))

        _, second = self.run_script([result(plan("# Two"))], "--refine", str(path), "--feedback", "-",
                                    stdin="split phase 2")
        self.assertEqual(Path(second["plan_path"]).name, f"{path.stem}-v2.md")
        self.assertIn("# One edited by hand", path.read_text())
        self.assertIn('task: "the task"', Path(second["plan_path"]).read_text())
        argv = self.calls()[-1]
        self.assertEqual(argv[argv.index("--conversation") + 1], "conv-1")
        prompt = argv[argv.index("-p") + 1]
        self.assertIn("split phase 2", prompt)
        self.assertIn("# One edited by hand", prompt)
        self.assertNotIn("task:", prompt)

        _, third = self.run_script([result(plan("# Three"))], "--refine", second["plan_path"], "--feedback", "more")
        self.assertEqual(Path(third["plan_path"]).name, f"{path.stem}-v3.md")

    def test_refine_prompt_stands_alone_when_agy_starts_a_new_conversation(self):
        _, first = self.run_script([result(plan("# One"))], "--task-file", "-")
        _, resumed = self.run_script([result(plan("# Two"))], "--refine", first["plan_path"], "--feedback", "x")
        self.assertIs(resumed["resumed"], True)
        prompt = self.calls()[-1][self.calls()[-1].index("-p") + 1]
        self.assertIn("<task>\nthe task\n</task>", prompt)
        self.assertIn("<<<AGY_PLAN_START>>>", prompt)

        # agy answers with a fresh conversation when the requested one no longer exists.
        _, fresh = self.run_script([result(plan("# Three"), conversation_id="conv-2")],
                                   "--refine", resumed["plan_path"], "--feedback", "y")
        self.assertIs(fresh["resumed"], False)
        self.assertIn('conversation_id: "conv-2"', Path(fresh["plan_path"]).read_text())
        _, new_plan = self.run_script([result(plan("# Four"))], "--task-file", "-")
        self.assertNotIn("resumed", new_plan)

    def test_refine_uses_the_recorded_project_after_the_plan_moves(self):
        _, first = self.run_script([result(plan("# One"))], "--task-file", "-")
        path = Path(first["plan_path"])
        self.assertIn(f"project: {json.dumps(str(self.project), ensure_ascii=False)}", path.read_text())
        moved = self.stub / path.name
        path.rename(moved)
        code, out = self.run_script([result(plan("# Two"))], "--refine", str(moved), "--feedback", "x")
        self.assertEqual(code, 0)
        self.assertEqual(Path(out["plan_path"]).parent, self.stub)
        self.assertEqual(Path(out["plan_path"]).name, f"{path.stem}-v2.md")
        self.assertEqual((self.stub / "cwd").read_text(), str(self.project))

        moved.write_text(moved.read_text().replace(str(self.project), str(self.project / "gone")))
        code, out = self.run_script([result(plan("# Two"))], "--refine", str(moved), "--feedback", "x")
        self.assertEqual(code, 1)
        self.assertIn("pass --project", out["error"])

    def test_refining_an_older_version_never_overwrites_a_newer_one(self):
        _, first = self.run_script([result(plan("# One"))], "--task-file", "-")
        path = Path(first["plan_path"])
        _, second = self.run_script([result(plan("# Two"))], "--refine", str(path), "--feedback", "a")
        _, third = self.run_script([result(plan("# Three"))], "--refine", str(path), "--feedback", "b")
        self.assertEqual(Path(third["plan_path"]).name, f"{path.stem}-v3.md")
        self.assertIn("# Two", Path(second["plan_path"]).read_text())

    def test_malformed_front_matter_is_reported_as_json_error(self):
        _, first = self.run_script([result(plan("# One"))], "--task-file", "-")
        path = Path(first["plan_path"])
        original = path.read_text()
        for broken, message in [(original.replace('task: "the task"', "task: the task"), "not 'key: <JSON value>'"),
                                (original.replace('task: "the task"\n', ""), "missing task")]:
            path.write_text(broken)
            code, out = self.run_script([result(plan("# Two"))], "--refine", str(path), "--feedback", "x")
            self.assertEqual(code, 1)
            self.assertFalse(out["ok"])
            self.assertIn(message, out["error"])

    def test_refine_accepts_a_plan_saved_with_windows_line_endings(self):
        _, first = self.run_script([result(plan("# One"))], "--task-file", "-")
        path = Path(first["plan_path"])
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        code, out = self.run_script([result(plan("# Two"))], "--refine", str(path), "--feedback", "x")
        self.assertEqual(code, 0, out)

    def test_flags_changes_to_the_worktree_but_not_the_plan_file(self):
        subprocess.run(["git", "init", "-q"], cwd=self.project, check=True)
        _, clean = self.run_script([result(plan("# Plan"))], "--task-file", "-")
        self.assertIs(clean["worktree_changed"], False)
        (self.stub / "calls.json").unlink()
        # The first run's plan is already in the status snapshot, so only this file differs.
        touched = {**result(plan("# Plan")), "touch": str(self.project / "edited.py")}
        _, dirty = self.run_script([touched], "--task-file", "-")
        self.assertIs(dirty["worktree_changed"], True)


if __name__ == "__main__":
    unittest.main()

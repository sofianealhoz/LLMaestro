import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import FakeClock, Scripted, Sleeps

from llmaestro.agent import Budget, Journal, Refused, Workspace, drive
from llmaestro.agent.tools import Toolbox
from llmaestro.messages import ToolCall
from llmaestro.providers.base import Completion
from llmaestro.router import Router

ALLOWED = [("python3", "-m", "unittest"), ("git", "status"), ("ls",)]


def scratch_repo() -> Path:
    directory = Path(tempfile.mkdtemp())

    def git(*args):
        subprocess.run(["git", *args], cwd=directory, capture_output=True, check=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (directory / "hello.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (directory / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "initial")
    return directory


def call(text="", calls=()):
    return Completion(text, "fake", "fake-model", 0.0, tool_calls=tuple(calls))


class SandboxCase(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.environ["LLMAESTRO_HOME"] = self.home
        self.repo = scratch_repo()
        self.workspace = Workspace.create(self.repo, "test-run", allowed=ALLOWED)

    def tearDown(self):
        try:
            self.workspace.discard()
        except Refused:
            pass
        os.environ.pop("LLMAESTRO_HOME", None)


class PathJail(SandboxCase):
    def test_climbing_out_with_dots_is_refused(self):
        with self.assertRaises(Refused):
            self.workspace.read("../../etc/passwd")

    def test_an_absolute_path_is_refused(self):
        with self.assertRaises(Refused):
            self.workspace.read("/etc/passwd")

    def test_a_symlink_pointing_out_is_refused(self):
        escape = Path(self.workspace.root) / "escape"
        escape.symlink_to("/etc")

        with self.assertRaises(Refused):
            self.workspace.read("escape/passwd")

    def test_the_git_directory_is_off_limits(self):
        with self.assertRaises(Refused):
            self.workspace.write(".git/config", "broken")

    def test_writing_inside_stays_inside(self):
        written = self.workspace.write("sub/new.py", "x = 1\n")

        self.assertEqual("sub/new.py", written)
        self.assertTrue((self.workspace.root / "sub" / "new.py").is_file())
        self.assertFalse((self.repo / "sub").exists(), "le dépôt réel ne doit pas bouger")


class CommandAllowlist(SandboxCase):
    def test_an_unlisted_command_is_refused(self):
        with self.assertRaises(Refused):
            self.workspace.run(["rm", "-rf", "."])

    def test_a_listed_family_does_not_open_the_whole_binary(self):
        # git status est autorisé, git push ne l'est pas.
        self.assertTrue(self.workspace.run(["git", "status"]).ok)
        with self.assertRaises(Refused):
            self.workspace.run(["git", "push"])

    def test_a_shell_style_string_cannot_smuggle_a_second_command(self):
        toolbox = Toolbox(self.workspace)

        outcome = toolbox.call("run_command", {"command": "git status; rm -rf /"})

        self.assertIn("refusé", outcome.content)

    def test_api_keys_are_stripped_from_the_environment(self):
        os.environ["FAKE_API_KEY"] = "secret"
        self.workspace.allowed = ALLOWED + [("env",)]
        try:
            ran = self.workspace.run(["env"])
            self.assertNotIn("FAKE_API_KEY", ran.output)
        finally:
            os.environ.pop("FAKE_API_KEY", None)


class Tools(SandboxCase):
    def setUp(self):
        super().setUp()
        self.toolbox = Toolbox(self.workspace)

    def test_reading_numbers_the_lines(self):
        outcome = self.toolbox.call("read_file", {"path": "notes.txt"})

        self.assertIn("1\talpha", outcome.content)
        self.assertIn("2\tbeta", outcome.content)

    def test_search_finds_a_literal(self):
        outcome = self.toolbox.call("search", {"pattern": "def hello"})

        self.assertIn("hello.py:1", outcome.content)

    def test_an_edit_must_match_exactly_once(self):
        self.workspace.write("twice.txt", "same\nsame\n")

        outcome = self.toolbox.call("edit_file", {"path": "twice.txt", "old": "same", "new": "x"})

        self.assertIn("unique", outcome.content)

    def test_a_refusal_comes_back_as_a_result_not_an_exception(self):
        outcome = self.toolbox.call("read_file", {"path": "../secrets"})

        self.assertIn("refusé", outcome.content)
        self.assertFalse(outcome.finished)

    def test_there_is_no_tool_that_deletes(self):
        names = {schema["name"] for schema in self.toolbox.schemas}

        self.assertEqual(set(), names & {"delete", "delete_file", "remove", "move", "rename"})

    def test_an_unknown_tool_is_reported_not_executed(self):
        self.assertIn("inconnu", self.toolbox.call("delete_file", {"path": "hello.py"}).content)


class Loop(SandboxCase):
    def journal(self):
        return Journal(Path(self.home) / "run.jsonl", "test-run")

    def router(self, script):
        return Router([Scripted("fake", script, tools=True)], clock=FakeClock(), sleep=Sleeps())

    def test_a_run_writes_then_finishes(self):
        script = [
            call(calls=[ToolCall("1", "write_file", {"path": "new.py", "content": "x = 1\n"})]),
            call(calls=[ToolCall("2", "finish", {"summary": "fichier ajouté"})]),
        ]
        journal = self.journal()

        outcome = drive(self.router(script), self.workspace, "ajoute un fichier", journal)

        self.assertTrue(outcome.ok)
        self.assertEqual("fichier ajouté", outcome.summary)
        self.assertTrue((self.workspace.root / "new.py").is_file())
        events = [record["event"] for record in journal.read()]
        self.assertIn("tool_called", events)
        self.assertIn("run_finished", events)

    def test_the_real_repository_is_untouched_until_promotion(self):
        script = [
            call(calls=[ToolCall("1", "write_file", {"path": "new.py", "content": "x = 1\n"})]),
            call(calls=[ToolCall("2", "finish", {"summary": "fait"})]),
        ]

        drive(self.router(script), self.workspace, "ajoute", self.journal())

        self.assertFalse((self.repo / "new.py").exists())
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=self.repo, capture_output=True, text=True
        )
        self.assertEqual("", status.stdout.strip())

    def test_each_write_is_committed_so_it_can_be_undone(self):
        script = [
            call(calls=[ToolCall("1", "write_file", {"path": "a.py", "content": "a\n"})]),
            call(calls=[ToolCall("2", "write_file", {"path": "b.py", "content": "b\n"})]),
            call(calls=[ToolCall("3", "finish", {"summary": "fait"})]),
        ]

        drive(self.router(script), self.workspace, "deux fichiers", self.journal())

        self.assertGreaterEqual(len(self.workspace.commits()), 2)

    def test_a_step_budget_stops_cleanly(self):
        endless = [call(calls=[ToolCall("1", "list_files", {})])]
        journal = self.journal()

        outcome = drive(
            self.router(endless), self.workspace, "tourne", journal, Budget(steps=3)
        )

        self.assertFalse(outcome.ok)
        self.assertIn("steps", outcome.stopped)
        self.assertIn("budget_exceeded", [r["event"] for r in journal.read()])

    def test_a_plain_answer_ends_the_run(self):
        outcome = drive(
            self.router([call(text="rien à faire")]), self.workspace, "regarde", self.journal()
        )

        self.assertTrue(outcome.ok)
        self.assertEqual("rien à faire", outcome.summary)

    def test_a_refused_tool_does_not_stop_the_run(self):
        script = [
            call(calls=[ToolCall("1", "write_file", {"path": "../evil", "content": "x"})]),
            call(calls=[ToolCall("2", "finish", {"summary": "j'ai changé d'approche"})]),
        ]

        outcome = drive(self.router(script), self.workspace, "essaie", self.journal())

        self.assertTrue(outcome.ok)
        self.assertFalse((Path(self.workspace.root).parent / "evil").exists())


class Preconditions(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        os.environ["LLMAESTRO_HOME"] = self.home

    def tearDown(self):
        os.environ.pop("LLMAESTRO_HOME", None)

    def test_a_dirty_repository_is_refused(self):
        repo = scratch_repo()
        (repo / "hello.py").write_text("changed\n", encoding="utf-8")

        with self.assertRaises(Refused) as caught:
            Workspace.create(repo, "dirty-run")

        self.assertIn("propre", str(caught.exception))

    def test_a_repository_without_a_commit_is_refused(self):
        empty = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=empty, check=True)

        with self.assertRaises(Refused) as caught:
            Workspace.create(empty, "empty-run")

        self.assertIn("sans commit", str(caught.exception))

    def test_a_plain_directory_is_refused(self):
        with self.assertRaises(Refused):
            Workspace.create(tempfile.mkdtemp(), "not-a-repo")


if __name__ == "__main__":
    unittest.main()

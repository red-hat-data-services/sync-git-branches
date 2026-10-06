"""Exercise the real action entrypoint with disposable local bare repositories."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[1] / "entrypoint.sh"
RELEASE = "rhoai-3.6"


class DryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="sync-dry-run-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.real_git = shutil.which("git")
        self.assertIsNotNone(self.real_git)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.push_log = self.root / "pushes.log"
        wrapper = self.bin / "git"
        wrapper.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$1" == push ]]; then\n'
            '  printf "%s\\n" "$*" >> "$PUSH_LOG"\n'
            '  if [[ "$REJECT_PUSH" == true ]]; then echo "Push rejected" >&2; exit 97; fi\n'
            'fi\n'
            'exec "$REAL_GIT" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        self.env = os.environ.copy()
        for key in list(self.env):
            if key.startswith("GIT_"):
                del self.env[key]
        self.env.update({
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "REAL_GIT": self.real_git,
            "PUSH_LOG": str(self.push_log),
            "GITHUB_ACTOR": "sync-test",
            "GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.ignoreCase",
            "GIT_CONFIG_VALUE_0": "false",
            "BASH_ENV": os.devnull,
            "LC_ALL": "C",
        })
        self.invocations = 0
        self.new_repository("repository")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            [self.real_git, *args], cwd=cwd or self.repo, env=self.env,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def new_repository(self, name: str) -> None:
        self.repo = self.root / name
        self.repo.mkdir()
        self.git("init", "--template=", "-b", "main")
        self.git("config", "user.name", "sync-test")
        self.git("config", "user.email", "sync-test@example.com")
        self.commit_file("base.txt", "base\n")
        self.git("branch", RELEASE)
        self.origin = self.root / f"{name}.git"

    def commit_file(self, path: str, content: str | bytes | None) -> None:
        if content is None:
            self.git("rm", "--", path)
        else:
            file = self.repo / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content.encode() if isinstance(content, str) else content)
            self.git("add", "--", path)
        self.git("commit", "-m", "Fixture change")

    def publish(self) -> None:
        self.git("clone", "--bare", str(self.repo), str(self.origin), cwd=self.root)
        self.git("--git-dir", str(self.origin), "symbolic-ref", "HEAD", "refs/heads/main")

    def snapshot(self) -> dict[str, str]:
        return {
            str(file.relative_to(self.origin)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in self.origin.rglob("*") if file.is_file()
        }

    def fail_cleanup(self, failures: int) -> Path:
        """Inject the reported ENOTEMPTY failure without relying on a timing race."""
        attempts = self.root / "cleanup-attempts"
        attempts.write_text("0\n", encoding="utf-8")
        wrapper = self.bin / "rm"
        wrapper.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$*" == "-rf work" ]]; then\n'
            '  read -r attempts < "$CLEANUP_ATTEMPTS"\n'
            '  attempts=$((attempts + 1))\n'
            '  printf "%s\\n" "$attempts" > "$CLEANUP_ATTEMPTS"\n'
            '  if [[ "$attempts" -le "$CLEANUP_FAILURES" ]]; then\n'
            '    echo "rm: can\'t remove work/.git: Directory not empty" >&2\n'
            '    exit 1\n'
            '  fi\n'
            'fi\n'
            'exec "$REAL_RM" "$@"\n', encoding="utf-8",
        )
        wrapper.chmod(0o755)
        self.env.update({
            "REAL_RM": shutil.which("rm"),
            "CLEANUP_ATTEMPTS": str(attempts),
            "CLEANUP_FAILURES": str(failures),
        })
        return attempts

    def run_action(
        self, *, dry_run: str | None = "true", source: str = "main", target: str = RELEASE,
        ignore: str = "", merge_args: str = "--no-edit", push_args: str = "",
        spawn_logs: str = "false", tag: str = "",
    ) -> subprocess.CompletedProcess[str]:
        self.invocations += 1
        self.action_dir = self.root / f"action-{self.invocations}"
        self.action_dir.mkdir()
        self.push_log.write_text("", encoding="utf-8")
        args = [
            "bash", str(ENTRYPOINT), str(self.origin), source, target,
            "read-only-test-token", "", merge_args, push_args, spawn_logs,
            str(self.origin), ignore, "", tag,
        ]
        if dry_run is not None:
            args.append(dry_run)
        return subprocess.run(
            args, cwd=self.action_dir,
            env={**self.env, "REJECT_PUSH": "true" if dry_run == "true" else "false"},
            capture_output=True, text=True, check=False,
        )

    def assert_dry_success(self, result, before) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Dry run completed", result.stdout)
        self.assertEqual(self.push_log.read_text(), "")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.action_dir / "work").exists())

    def test_clean_dry_run_performs_merge_but_never_pushes(self) -> None:
        self.commit_file("code.txt", "incoming\n")
        self.git("checkout", RELEASE)
        self.commit_file("release.txt", "release-only\n")
        self.publish()
        before = self.snapshot()
        result = self.run_action()
        self.assert_dry_success(result, before)
        self.assertIn("code.txt", result.stdout)

    def test_fast_forward_and_no_commit_arguments_are_supported(self) -> None:
        self.commit_file("code.txt", "incoming\n")
        self.publish()
        before = self.snapshot()
        self.assert_dry_success(self.run_action(), before)
        self.assert_dry_success(self.run_action(merge_args="--no-commit --no-ff --no-edit"), before)

    def test_already_contained_source_passes_without_push(self) -> None:
        self.publish()
        before = self.snapshot()
        result = self.run_action()
        self.assert_dry_success(result, before)
        self.assertIn("Already up to date", result.stdout)

    def test_squash_merge_arguments_still_commit_locally_and_sync(self) -> None:
        self.commit_file("code.txt", "incoming\n")
        self.publish()
        before = self.snapshot()
        self.assert_dry_success(self.run_action(merge_args="--squash --no-edit"), before)
        live = self.run_action(dry_run="false", merge_args="--squash --no-edit")
        self.assertEqual(live.returncode, 0, live.stdout + live.stderr)
        self.assertEqual(self.git("--git-dir", str(self.origin), "show", f"{RELEASE}:code.txt"), "incoming")

    def test_conflict_fails_without_mutating_remote(self) -> None:
        self.commit_file("code.txt", "base\n")
        self.git("branch", "-f", RELEASE)
        self.commit_file("code.txt", "source\n")
        self.git("checkout", RELEASE)
        self.commit_file("code.txt", "release\n")
        self.publish()
        before = self.snapshot()
        result = self.run_action()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("code.txt", result.stdout)
        self.assertNotIn("Dry run completed", result.stdout)
        self.assertEqual(self.push_log.read_text(), "")
        self.assertEqual(self.snapshot(), before)

    def test_ignored_conflicts_do_not_hide_blocking_conflicts(self) -> None:
        self.commit_file("code.txt", "base\n")
        self.commit_file(".tekton/task.yaml", "base\n")
        self.git("branch", "-f", RELEASE)
        self.commit_file("code.txt", "source\n")
        self.commit_file(".tekton/task.yaml", "source\n")
        self.git("checkout", RELEASE)
        self.commit_file("code.txt", "release\n")
        self.commit_file(".tekton/task.yaml", None)
        self.publish()
        before = self.snapshot()
        result = self.run_action(ignore=".tekton/*")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Auto-resolving excluded file: .tekton/task.yaml", result.stdout)
        self.assertIn("Unresolvable conflict on: code.txt", result.stdout)
        self.assertEqual(self.push_log.read_text(), "")
        self.assertEqual(self.snapshot(), before)

    def test_ignored_conflicts_have_same_outcome_as_live_sync(self) -> None:
        for kind in ("content", "add-add", "modify-delete", "delete-modify", "binary"):
            with self.subTest(kind=kind):
                self.new_repository(kind)
                path = ".tekton/task.yaml"
                if kind != "add-add":
                    self.commit_file(path, b"base\0\n" if kind == "binary" else "base\n")
                self.git("branch", "-f", RELEASE)
                self.commit_file(path, None if kind == "delete-modify" else (
                    b"source\0\n" if kind == "binary" else "source\n"
                ))
                self.git("checkout", RELEASE)
                target = None if kind == "modify-delete" else (
                    b"release\0\n" if kind == "binary" else "release\n"
                )
                self.commit_file(path, target)
                self.publish()
                before = self.snapshot()
                self.assert_dry_success(self.run_action(ignore=".tekton/*"), before)
                live = self.run_action(dry_run="false", ignore=".tekton/*")
                self.assertEqual(live.returncode, 0, live.stdout + live.stderr)
                if target is None:
                    self.assertNotIn(path, self.git("--git-dir", str(self.origin), "ls-tree", "-r", "--name-only", RELEASE))
                else:
                    result = subprocess.run(
                        [self.real_git, "--git-dir", str(self.origin), "show", f"{RELEASE}:{path}"],
                        env=self.env, capture_output=True, check=True,
                    )
                    self.assertEqual(result.stdout, target.encode() if isinstance(target, str) else target)

    def test_clean_excluded_changes_are_actually_restored(self) -> None:
        self.commit_file("keep.txt", "release config\n")
        self.commit_file("deleted.txt", "keep this file\n")
        self.git("branch", "-f", RELEASE)
        self.commit_file("keep.txt", "upstream config\n")
        self.commit_file("deleted.txt", None)
        self.commit_file("added.txt", "excluded upstream-only file\n")
        self.commit_file("code.txt", "new code\n")
        self.publish()
        before = self.snapshot()
        patterns = "keep.txt, deleted.txt, added.txt"
        dry = self.run_action(ignore=patterns)
        self.assert_dry_success(dry, before)
        for path in ("keep.txt", "deleted.txt", "added.txt"):
            self.assertIn(f"Restoring excluded file to downstream state: {path}", dry.stdout)
        live = self.run_action(dry_run="false", ignore=patterns)
        self.assertEqual(live.returncode, 0, live.stdout + live.stderr)
        self.assertEqual(self.git("--git-dir", str(self.origin), "show", f"{RELEASE}:keep.txt"), "release config")
        self.assertEqual(self.git("--git-dir", str(self.origin), "show", f"{RELEASE}:deleted.txt"), "keep this file")
        self.assertNotIn("added.txt", self.git("--git-dir", str(self.origin), "ls-tree", "-r", "--name-only", RELEASE))
        self.assertEqual(self.git("--git-dir", str(self.origin), "show", f"{RELEASE}:code.txt"), "new code")

    def test_spawn_logs_initial_push_is_also_suppressed(self) -> None:
        self.commit_file("code.txt", "incoming\n")
        self.publish()
        before = self.snapshot()
        self.assert_dry_success(self.run_action(spawn_logs="true"), before)

    def test_push_args_cannot_override_dry_run(self) -> None:
        self.commit_file("code.txt", "incoming\n")
        self.publish()
        before = self.snapshot()
        self.assert_dry_success(self.run_action(push_args="--force"), before)

    def test_omitted_input_and_explicit_false_still_push(self) -> None:
        for mode in (None, "false"):
            with self.subTest(mode=mode):
                self.new_repository("omitted" if mode is None else "false")
                self.commit_file("code.txt", "incoming\n")
                self.publish()
                before = self.snapshot()
                result = self.run_action(dry_run=mode)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(len(self.push_log.read_text().splitlines()), 2)
                self.assertNotEqual(self.snapshot(), before)
                self.assertEqual(self.git("--git-dir", str(self.origin), "show", f"{RELEASE}:code.txt"), "incoming")

    def test_tag_overrides_source_branch(self) -> None:
        self.commit_file("code.txt", "tagged code\n")
        self.git("tag", "v1")
        self.commit_file("code.txt", "later code\n")
        self.publish()
        before = self.snapshot()
        self.assert_dry_success(self.run_action(tag="v1", source="nonexistent"), before)
        live = self.run_action(dry_run="false", tag="v1", source="nonexistent")
        self.assertEqual(live.returncode, 0, live.stdout + live.stderr)
        self.assertEqual(self.git("--git-dir", str(self.origin), "show", f"{RELEASE}:code.txt"), "tagged code")

    def test_invalid_dry_run_value_fails_before_clone_or_push(self) -> None:
        for value in ("TRUE", "yes", "invalid"):
            with self.subTest(value=value):
                result = self.run_action(dry_run=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("dry_run must be", result.stdout)
                self.assertFalse((self.action_dir / "work").exists())
                self.assertEqual(self.push_log.read_text(), "")

    def test_clone_failure_fails_without_push(self) -> None:
        result = self.run_action()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Dry run completed", result.stdout)
        self.assertEqual(self.push_log.read_text(), "")

    def test_transient_cleanup_failure_retries_without_failing_sync(self) -> None:
        for mode in ("true", "false"):
            with self.subTest(dry_run=mode):
                self.new_repository(f"transient-{mode}")
                if mode == "false":
                    self.commit_file("code.txt", "incoming\n")
                self.publish()
                before = self.snapshot()
                attempts = self.fail_cleanup(1)
                result = self.run_action(dry_run=mode)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Directory not empty", result.stderr)
                self.assertIn("Cleanup attempt 1 failed; retrying", result.stdout)
                self.assertEqual(attempts.read_text().strip(), "2")
                self.assertNotIn("::warning::", result.stdout)
                self.assertFalse((self.action_dir / "work").exists())
                if mode == "true":
                    self.assertIn("Already up to date", result.stdout)
                    self.assert_dry_success(result, before)
                else:
                    self.assertEqual(self.git("--git-dir", str(self.origin), "show", f"{RELEASE}:code.txt"), "incoming")

    def test_persistent_cleanup_failure_warns_after_bounded_retries(self) -> None:
        for mode in ("true", "false"):
            with self.subTest(dry_run=mode):
                self.new_repository(f"persistent-{mode}")
                self.commit_file("code.txt", "incoming\n")
                self.publish()
                before = self.snapshot()
                attempts = self.fail_cleanup(99)
                result = self.run_action(dry_run=mode)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(attempts.read_text().strip(), "3")
                self.assertIn("::warning::Could not remove temporary checkout after 3 attempts", result.stdout)
                self.assertTrue((self.action_dir / "work/.git").is_dir())
                if mode == "true":
                    self.assertEqual(self.snapshot(), before)
                    self.assertEqual(self.push_log.read_text(), "")
                else:
                    self.assertEqual(self.git("--git-dir", str(self.origin), "show", f"{RELEASE}:code.txt"), "incoming")

    def test_cleanup_handling_does_not_mask_merge_conflict(self) -> None:
        self.commit_file("code.txt", "base\n")
        self.git("branch", "-f", RELEASE)
        self.commit_file("code.txt", "source\n")
        self.git("checkout", RELEASE)
        self.commit_file("code.txt", "release\n")
        self.publish()
        before = self.snapshot()
        attempts = self.fail_cleanup(99)
        result = self.run_action()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Merge conflicts detected", result.stdout)
        self.assertEqual(attempts.read_text().strip(), "0")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.push_log.read_text(), "")

    def test_cleanup_handling_does_not_mask_push_failure(self) -> None:
        self.commit_file("code.txt", "incoming\n")
        self.publish()
        hook = self.origin / "hooks/pre-receive"
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        before = self.git("--git-dir", str(self.origin), "rev-parse", RELEASE)
        attempts = self.fail_cleanup(99)
        result = self.run_action(dry_run="false")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pre-receive hook declined", result.stderr)
        self.assertEqual(attempts.read_text().strip(), "0")
        self.assertEqual(self.git("--git-dir", str(self.origin), "rev-parse", RELEASE), before)


if __name__ == "__main__":
    unittest.main()

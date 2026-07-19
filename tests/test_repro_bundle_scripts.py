import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = REPO_ROOT / "scripts" / "repro_bundle"


@unittest.skipUnless(
    all(shutil.which(command) for command in ("bash", "tar", "zstd", "split", "git")),
    "reproduction bundle tools require bash, tar, zstd, split, and git",
)
class ReproBundleScriptsTest(unittest.TestCase):
    def run_command(
        self, *args: str, check: bool = True, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            args,
            check=False,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if check and result.returncode != 0:
            self.fail(
                f"Command failed ({result.returncode}): {args}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def initialize_repository(self, repository: Path) -> None:
        repository.mkdir(parents=True)
        self.run_command("git", "init", "-q", cwd=repository)
        self.run_command("git", "config", "user.name", "Repro Test", cwd=repository)
        self.run_command("git", "config", "user.email", "repro@example.test", cwd=repository)
        (repository / ".gitignore").write_text(
            ".cache/\n__pycache__/\n", encoding="utf-8"
        )
        (repository / "tracked.txt").write_text("from git\n", encoding="utf-8")
        self.run_command("git", "add", ".", cwd=repository)
        self.run_command("git", "commit", "-q", "-m", "fixture", cwd=repository)

    def test_shell_scripts_parse(self) -> None:
        for script in SCRIPT_ROOT.glob("*.sh"):
            self.run_command("bash", "-n", str(script))

    def test_package_verify_and_restore_only_non_git_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "source" / "project"
            self.initialize_repository(repository)

            (repository / ".cache").mkdir()
            (repository / ".cache" / "model.bin").write_bytes(bytes(range(256)) * 20)
            (repository / "__pycache__").mkdir()
            (repository / "__pycache__" / "module.pyc").write_bytes(b"disposable")
            (repository / "research.json").write_text('{"result": true}\n', encoding="utf-8")
            os.symlink("research.json", repository / "research.link")
            (repository / "tracked.txt").write_text("staged change\n", encoding="utf-8")
            self.run_command("git", "add", "tracked.txt", cwd=repository)
            (repository / "tracked.txt").write_text("unstaged final\n", encoding="utf-8")

            data = root / "source" / "runtime"
            data.mkdir()
            (data / "result.tsv").write_text("metric\tvalue\nloss\t2.1\n", encoding="utf-8")

            profile = root / "profile.tsv"
            profile.write_text(
                "# requirement\tmode\tlabel\tabsolute_path\n"
                f"required\tgit-state\trepository\t{repository}\n"
                f"required\tentry\truntime\t{data}\n",
                encoding="utf-8",
            )
            bundle = root / "bundle"
            self.run_command(
                str(SCRIPT_ROOT / "package_profile.sh"),
                str(profile),
                str(bundle),
                "--part-size",
                "1K",
            )

            self.assertEqual((bundle / "STATE").read_text().strip(), "complete")
            self.assertTrue((bundle / "bundle.json").is_file())
            self.assertGreaterEqual(len(list((bundle / "archives").glob("*.part-*"))), 2)
            self.assertFalse(any(bundle.rglob("repository.bundle")))
            self.run_command(str(bundle / "tools" / "verify_bundle.sh"), str(bundle))
            self.run_command(
                str(bundle / "tools" / "verify_bundle.sh"), str(bundle), "--deep"
            )

            restore_prefix = root / "restore"
            restored_repository = restore_prefix / repository.relative_to("/")
            restored_repository.parent.mkdir(parents=True)
            self.run_command(
                "git", "clone", "-q", str(repository), str(restored_repository)
            )
            self.run_command(
                str(bundle / "tools" / "restore_bundle.sh"),
                str(bundle),
                str(restore_prefix),
            )

            self.assertEqual(
                (restored_repository / "tracked.txt").read_text(encoding="utf-8"),
                "unstaged final\n",
            )
            self.assertEqual(
                self.run_command(
                    "git", "show", ":tracked.txt", cwd=restored_repository
                ).stdout,
                "staged change\n",
            )
            self.assertEqual(
                (restored_repository / "research.json").read_text(encoding="utf-8"),
                '{"result": true}\n',
            )
            self.assertTrue((restored_repository / "research.link").is_symlink())
            self.assertFalse((restored_repository / "__pycache__").exists())
            self.assertEqual(
                hashlib.sha256(
                    (restored_repository / ".cache/model.bin").read_bytes()
                ).hexdigest(),
                hashlib.sha256((repository / ".cache/model.bin").read_bytes()).hexdigest(),
            )
            restored_data = restore_prefix / data.relative_to("/")
            self.assertEqual(
                (restored_data / "result.tsv").read_text(encoding="utf-8"),
                "metric\tvalue\nloss\t2.1\n",
            )

    def test_dry_run_rejects_output_inside_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            profile = Path(temporary) / "profile.tsv"
            profile.write_text(
                f"required\tentry\tsource\t{source}\n", encoding="utf-8"
            )
            result = self.run_command(
                str(SCRIPT_ROOT / "package_profile.sh"),
                str(profile),
                str(source / "bundle"),
                "--dry-run",
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cannot be inside", result.stderr)

    def test_children_mode_detects_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "gpu-root"
            repository = source / "project"
            self.initialize_repository(repository)
            (repository / "outside-git.txt").write_text("state\n", encoding="utf-8")
            (source / "checkpoints").mkdir()
            (source / "checkpoints" / "model.pt").write_bytes(b"weights")
            profile = root / "profile.tsv"
            profile.write_text(
                f"required\tchildren-except-git\tgpu\t{source}\n",
                encoding="utf-8",
            )
            result = self.run_command(
                str(SCRIPT_ROOT / "package_profile.sh"),
                str(profile),
                str(root / "bundle"),
                "--dry-run",
            )
            self.assertIn("git-state", result.stdout)
            self.assertIn("entry", result.stdout)
            self.assertIn("outside-git.txt", "\n".join(
                self.run_command(
                    "git", "-C", str(repository), "ls-files", "--others"
                ).stdout.splitlines()
            ))


if __name__ == "__main__":
    unittest.main()

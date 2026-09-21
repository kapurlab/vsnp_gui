"""One vsnp3 install is read AND run, or the app says why not.

Every consumer of ``vsnp3_path`` falls into one of two camps: those that read
``<prefix>/dependencies/reference_options_paths.txt`` (the Reference Editor,
refs.list_references) and those that execute ``<prefix>/bin/vsnp3_*.py``. When a
prefix satisfies the first and not the second, the app lists and edits the
references of one install while Step 2 runs a different vsnp3 found on PATH —
with its own registry. The analysis then succeeds against reference data the user
never edited, and nothing anywhere says so.

That is not hypothetical. On a bdtools sandbox deployment (Kapur Lab, 2026-09-11)
TOOLS_ROOT/vsnp3 does not exist, because vsnp3 ships INSIDE the vsnp_gui env. The
old default took ``~/miniforge3/envs/vsnp3`` without asking whether it existed;
startup then wrote a registry there, and that write's mkdir(parents=True) created
the whole tree. The phantom had dependencies/ but no bin/, so the Reference
Editor showed mtbc0_v1.1 from one root while Step 2 analysed the copy from
another.

Run directly:  python test_vsnp3_install_consistency.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import refs  # noqa: E402


def make_vsnp3(prefix: Path, *, with_bin: bool = True) -> Path:
    """A prefix that looks installed to the degree asked for."""
    (prefix / "dependencies").mkdir(parents=True, exist_ok=True)
    if with_bin:
        b = prefix / "bin"
        b.mkdir(parents=True, exist_ok=True)
        (b / "vsnp3_step2.py").write_text("#!/usr/bin/env python\n")
        (b / "vsnp3_step2.py").chmod(0o755)
    return prefix


class RegistryNeverFabricatesAnInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_writing_into_a_real_install_still_works(self):
        """The registry records the path it was given.

        add_reference_path stores the RESOLVED path, so compare resolved forms
        on both sides — otherwise this fails on macOS, where tempfile hands back
        /var/... and /var is a symlink to /private/var.
        """
        prefix = make_vsnp3(self.root / "env")
        refs.add_reference_path(prefix, str(self.root))
        self.assertIn(
            str(self.root.resolve()),
            [str(Path(p).resolve()) for p in refs.get_reference_paths(prefix)])

    def test_a_prefix_with_nothing_at_it_is_refused(self):
        """mkdir(parents=True) here invents ~/miniforge3/envs/vsnp3 out of nothing.

        The phantom then satisfies every "is it configured?" check while having
        no bin/ to run, which is precisely the split this module exists to stop.
        """
        ghost = self.root / "miniforge3" / "envs" / "vsnp3"
        with self.assertRaises(FileNotFoundError):
            refs.add_reference_path(ghost, str(self.root))
        self.assertFalse(ghost.exists(), "a vsnp3 install was fabricated")
        self.assertFalse((self.root / "miniforge3").exists(),
                         "parent directories were fabricated")

    def test_an_existing_prefix_may_gain_its_dependencies_dir(self):
        """A real env that has never had a registry is not a phantom."""
        prefix = self.root / "env"
        (prefix / "bin").mkdir(parents=True)
        refs.add_reference_path(prefix, str(self.root))
        self.assertTrue((prefix / "dependencies").is_dir())


class DefaultPathMustBeRunnable(unittest.TestCase):
    """config resolves to an install that can actually run vsnp3."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _resolver(self, tools_root, home, prefix):
        """Re-import config with the environment it derives its paths from."""
        import importlib
        env = {
            "BDTOOLS_TOOLS_ROOT": str(tools_root),
            "HOME": str(home),
            "VSNP_GUI_SITE_ROOT": str(self.root / "site"),
        }
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            import config
            importlib.reload(config)
            return Path(config._DEFAULT_VSNP3_PATH), config
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_a_shared_install_that_can_run_is_preferred(self):
        tools = self.root / "tools"
        make_vsnp3(tools / "vsnp3")
        got, _ = self._resolver(tools, self.root / "home", None)
        self.assertEqual(got, tools / "vsnp3")

    def test_a_personal_path_with_nothing_at_it_is_not_chosen(self):
        """The old rule took this unconditionally; it is where the phantom came from."""
        tools = self.root / "tools"          # no vsnp3 under it
        home = self.root / "home"
        home.mkdir(parents=True)
        got, _ = self._resolver(tools, home, None)
        self.assertNotEqual(
            got, home / "miniforge3" / "envs" / "vsnp3",
            "resolved to a personal path that does not exist")

    def test_a_directory_without_bin_does_not_count_as_installed(self):
        """Naming a non-runnable path is fine; TRUSTING it is not.

        With nothing runnable anywhere the resolver still reports the shared
        location, so the UI can say "no vsnp3 here" about a recognisable path
        rather than about "". What must not happen is that path being treated as
        an install — that is what _looks_like_vsnp3 gates, and what makes
        _require_vsnp3_script refuse instead of falling through to PATH.
        """
        tools = self.root / "tools"
        make_vsnp3(tools / "vsnp3", with_bin=False)   # dependencies/ only
        _got, cfg = self._resolver(tools, self.root / "home", None)
        self.assertFalse(cfg._looks_like_vsnp3(tools / "vsnp3"))


class ExecutionRefusesRatherThanFallingThrough(unittest.TestCase):
    """The decisive property: a half-present install must not run something else.

    Falling back to PATH is what turned a broken configuration into a wrong
    RESULT — Step 2 completing against a same-named reference from another
    install's registry, with nothing in the log to distinguish it from a correct
    run.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        import app.main as m
        self.main = m

    def test_the_configured_install_is_used_when_it_can_run(self):
        prefix = make_vsnp3(self.root / "env")
        got = self.main._require_vsnp3_script(
            {"vsnp3_path": str(prefix)}, "vsnp3_step2.py")
        self.assertEqual(got, prefix / "bin" / "vsnp3_step2.py")

    def test_a_half_present_install_refuses(self):
        prefix = make_vsnp3(self.root / "env", with_bin=False)
        with self.assertRaises(Exception) as ctx:
            self.main._require_vsnp3_script(
                {"vsnp3_path": str(prefix)}, "vsnp3_step2.py")
        self.assertIn("bin/vsnp3_step2.py", str(getattr(ctx.exception, "detail", ctx.exception)))

    def test_the_refusal_names_the_other_install_on_path(self):
        """The message has to be actionable: which install, and what to set."""
        other = make_vsnp3(self.root / "other")
        prefix = make_vsnp3(self.root / "env", with_bin=False)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(other / "bin") + os.pathsep + old_path
        try:
            with self.assertRaises(Exception) as ctx:
                self.main._require_vsnp3_script(
                    {"vsnp3_path": str(prefix)}, "vsnp3_step2.py")
            detail = str(getattr(ctx.exception, "detail", ctx.exception))
            self.assertIn(str(other), detail)
            self.assertIn("NOT being used", detail)
        finally:
            os.environ["PATH"] = old_path


if __name__ == "__main__":
    unittest.main()

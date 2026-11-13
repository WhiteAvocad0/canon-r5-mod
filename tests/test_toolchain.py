"""Public portable tests for toolchain admission and controlled environment rules."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from toolchain import OVERRIDE_VARIABLES, POLICY_PATH, admit_toolchain, controlled_environment


def write_mock_toolchain(
    bin_dir: Path,
    *,
    target: str = "arm-none-eabi",
    gcc_version: str = "15.2.1",
    omit: set[str] = frozenset(),
    non_exec: set[str] = frozenset(),
) -> None:
    policy = json.loads(POLICY_PATH.read_text())
    for name in policy["required_executables"]:
        if name in omit:
            continue
        p = bin_dir / name
        p.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "args = sys.argv[1:]\n"
            f"if args == ['-dumpmachine']: print({target!r})\n"
            f"elif args == ['-dumpfullversion']: print({gcc_version!r})\n"
            f"elif '--version' in args: print({name!r} + ' mock ' + {gcc_version!r})\n"
            "else: sys.exit(1)\n"
        )
        mode = 0o644 if name in non_exec else 0o755
        p.chmod(mode)


class ToolchainTests(unittest.TestCase):
    def test_controlled_environment_rejects_ambient_overrides_and_sets_lc_all(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            env = controlled_environment()
            self.assertEqual(env["LC_ALL"], "C")

        for var in ("CFLAGS", "LDFLAGS", "PYTHONPATH", "ML_OBJS"):
            self.assertIn(var, OVERRIDE_VARIABLES)
            with mock.patch.dict(os.environ, {var: "poison"}, clear=True):
                with self.assertRaisesRegex(ValueError, var):
                    controlled_environment()

    def test_portable_toolchain_admission_records_actual_digests_without_mac_pinning(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            tdir = Path(tmp) / "bin"
            tdir.mkdir()
            write_mock_toolchain(tdir)

            resolved, info = admit_toolchain(tdir)
            self.assertEqual(resolved, tdir.resolve())
            self.assertEqual(info["target"], "arm-none-eabi")
            self.assertEqual(info["gcc_version"], "15.2.1")
            self.assertEqual(len(info["executables"]), 6)

            gcc_sha = hashlib.sha256((tdir / "arm-none-eabi-gcc").read_bytes()).hexdigest()
            self.assertEqual(info["executables"]["arm-none-eabi-gcc"]["sha256"], gcc_sha)
            # Never persist os.environ in admission metadata
            self.assertNotIn("env", info)
            self.assertNotIn("environ", info)

            # Policy file itself has no host-OS executable SHA pins
            policy_text = POLICY_PATH.read_text()
            self.assertNotIn("darwin", policy_text.lower())
            self.assertNotIn(gcc_sha, policy_text)

    def test_rejects_missing_nonexec_wrong_target_and_wrong_version(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)

            with self.assertRaisesRegex(ValueError, "does not exist"):
                admit_toolchain(root / "missing")

            file_path = root / "not_a_dir"
            file_path.write_text("hi")
            with self.assertRaisesRegex(ValueError, "not a directory"):
                admit_toolchain(file_path)

            missing_tool = root / "missing_ld"
            missing_tool.mkdir()
            write_mock_toolchain(missing_tool, omit={"arm-none-eabi-ld"})
            with self.assertRaisesRegex(ValueError, "arm-none-eabi-ld"):
                admit_toolchain(missing_tool)

            non_exec = root / "non_exec"
            non_exec.mkdir()
            write_mock_toolchain(non_exec, non_exec={"arm-none-eabi-nm"})
            if os.geteuid() != 0 and not (os.stat(non_exec / "arm-none-eabi-nm").st_mode & stat.S_IXUSR):
                with self.assertRaisesRegex(ValueError, "not executable"):
                    admit_toolchain(non_exec)

            wrong_target = root / "wrong_target"
            wrong_target.mkdir()
            write_mock_toolchain(wrong_target, target="aarch64-none-elf")
            with self.assertRaisesRegex(ValueError, "Unsupported toolchain target"):
                admit_toolchain(wrong_target)

            wrong_ver = root / "wrong_ver"
            wrong_ver.mkdir()
            write_mock_toolchain(wrong_ver, gcc_version="13.2.0")
            with self.assertRaisesRegex(ValueError, "Unsupported GCC version"):
                admit_toolchain(wrong_ver)


if __name__ == "__main__":
    unittest.main()

"""Desktop packager tests with small stand-ins for the pinned xPack distributions."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
import build_app


class ToolchainFixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.zip = self.base / "fixture.zip"
        prefix = build_app.TOOLCHAIN_ROOT
        with zipfile.ZipFile(self.zip, "w") as zf:
            zf.writestr(f"{prefix}/bin/arm-none-eabi-gcc.exe", b"compiler")
            zf.writestr(f"{prefix}/distro-info/licenses/gcc/COPYING", b"license")
            zf.writestr(f"{prefix}/lib/empty/", b"")

    def target(self, platform, arch):
        target = build_app.target_for(self.base / "dist", "R5 Mod Assistant", platform, arch)
        for group in ("common", "windows", "macos"):
            notices = target.tree / "packaging" / "xpack-notices" / group
            notices.mkdir(parents=True)
            (notices / f"{group}.txt").write_bytes(group.encode())
        return target

    def pin(self, target, archive):
        key = (target.os, target.arch)
        suffix, _ = build_app.TOOLCHAIN_ARCHIVES[key]
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        return mock.patch.dict(build_app.TOOLCHAIN_ARCHIVES, {key: (suffix, digest)})


class PackagedToolchainTests(ToolchainFixture):
    def test_wrong_digest_fails_before_extraction(self):
        target = self.target("win32", "AMD64")
        with self.assertRaisesRegex(SystemExit, "SHA-256 .* expected .* for .*win32-x64.zip"):
            build_app.install_toolchain(target, self.zip)
        self.assertEqual(list(target.tree.parent.iterdir()), [target.tree])

    def test_downloads_host_archive_and_preserves_files_in_repacked_zip(self):
        target = self.target("win32", "AMD64")

        def download(url, dest):
            self.assertEqual(url, f"{build_app.TOOLCHAIN_RELEASE}/{build_app.TOOLCHAIN_ROOT}-win32-x64.zip")
            shutil.copyfile(self.zip, dest)

        with self.pin(target, self.zip), mock.patch.object(build_app.urllib.request, "urlretrieve", side_effect=download):
            build_app.install_toolchain(target, None)

        package = self.base / "app.zip"
        with mock.patch.object(build_app, "write_extras"):
            build_app.make_zip(target, package, {}, "", self.base)
        with zipfile.ZipFile(package) as zf:
            root = "R5 Mod Assistant/_internal/arm-toolchain/"
            self.assertEqual(zf.read(root + "bin/arm-none-eabi-gcc.exe"), b"compiler")
            self.assertEqual(zf.read(root + "distro-info/licenses/gcc/COPYING"), b"license")
            self.assertTrue(zf.getinfo(root + "lib/empty/").is_dir())
            notices = root + "distro-info/R5-notices/"
            self.assertEqual(zf.read(notices + "common.txt"), b"common")
            self.assertEqual(zf.read(notices + "windows.txt"), b"windows")
            self.assertNotIn(notices + "macos.txt", zf.namelist())

    @unittest.skipIf(os.name == "nt", "macOS archive uses POSIX symlinks and executable modes")
    def test_local_mac_archive_retains_permissions_and_relative_links(self):
        target = self.target("darwin", "arm64")
        archive = self.base / "mac.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            gcc = tarfile.TarInfo(f"{build_app.TOOLCHAIN_ROOT}/bin/gcc")
            gcc.mode = 0o755
            gcc.size = 3
            tar.addfile(gcc, io.BytesIO(b"gcc"))
            alias = tarfile.TarInfo(f"{build_app.TOOLCHAIN_ROOT}/bin/arm-none-eabi-gcc")
            alias.type, alias.linkname = tarfile.SYMTYPE, "gcc"
            tar.addfile(alias)
            license = tarfile.TarInfo(f"{build_app.TOOLCHAIN_ROOT}/distro-info/licenses/gcc-15.2.1/COPYING3")
            license.size = 4
            tar.addfile(license, io.BytesIO(b"GPL3"))
        with self.pin(target, archive), mock.patch.object(build_app.urllib.request, "urlretrieve") as download:
            build_app.install_toolchain(target, archive)
            download.assert_not_called()
        alias = target.app / "Contents/Resources/arm-toolchain/bin/arm-none-eabi-gcc"
        self.assertEqual(alias.readlink(), Path("gcc"))
        self.assertTrue(os.access(alias, os.X_OK))
        self.assertEqual(alias.read_bytes(), b"gcc")
        notices = target.tree.parent / "arm-toolchain/distro-info/R5-notices"
        self.assertEqual({p.name for p in notices.iterdir()}, {"common.txt", "macos.txt", "THIRD-PARTY-NOTICE.txt"})
        self.assertIn("Readline, which GDB uses", (notices / "THIRD-PARTY-NOTICE.txt").read_text(encoding="utf-8"))

    def test_refuses_unsupported_windows_architecture(self):
        target = self.target("win32", "ARM64")
        with self.assertRaisesRegex(SystemExit, "No bundled xPack toolchain for windows arm64"):
            build_app.install_toolchain(target, self.zip)


class ToolchainNoticeTests(ToolchainFixture):
    """The notice names the toolchain, its version and source, and the bundle stays unmodified."""

    def install(self):
        target = self.target("win32", "AMD64")
        with self.pin(target, self.zip):
            unmodified = build_app.install_toolchain(target, self.zip)
        return target, unmodified

    def test_notice_names_toolchain_version_archive_and_source(self):
        target, _ = self.install()
        digest = hashlib.sha256(self.zip.read_bytes()).hexdigest()
        notice = (build_app.toolchain_dir(target) / build_app.TOOLCHAIN_NOTICES / "THIRD-PARTY-NOTICE.txt").read_bytes()
        self.assertIn(b"\r\n", notice)  # Windows packages get Windows line endings
        text = notice.decode("utf-8")
        for needle in (f"xPack GNU Arm Embedded GCC {build_app.TOOLCHAIN_VERSION} for windows x64, unmodified",
                       f"{build_app.TOOLCHAIN_ROOT}-win32-x64.zip", digest, "COPYING.RUNTIME", "newlib-nano",
                       "(distro-info/licenses): gcc.", "common.txt, windows.txt",
                       build_app.TOOLCHAIN_SOURCE_NAME, build_app.TOOLCHAIN_SOURCE_SHA256, build_app.TOOLCHAIN_SOURCE_URL,
                       build_app.TOOLCHAIN_COMPANION_NAME, build_app.TOOLCHAIN_COMPANION_URL,
                       build_app.TOOLCHAIN_COMPANION_SHA256):
            self.assertIn(needle, text)
        self.assertNotIn("Readline", text)

    def test_unmodified_check_ignores_only_the_added_notices(self):
        target, unmodified = self.install()
        toolchain = build_app.toolchain_dir(target)
        self.assertEqual(set(unmodified), {"bin/arm-none-eabi-gcc.exe", "distro-info/licenses/gcc/COPYING"})
        self.assertEqual(build_app.verify_toolchain(toolchain, unmodified), 2)
        (toolchain / "bin" / "arm-none-eabi-gcc.exe").write_bytes(b"patched")
        with self.assertRaisesRegex(SystemExit, "changed \\['bin/arm-none-eabi-gcc.exe'\\]"):
            build_app.verify_toolchain(toolchain, unmodified)
        (toolchain / "bin" / "arm-none-eabi-gcc.exe").write_bytes(b"compiler")
        (toolchain / "distro-info" / "extra.txt").write_bytes(b"added")
        with self.assertRaisesRegex(SystemExit, "extra \\['distro-info/extra.txt'\\]"):
            build_app.verify_toolchain(toolchain, unmodified)

    def test_refuses_an_archive_without_component_licenses(self):
        bare = self.base / "bare.zip"
        with zipfile.ZipFile(bare, "w") as zf:
            zf.writestr(f"{build_app.TOOLCHAIN_ROOT}/bin/arm-none-eabi-gcc.exe", b"compiler")
        target = self.target("win32", "AMD64")
        with self.pin(target, bare), self.assertRaisesRegex(SystemExit, "no component licenses"):
            build_app.install_toolchain(target, bare)

    def test_release_workflow_attaches_the_pinned_source_and_names_the_toolchain(self):
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "desktop-app.yml").read_text(
            encoding="utf-8")
        self.assertIn("packaging/build_app.py --print-toolchain-source", workflow)
        self.assertIn("./*.tar.xz SHA256SUMS.txt", workflow)
        notes = workflow.split("cat > notes.md <<'EOF'", 1)[1].split("EOF", 1)[0]
        for needle in (build_app.TOOLCHAIN_NAME, "unmodified", build_app.TOOLCHAIN_SOURCE_NAME,
                       build_app.TOOLCHAIN_COMPANION_NAME):
            self.assertIn(needle, notes)
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(build_app.main(["--print-toolchain-source"]), 0)
        self.assertEqual([line.split() for line in out.getvalue().splitlines()], [
            [build_app.TOOLCHAIN_SOURCE_NAME, build_app.TOOLCHAIN_SOURCE_URL, build_app.TOOLCHAIN_SOURCE_SHA256],
            [build_app.TOOLCHAIN_COMPANION_NAME, build_app.TOOLCHAIN_COMPANION_URL, build_app.TOOLCHAIN_COMPANION_SHA256]])
        self.assertNotIn("vars.", workflow)  # the source packages are pinned in the tagged tree, not in settings
        self.assertIn("vcruntime140.dll", notes)

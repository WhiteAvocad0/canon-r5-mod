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


class PackagedToolchainTests(unittest.TestCase):
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
        with self.pin(target, archive), mock.patch.object(build_app.urllib.request, "urlretrieve") as download:
            build_app.install_toolchain(target, archive)
            download.assert_not_called()
        alias = target.app / "Contents/Resources/arm-toolchain/bin/arm-none-eabi-gcc"
        self.assertEqual(alias.readlink(), Path("gcc"))
        self.assertTrue(os.access(alias, os.X_OK))
        self.assertEqual(alias.read_bytes(), b"gcc")
        notices = target.tree.parent / "arm-toolchain/distro-info/R5-notices"
        self.assertEqual({p.name for p in notices.iterdir()}, {"common.txt", "macos.txt"})

    def test_refuses_unsupported_windows_architecture(self):
        target = self.target("win32", "ARM64")
        with self.assertRaisesRegex(SystemExit, "No bundled xPack toolchain for windows arm64"):
            build_app.install_toolchain(target, self.zip)

"""Build the corresponding-source companion for the bundled xPack GNU Arm Embedded GCC.

    python3 packaging/xpack_source_package.py --output DIR [--cache DIR]

Reads packaging/xpack-source-inputs.tsv (the owner of the component list), downloads every
row that has a file name into --cache (default DIR/cache), refuses any file whose SHA-256
differs from its pin, and writes:

    DIR/xpack-15.2.1-1.1-corresponding-source.tar      README.txt, MANIFEST.tsv, SHA256SUMS, sources/*
    DIR/MANIFEST.tsv                                    the inventory it was built from
    DIR/SHA256SUMS                                      the companion and every source file

The archive is a plain ustar file (its members are already compressed) with sorted members,
zero times and owners, so the same inventory always gives the same bytes. Rows without a file name are covered by
Arm's source package, which every app release attaches separately; they are listed in the
manifest, not downloaded. Nothing is uploaded.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
from pathlib import Path
import shutil
import sys
import tarfile
import urllib.request

HERE = Path(__file__).resolve().parent
INVENTORY = HERE / "xpack-source-inputs.tsv"
VERSION = "15.2.1-1.1"
NAME = f"xpack-{VERSION}-corresponding-source"
COLUMNS = ["component", "version", "hosts", "file", "url", "sha256", "license", "shipped", "verification"]
README = """\
Corresponding source for xPack GNU Arm Embedded GCC {version}
as bundled, unmodified, in R5 Mod Assistant (macOS arm64, macOS x86_64, Windows x64).

sources/ holds every source archive listed in MANIFEST.tsv with a file name, byte for byte as
downloaded from the listed upstream URL; SHA256SUMS lists their digests. The xPack build
scripts and patches are the recipe and xbb-helper archives at the exact commits of the
release tags (rows "xpack-recipe" and "xbb-helper"). Only the patches those scripts find are
applied; for this release that is xbb-helper's patches/gdb-16.3-cross.git.patch.

GCC, Newlib, Binutils and GDB are not repeated here. xPack builds them from the same
revisions that Arm's source package contains (see MANIFEST.tsv):
  arm-gnu-toolchain-src-snapshot-15.2.rel1.tar.xz
  {arm_url}
  SHA-256 {arm_sha256}
It is attached to every R5 Mod Assistant release that bundles this toolchain.

Not included: arm-toolchain/bin/DLLs/vcruntime140.dll and vcruntime140_1.dll on Windows are
Microsoft Visual C++ redistributable binaries under Microsoft's redistribution terms; they
have no published source. zlib and gmp are fetched from their GitHub release and GNU mirror
because the URLs in xPack's scripts no longer serve them to automated clients; both are the
same signed releases.

Rebuild: the recipes build with xpm (npm install, xpm install, xpm run build --config <host>);
see build-assets/README-DEVELOP.md inside the recipe archive.
"""
ARM_URL = ("https://developer.arm.com/-/media/Files/downloads/gnu/15.2.rel1/srcrel/"
           "arm-gnu-toolchain-src-snapshot-15.2.rel1.tar.xz")
ARM_SHA256 = "06f4e5792b566a771c300ac6f46c3422e23e34f4e3a5d44a8cb5594ead6fb82e"


def read_inventory(path: Path = INVENTORY) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t")
        if reader.fieldnames != COLUMNS:
            raise SystemExit(f"{path}: columns {reader.fieldnames}, expected {COLUMNS}")
        rows = list(reader)
    files = [row["file"] for row in rows if row["file"]]
    if len(files) != len(set(files)):
        raise SystemExit(f"{path}: duplicate file names")
    for row in rows:
        if row["file"] and (len(row["sha256"]) != 64 or "/" in row["file"] or not row["url"].startswith("https://")):
            raise SystemExit(f"{path}: {row['component']} needs an https URL, a plain file name and a SHA-256")
    return rows


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(row: dict[str, str], cache: Path) -> Path:
    """The pinned file in the cache, downloading it when it is missing or wrong."""
    path = cache / row["file"]
    if path.is_file() and sha256(path) == row["sha256"]:
        return path
    partial = path.with_name(path.name + ".part")
    print(f"Downloading {row['url']}", flush=True)
    with urllib.request.urlopen(row["url"], timeout=120) as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)
    digest = sha256(partial)
    if digest != row["sha256"]:
        partial.unlink()
        raise SystemExit(f"{row['file']}: SHA-256 {digest}, expected {row['sha256']} ({row['url']})")
    partial.replace(path)
    return path


def add(tar: tarfile.TarFile, name: str, data: bytes | None = None, source: Path | None = None) -> None:
    info = tarfile.TarInfo(f"{NAME}/{name}")
    info.size = len(data) if data is not None else source.stat().st_size
    info.mode, info.mtime, info.uid, info.gid, info.uname, info.gname = 0o644, 0, 0, 0, "", ""
    if data is not None:
        tar.addfile(info, io.BytesIO(data))
    else:
        with source.open("rb") as stream:
            tar.addfile(info, stream)


def build(output: Path, cache: Path, inventory: Path = INVENTORY) -> Path:
    rows = read_inventory(inventory)
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    sources = {row["file"]: fetch(row, cache) for row in rows if row["file"]}
    sums = "".join(f"{row['sha256']}  sources/{row['file']}\n" for row in sorted(
        (r for r in rows if r["file"]), key=lambda r: r["file"]))
    manifest = inventory.read_bytes()
    archive = output / f"{NAME}.tar"
    with tarfile.open(archive, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        add(tar, "README.txt", README.format(version=VERSION, arm_url=ARM_URL, arm_sha256=ARM_SHA256).encode())
        add(tar, "MANIFEST.tsv", manifest)
        add(tar, "SHA256SUMS", sums.encode())
        for name in sorted(sources):
            add(tar, f"sources/{name}", source=sources[name])
    (output / "MANIFEST.tsv").write_bytes(manifest)
    (output / "SHA256SUMS").write_text(f"{sha256(archive)}  {archive.name}\n" + sums, encoding="utf-8")
    size = archive.stat().st_size
    if size >= 2 << 30:
        raise SystemExit(f"{archive} is {size} bytes; GitHub release assets must stay under 2 GiB")
    print(f"{archive}: {size} bytes, SHA-256 {sha256(archive)}")
    return archive


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--cache", type=Path, help="where downloads are kept (default OUTPUT/cache)")
    args = ap.parse_args(argv)
    build(args.output.resolve(), (args.cache or args.output / "cache").resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())

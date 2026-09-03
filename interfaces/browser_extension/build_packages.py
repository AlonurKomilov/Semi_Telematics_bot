"""Package a build for the two ways it ships, into versions/.

    npm run build && python3 build_packages.py
    → versions/4truck-extension-store-<v>.zip     Web Store upload: manifest WITHOUT `key`
    → versions/4truck-extension-sideload-<v>.zip  Load unpacked: manifest WITH the store's public key

The store refuses a manifest that carries ``key`` — it generated the
package's key itself and keeps the private half.  The sideload build
carries the PUBLIC half so an unpacked copy computes the store's id.

versions/ holds exactly the current version; every earlier package
already there moves to versions/_archive/ first, so there is never a
choice between two zips.  The whole folder is git-ignored: packages are
built artefacts, the source is the repo.  Bump ``version`` before
building — the store refuses a version it has already seen, drafts
included.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
DIST = HERE / "dist"
VERSIONS = HERE / "versions"
ARCHIVE = VERSIONS / "_archive"
PREFIX = "4truck-extension-"


def _zip_dir(src: pathlib.Path, out: pathlib.Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(src).as_posix())


def archive_older(version: str) -> list[pathlib.Path]:
    """Move every package in versions/ that is not this version into _archive/."""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    moved = []
    for z in sorted(VERSIONS.glob(f"{PREFIX}*.zip")):
        if not z.name.endswith(f"-{version}.zip"):
            dest = ARCHIVE / z.name
            if dest.exists():
                dest.unlink()
            moved.append(z.rename(dest))
    return moved


def main() -> int:
    if not (DIST / "manifest.json").is_file():
        print("run `npm run build` first", file=sys.stderr); return 1
    manifest = json.loads((DIST / "manifest.json").read_text())
    version = manifest.get("version", "0.0.0")
    if not manifest.get("key"):
        print("dist/manifest.json has no `key` — the sideload build would get a random id", file=sys.stderr); return 1
    VERSIONS.mkdir(exist_ok=True)
    for old in archive_older(version):
        print(f"archived  {old.relative_to(HERE)}")

    sideload = VERSIONS / f"{PREFIX}sideload-{version}.zip"
    _zip_dir(DIST, sideload)
    print(f"sideload  {sideload.relative_to(HERE)}")

    with tempfile.TemporaryDirectory() as td:
        stage = pathlib.Path(td) / "pkg"
        shutil.copytree(DIST, stage)
        stripped = dict(manifest)
        stripped.pop("key", None)                # the store's own; it refuses a copy
        (stage / "manifest.json").write_text(json.dumps(stripped, indent=2) + "\n")
        store = VERSIONS / f"{PREFIX}store-{version}.zip"
        _zip_dir(stage, store)
        print(f"store     {store.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

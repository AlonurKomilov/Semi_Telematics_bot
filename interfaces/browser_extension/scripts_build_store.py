"""Build the zip the Chrome Web Store accepts.

The store refuses a manifest that carries ``key``: it generated the
package's key itself when the item was created, and that key never
leaves Google.  Our ``manifest.json`` carries the PUBLIC half of it
(Package → View public key) so a sideloaded build gets the store's id —
and the store build must not carry it.  This strips it, nothing else.

    npm run build && python3 scripts_build_store.py [out.zip]
    → 4truck-extension-store-<version>.zip

Bump ``version`` first: the store refuses a version it has already
seen, drafts included.
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


def main() -> int:
    if not (DIST / "manifest.json").is_file():
        print("run `npm run build` first", file=sys.stderr); return 1
    with tempfile.TemporaryDirectory() as td:
        stage = pathlib.Path(td) / "pkg"
        shutil.copytree(DIST, stage)
        mf = stage / "manifest.json"
        d = json.loads(mf.read_text())
        version = d.get("version", "0.0.0")
        d.pop("key", None)                       # the store's own; it refuses a copy
        mf.write_text(json.dumps(d, indent=2) + "\n")
        out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / f"4truck-extension-store-{version}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(stage.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(stage).as_posix())
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

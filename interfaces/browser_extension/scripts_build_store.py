"""Build the zip the Chrome Web Store accepts.

The store refuses a manifest that carries ``key`` — it assigns the key
itself.  To keep the SAME id the sideload build has, the private half
travels inside the zip as ``key.pem`` on the FIRST upload only; the
store reads it, derives the id, and never needs it again.  The file is
read from the server's off-repo secrets dir and written nowhere else.

    python3 scripts_build_store.py            # after `npm run build`
    → ../../../tmp… /4truck-extension-store-<version>.zip
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
KEY = pathlib.Path.home() / ".4truck-extension" / "key.pem"


def main() -> int:
    if not (DIST / "manifest.json").is_file():
        print("run `npm run build` first", file=sys.stderr); return 1
    if not KEY.is_file():
        print(f"private key missing: {KEY}", file=sys.stderr); return 1
    with tempfile.TemporaryDirectory() as td:
        stage = pathlib.Path(td) / "pkg"
        shutil.copytree(DIST, stage)
        mf = stage / "manifest.json"
        d = json.loads(mf.read_text())
        version = d.get("version", "0.0.0")
        d.pop("key", None)                       # the store assigns it
        mf.write_text(json.dumps(d, indent=2) + "\n")
        shutil.copy(KEY, stage / "key.pem")      # first upload only
        out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / f"4truck-extension-store-{version}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(stage.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(stage).as_posix())
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

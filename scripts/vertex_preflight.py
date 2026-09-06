"""Does a new Google Cloud project actually work — before the API restarts on it.

Moving the AI to another project is two lines in ``.env`` and a
restart, and the restart is where a wrong answer costs an outage: the
bot and the dashboard assistant both build their client at first use,
so a project that cannot serve is discovered by a customer.

This asks the questions the restart would ask, against the NEW
credentials, without touching the running configuration:

  1. Does the service-account JSON parse, and which project is it for?
  2. Does the environment agree with it?
  3. Can we build a client and get a real completion from the default
     model (``VERTEX_AI_MODEL``, today gemini-2.5-flash)?
  4. Which of the registry's other models answer, and which return the
     quota error that means "enabled, but this project has none yet"?

Nothing is written and no configuration is changed.  Point it at the
new files explicitly:

    python3 -m scripts.vertex_preflight \\
        --creds /home/abcdev/projects/Semi_Telematics_bot/new-project-key.json \\
        --project my-new-project-id

Omit both to test whatever ``.env`` already carries — which is how you
confirm the switch AFTER making it.

Exit code 0 means the default model answered.  A model that fails on
QUOTA is reported but does not fail the run: quota is requested per
base model per project and is expected to be missing on a fresh one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys

# Read the file BEFORE anything imports the AI stack: the client reads
# these from the environment at build time, so a late assignment would
# be ignored by a module that had already resolved them.
from dotenv import load_dotenv

load_dotenv()


def _fail(msg: str) -> int:
    print(f"\n  FAILED — {msg}\n")
    return 1


def inspect_creds(path: str) -> dict | None:
    p = pathlib.Path(path)
    if not p.is_file():
        print(f"  credentials  MISSING at {path}")
        return None
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        print(f"  credentials  UNREADABLE: {e}")
        return None
    if data.get("type") != "service_account":
        print(f"  credentials  wrong type: {data.get('type')!r} "
              f"(expected service_account)")
        return None
    print(f"  credentials  {p.name}")
    print(f"    project    {data.get('project_id')}")
    print(f"    identity   {data.get('client_email')}")
    return data


async def probe_model(name: str) -> tuple[str, str]:
    """Ask one model to say one word.  Returns (verdict, detail)."""
    from capabilities.ai import models as m
    try:
        caller = m._build_model(name, os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
        out = await asyncio.to_thread(
            caller.generate_content, "Reply with the single word: ok")
        text = (getattr(out, "text", None) or "").strip()
        return ("OK", text[:40] or "(answered, no text part)")
    except Exception as e:
        detail = str(e)
        low = detail.lower()
        if "quota" in low or "429" in low or "resource_exhausted" in low:
            return ("QUOTA", "enabled, but this project has no quota yet")
        if "permission" in low or "403" in low or "denied" in low:
            return ("DENIED", "service account lacks roles/aiplatform.user, "
                              "or the model is not enabled in Model Garden")
        if "not found" in low or "404" in low:
            return ("ABSENT", "not available in this project/region")
        return ("ERROR", detail[:160])


async def run(creds: str | None, project: str | None, deep: bool) -> int:
    if creds:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds
    if project:
        os.environ["GOOGLE_CLOUD_PROJECT"] = project

    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    proj = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    default_model = os.environ.get("VERTEX_AI_MODEL", "gemini-2.5-flash")

    print("\n  WHAT WOULD BE USED")
    print(f"    project    {proj or '(unset)'}")
    print(f"    location   {location}")
    print(f"    model      {default_model}\n")

    if not creds_path:
        return _fail("GOOGLE_APPLICATION_CREDENTIALS is unset — "
                     "pass --creds or set it in .env")
    if not proj:
        return _fail("GOOGLE_CLOUD_PROJECT is unset — pass --project")

    data = inspect_creds(creds_path)
    if data is None:
        return _fail("the credentials file is not usable")

    # The mismatch that produces a confusing 403 rather than a clear
    # error: the key belongs to one project, the env names another.
    if data.get("project_id") and data["project_id"] != proj:
        print(f"\n  MISMATCH — the key is for {data['project_id']!r} but "
              f"GOOGLE_CLOUD_PROJECT says {proj!r}.")
        print("  Vertex would authenticate as one and bill the other, "
              "and usually answers 403.\n")
        return 1

    print(f"\n  DEFAULT MODEL — {default_model}")
    verdict, detail = await probe_model(default_model)
    print(f"    {verdict:<7} {detail}")
    if verdict != "OK":
        print("\n  The assistant would not answer on this project.  Do not "
              "restart the API onto it yet.\n")
        return 1

    if deep:
        from capabilities.ai.registry import MODEL_REGISTRY
        others = [n for n in MODEL_REGISTRY if n != default_model]
        print(f"\n  EVERY OTHER MODEL IN THE REGISTRY ({len(others)})")
        print("    QUOTA means the model is reachable and this project has "
              "not been granted any yet —")
        print("    request it per base model at "
              "console.cloud.google.com/vertex-ai/quotas\n")
        for name in others:
            v, d = await probe_model(name)
            print(f"    {v:<7} {name:<28} {d}")

    print("\n  The default model answered.  The switch is safe to make:")
    print("    .env  GOOGLE_CLOUD_PROJECT=" + proj)
    print("    .env  GOOGLE_APPLICATION_CREDENTIALS=" + creds_path)
    print("  then restart the API and the bot.\n")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--creds", help="path to the NEW service-account JSON")
    p.add_argument("--project", help="the NEW project id")
    p.add_argument("--deep", action="store_true",
                   help="also probe every other model in the registry "
                        "(slower; tells you what still needs quota)")
    args = p.parse_args()
    return asyncio.run(run(args.creds, args.project, args.deep))


if __name__ == "__main__":
    sys.exit(main())

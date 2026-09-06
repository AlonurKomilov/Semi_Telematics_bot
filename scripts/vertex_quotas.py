"""What quota THIS project holds, per model in the registry — the number
a 429 will run into.

Google keeps two kinds.  Gemini GA models sit on dynamic shared quota,
which shows up as a ceiling in the billions of tokens a minute and is
never the thing that limits us.  Everything else — the newer Gemini
generations, every xAI model, Claude — carries a fixed per-base-model
limit that starts SMALL (Grok 4.6 opened at one request a minute) and
is raised by asking, per model, in the console.  A partner MaaS model
(DeepSeek, Qwen, Kimi, GLM, Llama) has no per-model row at all: it is
capacity-bound on Google's side, and there is nothing to request.

    python3 -m scripts.vertex_quotas            # every registry model
    python3 -m scripts.vertex_quotas --raw      # every base_model Google lists

Reads with the service account in .env, which needs
roles/serviceusage.serviceUsageViewer (granted 2026-09-06).  Writes
nothing.  The names printed in the middle column are the ones to type
into IAM & Admin → Quotas when asking for more.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict

import requests
from dotenv import load_dotenv

load_dotenv()

#: Google's internal suffixes on a base_model name.  ``gemini-2.5-flash``
#: is quota'd as ``gemini-2.5-flash-ga``; 3.x as ``…-qcd``.  Stripped
#: before matching so the registry key lines up.
_SUFFIXES = re.compile(r"-(ga|qcd|cider-qcd|latest)$")
#: Sibling products that share a prefix and are not the chat model.
_NOISE = re.compile(r"-(tts|cyber|image|embedding|audio|live|native-audio)")


def _fetch(project: str, token: str) -> list[dict]:
    url = (f"https://serviceusage.googleapis.com/v1beta1/projects/{project}"
           f"/services/aiplatform.googleapis.com/consumerQuotaMetrics")
    out, page = [], None
    while True:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"},
                         params={"pageSize": 200, **({"pageToken": page} if page else {})},
                         timeout=60)
        if r.status_code == 403:
            sys.exit("403 — the service account needs roles/serviceusage.serviceUsageViewer")
        r.raise_for_status()
        j = r.json()
        out += j.get("metrics", [])
        page = j.get("nextPageToken")
        if not page:
            return out


def _per_base_model(metrics: list[dict]) -> dict[str, list[tuple[str, str, object, object]]]:
    """base_model → [(metric, region, effective, default)]."""
    rows = defaultdict(list)
    for m in metrics:
        name = m.get("displayName", "")
        for lim in m.get("consumerQuotaLimits", []):
            for b in lim.get("quotaBuckets", []):
                dims = b.get("dimensions") or {}
                bm = dims.get("base_model")
                if not bm or _NOISE.search(bm):
                    continue
                region = dims.get("region") or ("global" if "global" in name.lower() else "multi-region")
                rows[bm].append((name, region, b.get("effectiveLimit"), b.get("defaultLimit")))
    return rows


def _wire_names(reg_name: str, info: dict) -> set[str]:
    """What Google might call this registry model in a quota dimension."""
    api = info.get("api_type", "gemini")
    if api == "anthropic":
        return {"anthropic-" + (info.get("anthropic_model_id") or "")}
    if api == "openai_compat":
        tail = (info.get("maas_model_id") or "").split("/")[-1]
        return {tail, tail.replace("-maas", ""), reg_name}
    return {reg_name}


def _short(metric: str) -> str:
    m = metric.lower()
    kind = ("requests" if "request" in m else
            "input tok" if "input token" in m else
            "output tok" if "output token" in m else metric[:18])
    return f"{kind}/min"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raw", action="store_true", help="every base_model Google lists, not just ours")
    args = p.parse_args()

    from capabilities.ai.registry import MODEL_REGISTRY, _get_credentials
    import google.auth.transport.requests as gart
    creds = _get_credentials()
    if creds is None:
        return sys.exit("credentials not loadable — check GOOGLE_APPLICATION_CREDENTIALS")
    creds.refresh(gart.Request())
    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    rows = _per_base_model(_fetch(project, creds.token))
    stripped = {bm: _SUFFIXES.sub("", bm) for bm in rows}

    print(f"\n  project {project}\n")
    if args.raw:
        for bm in sorted(rows):
            for metric, region, eff, dflt in sorted(rows[bm]):
                print(f"  {bm:<44} {_short(metric):<14} {region:<12} {str(eff):>12} {str(dflt):>12}")
        return 0

    print(f"  {'registry model':<26} {'quota name (type this in the console)':<40} {'limit':<14} {'region':<10} {'now':>12}")
    print("  " + "-" * 106)
    for reg_name, info in MODEL_REGISTRY.items():
        wanted = _wire_names(reg_name, info)
        hits = sorted(bm for bm, s in stripped.items() if s in wanted or bm in wanted)
        if not hits:
            kind = "partner MaaS — capacity-bound, no per-model quota to request" \
                if info.get("api_type") == "openai_compat" else \
                "dynamic shared quota — no per-model row"
            print(f"  {reg_name:<26} {kind}")
            continue
        first = True
        for bm in hits:
            keep = [r for r in rows[bm] if r[1] in ("global", "us-central1", "us-east5", "us-west2")]
            for metric, region, eff, dflt in sorted(keep, key=lambda r: (r[1], r[0])):
                flag = "" if eff == dflt else f"  (default {dflt})"
                print(f"  {(reg_name if first else ''):<26} {bm:<40} {_short(metric):<14} {region:<10} {str(eff):>12}{flag}")
                first = False
    print("\n  None = nothing provisioned: the model answers 429 'submit a quota increase' until one is granted.")
    print("  A limit in the billions is dynamic shared quota — not the thing that limits us.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

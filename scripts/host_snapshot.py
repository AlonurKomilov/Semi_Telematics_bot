#!/usr/bin/env python3
"""Host resource snapshot — CPU, memory, disk, and WHO is using them.

Reports only.  Never signals, throttles, or stops a process: the heavy
consumers on this box are development agents, and a deploy script has no
business deciding they should die.

Why this exists alongside capabilities/platform/capacity/sampler.py: that
sampler already records cpu_pct / load1 / mem_pct / disk_pct for the
Capacity page, which is the right home for the time series.  What it
cannot answer is *attribution* — it shows load1=52 without showing which
processes made it 52.  When a deploy takes 136s instead of 40s that
attribution is the whole question, so this prints per-command CPU totals.

Grouping matters: nine separate 85% processes sharing one command name
read as noise in `top` but as "codebase-memory-mcp 743%" here, which is
the form that answers "is it my agents or something else".

Usage:
    python3 scripts/host_snapshot.py            # full snapshot
    python3 scripts/host_snapshot.py --brief    # one line, for deploys
"""

from __future__ import annotations

import os
import sys
from collections import defaultdict

# 1.0s, not 0.3s.  This box's heaviest consumers are indexer processes
# that spawn, burn a core, and exit in bursts; a 0.3s window repeatedly
# landed in the gaps and reported 5% CPU while the 1-minute load average
# was 42 and climbing.  A second is long enough to average across a burst
# and still cheap enough to run before every deploy.
SAMPLE_SECONDS = 1.0
TOP_N = 4
BAR_WIDTH = 10


def _bar(pct: float) -> str:
    filled = max(0, min(BAR_WIDTH, round(pct / 100 * BAR_WIDTH)))
    return "█" * filled + "░" * (BAR_WIDTH - filled)


def _verdict(ratio: float) -> tuple[str, str]:
    """Map contention (1.0 == all cores busy) to an icon and a reading.

    Driven by the WORSE of two signals, because each is blind on its own:
    load average is a 1-minute exponential average that lags a fresh
    spike badly (observed: load1 3.97 on 8 cores while processes were
    already burning 743% — a "healthy" verdict beside a saturated box),
    while instantaneous CPU% misses processes blocked in uninterruptible
    I/O that load average does count.  Taking the max means a busy box is
    never reported as idle by whichever signal happens to lag.
    """
    if ratio < 0.7:
        return "✅", "Healthy — deploys should run at full speed."
    if ratio < 1.0:
        return "🟡", "Busy — deploys will be a little slower than normal."
    if ratio < 2.0:
        return "🟠", "Oversubscribed — deploys will take 2-3x longer than normal."
    return "🔴", "Heavily oversubscribed — deploys will crawl."


def _fallback() -> int:
    """No psutil — report what /proc alone can tell us rather than nothing."""
    with open("/proc/loadavg") as fh:
        load1 = float(fh.read().split()[0])
    cores = os.cpu_count() or 1
    icon, reading = _verdict(load1 / cores)
    print(f"{icon} load {load1:.2f} on {cores} cores — {reading} (psutil missing)")
    return 0


def main() -> int:
    brief = "--brief" in sys.argv
    # Deploys reuse the full layout with their own heading ("Server
    # before deploy"), so the report is identical everywhere it appears
    # rather than a bar chart in one place and a one-liner in another.
    title = "Server resources"
    if "--title" in sys.argv:
        idx = sys.argv.index("--title")
        if idx + 1 < len(sys.argv):
            title = sys.argv[idx + 1]

    try:
        import psutil
    except ImportError:
        return _fallback()

    cores = psutil.cpu_count() or 1
    load1 = os.getloadavg()[0]
    load_ratio = load1 / cores
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")

    # Capture pid -> name NOW, into our own dict.
    #
    # Not `p.info` later: psutil.process_iter() reuses cached Process
    # objects across calls, so a second process_iter() with different
    # attrs REPLACES .info on the objects we are holding.  The D-state
    # check below did exactly that and every command rendered as "?"
    # with 40 processes lumped together — but only on the runs where
    # that check fired, which is what made it look intermittent.
    names: dict[int, str] = {}
    procs = []
    for p in psutil.process_iter(["name"]):
        procs.append(p)
        nm = p.info.get("name")
        if not nm:
            # Readable even for other users' processes, unlike psutil's
            # name() which can raise AccessDenied.
            try:
                with open(f"/proc/{p.pid}/comm") as fh:
                    nm = fh.read().strip()
            except OSError:
                nm = ""
        names[p.pid] = nm or "(unknown)"
        try:
            p.cpu_percent(None)  # prime; first call always returns 0.0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    cpu_pct = psutil.cpu_percent(interval=SAMPLE_SECONDS)

    by_name: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    for p in procs:
        try:
            pct = p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if pct > 0:
            nm = names.get(p.pid, "(unknown)")
            by_name[nm] += pct
            counts[nm] += 1

    top = sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)[:TOP_N]

    # Instantaneous saturation (1.0 == every core busy) vs the lagging
    # 1-minute average; report against whichever is worse.
    ratio = max(load_ratio, cpu_pct / 100.0)
    icon, reading = _verdict(ratio)

    # When the two signals disagree, the reader gets "oversubscribed"
    # and "CPU is idle" on adjacent lines and cannot reconcile them.
    #
    # So: lead with what to DO, and drop the mechanism entirely.  The
    # earlier version explained exponential averages and sample windows,
    # which is the right explanation for the wrong question — nobody
    # running a deploy needs to know how load average is computed, they
    # need to know whether to deploy now.
    note = ""
    if load_ratio >= 1.0 and cpu_pct < 50:
        d_state = sum(
            1 for p in procs
            if _safe_status(p, psutil) == psutil.STATUS_DISK_SLEEP
        )
        if d_state:
            note = (f"Disk is the bottleneck right now, not CPU "
                    f"({d_state} processes waiting on it). Deploys will be slow.")
        else:
            note = ("The CPU is free right now — the high load number is a "
                    "burst that just ended. Safe to deploy.")
            icon, reading = "✅", "Healthy — deploys should run at full speed."

    if brief:
        # Same one-scale rule as the full view: cores, never per-core %.
        busiest = (
            f" · busiest {top[0][0]} {top[0][1] / 100:.1f}/{cores} cores"
            if top else ""
        )
        print(
            f"{icon} CPU {cpu_pct:.0f}% · RAM {vm.percent:.0f}% · "
            f"disk {du.percent:.0f}%{busiest}"
        )
        print(f"   {reading}")
        if note:
            print(f"   {note}")
        return 0

    print(f"📊 {title}")
    print()
    print(f"   CPU     {_bar(cpu_pct)}  {cpu_pct:3.0f}%   of {cores} cores")
    print(f"   Memory  {_bar(vm.percent)}  {vm.percent:3.0f}%   "
          f"{(vm.total - vm.available) / 1024**3:.1f}G used of {vm.total / 1024**3:.1f}G")
    print(f"   Disk    {_bar(du.percent)}  {du.percent:3.0f}%   "
          f"{du.used / 1024**3:.0f}G used of {du.total / 1024**3:.0f}G "
          f"({du.free / 1024**3:.0f}G free)")
    if top:
        print()
        # Cores, not percent.
        #
        # psutil (like top/htop) reports per-process CPU against ONE
        # core, so 678% means 6.78 cores — while the bar above reports
        # against the WHOLE machine, where 100% is all 8 cores.  Two
        # different meanings of "%" in one screen is unreadable, and it
        # is what made "678%" look like a typo for 67.8%.  Expressing
        # attribution in cores removes the second scale entirely: "6.8
        # of 8 cores" needs no convention to interpret.
        print(f"   Busiest commands  (this server has {cores} cores)")
        for nm, pct in top:
            n = counts[nm]
            suffix = f"  ({n} processes)" if n > 1 else ""
            print(f"      {pct / 100:4.1f} of {cores} cores   {nm}{suffix}")
    print()
    print(f"   {icon} {reading}")
    print(f"      Load average {load1:.2f} across {cores} cores.")
    if note:
        print(f"      {note}")
    return 0


def _safe_status(p, psutil_mod):
    try:
        return p.status()
    except (psutil_mod.NoSuchProcess, psutil_mod.AccessDenied):
        return None


if __name__ == "__main__":
    raise SystemExit(main())

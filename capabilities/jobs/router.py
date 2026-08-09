"""Job-queue diagnostics API — ARQ job status + manual triggers.

router.py is interface-layer code co-located with its domain
(docs/FEATURES.md): router.py and config.py are the interface-layer pair — those two may
# import interfaces.api.deps; nothing else in the feature may.
URL history: was GET/POST /admin/jobs/* until 2026-06-11.
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from interfaces.api.deps import require_permission

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ── Job queue diagnostics ───────────────────────────
#
# Read-only endpoints that surface the state of the ARQ job queue.
# Used by ops to:
#   * verify a freshly-enqueued job is being picked up by a worker
#   * poll a long-running job (PDF generation, report export) from the
#     dashboard without holding an HTTP connection open
#   * confirm the `/admin/jobs/enqueue/{name}` admin trigger for the
#     pre-warm fanout actually queued work
#
# Job results are JSON; the queue itself never holds binary payloads.
# Any large artifact (PDF, CSV) is written to object storage and the
# job result holds the URL.

@router.get("/{job_id}")
async def job_status(
    job_id: str,
    user: dict = Depends(require_permission("can_manage_account")),
):
    """Look up an ARQ background job by id.

    Returns the job's current status (deferred / queued / in_progress /
    complete / not_found), enqueue + start + finish times, and the
    job result when complete.

    Permission: ``can_manage_account`` — ARQ doesn't natively scope
    jobs to tenants so we restrict status access to admins. If you add
    user-facing async jobs (e.g. dashboard "Generate report" button),
    enforce ownership inside the job's result by stamping the requester
    on enqueue.
    """
    from infra import jobs as _jobs
    info = await _jobs.get_job_status(job_id)
    if info is None:
        raise HTTPException(404, f"Job {job_id} not found or queue unavailable")
    return info


@router.post("/prewarm-scorecards")
async def trigger_prewarm_scorecards(
    days: int = Query(7, ge=1, le=90),
    user: dict = Depends(require_permission("can_manage_account")),
):
    """Manually fire the scorecards cache pre-warm fanout for the
    caller's account. Useful for ops to re-warm the cache after a
    schema/rules change without waiting for the 06:00 cron.

    Returns ``{job_id}`` of the per-account precompute job. Poll
    ``GET /admin/jobs/{job_id}`` for status.
    """
    from infra import jobs as _jobs
    job = await _jobs.enqueue("precompute_scorecards", user["account_id"], days)
    if job is None:
        raise HTTPException(503, "Job queue unavailable — is the ARQ worker running?")
    return {"job_id": job.job_id, "function": "precompute_scorecards", "account_id": user["account_id"], "days": days}



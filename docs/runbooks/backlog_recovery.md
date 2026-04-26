# Async Backlog Recovery Runbook

## Goal
Recover from a growing async job backlog, stuck-running jobs, or dead-letter buildup without making the queue worse.

## Trigger signals
- Release gates show blocked backlog or dead-letter checks.
- Observability shows queued jobs climbing without draining.
- Worker heartbeat is stale or stuck-running jobs are present.

## Triage flow
1. Open the release center and review:
   - worker heartbeat
   - oldest pending job
   - stuck-running jobs
   - dead-letter jobs
   - recent failures
2. Confirm whether the problem is:
   - worker stopped
   - external delivery failures
   - one noisy tenant or webhook target
   - a new deployment or config issue

## Recovery actions
1. If the worker heartbeat is stale, restart the API/worker process path before retrying jobs.
2. If dead letters are growing because of one destination, pause that integration before replaying anything.
3. Use billing or webhook recovery controls only after the underlying failure cause is understood.
4. If backlog pressure is tied to app slowness, coordinate with the slow app triage runbook.

## Safety rules
- Do not bulk replay dead letters while upstream failures are still active.
- Do not resume a paused tenant launch path if release gates are still blocked.

## Exit criteria
- Worker heartbeat is current.
- Queued jobs are draining.
- Stuck-running count is zero.
- Dead-letter count is stable or decreasing.

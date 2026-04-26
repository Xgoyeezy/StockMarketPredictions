# Slow App Triage Runbook

## Goal
Stabilize user-facing latency when the desk loads slowly, charts stall, or requests time out.

## Check first
1. Open the release center.
2. Review:
   - request timeout risks
   - slow route profiles
   - upstream target health
   - job backlog and dead letters
   - market-data freshness

## Triage flow
1. Confirm whether the issue is broad or isolated to one tenant, route, or ticker.
2. If `frontend.bootstrap`, `dashboard`, `analyze`, or `compare` is slow, note the worst route stage from the route-profile panel.
3. If upstream timeouts are increasing, treat the issue as provider pressure first and avoid repeated manual refreshes.
4. If job backlog is rising with latency, move to the backlog recovery runbook.

## Recovery actions
1. Reduce repeated dashboard refreshes during the incident window.
2. Check whether the slow path is cacheable and already warming; give short-lived caches time to settle before escalating.
3. If one upstream target is the clear source, switch to the stale-feed runbook if data freshness is degraded.
4. If latency is tied to a recent change, prepare rollback using the rollback runbook.

## Exit criteria
- Request timeout risks return to normal.
- Slow-route profiles no longer show a sustained spike.
- User-facing pages load without retry loops or obvious stalls.

# Stale Market-Data Runbook

## Goal
Recover safely when charts, scans, or signal surfaces are serving lagging market data.

## Trigger signals
- Release center market-data freshness shows `warning` or `stale`.
- The desk shows a stale-feed banner.
- Chart timestamps stop advancing during an active session.

## Triage flow
1. Open the release center and note:
   - feed status
   - lag seconds
   - session label
   - upstream target health
2. Confirm whether the stale state affects one ticker, one interval, or the whole feed.
3. Check for upstream timeout spikes that may indicate provider degradation.

## Recovery actions
1. Avoid forcing repeated scans or compare requests while the feed is stale.
2. Let queued background work drain before retrying broad market surfaces.
3. If freshness remains stale across the active session, notify operators and treat the provider as degraded.
4. If a deployment introduced the issue, move to the rollback runbook.

## Communication
- Treat delayed data as a personal trading blocker and note whether the app is in fallback mode.
- Do not claim fresh signal readiness until the feed returns to `fresh`.

## Exit criteria
- Feed status returns to `fresh`.
- Chart timestamps resume advancing in the active session.
- No stale-feed warning is shown on the desk for the affected route.

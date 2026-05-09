# Readiness Scoring

Readiness is a 100-point score persisted to `readiness_snapshots` and `readiness_blockers`.

Weights:
- broker connectivity: 15
- data freshness: 15
- capital exposure: 15
- risk controls: 20
- paper evidence: 15
- execution quality: 10
- audit integrity: 10

Research-quality evidence is tracked separately from operational readiness. Evidence Reward can show whether timestamped prediction contracts are outperforming baselines, but those scores do not arm live trading, clear blockers, or bypass readiness requirements.

Hard caps:
- no linked broker: 45
- broker disconnected: 50
- stale data: 55
- no paper evidence: 60
- active kill switch: 35
- daily loss limit breached: 40
- max drawdown breached: 45
- audit logging unavailable: 50
- live trading disabled: 65
- options quote or liquidity failure: 60 for options strategies

Live arm/start requires a current readiness snapshot at or above `READINESS_MIN_LIVE_SCORE`, default `85`, no unresolved critical blocker, an active risk policy, signed authorization, fresh data, audit replay, no kill switch, and broker/provider gates.

Evidence Reward readiness note:
- rewardable prediction contracts require direction, horizon, target, invalidation, confidence, actual forward return, and baseline forward return;
- incomplete prediction evidence is useful for research hygiene but does not count toward reward averages;
- simulation evidence is excluded from live-observed reward metrics;
- reward analytics are manual-review output only.

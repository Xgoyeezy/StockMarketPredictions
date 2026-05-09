import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import LoadingBlock from '../components/LoadingBlock'
import MetricCard from '../components/MetricCard'
import SectionCard from '../components/SectionCard'
import StrategyCommandHeader from '../components/strategy/StrategyCommandHeader'
import StrategyLifecycleTimeline from '../components/strategy/StrategyLifecycleTimeline'
import StrategyRunHistory from '../components/strategy/StrategyRunHistory'
import StrategyVersionPanel from '../components/strategy/StrategyVersionPanel'
import ReadinessScorePanel from '../components/strategy/ReadinessScorePanel'
import PromotionGatePanel from '../components/strategy/PromotionGatePanel'
import {
  createStrategyVersion,
  evaluateStrategyReadiness,
  getStrategy,
  getStrategyAudit,
  getStrategyMetrics,
  getStrategyReadiness,
  getStrategyRuns,
  getStrategyVersions,
  promoteStrategy,
  rollbackStrategy,
  startStrategy,
  stopStrategy,
} from '../api/client'

export default function StrategyDetailPage() {
  const { strategyId } = useParams()
  const navigate = useNavigate()
  const [strategy, setStrategy] = useState(null)
  const [readiness, setReadiness] = useState(null)
  const [versions, setVersions] = useState([])
  const [runs, setRuns] = useState([])
  const [metrics, setMetrics] = useState(null)
  const [gate, setGate] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    if (!strategyId) return
    setLoading(true)
    setError('')
    try {
      const [detail, score, versionData, runData, metricData] = await Promise.all([
        getStrategy(strategyId),
        getStrategyReadiness(strategyId),
        getStrategyVersions(strategyId),
        getStrategyRuns(strategyId),
        getStrategyMetrics(strategyId),
      ])
      setStrategy(detail.strategy)
      setReadiness(score)
      setVersions(versionData.items || [])
      setRuns(runData.items || [])
      setMetrics(metricData)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load strategy detail.')
    } finally {
      setLoading(false)
    }
  }, [strategyId])

  useEffect(() => {
    load()
  }, [load])

  async function runAction(action) {
    setBusy(true)
    setError('')
    try {
      const result = await action()
      if (result?.gate) setGate(result.gate)
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Action failed.')
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <div className="ui-shell__page"><LoadingBlock label="Loading strategy detail..." /></div>

  return (
    <div className="ui-shell__page">
      {error ? <ErrorState description={error} onAction={load} /> : null}
      <div className="ui-action-row">
        <Button onClick={() => navigate('/strategies')}>Back to Strategies</Button>
        <Button onClick={() => navigate(`/audit?strategy=${encodeURIComponent(strategyId)}`)}>Open Audit</Button>
        <Button onClick={() => navigate(`/execution-quality?strategy=${encodeURIComponent(strategyId)}`)}>Execution Quality</Button>
        <Button onClick={() => navigate(`/strategies/${encodeURIComponent(strategyId)}/live`)}>Live Control</Button>
      </div>
      <StrategyCommandHeader
        strategy={strategy}
        readiness={readiness}
        busy={busy}
        onStart={() => runAction(() => startStrategy(strategyId, { deployment_mode: 'paper' }))}
        onStop={() => runAction(() => stopStrategy(strategyId, {}))}
        onPromote={() => runAction(() => promoteStrategy(strategyId, { from_stage: strategy?.lifecycle_stage || 'draft', to_stage: 'validated' }))}
        onRollback={() => runAction(() => rollbackStrategy(strategyId, {}))}
      />
      <div className="ui-dashboard-grid">
        <MetricCard label="Runs" value={metrics?.run_count || 0} />
        <MetricCard label="Decisions" value={metrics?.decision_count || 0} />
        <MetricCard label="Readiness" value={`${readiness?.score ?? 0}%`} />
      </div>
      <SectionCard title="Readiness score" subtitle="Weighted readiness plus hard-blocker caps and next actions.">
        <ReadinessScorePanel
          snapshot={readiness}
          onEvaluate={() => runAction(() => evaluateStrategyReadiness(strategyId, { force_refresh: true }))}
        />
      </SectionCard>
      <SectionCard title="Lifecycle" subtitle="Promotion state stays evidence-gated and paper-first.">
        <StrategyLifecycleTimeline
          stage={strategy?.lifecycle_stage}
          currentVersion={strategy?.current_version}
          promotionHistory={gate ? [gate] : []}
        />
      </SectionCard>
      <SectionCard title="Promotion gate" subtitle="Promotion creates durable gate evidence and does not bypass live safety gates.">
        <PromotionGatePanel
          gate={gate || readiness?.promotion}
          busy={busy}
          onPromote={() => runAction(() => promoteStrategy(strategyId, { from_stage: strategy?.lifecycle_stage || 'draft', to_stage: 'validated' }))}
        />
      </SectionCard>
      <SectionCard title="Versions" subtitle="Version records lock strategy config and risk profile history.">
        <StrategyVersionPanel
          versions={versions}
          activeVersionId={strategy?.current_version_id}
          onCreateVersion={() => runAction(() => createStrategyVersion(strategyId, { name: `${strategy?.name || 'Strategy'} revision` }))}
          onRollback={(version) => runAction(() => rollbackStrategy(strategyId, { version_id: version.id }))}
        />
      </SectionCard>
      <SectionCard title="Run history" subtitle="Paper runs and decision evidence appear here as sessions complete.">
        <StrategyRunHistory
          runs={runs}
          onOpenReplay={() => getStrategyAudit(strategyId).then(() => navigate(`/audit?strategy=${encodeURIComponent(strategyId)}`))}
        />
      </SectionCard>
    </div>
  )
}

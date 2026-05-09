import Button from '../Button'
import EmptyState from '../EmptyState'
import LoadingBlock from '../LoadingBlock'
import MetricCard from '../MetricCard'
import StatusBadge from '../StatusBadge'

function friendlyLabel(value) {
  return String(value || '').replaceAll('_', ' ')
}

export default function ReadinessScorePanel({
  snapshot = null,
  loading = false,
  onEvaluate,
}) {
  if (loading) return <LoadingBlock label="Evaluating readiness..." compact />
  if (!snapshot) {
    return (
      <EmptyState
        title="No readiness snapshot yet."
        description="Run evaluation to score linked account, data freshness, risk gates, paper evidence, execution, and audit readiness."
        actionLabel="Evaluate"
        onAction={onEvaluate}
        compact
      />
    )
  }

  const blockers = snapshot.blockers || []
  const warnings = snapshot.warnings || []
  const components = snapshot.components || {}

  return (
    <div className="ui-stack">
      <div className="ui-dashboard-grid">
        <MetricCard label="Score" value={`${snapshot.score ?? 0}%`} tone={(snapshot.score || 0) >= 75 ? 'positive' : 'warning'} helper={snapshot.status} />
        <MetricCard label="Blockers" value={blockers.length} helper={snapshot.recommendation || 'Compact explanation stays here until action is needed'} />
        <MetricCard label="Warnings" value={warnings.length} helper={snapshot.evaluated_at || 'Not evaluated'} />
      </div>
      <div className="ui-note">Readiness explains why a strategy can stay in paper, move to validation, or request live control without exposing background diagnostics.</div>
      <div className="ui-action-row">
        <StatusBadge value={snapshot.status} />
        <Button onClick={onEvaluate}>Refresh Evaluation</Button>
      </div>
      <div className="ui-dashboard-grid">
        {Object.entries(components).map(([key, value]) => (
          <MetricCard key={key} label={friendlyLabel(key)} value={`${Math.round(Number(value || 0))}%`} />
        ))}
      </div>
      {blockers.length ? (
        <div className="ui-list-shell">
          {blockers.map((item, index) => (
            <div key={`${item.key || item.blocker_key}-${index}`} className="ui-list-row">
              <span>{item.message || item.key || item.blocker_key}</span>
              <StatusBadge value={item.severity || 'blocker'} tone={item.severity === 'critical' ? 'negative' : 'warning'} />
            </div>
          ))}
        </div>
      ) : null}
      {warnings.length ? (
        <div className="ui-note">{warnings.map((item) => item.message || item.key || String(item)).join(' | ')}</div>
      ) : null}
    </div>
  )
}

import StatusBadge from '../StatusBadge'

const stages = ['draft', 'paper', 'validated', 'live_candidate', 'scaled_live']

function stageLabel(value) {
  return String(value || '').replaceAll('_', ' ')
}

export default function StrategyLifecycleTimeline({
  stage = 'draft',
  promotionHistory = [],
  currentVersion = null,
}) {
  const currentIndex = Math.max(0, stages.indexOf(String(stage || 'draft')))

  return (
    <div className="ui-stack">
      <div className="ui-step-row">
        {stages.map((item, index) => (
          <div key={item} className="ui-panel ui-panel--metric">
            <div className="ui-kicker">Stage {index + 1}</div>
            <div className="ui-value">{stageLabel(item)}</div>
            <StatusBadge value={index < currentIndex ? 'complete' : index === currentIndex ? 'current' : 'locked'} />
          </div>
        ))}
      </div>
      {currentVersion ? (
        <div className="ui-note">Current version: v{currentVersion.version_number} {currentVersion.name}</div>
      ) : null}
      {promotionHistory.length ? (
        <div className="ui-list-shell">
          {promotionHistory.map((item) => (
            <div key={item.id || `${item.from_stage}-${item.to_stage}-${item.created_at}`} className="ui-list-row">
              <span>{stageLabel(item.from_stage)} to {stageLabel(item.to_stage)}</span>
              <StatusBadge value={item.status} />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

import Button from '../Button'
import EmptyState from '../EmptyState'
import StatusBadge from '../StatusBadge'

export default function PromotionGatePanel({
  gate = null,
  busy = false,
  onPromote,
}) {
  if (!gate) {
    return (
      <EmptyState
        title="No promotion gate has been evaluated."
        description="Run promotion evaluation to record score, blockers, and the next safe stage requirements."
        actionLabel="Evaluate Promotion"
        onAction={onPromote}
        compact
      />
    )
  }

  const blockers = gate.blockers || []
  const requirements = gate.requirements || {}

  return (
    <div className="ui-stack">
      <div className="ui-action-row">
        <StatusBadge value={gate.status || 'pending'} />
        <span className="ui-note">Required {gate.required_score ?? requirements.required_score ?? 0} / actual {gate.actual_score ?? 0}</span>
        <Button disabled={busy} onClick={onPromote}>Re-evaluate</Button>
      </div>
      {blockers.length ? (
        <>
          <div className="ui-note">Promotion blocked until these items clear.</div>
        <div className="ui-list-shell">
          {blockers.map((item, index) => (
            <div key={`${item.key || item.message}-${index}`} className="ui-list-row">
              <span>{item.message || item.key}</span>
              <StatusBadge value={item.severity || 'blocker'} />
            </div>
          ))}
        </div>
        </>
      ) : (
        <div className="ui-note">No promotion blockers recorded for this gate.</div>
      )}
    </div>
  )
}

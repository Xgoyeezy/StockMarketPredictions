import Button from '../Button'
import EmptyState from '../EmptyState'
import ListTable, { ListCell } from '../ListTable'
import StatusBadge from '../StatusBadge'

export default function DecisionReplayPanel({
  trade = null,
  replay = [],
  onExport,
}) {
  if (!trade && !replay.length) {
    return (
      <EmptyState
        title="No trade decision selected."
        description="Select a decision or enter a trade id to inspect who authorized it, which gates passed, and what evidence was stored."
        eyebrow="Replay evidence"
        compact
      />
    )
  }

  return (
    <div className="ui-stack">
      <div className="ui-action-row">
        <StatusBadge value={trade?.decision_status || 'recorded'} />
        <span className="ui-note">Evidence stored: {trade?.symbol || 'Trade'} {trade?.side || ''} {trade?.quantity || ''}</span>
        <Button onClick={onExport}>Export Bundle</Button>
      </div>
      <ListTable>
        {replay.length ? (
          replay.map((item) => (
            <div key={item.id || item.sequence_number} className="ui-list-row">
              <ListCell
                kicker={`Step ${item.sequence_number}`}
                title={item.event_type}
                meta={item.event_time}
              />
            </div>
          ))
        ) : (
          <EmptyState title="No replay timeline stored for this decision." description="Decision metadata is present, but ordered replay events have not been written yet." eyebrow="Replay timeline" compact />
        )}
      </ListTable>
    </div>
  )
}

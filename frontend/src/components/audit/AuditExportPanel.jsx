import Button from '../Button'
import EmptyState from '../EmptyState'
import ListTable, { ListCell } from '../ListTable'
import StatusBadge from '../StatusBadge'

export default function AuditExportPanel({
  filters = {},
  jobs = [],
  busy = false,
  onExport,
}) {
  return (
    <div className="ui-stack">
      <div className="ui-action-row">
        <Button variant="solid" disabled={busy} onClick={() => onExport?.(filters)}>Queue Export</Button>
        <span className="ui-note">Export type: {filters.export_type || 'audit_bundle'} - disabled until the selected evidence can be packaged.</span>
      </div>
      {jobs.length ? (
        <ListTable>
          {jobs.map((job) => (
            <div key={job.id} className="ui-list-row">
              <ListCell kicker={job.export_type} title={job.id} meta={job.created_at} badges={[<StatusBadge key="status" value={job.status} />]} />
            </div>
          ))}
        </ListTable>
      ) : (
        <EmptyState title="No export jobs queued." description="Exports are queued as evidence bundles, preserve trading state, and create a reviewable audit packet." eyebrow="Audit exports" compact />
      )}
    </div>
  )
}

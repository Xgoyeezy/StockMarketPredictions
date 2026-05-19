import ListTable from './ListTable'
import MetricCard from './MetricCard'
import SectionCard from './SectionCard'
import StatusBadge from './StatusBadge'

function humanize(value, fallback = 'Unknown') {
  const text = String(value || '').trim()
  if (!text) return fallback
  return text.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase())
}

function countBy(items, key) {
  return items.reduce((counts, item) => {
    const value = item?.[key] || 'unknown'
    counts[value] = (counts[value] || 0) + 1
    return counts
  }, {})
}

function trackerItems(tracker) {
  return Array.isArray(tracker?.items) ? tracker.items : []
}

function trackerSourceDocs(tracker) {
  return Array.isArray(tracker?.source_docs) ? tracker.source_docs.filter(Boolean) : []
}

function trackerSummary(tracker, items) {
  const summary = tracker?.summary || {}
  const priorityCounts = summary.priority_counts || countBy(items, 'priority')
  const statusCounts = summary.status_counts || countBy(items, 'status')
  return {
    totalItems: summary.total_items ?? items.length,
    criticalOpenItems: summary.critical_open_items ?? items.filter((item) => item.priority === 'critical' && item.status !== 'done').length,
    criticalCount: priorityCounts.critical || 0,
    highCount: priorityCounts.high || 0,
    futureCount: priorityCounts.future || 0,
    inProgressCount: statusCounts.in_progress || 0,
    blockedCount: statusCounts.blocked_by_evidence || 0,
    deferredCount: statusCounts.deferred || 0,
    safeBoundary: summary.safe_boundary || 'Tracker items do not authorize live trading.',
    proofFirstRule: summary.proof_first_rule || '',
  }
}

function toneForStatus(status) {
  if (status === 'done' || status === 'ready') return 'positive'
  if (status === 'not_started' || status === 'deferred' || status === 'future_only') return 'neutral'
  if (status === 'blocked_by_evidence') return 'warning'
  return 'warning'
}

export default function FinishTrackerSection({ tracker, loading = false }) {
  const items = trackerItems(tracker)
  const sourceDocs = trackerSourceDocs(tracker)
  const summary = trackerSummary(tracker, items)

  return (
    <SectionCard
      title="Project Finish Tracker"
      subtitle="Project-wide remaining work across the proof-first foundation and deferred expansion backlog."
      eyebrow="Report footer"
    >
      <div className="ui-dashboard-grid">
        <MetricCard label="Open items" value={summary.totalItems} helper="All tracked project areas" />
        <MetricCard label="Critical open" value={summary.criticalOpenItems} helper={`${summary.criticalCount} critical items tracked`} tone={summary.criticalOpenItems ? 'warning' : 'default'} />
        <MetricCard label="High priority" value={summary.highCount} helper={`${summary.deferredCount || summary.futureCount} deferred expansion items`} />
        <MetricCard label="In progress" value={summary.inProgressCount} helper={`${summary.blockedCount} blocked by evidence`} />
      </div>

      <div className="ui-action-row">
        <StatusBadge tone="neutral">{tracker?.scope || 'project_wide'}</StatusBadge>
        <StatusBadge tone="neutral">{tracker?.version || 'project_finish_tracker_v2'}</StatusBadge>
        <StatusBadge tone="warning">No live-trading authorization</StatusBadge>
      </div>

      <ListTable>
        <table className="ui-list-table">
          <thead>
            <tr>
              <th>Area</th>
              <th>Item</th>
              <th>Status</th>
              <th>Priority</th>
              <th>Next safe action</th>
              <th>Remaining work</th>
              <th>Done when</th>
            </tr>
          </thead>
          <tbody>
            {items.length ? items.map((item) => (
              <tr key={item.id || item.title}>
                <td>{humanize(item.area)}</td>
                <td>{item.title || humanize(item.id)}</td>
                <td><StatusBadge tone={toneForStatus(item.status)}>{humanize(item.status)}</StatusBadge></td>
                <td><StatusBadge tone={item.priority === 'critical' ? 'warning' : 'neutral'}>{humanize(item.priority)}</StatusBadge></td>
                <td>{item.next_safe_action || (Array.isArray(item.remaining_work) ? item.remaining_work[0] : item.remaining_work) || '--'}</td>
                <td>{Array.isArray(item.remaining_work) ? item.remaining_work.slice(0, 2).join(' ') : item.remaining_work || '--'}</td>
                <td>{item.done_when || '--'}</td>
              </tr>
            )) : (
              <tr>
                <td colSpan="7">{loading ? 'Loading project finish tracker...' : 'Project finish tracker is unavailable for this report.'}</td>
              </tr>
            )}
          </tbody>
        </table>
      </ListTable>

      {sourceDocs.length ? (
        <div className="ui-action-row" aria-label="Proof source docs">
          <span className="ui-note">Proof source docs</span>
          {sourceDocs.slice(0, 6).map((doc) => (
            <StatusBadge key={doc} tone="neutral">{doc}</StatusBadge>
          ))}
        </div>
      ) : null}

      <p className="ui-note">{summary.proofFirstRule ? `${summary.proofFirstRule} ` : ''}{summary.safeBoundary}</p>
    </SectionCard>
  )
}

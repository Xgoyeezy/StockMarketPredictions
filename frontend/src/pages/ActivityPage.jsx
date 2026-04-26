import { useCallback, useEffect, useMemo, useState } from 'react'
import { getFrontendActivity, getFrontendFilters } from '../api/client'
import Button from '../components/Button'
import Chip from '../components/Chip'
import DataToolbar from '../components/DataToolbar'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import { SelectField, ToggleField } from '../components/FormFields'
import Kicker from '../components/Kicker'
import LoadingBlock from '../components/LoadingBlock'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import usePolling from '../hooks/usePolling'
import { usePreferences } from '../context/PreferencesContext'

export default function ActivityPage() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [payload, setPayload] = useState(null)
  const [search, setSearch] = useState('')
  const [severity, setSeverity] = useState('all')
  const [type, setType] = useState('all')
  const [filters, setFilters] = useState({ activity_types: ['all', 'alert', 'workspace', 'portfolio'], alert_severities: ['all', 'critical', 'high', 'medium', 'low'] })
  const [autoRefresh, setAutoRefresh] = useState(false)
  const { preferences } = usePreferences()

  const load = useCallback(async () => {
    try {
      setError('')
      const [activityPayload, filterPayload] = await Promise.all([
        getFrontendActivity({ search, severity, type, limit: 20 }),
        getFrontendFilters(),
      ])
      setPayload(activityPayload)
      setFilters(filterPayload)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load activity feed.')
    } finally {
      setLoading(false)
    }
  }, [search, severity, type])

  useEffect(() => { load() }, [load])
  usePolling(load, preferences?.pollingMs || 15000, autoRefresh)

  const metrics = useMemo(() => [
    { label: 'Feed Items', value: payload?.count ?? 0 },
    { label: 'Alerts', value: payload?.alert_count ?? 0, tone: Number(payload?.alert_count || 0) > 0 ? 'positive' : 'default' },
    { label: 'Saved Workspaces', value: payload?.workspace_count ?? 0 },
    { label: 'Updated', value: new Date().toLocaleTimeString() },
  ], [payload])

  function severityTone(value) {
    const normalized = String(value || '').trim().toLowerCase()
    if (normalized === 'critical' || normalized === 'high') return 'negative'
    if (normalized === 'medium') return 'warning'
    return 'neutral'
  }

  if (loading) {
    return (
      <LoadingBlock
        label="Loading activity feed"
        detail="Pulling the shared operator tape so alerts, workspace changes, and portfolio activity open in one timeline."
      />
    )
  }

  return (
    <>
      {error ? (
        <ErrorState
          title="Activity feed unavailable"
          description={error}
          actionLabel="Reload activity"
          onAction={load}
        />
      ) : null}
      <PageIntro
        kicker="Activity feed"
        title="Read the combined operational tape"
        description="Track alerts, portfolio state, and saved-workspace updates from one shared operator feed."
        badge={`${payload?.count ?? 0} feed items`}
        actions={(
          <Button type="button" variant="subtle" onClick={load}>
            Refresh feed
          </Button>
        )}
      />
      <section className="metrics-grid">{metrics.map((item) => <MetricCard key={item.label} {...item} />)}</section>
      <SectionCard title="Activity feed" subtitle="Combined operational feed across alerts, portfolio state, and saved workspace updates." actions={(
        <DataToolbar
          searchValue={search}
          onSearchChange={setSearch}
          searchPlaceholder="Search activity"
          actions={(
            <>
              <SelectField ariaLabel="Filter activity by severity" value={severity} onChange={(e) => setSeverity(e.target.value)}>{(filters.alert_severities || ['all']).map((option) => <option key={option} value={option}>{option}</option>)}</SelectField>
              <SelectField ariaLabel="Filter activity by type" value={type} onChange={(e) => setType(e.target.value)}>{(filters.activity_types || ['all']).map((option) => <option key={option} value={option}>{option}</option>)}</SelectField>
              <ToggleField label="Auto refresh" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
              <Button type="button" variant="ghost" size="sm" onClick={load}>Refresh</Button>
            </>
          )}
        />
      )}>
        <div className="activity-feed">
          {(payload?.items || []).map((item, index) => (
            <article className={`activity-item activity-item--${String(item.severity || 'low').toLowerCase()}`} key={`${item.type}-${index}`}>
              <div className="alert-card__head">
                <div>
                  <Kicker as="div">{item.type}</Kicker>
                  <h3>{item.title}</h3>
                </div>
                <Chip
                  tone={severityTone(item.severity)}
                  size="sm"
                  className={`alert-chip alert-chip--${String(item.severity || 'low').toLowerCase()}`}
                >
                  {item.severity}
                </Chip>
              </div>
              <p>{item.detail}</p>
            </article>
          ))}
          {!(payload?.items || []).length ? <EmptyState title="No activity yet" description="Nothing matched the current feed filters. Try widening the search or refreshing the tape." /> : null}
        </div>
      </SectionCard>
    </>
  )
}

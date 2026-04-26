import { useEffect, useMemo, useState } from 'react'
import {
  getDeskSummaries,
  getFrontendAlerts,
  getNotesSummary,
  getOpsStatus,
  getReleaseInfo,
  getSavedWorkspaces,
  getTickerHub,
} from '../api/client'
import { useAuth } from '../context/useAuth'
import useApiHeartbeat from '../hooks/useApiHeartbeat'
import { usePreferences } from '../context/PreferencesContext'
import { appConfig } from '../config/appConfig'
import Button from './Button'
import Chip from './Chip'
import { SelectField } from './FormFields'
import Kicker from './Kicker'

function statusLabel(status) {
  if (status === 'connected') return 'API connected'
  if (status === 'checking') return 'Checking API'
  return 'API degraded'
}

function accountStatusLabel(status) {
  const normalized = String(status || '').trim().toLowerCase()
  if (normalized === 'connected') return 'Connected'
  if (normalized === 'attention') return 'Attention'
  if (normalized === 'disconnected') return 'Disconnected'
  return 'Not linked'
}

function formatDeskActivityLabel(value) {
  const timestamp = Date.parse(String(value || ''))
  if (!Number.isFinite(timestamp)) return 'No recent activity'
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(timestamp))
}

function accountProfileLabel(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'personal_live') return 'Personal live'
  if (normalized === 'brokerage') return 'Brokerage-linked'
  return 'Personal paper'
}

export default function Topbar({ title = 'Workspace' }) {
  const { session, busy, signOut, switchOrganization } = useAuth()
  const { preferences } = usePreferences()
  const { health, status, lastCheckedAt, refresh } = useApiHeartbeat(15000)
  const [release, setRelease] = useState(null)
  const [alertSummary, setAlertSummary] = useState(null)
  const [workspaceSummary, setWorkspaceSummary] = useState(null)
  const [tickerHub, setTickerHub] = useState(null)
  const [noteSummary, setNoteSummary] = useState(null)
  const [opsStatus, setOpsStatus] = useState(null)
  const [deskSummaries, setDeskSummaries] = useState(null)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const personalMode = appConfig.personalMode
  const brandSettings = session?.active_tenant?.brand_settings || {}
  const brandName = personalMode ? appConfig.appName : brandSettings.app_name || session?.active_tenant?.name || 'No organization'
  const brandTagline = personalMode
    ? appConfig.appTagline
    : brandSettings.app_tagline || 'Focus on the current market view first. Deeper status is available on demand.'
  const brandLogoUrl = personalMode ? '' : session?.active_tenant?.logo_url || ''
  const brandInitials = brandName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() || '')
    .join('') || (personalMode ? 'PT' : 'TD')

  useEffect(() => {
    Promise.allSettled([
      getReleaseInfo(),
      getDeskSummaries(),
      getFrontendAlerts({ limit: 6, minSeverity: 'high' }),
      getSavedWorkspaces(),
      getTickerHub(6),
      getNotesSummary(),
      getOpsStatus(),
    ]).then((results) => {
      const [releaseResult, deskSummariesResult, alertsResult, workspacesResult, tickerHubResult, notesResult, opsResult] =
        results

      if (releaseResult.status === 'fulfilled') setRelease(releaseResult.value)
      if (deskSummariesResult.status === 'fulfilled') setDeskSummaries(deskSummariesResult.value)
      if (alertsResult.status === 'fulfilled') setAlertSummary(alertsResult.value)
      if (workspacesResult.status === 'fulfilled') setWorkspaceSummary(workspacesResult.value)
      if (tickerHubResult.status === 'fulfilled') setTickerHub(tickerHubResult.value)
      if (notesResult.status === 'fulfilled') setNoteSummary(notesResult.value)
      if (opsResult.status === 'fulfilled') setOpsStatus(opsResult.value)
    })
  }, [session?.active_tenant?.slug])

  const primaryChips = useMemo(() => {
    const sharedChips = [
      { label: session?.environment || 'development', tone: 'neutral' },
      { label: session?.mode || 'demo', tone: 'neutral' },
      {
        label: statusLabel(status),
        tone: status === 'connected' ? 'positive' : status === 'degraded' ? 'negative' : 'neutral',
      },
      { label: `Alerts ${alertSummary?.total ?? alertSummary?.count ?? 0}`, tone: 'neutral' },
      { label: `Trades ${opsStatus?.counts?.open_trades ?? 0}`, tone: 'neutral' },
      {
        label: `${preferences?.defaultTicker || 'SPY'} | ${preferences?.defaultInterval || '5m'}`,
        tone: 'neutral',
      },
    ]

    if (personalMode) {
      return [
        { label: 'Own account only', tone: 'positive' },
        { label: accountProfileLabel(preferences?.activeAccountProfile), tone: preferences?.activeAccountProfile === 'personal_live' ? 'negative' : 'warning' },
        { label: 'Self-directed research', tone: 'neutral' },
        { label: 'No client advisory service', tone: 'neutral' },
        ...sharedChips,
      ]
    }

    return [
      { label: session?.active_tenant?.name || 'No organization', tone: 'neutral' },
      {
        label: `Organization ${(session?.active_tenant?.status || 'active').toUpperCase()}`,
        tone: session?.active_tenant?.status === 'paused' ? 'negative' : 'neutral',
      },
      { label: `Plan ${(session?.active_tenant?.plan_key || 'starter').toUpperCase()}`, tone: 'neutral' },
      ...sharedChips,
    ]
  }, [personalMode, session, status, alertSummary, opsStatus, preferences])

  const detailItems = personalMode
    ? [
        { label: 'Use', value: 'Personal funds and paper rehearsal only' },
        { label: 'Research status', value: 'Decision support, not client advice' },
        { label: 'Selected profile', value: accountProfileLabel(preferences?.activeAccountProfile) },
        { label: 'Release', value: `${release?.phase || 'preview'}${release?.version ? ` | ${release.version}` : ''}` },
        { label: 'Service', value: `${health?.version || 'service'}${lastCheckedAt ? ` | ${lastCheckedAt}` : ''}` },
        { label: 'User', value: session?.user?.name || 'Trader' },
        { label: 'Favorites', value: tickerHub?.favorite_count ?? 0 },
        { label: 'Notes', value: noteSummary?.active_count ?? 0 },
        { label: 'High priority', value: noteSummary?.high_priority_count ?? 0 },
        { label: 'Overdue', value: noteSummary?.overdue_count ?? 0 },
        { label: 'Blocked', value: noteSummary?.blocked_count ?? 0 },
        { label: 'In progress', value: noteSummary?.in_progress_count ?? 0 },
      ]
    : [
        { label: 'Organization', value: session?.active_tenant?.name || 'No organization selected' },
        { label: 'Organization role', value: session?.active_tenant?.role || session?.user?.role || 'member' },
        { label: 'Release', value: `${release?.phase || 'preview'}${release?.version ? ` | ${release.version}` : ''}` },
        { label: 'Service', value: `${health?.version || 'service'}${lastCheckedAt ? ` | ${lastCheckedAt}` : ''}` },
        { label: 'User', value: session?.user?.name || 'Trader' },
        { label: 'Workspaces', value: workspaceSummary?.count ?? 0 },
        { label: 'Favorites', value: tickerHub?.favorite_count ?? 0 },
        { label: 'Notes', value: noteSummary?.active_count ?? 0 },
        { label: 'High priority', value: noteSummary?.high_priority_count ?? 0 },
        { label: 'Overdue', value: noteSummary?.overdue_count ?? 0 },
        { label: 'Blocked', value: noteSummary?.blocked_count ?? 0 },
        { label: 'In progress', value: noteSummary?.in_progress_count ?? 0 },
      ]

  const memberships = session?.memberships || []
  const deskSummaryItems = personalMode
    ? []
    : (deskSummaries?.items || []).filter((item) => memberships.some((membership) => membership?.tenant?.slug === item?.tenant_slug))

  async function handleOrganizationSwitch(event) {
    const nextOrganizationSlug = event.target.value
    if (!nextOrganizationSlug || nextOrganizationSlug === session?.active_tenant?.slug) return
    try {
      await switchOrganization(nextOrganizationSlug)
    } catch (error) {
      console.error('Failed to switch organization.', error)
    }
  }

  return (
    <header className="topbar topbar--clean">
      <div className="topbar__intro">
        <div className="topbar__brand">
          <div className="topbar-brand-mark" aria-hidden="true">
            {brandLogoUrl ? <img src={brandLogoUrl} alt="" /> : <span>{brandInitials}</span>}
          </div>
          <div>
            <Kicker as="div">{brandName}</Kicker>
            <h2>{title}</h2>
            <p className="topbar__subtitle">
              {brandTagline}
            </p>
          </div>
        </div>
        <div className="topbar__actions">
          {!personalMode && memberships.length > 1 ? (
            <SelectField ariaLabel="Switch active organization" className="topbar__tenant-switch" inputClassName="topbar-select" value={session?.active_tenant?.slug || ''} onChange={handleOrganizationSwitch} disabled={busy}>
              {memberships.map((membership) => (
                <option key={membership.membership_id || membership.tenant?.slug} value={membership.tenant?.slug || ''}>
                  {membership.tenant?.name || membership.tenant?.slug || 'Organization'}
                </option>
              ))}
            </SelectField>
          ) : null}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setDetailsOpen((value) => !value)}
            disabled={busy}
            aria-expanded={detailsOpen}
            aria-controls="topbar-details-panel"
          >
            {detailsOpen ? 'Hide details' : 'Show details'}
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={refresh} disabled={busy}>
            Refresh status
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={signOut} disabled={busy}>
            {busy ? 'Working...' : 'Sign out'}
          </Button>
        </div>
      </div>

      <div className="topbar__meta topbar__meta--primary">
        {primaryChips.map((chip) => (
          <Chip key={chip.label} tone={chip.tone || 'neutral'} size="sm">
            {chip.label}
          </Chip>
        ))}
      </div>

      {deskSummaryItems.length > 1 ? (
        <div className="desk-summary-strip" aria-label="All desks overview">
          {deskSummaryItems.map((item) => {
            const isActive = item?.tenant_slug === session?.active_tenant?.slug
            return (
              <button
                key={item?.tenant_slug || item?.tenant_name}
                type="button"
                className={`desk-summary-card${isActive ? ' desk-summary-card--active' : ''}`}
                onClick={() => {
                  if (!item?.tenant_slug || isActive || busy) return
                  void handleOrganizationSwitch({ target: { value: item.tenant_slug } })
                }}
                disabled={busy || !item?.tenant_slug || isActive}
                aria-pressed={isActive}
              >
                <div className="desk-summary-card__head">
                  <span>{item?.tenant_name || item?.tenant_slug || 'Desk'}</span>
                  <strong>{isActive ? 'Active' : 'Open'}</strong>
                </div>
                <div className="desk-summary-card__grid">
                  <div>
                    <span>Paper</span>
                    <strong>{accountStatusLabel(item?.paper_account_status)}</strong>
                  </div>
                  <div>
                    <span>Live</span>
                    <strong>{accountStatusLabel(item?.live_account_status)}</strong>
                  </div>
                  <div>
                    <span>Open</span>
                    <strong>{item?.open_trades ?? 0}</strong>
                  </div>
                  <div>
                    <span>Pending</span>
                    <strong>{item?.pending_orders ?? 0}</strong>
                  </div>
                  <div>
                    <span>Alerts</span>
                    <strong>{item?.alerts ?? 0}</strong>
                  </div>
                  <div>
                    <span>Activity</span>
                    <strong>{formatDeskActivityLabel(item?.last_activity_at)}</strong>
                  </div>
                </div>
              </button>
            )
          })}
        </div>
      ) : null}

      {detailsOpen ? (
        <div className="topbar-details-card" id="topbar-details-panel">
          <div className="topbar-details-grid">
            {detailItems.map((item) => (
              <div key={item.label} className="topbar-detail-item">
                <span>{item.label}</span>
                <strong>{item.value}</strong>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </header>
  )
}

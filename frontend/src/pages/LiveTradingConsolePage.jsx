import { useCallback, useEffect, useState } from 'react'
import ErrorState from '../components/ErrorState'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import LiveAuditTimelinePanel from '../components/live/LiveAuditTimelinePanel'
import LiveKillSwitchPanel from '../components/live/LiveKillSwitchPanel'
import LiveModeStrip from '../components/live/LiveModeStrip'
import LiveSessionStatusPanel from '../components/live/LiveSessionStatusPanel'
import StatusBadge from '../components/StatusBadge'
import {
  activateLiveKillSwitch,
  clearLiveKillSwitch,
  getOrganizationTradeAutomationAlpacaPaperReadiness,
  getOrganizationTradeAutomationDesks,
  getOrganizationTradeAutomationHftWatchdogLatest,
  getOrganizationTradeAutomationWatchdog,
  getLiveKillSwitch,
  getLiveRiskEvents,
  getLiveStatus,
} from '../api/client'

export default function LiveTradingConsolePage() {
  const [status, setStatus] = useState(null)
  const [killSwitch, setKillSwitch] = useState(null)
  const [events, setEvents] = useState([])
  const [watchdogState, setWatchdogState] = useState(null)
  const [desks, setDesks] = useState(null)
  const [hftWatchdog, setHftWatchdog] = useState(null)
  const [alpacaReadiness, setAlpacaReadiness] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [statusData, killData, eventData, watchdogData, deskData, hftData, alpacaData] = await Promise.all([
        getLiveStatus(),
        getLiveKillSwitch(),
        getLiveRiskEvents({ limit: 25 }),
        getOrganizationTradeAutomationWatchdog({ force: true }),
        getOrganizationTradeAutomationDesks(),
        getOrganizationTradeAutomationHftWatchdogLatest(),
        getOrganizationTradeAutomationAlpacaPaperReadiness(),
      ])
      setStatus(statusData)
      setKillSwitch(killData)
      setEvents(eventData.items || [])
      setWatchdogState(watchdogData)
      setDesks(deskData)
      setHftWatchdog(hftData)
      setAlpacaReadiness(alpacaData)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load live trading console.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function handleKillAll() {
    setBusy(true)
    try {
      await activateLiveKillSwitch({ scope: 'tenant', reason: 'Operator activated global live kill switch from console.' })
      await load()
    } finally {
      setBusy(false)
    }
  }

  async function handleClearKill() {
    setBusy(true)
    try {
      await clearLiveKillSwitch({ scope: 'tenant', reason: 'Operator cleared global live kill switch from console.' })
      await load()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Connected-account live controls"
        title="Live Trading Console"
        description="Armed sessions, live feature flags, connected-account readiness gates, risk events, and global kill-switch controls."
        badge={loading ? 'Loading' : `${status?.summary?.active_session_count || 0} active`}
      />
      <LiveModeStrip activeKey="supervised-automation" />
      {error ? <ErrorState description={error} onAction={load} /> : null}
      <SectionCard title="Market Watchdog" subtitle="Continuous proof that the app is alive, scanning, connected, and allowed to trade through Alpaca paper execution only.">
        <div className="ui-list-shell">
          <div className="ui-list-row">
            <span>Watchdog state</span>
            <StatusBadge value={watchdogState?.label || 'Needs attention'} tone={watchdogState?.tone || 'warning'} />
          </div>
          <div className="ui-list-row">
            <span>Paper only marker</span>
            <StatusBadge value={watchdogState?.paper_route_only === false ? 'route needs review' : 'Alpaca paper execution only'} tone={watchdogState?.paper_route_only === false ? 'warning' : 'info'} />
          </div>
          <div className="ui-list-row">
            <span>Current blocker</span>
            <span className="ui-muted">{watchdogState?.blocker || 'No blocking watchdog condition is reported.'}</span>
          </div>
          <div className="ui-list-row">
            <span>Next safe action</span>
            <span className="ui-muted">{watchdogState?.next_action || 'Run market-open readiness before unattended paper automation.'}</span>
          </div>
          {(watchdogState?.cards || watchdogState?.components || []).slice(0, 6).map((card) => (
            <div className="ui-list-row" key={`watchdog-card:${card.key}`}>
              <span>{card.label || card.key}</span>
              <StatusBadge value={card.status || 'watching'} tone={card.tone || (['blocked', 'killed'].includes(String(card.status || '').toLowerCase()) ? 'negative' : String(card.status || '').toLowerCase() === 'degraded' ? 'warning' : 'positive')} />
              <span className="ui-muted">{card.blocker || card.next_action || card.detail || 'Monitoring'}</span>
            </div>
          ))}
          <div className="ui-list-row">
            <span>Alpaca credentials</span>
            <StatusBadge
              value={alpacaReadiness?.credentials?.api_key_present && alpacaReadiness?.credentials?.secret_key_present ? 'present' : 'needs review'}
              tone={alpacaReadiness?.credentials?.api_key_present && alpacaReadiness?.credentials?.secret_key_present ? 'positive' : 'warning'}
            />
          </div>
          <div className="ui-list-row">
            <span>Local reconciliation</span>
            <span className="ui-muted">
              {alpacaReadiness?.reconciliation?.pending_count || 0} pending, {alpacaReadiness?.reconciliation?.open_count || 0} open, {alpacaReadiness?.reconciliation?.closed_count || 0} closed
            </span>
          </div>
        </div>
      </SectionCard>
      <SectionCard title="Desk command center" subtitle="Each desk reports cadence, blocker, next scan, and whether it is safe to trade under the global risk allocator.">
        <div className="ui-list-shell">
          {(desks?.items || []).map((desk) => (
            <div key={desk.desk_key} className="ui-list-row">
              <span>{desk.label || desk.desk_key}</span>
              <span className="ui-muted">{desk.cadence?.interval} / {desk.cadence?.cycle_interval_seconds}s</span>
              <StatusBadge value={desk.safe_to_trade ? 'safe' : desk.top_blocker || 'waiting'} tone={desk.safe_to_trade ? 'positive' : desk.top_blocker ? 'warning' : 'neutral'} />
              <span className="ui-muted">{desk.runtime?.due_now ? 'Due now' : desk.next_action}</span>
            </div>
          ))}
          {!(desks?.items || []).length ? (
            <div className="ui-list-row">
              <span>Desk lanes</span>
              <span className="ui-muted">No desk summaries are available yet.</span>
            </div>
          ) : null}
        </div>
      </SectionCard>
      <SectionCard title="HFT watchdog evidence" subtitle="Millisecond paper slices are supervised, locked by symbol set, and stopped by watchdog blockers.">
        <div className="ui-list-shell">
          <div className="ui-list-row">
            <span>Status</span>
            <StatusBadge value={hftWatchdog?.status || hftWatchdog?.latest?.status || 'not started'} tone={hftWatchdog?.available ? 'positive' : 'neutral'} />
          </div>
          <div className="ui-list-row">
            <span>Latest child run</span>
            <span className="ui-muted">{hftWatchdog?.metrics?.last_child_run_id || hftWatchdog?.latest_child_run_id || 'None yet'}</span>
          </div>
          <div className="ui-list-row">
            <span>Lock state</span>
            <span className="ui-muted">{hftWatchdog?.active_lock_count || 0} active, {hftWatchdog?.stale_lock_count || 0} stale</span>
          </div>
        </div>
      </SectionCard>
      <SectionCard title="Session state" subtitle="Live automation remains inactive until explicit flags, authorization, readiness, risk, and approval gates pass.">
        <LiveSessionStatusPanel status={status} />
      </SectionCard>
      <SectionCard title="Global kill switch" subtitle="This blocks live sessions and live order approval flow until cleared after review.">
        <LiveKillSwitchPanel state={killSwitch} busy={busy} onActivate={handleKillAll} onClear={handleClearKill} />
      </SectionCard>
      <SectionCard title="Live risk event trail" subtitle="Critical live blockers and kill-switch events are stored as evidence.">
        <LiveAuditTimelinePanel events={events} />
      </SectionCard>
    </div>
  )
}

import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { exportSupportDiagnostics, getOpsStatus, getReleaseInfo, getReleaseNotes } from '../api/client'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import FeedbackState from '../components/FeedbackState'
import LoadingBlock from '../components/LoadingBlock'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import StrategyDeskStatusPanel from '../components/StrategyDeskStatusPanel'
import { UX_TEST_COUNTS, UX_TEST_PATHS } from '../data/uxTestingPlaybook'

function formatReleaseStatusTone(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (['complete', 'healthy', 'ready', 'live', 'active', 'enabled'].includes(normalized)) return 'positive'
  if (['blocked', 'failed', 'error', 'disabled'].includes(normalized)) return 'negative'
  if (['pending', 'warning', 'monitoring', 'in progress'].includes(normalized)) return 'warning'
  return 'neutral'
}

export default function ReleasePage() {
  const navigate = useNavigate()
  const [release, setRelease] = useState(null)
  const [notes, setNotes] = useState(null)
  const [ops, setOps] = useState(null)
  const [error, setError] = useState('')
  const [diagnosticsBusy, setDiagnosticsBusy] = useState(false)
  const [diagnosticsStatus, setDiagnosticsStatus] = useState(null)

  async function loadReleaseSurface() {
    try {
      setError('')
      const [releaseInfo, releaseNotes, opsStatus] = await Promise.all([
        getReleaseInfo(),
        getReleaseNotes(),
        getOpsStatus(),
      ])
      setRelease(releaseInfo)
      setNotes(releaseNotes)
      setOps(opsStatus)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load release data.')
    }
  }

  useEffect(() => {
    loadReleaseSurface()
  }, [])

  if (!release && !notes && !ops && !error) {
    return (
      <LoadingBlock
        label="Loading release center"
        detail="Pulling release notes, operational readiness, and launch diagnostics so the handoff opens with current status."
      />
    )
  }

  const releaseGateBlockers = ops?.release_gates?.summary?.blockers || []
  const releaseGateWarnings = ops?.release_gates?.summary?.warnings || []
  const releaseGateChecks = ops?.release_gates?.gates || []
  const releaseGatesUnseeded =
    !releaseGateBlockers.length &&
    !releaseGateWarnings.length &&
    !releaseGateChecks.length
  const billingDrills = ops?.billing?.drills?.items || []
  const billingFailedEvents = ops?.billing?.failed_events || []
  const billingRehearsalUnseeded = !billingDrills.length && !billingFailedEvents.length

  const handleDiagnosticsExport = async () => {
    setDiagnosticsBusy(true)
    setDiagnosticsStatus(null)
    try {
      const { blob, filename } = await exportSupportDiagnostics()
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
      setDiagnosticsStatus({
        tone: 'positive',
        title: 'Diagnostics package exported',
        description: `Support diagnostics exported as ${filename}.`,
      })
    } catch (err) {
      setDiagnosticsStatus({
        tone: 'negative',
        title: 'Diagnostics export failed',
        description: err?.response?.data?.detail || err.message || 'Support diagnostics export failed.',
      })
    } finally {
      setDiagnosticsBusy(false)
    }
  }

  return (
    <>
      <PageIntro
        kicker="Release center"
        title="Final handoff summary"
        description={`Version ${release?.version || notes?.version || '--'} | ${release?.phase || notes?.phase || 'preview'} | ${release?.environment || notes?.environment || 'development'}`}
        badge={ops?.readiness?.summary?.status || release?.phase || 'preview'}
        actions={(
          <Button type="button" variant="subtle" onClick={handleDiagnosticsExport} disabled={diagnosticsBusy}>
            {diagnosticsBusy ? 'Exporting diagnostics...' : 'Export support diagnostics'}
          </Button>
        )}
      />
      {error ? (
        <ErrorState
          title={release || notes || ops ? 'Release center refresh needs attention' : 'Release center unavailable'}
          description={error}
          actionLabel={release || notes || ops ? 'Refresh release center' : 'Reload release center'}
          onAction={loadReleaseSurface}
          compact={Boolean(release || notes || ops)}
        />
      ) : null}
      {diagnosticsStatus ? (
        <FeedbackState
          tone={diagnosticsStatus.tone}
          eyebrow="Support diagnostics"
          title={diagnosticsStatus.title}
          description={diagnosticsStatus.description}
          compact
          role={diagnosticsStatus.tone === 'negative' ? 'alert' : 'status'}
          actions={
            diagnosticsStatus.tone === 'negative'
              ? [{ label: 'Try export again', onAction: handleDiagnosticsExport, variant: 'ghost' }]
              : []
          }
        />
      ) : null}

      <div className="metric-grid">
        <MetricCard label="API status" value={ops?.health?.status || '--'} helper={ops?.health?.service || 'Service'} />
        <MetricCard
          label="Launch readiness"
          value={ops?.readiness?.summary?.status || '--'}
          helper={`${ops?.readiness?.summary?.readiness_percent ?? 0}% ready`}
        />
        <MetricCard
          label="Enterprise readiness"
          value={ops?.enterprise_readiness?.summary?.status || '--'}
          helper={`${ops?.enterprise_readiness?.summary?.readiness_percent ?? 0}% ready`}
        />
        <MetricCard label="Open trades" value={ops?.counts?.open_trades ?? 0} helper="Live portfolio count" />
        <MetricCard label="Active notes" value={ops?.counts?.active_notes ?? 0} helper={`Overdue ${ops?.counts?.overdue_notes ?? 0}`} />
        <MetricCard label="Alerts" value={ops?.counts?.alerts ?? 0} helper={`Favorites ${ops?.counts?.favorite_tickers ?? 0}`} />
      </div>

      <SectionCard title="Enterprise readiness" subtitle="Consolidated gate across production, deployment, tenant launch, order lifecycle, and strategy validation.">
        <div className="metric-grid">
          <MetricCard
            label="Enterprise readiness"
            value={ops?.enterprise_readiness?.summary?.status || '--'}
            helper={`${ops?.enterprise_readiness?.summary?.readiness_percent ?? 0}% ready`}
            tone={formatReleaseStatusTone(ops?.enterprise_readiness?.summary?.status)}
          />
          <MetricCard
            label="Ready checks"
            value={`${ops?.enterprise_readiness?.summary?.ready_checks ?? 0}/${ops?.enterprise_readiness?.summary?.total_checks ?? 0}`}
            helper={`Warnings ${ops?.enterprise_readiness?.summary?.warning_checks ?? 0}`}
          />
          <MetricCard
            label="Blocked checks"
            value={ops?.enterprise_readiness?.summary?.blocked_checks ?? 0}
            helper="Hard blockers before broader rollout"
            tone={(ops?.enterprise_readiness?.summary?.blocked_checks ?? 0) > 0 ? 'negative' : 'positive'}
          />
          <MetricCard
            label="Validation tracker"
            value={ops?.enterprise_readiness?.validation_tracker?.status || '--'}
            helper={ops?.enterprise_readiness?.validation_tracker?.version || 'No version'}
            tone={formatReleaseStatusTone(ops?.enterprise_readiness?.validation_tracker?.status)}
          />
        </div>
        <p><strong>Next action:</strong> {ops?.enterprise_readiness?.summary?.next_action || 'No next action recorded.'}</p>
        <div className="grid-two">
          <div>
            <strong>Blockers</strong>
            <ul className="simple-list">
              {(ops?.enterprise_readiness?.summary?.blockers || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {!(ops?.enterprise_readiness?.summary?.blockers || []).length ? (
                <li>No enterprise blockers recorded.</li>
              ) : null}
            </ul>
          </div>
          <div>
            <strong>Checks</strong>
            <ul className="simple-list">
              {(ops?.enterprise_readiness?.checks || []).map((item) => (
                <li key={item.key}>
                  {item.label} | {item.status} | {item.detail}
                </li>
              ))}
            </ul>
          </div>
        </div>
      </SectionCard>

      <StrategyDeskStatusPanel
        eyebrow="Quant release lane"
        title="Strategy desk rollout readiness"
        subtitle="Treat desk runs, allocator exposure, and research backtests as a release input. Macro and stat-arb should stay paper-clean before they touch broader execution paths."
      />

      <div className="grid-two">
        <SectionCard title="Release highlights">
          <ul className="simple-list">
            {(release?.highlights || []).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </SectionCard>

        <SectionCard title="Launch checklist">
          <ul className="simple-list">
            {(notes?.next_steps || []).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </SectionCard>
      </div>

      <SectionCard
        title="UX acceptance walkthroughs"
        subtitle="Treat the core workstation flows as a release gate, not just a design aspiration."
        actions={(
          <Link className="education-test-card__action" to="/education">
            Open UX guide
          </Link>
        )}
      >
        <div className="metric-grid">
          <MetricCard
            label="Walkthroughs"
            value={UX_TEST_COUNTS.walkthroughs}
            helper="Critical user paths that should feel clear, safe, and recoverable."
          />
          <MetricCard
            label="Pass checks"
            value={UX_TEST_COUNTS.criteria}
            helper="Concrete criteria to validate before treating the workflow as stable."
          />
          <MetricCard
            label="Release posture"
            value="Required"
            helper="Run these before calling the workstation ready for broader use."
          />
        </div>
        <div className="ux-test-grid">
          {UX_TEST_PATHS.map((path) => (
            <article key={path.id} className="ux-test-card">
              <div className="ux-test-card__header">
                <span>Release gate</span>
                <strong>{path.title}</strong>
              </div>
              <p className="ux-test-card__goal">{path.goal}</p>
              <div className="ux-test-card__section">
                <strong>Must pass</strong>
                <ul className="ux-test-card__list">
                  {path.passCriteria.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
              <div className="ux-test-card__section">
                <strong>Failure signs</strong>
                <ul className="ux-test-card__list">
                  {path.watchFor.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
              <div className="ux-test-card__actions">
                <Link className="education-test-card__action" to={path.startRoute}>
                  {path.startLabel}
                </Link>
                <Link className="education-test-card__action" to="/notes">
                  Log finding in notes
                </Link>
                <Link className="education-test-card__action" to="/education">
                  View operator guide
                </Link>
              </div>
            </article>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="Personal readiness">
        <div className="metric-grid">
          <MetricCard
            label="Readiness status"
            value={ops?.phase_a?.summary?.status || '--'}
            helper={ops?.phase_a?.summary?.next_action || 'Personal readiness snapshot'}
          />
          <MetricCard
            label="Readiness checklist"
            value={`${ops?.phase_a?.summary?.tracker_completed ?? 0}/${ops?.phase_a?.summary?.tracker_total ?? 0}`}
            helper={`Warnings ${ops?.phase_a?.summary?.warning_checks ?? 0}`}
          />
          <MetricCard
            label="Blocked"
            value={ops?.phase_a?.summary?.blocked_checks ?? 0}
            helper={`Checks ${ops?.phase_a?.summary?.total_checks ?? 0}`}
          />
          <MetricCard
            label="Desk"
            value={ops?.phase_a?.tenant?.slug || '--'}
            helper="Own-account scope"
          />
        </div>

        <div className="grid-two">
          <div>
            <h3>Exit checklist</h3>
            <ul className="simple-list">
              {(ops?.phase_a?.checklist || []).map((item) => (
                <li key={item.key}>
                  {item.label} | {item.status} | {item.message}
                </li>
              ))}
              {!(ops?.phase_a?.checklist || []).length ? (
                <li>No personal readiness checklist items recorded.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Remaining tracker items</h3>
            <ul className="simple-list">
              {(ops?.phase_a?.remaining_items || []).map((item) => (
                <li key={`${item.index}-${item.text}`}>
                  {item.index}. {item.text} | {item.status}
                </li>
              ))}
              {!(ops?.phase_a?.remaining_items || []).length ? (
                <li>No personal readiness items are left open.</li>
              ) : null}
            </ul>
          </div>
        </div>

        <div className="grid-two">
          <div>
            <h3>Probe endpoints</h3>
            <ul className="simple-list">
              <li>Liveness | {ops?.phase_a?.probe_endpoints?.liveness || '/api/healthz'}</li>
              <li>Readiness | {ops?.phase_a?.probe_endpoints?.readiness || '/api/readyz'}</li>
              <li>Diagnostics export | {ops?.phase_a?.probe_endpoints?.diagnostics_export || '/api/ops/diagnostics'}</li>
            </ul>
          </div>

          <div>
            <h3>Personal docs</h3>
            <ul className="simple-list">
              {(ops?.phase_a?.docs || []).map((item) => (
                <li key={item.path}>
                  {item.label} | {item.status} | {item.path}
                </li>
              ))}
              {!(ops?.phase_a?.docs || []).length ? (
                <li>No personal docs recorded.</li>
              ) : null}
            </ul>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Milestones">
        <div className="stack-list">
          {(notes?.milestones || []).map((item) => (
              <div key={item.name} className="release-item">
              <div className="release-item__header">
                <strong>{item.name}</strong>
                <StatusBadge tone={formatReleaseStatusTone(item.status)}>
                  {item.status}
                </StatusBadge>
              </div>
              <ul className="simple-list">
                {(item.details || []).map((detail) => (
                  <li key={detail}>{detail}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </SectionCard>

      <SectionCard title="Operational snapshot">
        <div className="metric-grid">
          <MetricCard label="Profit factor" value={ops?.portfolio?.profit_factor ?? 0} helper="From realized trades" />
          <MetricCard label="Win rate" value={ops?.portfolio?.win_rate ?? 0} helper="Percent" />
          <MetricCard label="Realized PnL" value={ops?.portfolio?.realized_pnl ?? 0} helper="Journal-backed" />
          <MetricCard label="Updated" value={ops?.timestamp || '--'} helper="Backend timestamp" />
        </div>
      </SectionCard>

      <SectionCard title="Launch readiness">
        <div className="metric-grid">
          <MetricCard
            label="Launch readiness"
            value={ops?.readiness?.summary?.status || '--'}
            helper={ops?.readiness?.summary?.ready ? 'Launch path currently clear' : 'Action still required'}
          />
          <MetricCard
            label="Ready checks"
            value={`${ops?.readiness?.summary?.ready_checks ?? 0}/${ops?.readiness?.summary?.total_checks ?? 0}`}
            helper={`Warnings ${ops?.readiness?.summary?.warning_checks ?? 0}`}
          />
          <MetricCard
            label="Blocked"
            value={ops?.readiness?.summary?.blocked_checks ?? 0}
            helper={`Warnings ${ops?.readiness?.summary?.warning_checks ?? 0}`}
          />
          <MetricCard
            label="Checked"
            value={ops?.readiness?.summary?.checked_at || '--'}
            helper={ops?.readiness?.tenant?.slug ? `Organization ${ops.readiness.tenant.slug}` : 'System-wide snapshot'}
          />
        </div>

        <div className="grid-two">
          <div>
            <h3>Launch blockers</h3>
            <ul className="simple-list">
              {(ops?.readiness?.summary?.blockers || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {!(ops?.readiness?.summary?.blockers || []).length ? (
                <li>No launch blockers recorded.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Launch warnings</h3>
            <ul className="simple-list">
              {(ops?.readiness?.summary?.warnings || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {!(ops?.readiness?.summary?.warnings || []).length ? (
                <li>No launch warnings recorded.</li>
              ) : null}
            </ul>
            <p><strong>Next action:</strong> {ops?.readiness?.summary?.next_action || 'No next action recorded.'}</p>
          </div>
        </div>

        <div>
          <h3>Launch checks</h3>
          <ul className="simple-list">
            {(ops?.readiness?.checks || []).map((item) => (
              <li key={item.key}>
                {item.label} | {item.status} | {item.message}
              </li>
            ))}
            {!(ops?.readiness?.checks || []).length ? (
              <li>No launch checks recorded.</li>
            ) : null}
          </ul>
        </div>
      </SectionCard>

      <SectionCard title="Launch gates">
        <div className="metric-grid">
          <MetricCard
            label="Gate status"
            value={ops?.release_gates?.summary?.status || '--'}
            helper={ops?.release_gates?.summary?.ready ? 'Launch gates are clear' : 'Launch still has blockers'}
          />
          <MetricCard
            label="Ready gates"
            value={`${ops?.release_gates?.summary?.ready_gates ?? 0}/${ops?.release_gates?.summary?.total_gates ?? 0}`}
            helper={`Warnings ${ops?.release_gates?.summary?.warning_gates ?? 0}`}
          />
          <MetricCard
            label="Blocked gates"
            value={ops?.release_gates?.summary?.blocked_gates ?? 0}
            helper={ops?.release_gates?.tenant?.slug ? `Organization ${ops.release_gates.tenant.slug}` : 'System-wide snapshot'}
          />
          <MetricCard
            label="Checked"
            value={ops?.release_gates?.summary?.checked_at || '--'}
            helper={ops?.release_gates?.summary?.next_action || 'No next action recorded.'}
          />
        </div>

        {releaseGatesUnseeded ? (
          <FeedbackState
            tone="info"
            eyebrow="First operational gate"
            title="Launch gate history has not been seeded yet"
            description="Start here with organization setup and the UX acceptance walkthroughs, then refresh this page so release blockers, warnings, and checks have real launch context."
            compact
            actions={[
              { label: 'Open settings', onAction: () => navigate('/settings') },
              { label: 'Open UX guide', onAction: () => navigate('/education'), variant: 'ghost' },
            ]}
          />
        ) : null}

        <div className="grid-two">
          <div>
            <h3>Gate blockers</h3>
            <ul className="simple-list">
              {(ops?.release_gates?.summary?.blockers || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {!(ops?.release_gates?.summary?.blockers || []).length ? (
                <li>No release-gate blockers recorded.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Gate warnings</h3>
            <ul className="simple-list">
              {(ops?.release_gates?.summary?.warnings || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {!(ops?.release_gates?.summary?.warnings || []).length ? (
                <li>No release-gate warnings recorded.</li>
              ) : null}
            </ul>
            <p><strong>Next action:</strong> {ops?.release_gates?.summary?.next_action || 'No next action recorded.'}</p>
          </div>
        </div>

        <div>
          <h3>Gate detail</h3>
          <ul className="simple-list">
            {(ops?.release_gates?.gates || []).map((item) => (
              <li key={item.key}>
                {item.label} | {item.status} | {item.message}
              </li>
            ))}
            {!(ops?.release_gates?.gates || []).length ? (
              <li>No release-gate checks recorded.</li>
            ) : null}
          </ul>
        </div>
      </SectionCard>

      <SectionCard title="Billing reconciliation">
        <div className="metric-grid">
          <MetricCard
            label="Sync status"
            value={ops?.billing?.summary?.status || '--'}
            helper={ops?.billing?.tenant?.provider || 'Billing provider'}
          />
          <MetricCard
            label="Pending jobs"
            value={ops?.billing?.summary?.pending_job_count ?? 0}
            helper={`Failed events ${ops?.billing?.summary?.failed_event_count ?? 0}`}
          />
          <MetricCard
            label="Recovery drills"
            value={ops?.billing?.summary?.drill_count ?? 0}
            helper={`Replays ${ops?.billing?.summary?.replay_count ?? 0}`}
          />
          <MetricCard
            label="Last drill"
            value={ops?.billing?.summary?.last_drill_at || '--'}
            helper={ops?.billing?.summary?.last_replay_at ? `Last replay ${ops.billing.summary.last_replay_at}` : 'No replay recorded'}
          />
        </div>

        {billingRehearsalUnseeded ? (
          <FeedbackState
            tone="info"
            eyebrow="First billing rehearsal"
            title="Billing replay history has not started yet"
            description="Start here once organization delivery and launch setup are in place, then run the first billing drill or live checkout flow so reconciliation history starts showing up here."
            compact
            actions={[
              { label: 'Open settings', onAction: () => navigate('/settings') },
              { label: 'Refresh release center', onAction: loadReleaseSurface, variant: 'ghost' },
            ]}
          />
        ) : null}

        <div className="grid-two">
          <div>
            <h3>Billing sync summary</h3>
            <ul className="simple-list">
              <li>Provider | {ops?.billing?.sync?.provider || '--'}</li>
              <li>Last event | {ops?.billing?.sync?.last_event_key || '--'}</li>
              <li>Last processed | {ops?.billing?.sync?.last_processed_at || 'Not recorded yet'}</li>
              <li>Last failure | {ops?.billing?.sync?.last_failed_at || 'Not recorded yet'}</li>
              <li>Duplicate replays | {ops?.billing?.sync?.duplicate_count ?? 0}</li>
            </ul>
            <p>{ops?.billing?.summary?.message || 'No billing sync message recorded.'}</p>
          </div>

          <div>
            <h3>Recovery posture</h3>
            <ul className="simple-list">
              <li>Last reconciled | {ops?.billing?.recovery?.last_reconciled_at || 'Not recorded yet'}</li>
              <li>Last recovery action | {ops?.billing?.recovery?.last_recovery_action || 'None'}</li>
              <li>Last recovery status | {ops?.billing?.recovery?.last_recovery_status || 'None'}</li>
              <li>Latest failed event | {ops?.billing?.recovery?.latest_failed_event_id || 'None'}</li>
            </ul>
            <p>{ops?.billing?.recovery?.last_recovery_error || 'No billing recovery errors recorded.'}</p>
          </div>
        </div>

        <div className="grid-two">
          <div>
            <h3>Recent drills & replays</h3>
            <ul className="simple-list">
              {(ops?.billing?.drills?.items || []).map((item) => (
                <li key={item.id}>
                  {item.action} | {item.kind} | {item.status} | {item.completed_at || item.started_at || '--'}
                </li>
              ))}
              {!(ops?.billing?.drills?.items || []).length ? (
                <li>No billing drills or replay events recorded yet.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Replay visibility</h3>
            <ul className="simple-list">
              {(ops?.billing?.failed_events || []).map((item) => (
                <li key={item.id}>
                  {item.event_key} | failed {item.processed_at || item.received_at || '--'} | {item.external_event_id || 'no external id'}
                </li>
              ))}
              {!(ops?.billing?.failed_events || []).length ? (
                <li>No failed billing events are waiting for replay.</li>
              ) : null}
            </ul>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Rate-limit & abuse controls">
        <div className="metric-grid">
          <MetricCard
            label="Limiter"
            value={ops?.rate_limits?.summary?.enabled ? 'enabled' : 'disabled'}
            helper={ops?.rate_limits?.summary?.last_throttle_at || 'No recent throttle'}
          />
          <MetricCard
            label="Throttle events"
            value={ops?.rate_limits?.summary?.throttle_event_count ?? 0}
            helper={`Abuse failures ${ops?.rate_limits?.summary?.abuse_failure_count ?? 0}`}
          />
          <MetricCard
            label="Blocked actors"
            value={ops?.rate_limits?.summary?.blocked_actor_count ?? 0}
            helper={`Auth lockouts ${ops?.rate_limits?.summary?.auth_lockout_count ?? 0}`}
          />
          <MetricCard
            label="Last abuse event"
            value={ops?.rate_limits?.summary?.last_abuse_event_at || '--'}
            helper="Recent auth and throttle pressure"
          />
        </div>

        <div className="grid-two">
          <div>
            <h3>Recent throttle events</h3>
            <ul className="simple-list">
              {(ops?.rate_limits?.recent_events || []).map((item) => (
                <li key={`${item.policy_key}-${item.bucket}-${item.at}`}>
                  {item.policy_label || item.policy_key} | retry {item.retry_after_seconds}s | {item.at}
                </li>
              ))}
              {!(ops?.rate_limits?.recent_events || []).length ? (
                <li>No recent throttle events recorded.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Blocked actors</h3>
            <ul className="simple-list">
              {(ops?.rate_limits?.blocked_actors || []).map((item) => (
                <li key={item.actor_key}>
                  {item.actor_key} | until {item.blocked_until || '--'} | {item.reason || 'lockout'}
                </li>
              ))}
              {!(ops?.rate_limits?.blocked_actors || []).length ? (
                <li>No blocked actors recorded.</li>
              ) : null}
            </ul>
          </div>
        </div>

        <div>
          <h3>Recent abuse events</h3>
          <ul className="simple-list">
            {(ops?.rate_limits?.recent_abuse || []).map((item) => (
              <li key={`${item.actor_key}-${item.event_type}-${item.at}`}>
                {item.event_type} | {item.actor_key} | {item.at}
              </li>
            ))}
            {!(ops?.rate_limits?.recent_abuse || []).length ? (
              <li>No recent abuse events recorded.</li>
            ) : null}
          </ul>
        </div>
      </SectionCard>

      <SectionCard title="Order lifecycle health">
        <div className="metric-grid">
          <MetricCard
            label="Lifecycle"
            value={ops?.orders?.summary?.status || '--'}
            helper={ops?.orders?.summary?.message || 'No lifecycle summary recorded.'}
          />
          <MetricCard
            label="Pending orders"
            value={ops?.orders?.summary?.pending_order_count ?? 0}
            helper={`Stale ${ops?.orders?.summary?.stale_pending_count ?? 0}`}
          />
          <MetricCard
            label="Rejects"
            value={ops?.orders?.summary?.reject_count ?? 0}
            helper={ops?.orders?.summary?.last_reject_at || 'No recent rejects'}
          />
          <MetricCard
            label="Fills"
            value={ops?.orders?.summary?.fill_count ?? 0}
            helper={ops?.orders?.summary?.last_fill_at || 'No recent fills'}
          />
        </div>

        <div className="grid-two">
          <div>
            <h3>Lifecycle checks</h3>
            <ul className="simple-list">
              {(ops?.orders?.checks || []).map((item) => (
                <li key={item.key}>
                  {item.label} | {item.status} | {item.message}
                </li>
              ))}
              {!(ops?.orders?.checks || []).length ? (
                <li>No order lifecycle checks recorded.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Stale pending orders</h3>
            <ul className="simple-list">
              {(ops?.orders?.stale_pending_orders || []).map((item) => (
                <li key={item.order_id || item.trade_id || `${item.ticker}-${item.updated_at}`}>
                  {item.ticker} | {item.order_type} | {item.age_minutes} min | stale after {item.stale_after_minutes} min
                </li>
              ))}
              {!(ops?.orders?.stale_pending_orders || []).length ? (
                <li>No stale pending orders recorded.</li>
              ) : null}
            </ul>
          </div>
        </div>

        <div className="grid-two">
          <div>
            <h3>Recent rejects</h3>
            <ul className="simple-list">
              {(ops?.orders?.recent_rejections || []).map((item) => (
                <li key={item.id}>
                  {item.ticker} | {item.order_type || '--'} | {item.detail || 'Rejected'} | {item.created_at || '--'}
                </li>
              ))}
              {!(ops?.orders?.recent_rejections || []).length ? (
                <li>No recent rejected orders recorded.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Recent fills</h3>
            <ul className="simple-list">
              {(ops?.orders?.recent_fills || []).map((item) => (
                <li key={item.id}>
                  {item.ticker} | {item.order_type || '--'} | {item.label || 'Filled'} | {item.created_at || '--'}
                </li>
              ))}
              {!(ops?.orders?.recent_fills || []).length ? (
                <li>No recent filled orders recorded.</li>
              ) : null}
            </ul>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Core service smoke checks">
        <div className="metric-grid">
          <MetricCard
            label="Smoke status"
            value={ops?.service_smoke?.summary?.status || '--'}
            helper={ops?.service_smoke?.summary?.next_action || 'Service smoke snapshot'}
          />
          <MetricCard
            label="Ready"
            value={ops?.service_smoke?.summary?.ready_checks ?? 0}
            helper={`Warnings ${ops?.service_smoke?.summary?.warning_checks ?? 0}`}
          />
          <MetricCard
            label="Blocked"
            value={ops?.service_smoke?.summary?.blocked_checks ?? 0}
            helper={`Total ${ops?.service_smoke?.summary?.total_checks ?? 0}`}
          />
          <MetricCard
            label="Organization"
            value={ops?.service_smoke?.tenant?.slug || '--'}
            helper="Auth, billing, market, jobs"
          />
        </div>

        <div className="grid-two">
          <div>
            <h3>Checks</h3>
            <ul className="simple-list">
              {(ops?.service_smoke?.checks || []).map((item) => (
                <li key={item.key}>
                  {item.label} | {item.status} | {item.message}
                </li>
              ))}
              {!(ops?.service_smoke?.checks || []).length ? (
                <li>No service smoke checks recorded.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Blockers and warnings</h3>
            <ul className="simple-list">
              {(ops?.service_smoke?.summary?.blockers || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {(ops?.service_smoke?.summary?.warnings || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {!(ops?.service_smoke?.summary?.blockers || []).length &&
              !(ops?.service_smoke?.summary?.warnings || []).length ? (
                <li>No core service smoke blockers or warnings recorded.</li>
              ) : null}
            </ul>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Organization launch readiness">
        <div className="metric-grid">
          <MetricCard
            label="Launch stage"
            value={ops?.launch?.summary?.stage || '--'}
            helper={ops?.launch?.summary?.status || 'Launch snapshot'}
          />
          <MetricCard
            label="Launch ready"
            value={ops?.launch?.summary?.launch_ready ? 'yes' : 'no'}
            helper={ops?.launch?.summary?.enabled ? 'White-label path enabled' : 'Standard organization path'}
          />
          <MetricCard
            label="Release lane"
            value={ops?.launch?.summary?.release_channel || '--'}
            helper={ops?.launch?.tenant?.name || 'Organization'}
          />
          <MetricCard
            label="Checks"
            value={`${ops?.launch?.summary?.completed_checks ?? 0}/${ops?.launch?.summary?.total_checks ?? 0}`}
            helper={`Blockers ${ops?.launch?.summary?.blocker_count ?? 0}`}
          />
        </div>

        <div className="grid-two">
          <div>
            <h3>Launch blockers</h3>
            <ul className="simple-list">
              {(ops?.launch?.blockers || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {!(ops?.launch?.blockers || []).length ? <li>No launch blockers recorded.</li> : null}
            </ul>
          </div>

          <div>
            <h3>Checklist</h3>
            <ul className="simple-list">
              {(ops?.launch?.checklist || []).map((item) => (
                <li key={item.key}>
                  {item.label} | {item.complete ? 'complete' : 'pending'} | {item.detail}
                </li>
              ))}
              {!(ops?.launch?.checklist || []).length ? <li>No launch checklist items recorded.</li> : null}
            </ul>
          </div>
        </div>

        <div className="grid-two">
          <div>
            <h3>Launch routing checks</h3>
            <ul className="simple-list">
              <li>Domain: {ops?.launch?.checks?.domain_required ? (ops?.launch?.checks?.domain_ready ? 'ready' : 'pending') : 'not required'}</li>
              <li>Sender: {ops?.launch?.checks?.sender_required ? (ops?.launch?.checks?.sender_ready ? 'ready' : 'pending') : 'not required'}</li>
              <li>Auth: {ops?.launch?.checks?.auth_required ? (ops?.launch?.checks?.auth_ready ? 'ready' : 'pending') : 'not required'}</li>
            </ul>
          </div>

          <div>
            <h3>Recent launch operations</h3>
            <ul className="simple-list">
              {(ops?.launch?.recent_operations || []).map((item, index) => (
                <li key={`${item.key || item.action || 'launch-op'}-${item.at || index}`}>
                  {item.label || item.action || item.key || 'Operation'} | {item.status || '--'} | {item.at || '--'}
                </li>
              ))}
              {!(ops?.launch?.recent_operations || []).length ? (
                <li>{ops?.launch?.summary?.next_action || 'No recent launch operations recorded.'}</li>
              ) : null}
            </ul>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Deployment readiness">
        <div className="metric-grid">
          <MetricCard
            label="Readiness"
            value={`${ops?.deployment?.summary?.readiness_percent ?? 0}%`}
            helper={`${ops?.deployment?.summary?.ready_checks ?? 0}/${ops?.deployment?.summary?.total_checks ?? 0} checks ready`}
          />
          <MetricCard
            label="Artifacts"
            value={`${ops?.deployment?.deployment?.ready_count ?? 0}/${ops?.deployment?.deployment?.count ?? 0}`}
            helper="Compose, Dockerfiles, env template, make targets"
          />
          <MetricCard
            label="Runbooks"
            value={`${ops?.deployment?.runbooks?.ready_count ?? 0}/${ops?.deployment?.runbooks?.count ?? 0}`}
            helper="Deployment, rollback, incident, backup, slow app, stale feed, backlog"
          />
          <MetricCard
            label="Backup status"
            value={ops?.deployment?.backups?.status || '--'}
            helper={ops?.deployment?.backups?.provider || 'Backup provider'}
          />
          <MetricCard
            label="Manifest valid"
            value={ops?.deployment?.backups?.validation?.valid ? 'yes' : 'no'}
            helper={`${ops?.deployment?.backups?.validation?.issue_count ?? 0} issues`}
          />
        </div>

        <div className="grid-two">
          <div>
            <h3>Deployment artifacts</h3>
            <ul className="simple-list">
              {(ops?.deployment?.deployment?.items || []).map((item) => (
                <li key={item.path}>
                  {item.label} | {item.status} | {item.modified_at || 'Not found'}
                </li>
              ))}
              {!(ops?.deployment?.deployment?.items || []).length ? (
                <li>No deployment artifacts recorded.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Runbook coverage</h3>
            <ul className="simple-list">
              {(ops?.deployment?.runbooks?.items || []).map((item) => (
                <li key={item.path}>
                  {item.label} | {item.status} | {item.modified_at || 'Not found'}
                </li>
              ))}
              {!(ops?.deployment?.runbooks?.items || []).length ? (
                <li>No runbooks recorded.</li>
              ) : null}
            </ul>
          </div>
        </div>

        <div className="grid-two">
          <div>
            <h3>Backup posture</h3>
            <ul className="simple-list">
              <li>Manifest | {ops?.deployment?.backups?.manifest_path || '--'}</li>
              <li>Schedule | {ops?.deployment?.backups?.schedule || '--'}</li>
              <li>Last success | {ops?.deployment?.backups?.last_success_at || 'Not recorded yet'}</li>
              <li>Restore drill | {ops?.deployment?.backups?.restore_tested_at || 'Not recorded yet'}</li>
              <li>
                Restore age | {ops?.deployment?.backups?.restore_age_days !== null && ops?.deployment?.backups?.restore_age_days !== undefined
                  ? `${ops.deployment.backups.restore_age_days} days`
                  : 'Not recorded yet'}
              </li>
              <li>Restore warning window | {ops?.deployment?.backups?.restore_warning_days ?? 0} days</li>
              <li>Retention | {ops?.deployment?.backups?.retention_days ?? 0} days</li>
              <li>Location | {ops?.deployment?.backups?.location || '--'}</li>
            </ul>
          </div>

          <div>
            <h3>Release blockers and warnings</h3>
            <ul className="simple-list">
              {(ops?.deployment?.summary?.blockers || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {(ops?.deployment?.summary?.warnings || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {!(ops?.deployment?.summary?.blockers || []).length ? (
                !(ops?.deployment?.summary?.warnings || []).length ? <li>No deployment blockers or warnings recorded.</li> : null
              ) : null}
            </ul>
            <p><strong>Next action:</strong> {ops?.deployment?.summary?.next_action || 'No next action recorded.'}</p>
          </div>
        </div>

        <div className="grid-two">
          <div>
            <h3>Backup validation</h3>
            <ul className="simple-list">
              {(ops?.deployment?.backups?.validation?.issues || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {!(ops?.deployment?.backups?.validation?.issues || []).length ? (
                <li>Backup manifest validation is clean.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Backup checklist</h3>
            <ul className="simple-list">
              {(ops?.deployment?.backups?.checklist || []).map((item) => (
                <li key={item.key}>
                  {item.label} | {item.ready ? 'ready' : 'pending'}
                </li>
              ))}
              {!(ops?.deployment?.backups?.checklist || []).length ? (
                <li>No backup checklist recorded.</li>
              ) : null}
            </ul>
          </div>
        </div>

        <div className="grid-two">
          <div>
            <h3>Environment configuration</h3>
            <div className="metric-grid">
              <MetricCard
                label="Env status"
                value={ops?.deployment?.environment?.summary?.status || '--'}
                helper={ops?.deployment?.environment?.summary?.next_action || 'Environment validation'}
              />
              <MetricCard
                label="Config checks"
                value={`${ops?.deployment?.environment?.summary?.ready_checks ?? 0}/${ops?.deployment?.environment?.summary?.total_checks ?? 0}`}
                helper={`Blockers ${(ops?.deployment?.environment?.summary?.blockers || []).length}`}
              />
            </div>
            <ul className="simple-list">
              {(ops?.deployment?.environment?.checks || []).map((item) => (
                <li key={item.key}>
                  {item.label} | {item.status} | {item.message}
                </li>
              ))}
              {!(ops?.deployment?.environment?.checks || []).length ? (
                <li>No environment validation checks recorded.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Environment blockers and warnings</h3>
            <ul className="simple-list">
              {(ops?.deployment?.environment?.summary?.blockers || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {(ops?.deployment?.environment?.summary?.warnings || []).map((item) => (
                <li key={item}>{item}</li>
              ))}
              {!(ops?.deployment?.environment?.summary?.blockers || []).length &&
              !(ops?.deployment?.environment?.summary?.warnings || []).length ? (
                <li>No environment blockers or warnings recorded.</li>
              ) : null}
            </ul>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Market-data freshness">
        <div className="metric-grid">
          <MetricCard
            label="Feed status"
            value={ops?.market_data?.status || '--'}
            helper={`${ops?.market_data?.ticker || '--'} ${ops?.market_data?.interval || ''}`.trim()}
          />
          <MetricCard
            label="Session"
            value={ops?.market_data?.session_label || '--'}
            helper={ops?.market_data?.feed_expected ? 'Feed expected' : 'Outside active feed window'}
          />
          <MetricCard
            label="Latest bar age"
            value={
              ops?.market_data?.latest_bar_age_minutes !== null && ops?.market_data?.latest_bar_age_minutes !== undefined
                ? `${ops.market_data.latest_bar_age_minutes} min`
                : '--'
            }
            helper={`Warn ${ops?.market_data?.warning_threshold_seconds ?? 0}s | stale ${ops?.market_data?.stale_threshold_seconds ?? 0}s`}
          />
          <MetricCard
            label="Latest bar"
            value={ops?.market_data?.latest_bar_at || '--'}
            helper={ops?.market_data?.checked_at_et || 'No probe yet'}
          />
        </div>

        <div className="grid-two">
          <div>
            <h3>Freshness summary</h3>
            <ul className="simple-list">
              <li>Status | {ops?.market_data?.status || '--'}</li>
              <li>Source | {ops?.market_data?.source || '--'}</li>
              <li>Bars loaded | {ops?.market_data?.point_count ?? 0}</li>
              <li>Session | {ops?.market_data?.session_label || '--'}</li>
            </ul>
          </div>

          <div>
            <h3>Feed warning</h3>
            <p>{ops?.market_data?.message || 'No market-data freshness message recorded.'}</p>
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Observability">
        <div className="metric-grid">
          <MetricCard
            label="Req window"
            value={ops?.observability?.requests?.summary?.total_requests ?? 0}
            helper={`Lifetime ${ops?.observability?.requests?.lifetime_requests ?? 0}`}
          />
          <MetricCard
            label="Avg latency"
            value={`${ops?.observability?.requests?.summary?.average_duration_ms ?? 0} ms`}
            helper={`P95 ${ops?.observability?.requests?.summary?.p95_duration_ms ?? 0} ms`}
          />
          <MetricCard
            label="Slow reqs"
            value={ops?.observability?.requests?.summary?.slow_request_count ?? 0}
            helper={`Threshold ${ops?.observability?.requests?.summary?.slow_request_threshold_ms ?? 0} ms`}
          />
          <MetricCard
            label="Error rate"
            value={`${ops?.observability?.requests?.summary?.error_rate ?? 0}%`}
            helper={`${ops?.observability?.requests?.summary?.error_count ?? 0} errors`}
          />
          <MetricCard
            label="Timeout risks"
            value={ops?.observability?.requests?.summary?.timeout_warning_count ?? 0}
            helper={`Warn at ${ops?.observability?.requests?.summary?.timeout_warning_threshold_ms ?? 0} ms`}
          />
        </div>

        <div className="metric-grid">
          <MetricCard
            label="Op window"
            value={ops?.observability?.operations?.summary?.total_operations ?? 0}
            helper={`Lifetime ${ops?.observability?.operations?.lifetime_operations ?? 0}`}
          />
          <MetricCard
            label="Op avg"
            value={`${ops?.observability?.operations?.summary?.average_duration_ms ?? 0} ms`}
            helper={`P95 ${ops?.observability?.operations?.summary?.p95_duration_ms ?? 0} ms`}
          />
          <MetricCard
            label="Cache hits"
            value={ops?.observability?.operations?.summary?.cache_hit_count ?? 0}
            helper={`Misses ${ops?.observability?.operations?.summary?.cache_miss_count ?? 0}`}
          />
          <MetricCard
            label="Slow ops"
            value={ops?.observability?.operations?.summary?.slow_operation_count ?? 0}
            helper={`Threshold ${ops?.observability?.operations?.summary?.slow_operation_threshold_ms ?? 0} ms`}
          />
          <MetricCard
            label="Op timeouts"
            value={ops?.observability?.operations?.summary?.timeout_count ?? 0}
            helper={`${ops?.observability?.operations?.summary?.error_count ?? 0} non-ok ops`}
          />
        </div>

        <div className="metric-grid">
          <MetricCard
            label="Route profiles"
            value={ops?.observability?.route_profiles?.summary?.total_profiles ?? 0}
            helper={`Lifetime ${ops?.observability?.route_profiles?.lifetime_profiles ?? 0}`}
          />
          <MetricCard
            label="Route avg"
            value={`${ops?.observability?.route_profiles?.summary?.average_total_duration_ms ?? 0} ms`}
            helper={`P95 ${ops?.observability?.route_profiles?.summary?.p95_total_duration_ms ?? 0} ms`}
          />
          <MetricCard
            label="Slow profiles"
            value={ops?.observability?.route_profiles?.summary?.slow_profile_count ?? 0}
            helper={`Threshold ${ops?.observability?.route_profiles?.summary?.slow_profile_threshold_ms ?? 0} ms`}
          />
          <MetricCard
            label="Profile timeouts"
            value={ops?.observability?.route_profiles?.summary?.timeout_profile_count ?? 0}
            helper={ops?.observability?.route_profiles?.summary?.last_profile_at || 'No recent route profiles'}
          />
        </div>

        <div className="metric-grid">
          <MetricCard
            label="Upstream calls"
            value={ops?.observability?.upstream?.summary?.total_calls ?? 0}
            helper={`Lifetime ${ops?.observability?.upstream?.lifetime_calls ?? 0}`}
          />
          <MetricCard
            label="Upstream avg"
            value={`${ops?.observability?.upstream?.summary?.average_duration_ms ?? 0} ms`}
            helper={`P95 ${ops?.observability?.upstream?.summary?.p95_duration_ms ?? 0} ms`}
          />
          <MetricCard
            label="Upstream timeouts"
            value={ops?.observability?.upstream?.summary?.timeout_count ?? 0}
            helper={`Lifetime ${ops?.observability?.upstream?.lifetime_timeouts ?? 0}`}
          />
          <MetricCard
            label="Upstream errors"
            value={ops?.observability?.upstream?.summary?.error_count ?? 0}
            helper={`${ops?.observability?.upstream?.summary?.error_rate ?? 0}% error rate`}
          />
        </div>

        <div className="metric-grid">
          <MetricCard
            label="Worker"
            value={ops?.observability?.jobs?.worker?.running ? 'running' : 'stopped'}
            helper={ops?.observability?.jobs?.worker?.enabled ? (ops?.observability?.jobs?.worker?.last_loop_at || 'Heartbeat pending') : 'Worker disabled'}
          />
          <MetricCard
            label="Queued jobs"
            value={ops?.observability?.jobs?.summary?.queued ?? 0}
            helper={`Retrying ${ops?.observability?.jobs?.summary?.retrying ?? 0}`}
          />
          <MetricCard
            label="Running jobs"
            value={ops?.observability?.jobs?.summary?.running ?? 0}
            helper={`Pending ${ops?.observability?.jobs?.summary?.pending ?? 0}`}
          />
          <MetricCard
            label="Dead letters"
            value={ops?.observability?.jobs?.summary?.dead_letter ?? 0}
            helper={`Recent failures ${ops?.observability?.jobs?.summary?.recent_failure_count ?? 0}`}
          />
          <MetricCard
            label="Stuck jobs"
            value={ops?.observability?.jobs?.summary?.stuck_running_count ?? 0}
            helper={
              ops?.observability?.jobs?.summary?.oldest_running_at
                ? `Oldest running ${ops?.observability?.jobs?.summary?.oldest_running_at}`
                : `Threshold ${ops?.observability?.jobs?.summary?.running_stale_after_minutes ?? 10} min`
            }
          />
        </div>

        <div className="grid-two">
          <div>
            <h3>Top routes</h3>
            <ul className="simple-list">
              {(ops?.observability?.requests?.route_groups || []).map((item) => (
                <li key={item.key}>
                  {item.key} | {item.count} requests
                </li>
              ))}
              {!(ops?.observability?.requests?.route_groups || []).length ? (
                <li>No route activity recorded yet.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Recent slow requests</h3>
            <ul className="simple-list">
              {(ops?.observability?.requests?.recent_slow_requests || []).map((item) => (
                <li key={`${item.request_id}-${item.at}`}>
                  {item.method} {item.path} | {item.duration_ms} ms | {item.status_code}
                </li>
              ))}
              {!(ops?.observability?.requests?.recent_slow_requests || []).length ? (
                <li>No slow requests recorded in the current window.</li>
              ) : null}
            </ul>
          </div>
        </div>

        <div className="grid-two">
          <div>
            <h3>Request timeout risks</h3>
            <ul className="simple-list">
              {(ops?.observability?.requests?.recent_timeout_risks || []).map((item) => (
                <li key={`${item.request_id}-${item.at}-timeout`}>
                  {item.method} {item.path} | {item.duration_ms} ms | {item.status_code}
                </li>
              ))}
              {!(ops?.observability?.requests?.recent_timeout_risks || []).length ? (
                <li>No request timeout risks recorded in the current window.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Upstream targets</h3>
            <ul className="simple-list">
              {(ops?.observability?.upstream?.targets || []).map((item) => (
                <li key={item.key}>
                  {item.key} | {item.count} calls | {item.timeout_count} timeouts
                </li>
              ))}
              {!(ops?.observability?.upstream?.targets || []).length ? (
                <li>No upstream target activity recorded yet.</li>
              ) : null}
            </ul>
          </div>
        </div>

        <div className="grid-two">
          <div>
            <h3>Hot route profiles</h3>
            <ul className="simple-list">
              {(ops?.observability?.route_profiles?.routes || []).map((item) => (
                <li key={item.key}>
                  {item.key} | avg {item.average_duration_ms} ms | slow {item.slow_count}
                </li>
              ))}
              {!(ops?.observability?.route_profiles?.routes || []).length ? (
                <li>No route profile telemetry recorded yet.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Hot operations</h3>
            <ul className="simple-list">
              {(ops?.observability?.operations?.operations || []).map((item) => (
                <li key={item.key}>
                  {item.key} | {item.count} runs | avg {item.average_duration_ms} ms | hits {item.cache_hits}
                </li>
              ))}
              {!(ops?.observability?.operations?.operations || []).length ? (
                <li>No operation telemetry recorded yet.</li>
              ) : null}
            </ul>
          </div>
        </div>

        <div className="grid-two">
          <div>
            <h3>Recent route profiles</h3>
            <ul className="simple-list">
              {(ops?.observability?.route_profiles?.recent_profiles || []).map((item) => (
                <li key={`${item.route_key}-${item.at}`}>
                  {item.route_key} | {item.total_duration_ms} ms | {item.status}
                </li>
              ))}
              {!(ops?.observability?.route_profiles?.recent_profiles || []).length ? (
                <li>No recent route profiles recorded.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Recent slow operations</h3>
            <ul className="simple-list">
              {(ops?.observability?.operations?.recent_slow_operations || []).map((item) => (
                <li key={`${item.name}-${item.at}`}>
                  {item.name} | {item.duration_ms} ms | {item.cache_status}
                </li>
              ))}
              {!(ops?.observability?.operations?.recent_slow_operations || []).length ? (
                <li>No slow operations recorded in the current window.</li>
              ) : null}
            </ul>
          </div>
        </div>

        <div>
          <h3>Profile stage breakdowns</h3>
          <ul className="simple-list">
            {(ops?.observability?.route_profiles?.routes || []).flatMap((route) =>
              (route.stages || []).slice(0, 4).map((stage) => (
                <li key={`${route.key}-${stage.key}`}>
                  {route.key} | {stage.key} | avg {stage.average_duration_ms} ms | max {stage.max_duration_ms} ms
                </li>
              )),
            )}
            {!(ops?.observability?.route_profiles?.routes || []).length ? (
              <li>No route stage profiling recorded yet.</li>
            ) : null}
          </ul>
        </div>

        <div className="grid-two">
          <div>
            <h3>Recent upstream calls</h3>
            <ul className="simple-list">
              {(ops?.observability?.upstream?.recent_calls || []).map((item) => (
                <li key={`${item.target}-${item.operation}-${item.at}`}>
                  {item.target} | {item.operation} | {item.status} | {item.duration_ms} ms
                </li>
              ))}
              {!(ops?.observability?.upstream?.recent_calls || []).length ? (
                <li>No upstream activity recorded in the current window.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Recent upstream timeouts</h3>
            <ul className="simple-list">
              {(ops?.observability?.upstream?.recent_timeouts || []).map((item) => (
                <li key={`${item.target}-${item.operation}-${item.at}-timeout`}>
                  {item.target} | {item.operation} | {item.duration_ms} ms | {item.error_message || 'Timeout'}
                </li>
              ))}
              {!(ops?.observability?.upstream?.recent_timeouts || []).length ? (
                <li>No upstream timeouts recorded in the current window.</li>
              ) : null}
            </ul>
          </div>
        </div>

        <div className="grid-two">
          <div>
            <h3>Recent jobs</h3>
            <ul className="simple-list">
              {(ops?.observability?.jobs?.recent_jobs || []).map((item) => (
                <li key={item.id}>
                  {item.job_label || item.job_type} | {item.status} | {item.attempt_count}/{item.max_attempts}
                </li>
              ))}
              {!(ops?.observability?.jobs?.recent_jobs || []).length ? (
                <li>No background job activity recorded yet.</li>
              ) : null}
            </ul>
          </div>

          <div>
            <h3>Worker heartbeat</h3>
            <ul className="simple-list">
              <li>Enabled | {ops?.observability?.jobs?.worker?.enabled ? 'yes' : 'no'}</li>
              <li>Running | {ops?.observability?.jobs?.worker?.running ? 'yes' : 'no'}</li>
              <li>Thread | {ops?.observability?.jobs?.worker?.thread_name || '--'}</li>
              <li>Last loop | {ops?.observability?.jobs?.worker?.last_loop_at || 'Not recorded yet'}</li>
              <li>Last success | {ops?.observability?.jobs?.worker?.last_success_at || 'Not recorded yet'}</li>
              <li>Last error | {ops?.observability?.jobs?.worker?.last_error_at || 'None'}</li>
              <li>Error detail | {ops?.observability?.jobs?.worker?.last_error_message || 'No recent worker error'}</li>
            </ul>
          </div>

          <div>
            <h3>Dead letters</h3>
            <ul className="simple-list">
              {(ops?.observability?.jobs?.dead_letters || []).map((item) => (
                <li key={item.id}>
                  {item.job_type} | {item.attempt_count}/{item.max_attempts} | {item.error_message || 'Unknown error'}
                </li>
              ))}
              {!(ops?.observability?.jobs?.dead_letters || []).length ? (
                <li>No dead-letter jobs in the current snapshot.</li>
              ) : null}
            </ul>
          </div>
        </div>

        <div>
          <h3>Stuck running jobs</h3>
          <ul className="simple-list">
            {(ops?.observability?.jobs?.stuck_running || []).map((item) => (
              <li key={item.id}>
                {item.job_type} | started {item.started_at || '--'} | {item.attempt_count}/{item.max_attempts}
              </li>
            ))}
            {!(ops?.observability?.jobs?.stuck_running || []).length ? (
              <li>No stuck running jobs detected.</li>
            ) : null}
          </ul>
        </div>
      </SectionCard>
    </>
  )
}

import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import {
  getAiAgentMemos,
  getAiAgentProposals,
  getAiAgentRoles,
  getAiAgentsCommitteeLatest,
  getAiAgentsExternalReview,
  getAiAgentsLlmStatus,
  getAiAgentsReadinessBacklog,
  getAiAgentsSafety,
  getAiAgentsSummary,
  createAiAgentProposal,
  decideAiAgentProposal,
  runAiAgentRole,
  runAiAgentsCommittee,
  runAiDeskAgent,
} from '../api/client'

const SAFETY_LABELS = [
  'Research only. Does not affect trading.',
  'Agents cannot place orders.',
  'Agents cannot change broker routes.',
  'Agents cannot bypass risk gates.',
  'Agents cannot change ranking weights automatically.',
  'Agents cannot clear kill switches.',
]

function humanize(value, fallback = 'Unknown') {
  const text = String(value || '').trim()
  if (!text) return fallback
  return text.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase())
}

function toneForSeverity(value) {
  const severity = String(value || '').toLowerCase()
  if (severity === 'high' || severity === 'critical') return 'negative'
  if (severity === 'medium' || severity === 'warning') return 'warning'
  if (severity === 'low' || severity === 'info') return 'neutral'
  return 'neutral'
}

function DataTable({ columns, rows, empty }) {
  return (
    <ListTable>
      <table className="ui-list-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column.key}>{column.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.length ? rows.map((row, index) => (
            <tr key={row.memo_id || row.role_name || row.flag_id || row.finding_id || row.report_id || row.key || index}>
              {columns.map((column) => (
                <td key={column.key}>{column.render ? column.render(row) : row[column.key]}</td>
              ))}
            </tr>
          )) : (
            <tr><td colSpan={columns.length}>{empty}</td></tr>
          )}
        </tbody>
      </table>
    </ListTable>
  )
}

function flattenMemoItems(memos, key) {
  return memos.flatMap((memo) => (memo[key] || []).map((item) => ({
    ...item,
    memo_id: memo.memo_id,
    agent_name: memo.agent_name,
    agent_role: memo.agent_role,
  })))
}

function committeeRecord(latestCommittee) {
  return latestCommittee?.record || latestCommittee?.committee_report || null
}

export default function AICommitteePage() {
  const [summary, setSummary] = useState(null)
  const [roles, setRoles] = useState([])
  const [memos, setMemos] = useState([])
  const [latestCommittee, setLatestCommittee] = useState(null)
  const [safety, setSafety] = useState(null)
  const [llmStatus, setLlmStatus] = useState(null)
  const [readinessBacklog, setReadinessBacklog] = useState([])
  const [externalReview, setExternalReview] = useState([])
  const [proposals, setProposals] = useState([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState('')
  const [error, setError] = useState('')
  const [memoFilters, setMemoFilters] = useState({ agent_role: '', desk: '', warning_type: '' })
  const [proposalStatus, setProposalStatus] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const proposalParams = proposalStatus ? { status: proposalStatus } : {}
      const memoParams = Object.fromEntries(Object.entries(memoFilters).filter(([, value]) => value))
      const [summaryData, roleData, memoData, committeeData, safetyData, llmData, backlogData, externalReviewData, proposalData] = await Promise.all([
        getAiAgentsSummary(),
        getAiAgentRoles(),
        getAiAgentMemos(memoParams),
        getAiAgentsCommitteeLatest(),
        getAiAgentsSafety(),
        getAiAgentsLlmStatus(),
        getAiAgentsReadinessBacklog(),
        getAiAgentsExternalReview(),
        getAiAgentProposals(proposalParams),
      ])
      setSummary(summaryData)
      setRoles(roleData?.records || [])
      setMemos(memoData?.records || [])
      setLatestCommittee(committeeData)
      setSafety(safetyData)
      setLlmStatus(llmData)
      setReadinessBacklog(backlogData?.records || [])
      setExternalReview(externalReviewData?.records || [])
      setProposals(proposalData?.records || [])
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load AI Committee.')
    } finally {
      setLoading(false)
    }
  }, [memoFilters, proposalStatus])

  useEffect(() => {
    load()
  }, [load])

  const runRole = useCallback(async (roleName) => {
    setRunning(roleName)
    setError('')
    try {
      await runAiAgentRole(roleName)
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || `Failed to run ${humanize(roleName)}.`)
    } finally {
      setRunning('')
    }
  }, [load])

  const runDesk = useCallback(async (deskName) => {
    setRunning(deskName)
    setError('')
    try {
      await runAiDeskAgent(deskName)
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || `Failed to run ${humanize(deskName)}.`)
    } finally {
      setRunning('')
    }
  }, [load])

  const runCommittee = useCallback(async () => {
    setRunning('investment_committee')
    setError('')
    try {
      await runAiAgentsCommittee()
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to run Investment Committee.')
    } finally {
      setRunning('')
    }
  }, [load])

  const createProposalFromCommittee = useCallback(async () => {
    const committeeReport = committeeRecord(latestCommittee)
    setRunning('proposal')
    setError('')
    try {
      await createAiAgentProposal({
        proposal_type: 'research_followup',
        title: 'Committee follow-up research proposal',
        rationale: committeeReport?.recommended_next_safe_action || 'Created from the latest AI Committee review for human follow-up.',
        scope: 'research_metadata_only',
        linked_committee_report_id: committeeReport?.report_id || '',
        evidence_refs: committeeReport?.memo_ids || [],
        proposed_change_summary: 'Queue human-reviewed research follow-up only. No execution, broker, risk, ranking, or strategy config change is applied.',
      })
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to create research proposal.')
    } finally {
      setRunning('')
    }
  }, [latestCommittee, load])

  const decideProposal = useCallback(async (proposalId, decision) => {
    setRunning(proposalId)
    setError('')
    try {
      await decideAiAgentProposal(proposalId, {
        decision,
        reason: decision === 'approved_for_research'
          ? 'Approved as research metadata only. No automatic configuration or execution change is applied.'
          : 'Human review metadata decision. No automatic configuration or execution change is applied.',
      })
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to update proposal decision.')
    } finally {
      setRunning('')
    }
  }, [load])

  const roleRows = useMemo(() => roles.filter((role) => !role.desk_key), [roles])
  const deskRows = useMemo(() => roles.filter((role) => role.desk_key), [roles])
  const recentMemos = useMemo(() => [...memos].reverse().slice(0, 20), [memos])
  const riskFlags = useMemo(() => flattenMemoItems(recentMemos, 'risk_flags').slice(0, 16), [recentMemos])
  const findings = useMemo(() => flattenMemoItems(recentMemos, 'supporting_evidence').slice(0, 16), [recentMemos])
  const missingWarnings = useMemo(() => recentMemos.flatMap((memo) => (memo.missing_data || []).map((item) => ({
    key: `${memo.memo_id}-${item}`,
    agent_name: memo.agent_name,
    missing: item,
  }))).slice(0, 16), [recentMemos])
  const refereeWarnings = useMemo(() => riskFlags.filter((flag) => flag.agent_role === 'ai_referee_supervisor'), [riskFlags])
  const complianceWarnings = useMemo(() => riskFlags.filter((flag) => flag.agent_role === 'compliance_claims'), [riskFlags])
  const committee = committeeRecord(latestCommittee)
  const safetyNotes = safety?.safety_notes || summary?.safety_notes || SAFETY_LABELS

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Read-only decision support"
        title="AI Committee"
        description="Hedge-fund-style role agents review evidence, challenge assumptions, and write append-only sanitized research memos. They do not control trading authority."
        badge="Research only"
        actions={(
          <Button type="button" variant="primary" size="sm" onClick={runCommittee} disabled={loading || Boolean(running)}>
            {running === 'investment_committee' ? 'Running...' : 'Run Committee'}
          </Button>
        )}
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        {SAFETY_LABELS.map((label) => <StatusBadge key={label} tone="neutral">{label}</StatusBadge>)}
        <Button type="button" variant="ghost" size="sm" onClick={load} disabled={loading || Boolean(running)}>
          Refresh
        </Button>
      </div>

      <SectionCard title="Authority Boundary" subtitle="Agents may influence human understanding. Agents may not control trading authority.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Authority" value={summary?.authority_level || 'research_only'} helper="Read-only to execution state" />
          <MetricCard label="Memos" value={summary?.summary?.memo_count ?? memos.length} helper="Append-only sanitized research records" />
          <MetricCard label="Committee reports" value={summary?.summary?.committee_report_count ?? 0} helper="Research committee history" />
          <MetricCard label="LLM fallback" value={llmStatus?.summary?.fallback_used === false ? 'Configured' : 'Deterministic'} helper="Malformed or unsafe LLM output falls back safely" />
          <MetricCard label="Backlog items" value={readinessBacklog.length} helper="Proof and governance work remaining" />
          <MetricCard label="Proposals" value={proposals.length} helper="Research metadata only" />
        </div>
        <DataTable
          rows={safetyNotes.map((note, index) => ({ key: index, note }))}
          empty="Safety notes unavailable."
          columns={[{ key: 'note', label: 'Safety note' }]}
        />
      </SectionCard>

      <SectionCard title="Agent Roles" subtitle="Run one role at a time for a focused memo, or run the committee for an aggregate research memo.">
        <DataTable
          rows={roleRows}
          empty={loading ? 'Loading agent roles...' : 'No agent roles found.'}
          columns={[
            { key: 'agent_name', label: 'Agent' },
            { key: 'purpose', label: 'Purpose' },
            { key: 'input_sources', label: 'Inputs', render: (row) => (row.input_sources || []).join(', ') },
            { key: 'reviewer_questions', label: 'Agent playbook', render: (row) => (row.reviewer_questions || []).slice(0, 2).join(' ') || '--' },
            { key: 'must_flag', label: 'Must flag', render: (row) => (row.must_flag || []).slice(0, 3).join(', ') || '--' },
            {
              key: 'run',
              label: 'Run',
              render: (row) => (
                <Button type="button" size="sm" variant="ghost" onClick={() => runRole(row.role_name)} disabled={loading || Boolean(running)}>
                  {running === row.role_name ? 'Running...' : 'Run role'}
                </Button>
              ),
            },
          ]}
        />
      </SectionCard>

      <SectionCard title="Latest Committee Memo" subtitle="The committee aggregates role memos, keeps dissent visible, and recommends only safe research actions.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Status" value={latestCommittee?.status || 'empty'} helper="Latest aggregate report" />
          <MetricCard label="Report" value={committee?.report_id || '--'} helper="Research-only committee record" />
          <MetricCard label="Risk objections" value={(committee?.risk_objections || []).length} helper="Cannot be overridden by AI" />
          <MetricCard label="Dissenting views" value={(committee?.dissenting_views || []).length} helper="Must remain visible" />
        </div>
        <p className="ui-muted">{committee?.committee_thesis || (loading ? 'Loading committee memo...' : 'No committee memo has been created yet.')}</p>
        <DataTable
          rows={(committee?.human_decision_checklist || []).map((item, index) => ({ key: index, item }))}
          empty="Run the committee to create a human decision checklist."
          columns={[{ key: 'item', label: 'Human decision checklist' }]}
        />
      </SectionCard>

      <SectionCard title="Agent Memo Table" subtitle="Memo records are sanitized, append-only, and research-only.">
        <div className="ui-action-row">
          <select
            aria-label="Filter memos by agent role"
            value={memoFilters.agent_role}
            onChange={(event) => setMemoFilters((current) => ({ ...current, agent_role: event.target.value }))}
          >
            <option value="">All roles</option>
            {roles.map((role) => <option key={role.role_name} value={role.role_name}>{role.agent_name}</option>)}
          </select>
          <select
            aria-label="Filter memos by desk"
            value={memoFilters.desk}
            onChange={(event) => setMemoFilters((current) => ({ ...current, desk: event.target.value }))}
          >
            <option value="">All desks</option>
            {deskRows.map((role) => <option key={role.desk_key} value={role.desk_key}>{humanize(role.desk_key)}</option>)}
          </select>
          <input
            aria-label="Filter memos by warning type"
            placeholder="Warning type"
            value={memoFilters.warning_type}
            onChange={(event) => setMemoFilters((current) => ({ ...current, warning_type: event.target.value }))}
          />
        </div>
        <DataTable
          rows={recentMemos}
          empty={loading ? 'Loading memos...' : 'No AI agent memos have been created yet.'}
          columns={[
            { key: 'created_at', label: 'Created' },
            { key: 'agent_name', label: 'Agent' },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={row.status === 'ready' ? 'positive' : 'warning'}>{humanize(row.status)}</StatusBadge> },
            { key: 'confidence', label: 'Confidence', render: (row) => Number(row.confidence || 0).toFixed(2) },
            { key: 'conclusion', label: 'Conclusion' },
            { key: 'recommended_next_safe_action', label: 'Safe next action' },
          ]}
        />
      </SectionCard>

      <SectionCard
        title="Research Proposal Queue"
        subtitle="Proposals and decisions are human-review metadata only. They do not apply config changes, place orders, approve live trading, or bypass gates."
        actions={(
          <Button type="button" size="sm" variant="ghost" onClick={createProposalFromCommittee} disabled={loading || Boolean(running) || !committee}>
            {running === 'proposal' ? 'Creating...' : 'Create committee proposal'}
          </Button>
        )}
      >
        <div className="ui-action-row">
          <select
            aria-label="Filter proposals by status"
            value={proposalStatus}
            onChange={(event) => setProposalStatus(event.target.value)}
          >
            <option value="">All proposal statuses</option>
            <option value="proposed">Proposed</option>
            <option value="needs_more_evidence">Needs more evidence</option>
            <option value="approved_for_research">Approved for research</option>
            <option value="rejected">Rejected</option>
          </select>
        </div>
        <DataTable
          rows={proposals}
          empty={loading ? 'Loading proposals...' : 'No research proposals have been queued yet.'}
          columns={[
            { key: 'created_at', label: 'Created' },
            { key: 'title', label: 'Proposal' },
            { key: 'proposal_type', label: 'Type', render: (row) => humanize(row.proposal_type) },
            { key: 'effective_status', label: 'Status', render: (row) => <StatusBadge tone={row.effective_status === 'approved_for_research' ? 'positive' : row.effective_status === 'rejected' ? 'negative' : 'warning'}>{humanize(row.effective_status || row.status)}</StatusBadge> },
            { key: 'proposed_change_summary', label: 'Research-only summary' },
            {
              key: 'decision',
              label: 'Human review metadata',
              render: (row) => (
                <div className="ui-action-row">
                  <Button type="button" size="sm" variant="ghost" onClick={() => decideProposal(row.proposal_id, 'approved_for_research')} disabled={Boolean(running)}>
                    Approve research
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => decideProposal(row.proposal_id, 'needs_more_evidence')} disabled={Boolean(running)}>
                    Need evidence
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => decideProposal(row.proposal_id, 'rejected')} disabled={Boolean(running)}>
                    Reject
                  </Button>
                </div>
              ),
            },
          ]}
        />
      </SectionCard>

      <SectionCard title="10/10 Readiness Backlog" subtitle="This tracks what is still needed from the roadmap. Items are not readiness upgrades and not proof of alpha.">
        <DataTable
          rows={readinessBacklog}
          empty={loading ? 'Loading readiness backlog...' : 'No readiness backlog items found.'}
          columns={[
            { key: 'priority', label: 'Priority' },
            { key: 'title', label: 'Item' },
            { key: 'category', label: 'Category', render: (row) => humanize(row.category) },
            { key: 'current_gap', label: 'Gap' },
            { key: 'safe_next_action', label: 'Safe next action' },
          ]}
        />
      </SectionCard>

      <SectionCard title="External Review And LLM Status" subtitle="External review is planned metadata, not certification. No external LLM provider is enabled by this surface.">
        <div className="ui-dashboard-grid">
          <MetricCard label="Approved LLM provider" value={llmStatus?.summary?.approved_provider_configured ? 'Configured' : 'None'} helper={llmStatus?.summary?.reason || 'Deterministic fallback remains active'} />
          <MetricCard label="Structured contract" value={llmStatus?.summary?.structured_contract_available ? 'Available' : 'Unavailable'} helper="Role-specific prompt contracts" />
          <MetricCard label="Review areas" value={externalReview.length} helper="Security, legal, compliance" />
        </div>
        <DataTable
          rows={externalReview}
          empty={loading ? 'Loading external review plan...' : 'No external review plan found.'}
          columns={[
            { key: 'area', label: 'Area', render: (row) => humanize(row.area) },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone="warning">{humanize(row.status)}</StatusBadge> },
            { key: 'requirement', label: 'Requirement' },
            { key: 'safe_next_action', label: 'Safe next action' },
            { key: 'blocks_claims', label: 'Claims blocked', render: (row) => (row.blocks_claims || []).join(', ') },
          ]}
        />
      </SectionCard>

      <SectionCard title="Findings And Evidence Links" subtitle="Evidence links point to internal source sections, not raw logs, account records, secrets, or local paths.">
        <DataTable
          rows={findings}
          empty={loading ? 'Loading findings...' : 'No findings available.'}
          columns={[
            { key: 'agent_name', label: 'Agent' },
            { key: 'title', label: 'Finding' },
            { key: 'detail', label: 'Detail' },
            { key: 'evidence_refs', label: 'Evidence', render: (row) => (row.evidence_refs || []).join(', ') || '--' },
          ]}
        />
      </SectionCard>

      <SectionCard title="Risk Flags And Missing Data" subtitle="Missing evidence remains missing. It is not fabricated or treated as proof.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={riskFlags}
            empty="No risk flags in recent memos."
            columns={[
              { key: 'agent_name', label: 'Agent' },
              { key: 'flag_type', label: 'Flag', render: (row) => <StatusBadge tone={toneForSeverity(row.severity)}>{humanize(row.flag_type)}</StatusBadge> },
              { key: 'detail', label: 'Detail' },
            ]}
          />
          <DataTable
            rows={missingWarnings}
            empty="No missing data warnings in recent memos."
            columns={[
              { key: 'agent_name', label: 'Agent' },
              { key: 'missing', label: 'Missing data' },
            ]}
          />
        </div>
      </SectionCard>

      <SectionCard title="Desk Agent Summaries" subtitle="Desk agents review only their desk context when available and cannot change desk config automatically.">
        <DataTable
          rows={deskRows}
          empty={loading ? 'Loading desk agents...' : 'No desk agents found.'}
          columns={[
            { key: 'agent_name', label: 'Desk agent' },
            { key: 'desk_key', label: 'Desk', render: (row) => humanize(row.desk_key) },
            { key: 'purpose', label: 'Purpose' },
            {
              key: 'run',
              label: 'Run',
              render: (row) => (
                <Button type="button" size="sm" variant="ghost" onClick={() => runDesk(row.desk_key)} disabled={loading || Boolean(running)}>
                  {running === row.desk_key ? 'Running...' : 'Run desk'}
                </Button>
              ),
            },
          ]}
        />
      </SectionCard>

      <SectionCard title="Supervisor And Claims Warnings" subtitle="The AI Referee Supervisor and Compliance and Claims Agent flag unsupported claims, contradictions, bad recommendations, and missing labels.">
        <div className="ui-dashboard-grid">
          <DataTable
            rows={refereeWarnings}
            empty="No AI Referee Supervisor warnings in recent memos."
            columns={[
              { key: 'flag_type', label: 'Supervisor flag', render: (row) => humanize(row.flag_type) },
              { key: 'detail', label: 'Detail' },
            ]}
          />
          <DataTable
            rows={complianceWarnings}
            empty="No Compliance and Claims warnings in recent memos."
            columns={[
              { key: 'flag_type', label: 'Claims flag', render: (row) => humanize(row.flag_type) },
              { key: 'detail', label: 'Detail' },
            ]}
          />
        </div>
      </SectionCard>
    </div>
  )
}

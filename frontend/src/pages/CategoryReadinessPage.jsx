import { useCallback, useEffect, useMemo, useState } from 'react'
import Button from '../components/Button'
import ErrorState from '../components/ErrorState'
import ListTable from '../components/ListTable'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import {
  getCategoryUpgradeBacklog,
  getCategoryUpgradeProofChain,
  getCategoryUpgradeProofGates,
  getCategoryUpgradeReadiness,
  getCategoryUpgradeSupportExport,
  writeCategoryUpgradeSupportExport,
} from '../api/client'

const SAFETY_LABELS = [
  'Read-only readiness evaluator. Does not affect trading.',
  'Does not place orders.',
  'Does not change broker routes.',
  'Does not bypass risk gates.',
  'Does not clear kill switches.',
  'Does not change ranking weights automatically.',
  'Does not grant AI order authority.',
]

function humanize(value, fallback = 'Unknown') {
  const text = String(value || '').trim()
  if (!text) return fallback
  return text.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase())
}

function formatPercent(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return '--'
  return `${numeric.toFixed(1)}%`
}

function statusTone(status) {
  const text = String(status || '').toLowerCase()
  if (text.includes('passed') || text.includes('ready') || text.includes('complete')) return 'positive'
  if (text.includes('blocked') || text.includes('failed')) return 'negative'
  if (text.includes('partial') || text.includes('missing') || text.includes('progress') || text.includes('next')) return 'warning'
  return 'neutral'
}

function compactList(items, fallback = 'None') {
  const list = Array.isArray(items) ? items.filter(Boolean) : []
  if (!list.length) return fallback
  return list.slice(0, 3).join('; ')
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
            <tr key={row.key || row.category_key || row.sequence || index}>
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

export default function CategoryReadinessPage() {
  const [report, setReport] = useState(null)
  const [proofGates, setProofGates] = useState([])
  const [proofChain, setProofChain] = useState([])
  const [backlog, setBacklog] = useState([])
  const [exportPreview, setExportPreview] = useState(null)
  const [exportWrite, setExportWrite] = useState(null)
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [readinessPayload, gatePayload, chainPayload, backlogPayload] = await Promise.all([
        getCategoryUpgradeReadiness(),
        getCategoryUpgradeProofGates(),
        getCategoryUpgradeProofChain(),
        getCategoryUpgradeBacklog(),
      ])
      setReport(readinessPayload)
      setProofGates(gatePayload?.records || readinessPayload?.gates || [])
      setProofChain(chainPayload?.records || [])
      setBacklog(backlogPayload?.records || readinessPayload?.backlog || [])
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load 10/10 category readiness.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const previewSupportExport = useCallback(async () => {
    setRunning('preview')
    setError('')
    try {
      setExportPreview(await getCategoryUpgradeSupportExport())
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to preview the sanitized support export.')
    } finally {
      setRunning('')
    }
  }, [])

  const writeSupportExport = useCallback(async () => {
    setRunning('write')
    setError('')
    try {
      setExportWrite(await writeCategoryUpgradeSupportExport())
      await load()
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to write the sanitized support export.')
    } finally {
      setRunning('')
    }
  }, [load])

  const summary = report?.summary || {}
  const progressByCategory = useMemo(() => {
    const rows = report?.category_progress || []
    return Object.fromEntries(rows.map((row) => [row.category_key, row]))
  }, [report?.category_progress])
  const categories = report?.categories || []
  const claimsToAvoid = report?.claims_to_avoid || []
  const safetyNotes = report?.safety_notes?.length ? report.safety_notes : SAFETY_LABELS
  const documentedCoverage = report?.documented_scope_coverage || {}
  const proofCoverage = documentedCoverage?.records || []

  return (
    <div className="ui-shell__page">
      <PageIntro
        kicker="Proof gated"
        title="10/10 Category Readiness"
        description="Tracks the roadmap gates needed before any category rating can be reviewed. This surface is research-only and does not change trading behavior."
        badge="No execution authority"
      />
      {error ? <ErrorState description={error} onAction={load} /> : null}

      <div className="ui-action-row">
        <StatusBadge tone={statusTone(report?.status)}>{humanize(report?.status || (loading ? 'loading' : 'unavailable'))}</StatusBadge>
        <StatusBadge tone="neutral">Current estimated readiness</StatusBadge>
        <StatusBadge tone="neutral">No proof of alpha</StatusBadge>
        <StatusBadge tone="neutral">No live-money autonomy</StatusBadge>
        <Button onClick={load} disabled={loading || Boolean(running)} variant="primary">{loading ? 'Refreshing...' : 'Refresh'}</Button>
      </div>

      <div className="ui-dashboard-grid ui-dashboard-grid--four">
        <MetricCard label="Proof gates passed" value={`${summary.passed_gate_count || 0}/${summary.gate_count || 9}`} helper="Ratings stay estimates until gates pass." />
        <MetricCard label="Blocked gates" value={summary.blocked_gate_count || 0} helper="Blocked gates prevent rating review." tone={summary.blocked_gate_count ? 'warning' : 'default'} />
        <MetricCard label="Categories ready for review" value={`${summary.ready_category_count || 0}/${summary.category_count || 6}`} helper="Review means evidence check, not a public claim." />
        <MetricCard label="Documented scope added" value={documentedCoverage.all_documented_scope_added ? 'Yes' : 'No'} helper={`${documentedCoverage.complete_count || summary.documented_requirement_complete_count || 0}/${documentedCoverage.requirement_count || summary.documented_requirement_count || 0} checklist items complete.`} />
      </div>

      <SectionCard title="Safety Boundary">
        <div className="ui-action-row">
          {safetyNotes.slice(0, 8).map((note) => <StatusBadge key={note} tone="neutral">{note}</StatusBadge>)}
        </div>
      </SectionCard>

      <SectionCard title="Category Roadmap">
        <DataTable
          columns={[
            { key: 'label', label: 'Category' },
            { key: 'current', label: 'Current estimate', render: (row) => row.current_estimated_readiness },
            {
              key: 'progress',
              label: 'Planning progress',
              render: (row) => formatPercent(progressByCategory[row.key]?.planning_progress_to_10_pct),
            },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'next', label: 'Next action', render: (row) => compactList(row.next_actions, 'Review evidence artifacts.') },
          ]}
          rows={categories}
          empty="No category readiness rows are available."
        />
      </SectionCard>

      <SectionCard title="Proof Gates">
        <DataTable
          columns={[
            { key: 'label', label: 'Gate' },
            { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'claims', label: 'Claims allowed', render: (row) => compactList(row.claims_allowed, 'Planning only') },
            { key: 'warnings', label: 'Warnings', render: (row) => compactList([...(row.blockers || []), ...(row.warnings || [])], 'None') },
          ]}
          rows={proofGates}
          empty="No proof gate records are available."
        />
      </SectionCard>

      <SectionCard title="Proof Chain">
        <DataTable
          columns={[
            { key: 'sequence', label: '#' },
            { key: 'label', label: 'Stage' },
            { key: 'status', label: 'Gate status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            { key: 'boundary', label: 'Proof boundary', render: (row) => row.proof_boundary },
            { key: 'next', label: 'Next safe action', render: (row) => row.safe_next_action },
            { key: 'avoid', label: 'Do not build/claim yet', render: (row) => row.what_not_to_build_yet || row.claim_boundary },
          ]}
          rows={proofChain}
          empty="No proof chain records are available."
        />
      </SectionCard>

      <SectionCard title="Priority Build Sequence">
        <DataTable
          columns={[
            { key: 'sequence', label: '#' },
            { key: 'label', label: 'Build stage' },
            { key: 'priority', label: 'Priority' },
            { key: 'state', label: 'State', render: (row) => <StatusBadge tone={statusTone(row.state)}>{humanize(row.state)}</StatusBadge> },
            { key: 'missing', label: 'Missing proof', render: (row) => compactList([...(row.missing_gates || []), ...(row.missing_extra_proof || [])], 'None') },
            { key: 'avoid', label: 'Do not build yet', render: (row) => row.what_not_to_build_yet },
          ]}
          rows={backlog}
          empty="No build sequence rows are available."
        />
      </SectionCard>

      <div className="ui-dashboard-grid ui-dashboard-grid--two">
        <SectionCard title="Acceptance Checklist Coverage">
          <DataTable
            columns={[
              { key: 'category_key', label: 'Category', render: (row) => humanize(row.category_key) },
              { key: 'description', label: 'Requirement' },
              { key: 'status', label: 'Status', render: (row) => <StatusBadge tone={statusTone(row.status)}>{humanize(row.status)}</StatusBadge> },
            ]}
            rows={proofCoverage.slice(0, 12)}
            empty="No checklist coverage rows are available."
          />
        </SectionCard>

        <SectionCard title="Claims To Avoid">
          <DataTable
            columns={[
              { key: 'claim', label: 'Unsupported claim', render: (row) => humanize(row.claim) },
              { key: 'boundary', label: 'Boundary', render: () => 'Do not use until proof gates and external review support it.' },
            ]}
            rows={claimsToAvoid.map((claim) => ({ claim }))}
            empty="No claim boundaries are available."
          />
        </SectionCard>
      </div>

      <SectionCard title="Sanitized Support Export">
        <div className="ui-action-row">
          <Button onClick={previewSupportExport} disabled={Boolean(running)} variant="secondary">{running === 'preview' ? 'Previewing...' : 'Preview Export'}</Button>
          <Button onClick={writeSupportExport} disabled={Boolean(running)} variant="primary">{running === 'write' ? 'Writing...' : 'Write Export'}</Button>
          {exportWrite ? <StatusBadge tone="positive">Written: {exportWrite.artifact_name || 'support export'}</StatusBadge> : null}
          {exportPreview ? <StatusBadge tone="neutral">Preview sanitized</StatusBadge> : null}
        </div>
        <p className="ui-note">
          The export excludes secrets, credentials, broker records, raw broker payloads, raw logs, account IDs, and raw local paths.
        </p>
      </SectionCard>
    </div>
  )
}

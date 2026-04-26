import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { exportTradeJournalCsv, getNotesSummary, getTradeJournal } from '../api/client'
import ActionBar from '../components/ActionBar'
import Button from '../components/Button'
import Chip from '../components/Chip'
import DataToolbar from '../components/DataToolbar'
import EducationCallout from '../components/EducationCallout'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import FeedbackState from '../components/FeedbackState'
import { SelectField } from '../components/FormFields'
import InlineMeta from '../components/InlineMeta'
import ListTable from '../components/ListTable'
import LoadingBlock from '../components/LoadingBlock'
import MetricCard from '../components/MetricCard'
import SectionCard from '../components/SectionCard'
import StatusBadge from '../components/StatusBadge'
import ValueFlow from '../components/ValueFlow'
import WorkflowArrivalBanner from '../components/WorkflowArrivalBanner'
import WorkflowGuide, { buildWorkflowSteps } from '../components/WorkflowGuide'
import { usePreferences } from '../context/PreferencesContext'
import { useToast } from '../context/ToastContext'
import useDebouncedValue from '../hooks/useDebouncedValue'
import useKeyboardListNavigation from '../hooks/useKeyboardListNavigation'
import usePageActionShortcuts, { focusFirstMatching } from '../hooks/usePageActionShortcuts'
import { buildCapitalPreservationPolicy, buildPromotionGateSummary } from '../utils/capitalPreservation'
import { buildIntradayJournalReview, buildIntradayReviewLens } from '../utils/intradayReviewModel'

const moneyFormatter = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
})

function toNumber(value) {
  const normalized = Number(value)
  return Number.isFinite(normalized) ? normalized : null
}

function formatDollars(value) {
  const normalized = toNumber(value)
  if (normalized === null) return '--'
  return moneyFormatter.format(normalized)
}

function formatPrice(value) {
  const normalized = toNumber(value)
  if (normalized === null) return '--'
  return normalized.toFixed(normalized >= 100 ? 2 : 3)
}

function formatTimestamp(value) {
  if (!value) return '--'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return '--'
  return parsed.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatPercent(value) {
  const normalized = toNumber(value)
  if (normalized === null) return '--'
  return `${normalized.toFixed(1)}%`
}

function formatBasisPoints(value) {
  const normalized = toNumber(value)
  if (normalized === null) return '--'
  return `${normalized.toFixed(1)} bps`
}

function formatSignedBasisPoints(value) {
  const normalized = toNumber(value)
  if (normalized === null) return '--'
  return `${normalized > 0 ? '+' : ''}${normalized.toFixed(1)} bps`
}

function formatUnits(value) {
  const normalized = toNumber(value)
  if (normalized === null) return '--'
  if (Math.abs(normalized - Math.round(normalized)) < 0.0005) {
    return String(Math.round(normalized))
  }
  return normalized.toFixed(3).replace(/\.?0+$/, '')
}

function formatSlippage(bpsValue, dollarValue) {
  const bps = toNumber(bpsValue)
  const dollars = toNumber(dollarValue)
  if (bps === null && dollars === null) return ''
  if (bps !== null && dollars !== null) {
    return `${bps.toFixed(1)} bps / ${formatDollars(dollars)}`
  }
  if (bps !== null) return `${bps.toFixed(1)} bps`
  return formatDollars(dollars)
}

function applyReplayQueryParams(
  params,
  { workflowFrom = 'journal', replaySource = '', replayTitle = '', replayStatus = '' } = {},
) {
  if (workflowFrom) {
    params.set('workflowFrom', String(workflowFrom).trim().toLowerCase())
  } else {
    params.delete('workflowFrom')
  }
  if (replaySource) {
    params.set('replaySource', String(replaySource).trim().toLowerCase())
  } else {
    params.delete('replaySource')
  }
  if (replayTitle) {
    params.set('replayTitle', String(replayTitle).trim())
  } else {
    params.delete('replayTitle')
  }
  if (replayStatus) {
    params.set('replayStatus', String(replayStatus).trim().toLowerCase())
  } else {
    params.delete('replayStatus')
  }
  return params
}

function buildNotesReviewLoopUrl(
  search,
  { completed = 'all', note = null, replaySource = '', replayTitle = '', replayStatus = '' } = {},
) {
  const params = new URLSearchParams(search || '')
  params.set('noteFocus', 'review-loop')
  params.set('noteTag', 'review-loop')
  params.set('journalReturn', '1')
  params.set('journalRepairView', completed === 'completed' ? 'completed' : 'open')
  applyReplayQueryParams(params, { workflowFrom: 'journal', replaySource, replayTitle, replayStatus })

  if (completed && completed !== 'all') {
    params.set('noteCompletion', completed)
  } else {
    params.delete('noteCompletion')
  }

  if (note?.id) {
    params.set('noteId', String(note.id))
  } else {
    params.delete('noteId')
  }
  if (note?.ticker) {
    params.set('noteTicker', String(note.ticker).trim().toUpperCase())
  } else {
    params.delete('noteTicker')
  }
  if (note?.title) {
    params.set('noteTitle', String(note.title).trim())
  } else {
    params.delete('noteTitle')
  }

  const nextQuery = params.toString()
  return `/notes${nextQuery ? `?${nextQuery}` : ''}`
}

function buildDashboardTickerUrl(ticker = '', { replaySource = '', replayTitle = '', replayStatus = '' } = {}) {
  const normalized = String(ticker || '').trim().toUpperCase()
  if (!normalized) return '/'
  const params = new URLSearchParams()
  params.set('ticker', normalized)
  applyReplayQueryParams(params, { workflowFrom: 'journal', replaySource, replayTitle, replayStatus })
  return `/?${params.toString()}`
}

function parseJournalParams(search) {
  const params = new URLSearchParams(search || '')
  const page = Math.max(0, Number.parseInt(String(params.get('journalPage') || '0'), 10) || 0)
  const repairView = String(params.get('journalRepairView') || 'open').trim().toLowerCase()
  return {
    search: String(params.get('journalSearch') || '').trim(),
    resultFilter: String(params.get('journalResult') || 'all').trim().toLowerCase() || 'all',
    directionFilter: String(params.get('journalDirection') || 'all').trim().toLowerCase() || 'all',
    attributionFilter: String(params.get('journalAttribution') || 'all').trim().toLowerCase() || 'all',
    page,
    repairView: repairView === 'completed' ? 'completed' : 'open',
    restored: String(params.get('journalRestored') || '').trim() === '1',
  }
}

function selectRepairView(setRepairView, nextView) {
  setRepairView(nextView === 'completed' ? 'completed' : 'open')
}

const OPEN_REPAIR_ATTRIBUTION_KEYS = new Set([
  'thesis_right_execution_wrong',
  'execution_drift',
  'thesis_wrong_execution_fine',
  'thesis_miss',
  'sizing_wrong',
  'rule_review',
])

const COMPLETED_REPAIR_ATTRIBUTION_KEYS = new Set([
  'clean_win',
  'flat_review',
])

function rowMatchesRepairView(row, repairView) {
  const attributionKey = String(row?.attributionKey || '').trim().toLowerCase()
  const attributionLabel = String(row?.attributionLabel || '').trim().toLowerCase()

  if (repairView === 'completed') {
    return (
      COMPLETED_REPAIR_ATTRIBUTION_KEYS.has(attributionKey) ||
      attributionLabel.includes('clean') ||
      attributionLabel.includes('flat')
    )
  }

  return (
    OPEN_REPAIR_ATTRIBUTION_KEYS.has(attributionKey) ||
    attributionLabel.includes('execution') ||
    attributionLabel.includes('thesis') ||
    attributionLabel.includes('risk') ||
    attributionLabel.includes('sizing') ||
    attributionLabel.includes('rule')
  )
}

function escapeCsvCell(value) {
  const normalized = String(value ?? '')
  if (/[",\n]/.test(normalized)) {
    return `"${normalized.replace(/"/g, '""')}"`
  }
  return normalized
}

function buildJournalCsv(rows) {
  const headers = [
    'Ticker',
    'Instrument',
    'Source',
    'Opened',
    'Closed',
    'Thesis',
    'Thesis Details',
    'Execution',
    'Execution Details',
    'Review',
    'Review Detail',
    'Execution Review',
    'Execution Review Detail',
    'Result',
    'PnL',
    'Max Risk',
    'Position Cost',
    'Target',
    'Invalidation',
    'Slippage',
    'Event',
    'Tags',
  ]

  const lines = rows.map((row) =>
    [
      row.ticker,
      row.instrumentLabel,
      row.sourceLabel,
      row.openedAt,
      row.closedAt,
      row.thesisTitle,
      row.thesisDetailParts.join(' | '),
      row.executionTitle,
      row.executionDetailParts.join(' | '),
      row.attributionLabel,
      row.attributionDetail || row.resultMeta,
      row.executionReviewLabel,
      row.executionReviewDetail,
      row.resultLabel,
      row.pnlLabel,
      row.maxRiskLabel,
      row.positionCostLabel,
      row.targetLabel,
      row.invalidationLabel,
      row.slippageLabel,
      row.eventLabel,
      row.reviewTags.join(' | '),
    ]
      .map(escapeCsvCell)
      .join(','),
  )

  return [headers.map(escapeCsvCell).join(','), ...lines].join('\n')
}

function buildResultMeta(row, pnl) {
  if (row.attribution_detail) {
    return row.attribution_detail
  }
  if (row.event_risk) {
    return row.event_label || row.event_reason || 'Event risk flagged'
  }
  if (pnl > 0) {
    return 'Closed green relative to entry thesis.'
  }
  if (pnl < 0) {
    return row.reject_reason || 'Review invalidation, timing, and exit discipline.'
  }
  return 'Closed flat. Review costs, timing, and conviction quality.'
}

function normalizeJournalRow(row, index) {
  const instrumentType = String(row.instrument_type || '').trim().toLowerCase()
  const instrumentLabel =
    row.instrument_label ||
    (instrumentType === 'equity'
      ? 'Equity'
      : row.contract_symbol
        ? 'Listed option'
        : 'Trade')
  const pnl = toNumber(row.pnl_dollars ?? row.realized_pnl) ?? 0
  const resultLabel = row.result_label || (pnl > 0 ? 'Win' : pnl < 0 ? 'Loss' : 'Flat')
  const resultTone = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : 'neutral'
  const thesisTitle =
    row.verdict ||
    row.direction ||
    row.trade_decision ||
    row.trade_status ||
    'Recorded trade'
  const thesisDetail = [
    row.setup_grade,
    row.alignment_label,
    row.conviction_label,
    row.interval,
  ].filter(Boolean)
  const executionTitle =
    row.contract_symbol ||
    (instrumentType === 'equity' ? `Spot ${row.ticker || ''}`.trim() : 'Trade contract')
  const executionDetail = [
    row.order_type ? String(row.order_type).toUpperCase() : null,
    row.time_in_force ? String(row.time_in_force).toUpperCase() : null,
    row.contract_expiration ? `Exp ${row.contract_expiration}` : null,
  ].filter(Boolean)
  const reviewTags = [
    row.option_strategy || null,
    row.option_right ? String(row.option_right).toUpperCase() : null,
    row.event_risk ? 'Event risk' : null,
  ].filter(Boolean)
  const attributionLabel = row.attribution_label || (pnl > 0 ? 'Clean win' : pnl < 0 ? 'Thesis miss' : 'Flat review')
  const attributionTone = row.attribution_tone || (pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : 'neutral')
  const executionReviewLabel = row.execution_review_label || 'Manual fill record'
  const executionReviewTone = row.execution_review_tone || 'neutral'
  const attributionKey = row.attribution_key || ''
  const reviewLoopCompletion = rowMatchesRepairView({ attributionKey, attributionLabel }, 'completed')
    ? 'completed'
    : 'open'
  const reviewLoopTitle = `${row.ticker || 'Desk'} ${attributionLabel}`.trim()

  return {
    key: row.trade_id || row.order_id || row.contract_symbol || `${row.ticker || 'trade'}-${index}`,
    ticker: row.ticker || '--',
    instrumentLabel,
    instrumentType,
    thesisTitle,
    thesisDetailParts: thesisDetail,
    executionTitle,
    executionDetailParts: executionDetail,
    openedAt: formatTimestamp(row.opened_at || row.timestamp),
    closedAt: formatTimestamp(row.closed_at || row.timestamp),
    entryValue: formatPrice(row.entry_contract_mid ?? row.contract_mid_at_open),
    exitValue: formatPrice(row.close_contract_mid ?? row.contract_mid_at_close),
    pnl,
    pnlLabel: formatDollars(pnl),
    resultLabel,
    resultTone,
    resultMeta: buildResultMeta(row, pnl),
    maxRiskLabel: formatDollars(row.max_risk_dollars),
    positionCostLabel: formatDollars(row.position_cost),
    targetLabel: formatPrice(row.target_price),
    invalidationLabel: formatPrice(row.invalidation_price),
    contractCount: formatUnits(row.suggested_contracts),
    reviewTags,
    sourceLabel: row.journal_source === 'legacy' ? 'Legacy history' : 'Closed trade',
    setupScore: toNumber(row.setup_score),
    eventLabel: row.event_label || '',
    attributionKey,
    attributionLabel,
    attributionTone,
    attributionDetail: row.attribution_detail || '',
    executionReviewLabel,
    executionReviewTone,
    executionReviewDetail: row.execution_review_detail || 'No expected versus realized fill record was saved for this close.',
    slippageLabel: formatSlippage(row.fill_slippage_bps, row.fill_slippage_dollars),
    reviewLoopCompletion,
    reviewLoopTitle,
    openedAtRaw: row.opened_at || row.timestamp || '',
    closedAtRaw: row.closed_at || row.timestamp || '',
    timestampRaw: row.timestamp || row.closed_at || row.opened_at || '',
    fillSlippageBps: row.fill_slippage_bps,
  }
}

export default function JournalPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const initialJournalParams = useMemo(() => parseJournalParams(location.search), [location.search])
  const hasMountedFilterResetRef = useRef(false)
  const [journal, setJournal] = useState([])
  const [validationSnapshot, setValidationSnapshot] = useState(null)
  const [notesSummary, setNotesSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState(() => initialJournalParams.search)
  const debouncedSearch = useDebouncedValue(search, 350)
  const [resultFilter, setResultFilter] = useState(() => initialJournalParams.resultFilter)
  const [directionFilter, setDirectionFilter] = useState(() => initialJournalParams.directionFilter)
const [attributionFilter, setAttributionFilter] = useState(() => initialJournalParams.attributionFilter)
const [page, setPage] = useState(() => initialJournalParams.page)
const [repairView, setRepairView] = useState(() => initialJournalParams.repairView)
const [dismissedArrivalKey, setDismissedArrivalKey] = useState('')
const [total, setTotal] = useState(0)
  const pageSize = 25
  const { preferences } = usePreferences()
  const { pushToast } = useToast()
  const journalTableNavigation = useKeyboardListNavigation({ selector: '.table-row-action', layout: 'list' })

  usePageActionShortcuts({
    focusInput: () => focusFirstMatching(['#journal-search-input']),
    focusResult: () => focusFirstMatching(['.ui-list-table .table-row-action']),
  })

  const loadJournal = useCallback(async () => {
    try {
      setError('')
      setLoading(true)
      const [payload, noteSummaryPayload] = await Promise.all([
        getTradeJournal({
          search: debouncedSearch,
          limit: pageSize,
          offset: page * pageSize,
          resultFilter,
          directionFilter,
          attributionFilter,
        }),
        getNotesSummary(),
      ])
      setJournal(payload.journal || [])
      setValidationSnapshot(payload.validation_snapshot || null)
      setTotal(Number(payload.total || 0))
      setNotesSummary(noteSummaryPayload)
    } catch (err) {
      setValidationSnapshot(null)
      setError(err?.response?.data?.detail || err.message || 'Failed to load trade journal.')
    } finally {
      setLoading(false)
    }
  }, [attributionFilter, debouncedSearch, directionFilter, page, resultFilter, pageSize])

  useEffect(() => {
    loadJournal()
  }, [loadJournal])

  useEffect(() => {
    if (!hasMountedFilterResetRef.current) {
      hasMountedFilterResetRef.current = true
      return
    }
    setPage(0)
  }, [debouncedSearch, resultFilter, directionFilter, attributionFilter])

  useEffect(() => {
    const params = new URLSearchParams(location.search || '')

    if (search) {
      params.set('journalSearch', search)
    } else {
      params.delete('journalSearch')
    }

    if (resultFilter && resultFilter !== 'all') {
      params.set('journalResult', resultFilter)
    } else {
      params.delete('journalResult')
    }

    if (directionFilter && directionFilter !== 'all') {
      params.set('journalDirection', directionFilter)
    } else {
      params.delete('journalDirection')
    }

    if (attributionFilter && attributionFilter !== 'all') {
      params.set('journalAttribution', attributionFilter)
    } else {
      params.delete('journalAttribution')
    }

    if (page > 0) {
      params.set('journalPage', String(page))
    } else {
      params.delete('journalPage')
    }

    if (repairView && repairView !== 'open') {
      params.set('journalRepairView', repairView)
    } else {
      params.delete('journalRepairView')
    }

    params.delete('journalRestored')

    const nextQuery = params.toString()
    const nextSearch = nextQuery ? `?${nextQuery}` : ''
    if (nextSearch !== location.search) {
      navigate(`${location.pathname}${nextSearch}`, { replace: true })
    }
  }, [
    attributionFilter,
    directionFilter,
    location.pathname,
    location.search,
    navigate,
    page,
    repairView,
    resultFilter,
    search,
  ])

  const rows = useMemo(
    () =>
      journal.map((row, index) => {
        const normalized = normalizeJournalRow(row, index)
        const intradayReview = buildIntradayJournalReview(normalized, {
          tradingStyle: preferences?.tradingStyle,
          preferences,
        })
        return intradayReview ? { ...normalized, intradayReview } : normalized
      }),
    [journal, preferences],
  )

  const metrics = useMemo(() => {
    const pnl = rows.reduce((sum, row) => sum + row.pnl, 0)
    const winners = rows.filter((row) => row.pnl > 0).length
    const executionDrifts = rows.filter((row) => ['thesis_right_execution_wrong', 'execution_drift'].includes(row.attributionKey)).length
    const thesisMisses = rows.filter((row) => ['thesis_wrong_execution_fine', 'thesis_miss'].includes(row.attributionKey)).length
    const riskReviews = rows.filter((row) => ['sizing_wrong', 'rule_review'].includes(row.attributionKey)).length
    const winRate = rows.length ? (winners / rows.length) * 100 : 0
    return [
      { label: 'Rows', value: rows.length, helper: 'Visible reviewed trades.' },
      { label: 'Visible PnL', value: pnl.toFixed(2), tone: pnl >= 0 ? 'positive' : 'negative', helper: formatDollars(pnl) },
      { label: 'Win rate', value: formatPercent(winRate), tone: winRate >= 50 ? 'positive' : 'neutral', helper: `${winners} winners in view` },
      { label: 'Execution drifts', value: executionDrifts, tone: executionDrifts > 0 ? 'warning' : 'positive', helper: 'Rows where fill quality deserves review.' },
      { label: 'Thesis misses', value: thesisMisses, tone: thesisMisses > 0 ? 'negative' : 'positive', helper: 'Rows where the idea failed with controlled fills.' },
      { label: 'Risk reviews', value: riskReviews, tone: riskReviews > 0 ? 'warning' : 'positive', helper: 'Sizing or rule-quality reviews in the current page.' },
    ]
  }, [rows])
  const repairLoopSummary = notesSummary?.review_loop_summary || { open_count: 0, resolved_count: 0, latest_resolved: null }
  const latestResolvedRepair = repairLoopSummary.latest_resolved || null
  const repairViewLabel = repairView === 'completed' ? 'Repairs cleared' : 'Open repairs'
  const repairLensPaused = attributionFilter !== 'all'
  const validationScorecards = Array.isArray(validationSnapshot?.scorecards) ? validationSnapshot.scorecards : []
  const validationRouteQuality = validationSnapshot?.route_quality || {}
  const validationBoardHistory = validationSnapshot?.board_snapshot_history || { count: 0, items: [] }
  const validationBoardItems = Array.isArray(validationBoardHistory.items) ? validationBoardHistory.items : []
  const replayComparisons = validationSnapshot?.replay_comparisons || {}
  const boardOutcomeReplay = replayComparisons.board_outcomes || { count: 0, resolved_count: 0, open_count: 0, items: [] }
  const boardOutcomeItems = Array.isArray(boardOutcomeReplay.items) ? boardOutcomeReplay.items : []
  const paperLiveReplay = replayComparisons.paper_live_slippage || { count: 0, items: [] }
  const paperLiveItems = Array.isArray(paperLiveReplay.items) ? paperLiveReplay.items : []
  const capitalPreservationPolicy = useMemo(
    () =>
      buildCapitalPreservationPolicy({
        preferences,
        tradeTicket: null,
        defaults: {
          accountSize: preferences?.defaultAccountSize,
          riskPercent: preferences?.defaultRiskPercent,
        },
      }),
    [preferences],
  )
  const promotionGateSummary = useMemo(
    () =>
      buildPromotionGateSummary({
        validationSnapshot,
        policy: capitalPreservationPolicy.promotionGate,
    }),
    [capitalPreservationPolicy, validationSnapshot],
  )
  const intradayReview = useMemo(
    () =>
      buildIntradayReviewLens({
        tradingStyle: preferences?.tradingStyle,
        preferences,
        journalRows: rows,
        validationSnapshot,
        notesSummary,
      }),
    [notesSummary, preferences, rows, validationSnapshot],
  )
  const repairLens = useMemo(() => {
    if (repairLensPaused) {
      return { active: false, rows, label: '', detail: '' }
    }

    const filteredRows = rows.filter((row) => rowMatchesRepairView(row, repairView))
    return {
      active: true,
      rows: filteredRows,
      label: repairView === 'completed' ? 'Resolution lens' : 'Repair lens',
      detail:
        repairView === 'completed'
          ? 'Showing clean-win and flat-review rows from the current page while the repair loop is focused on cleared work.'
          : 'Showing execution, thesis, and risk-review rows from the current page while the repair loop is focused on open work.',
    }
  }, [repairLensPaused, repairView, rows])
  const displayRows = repairLens.rows
  const journalArrivalKey = initialJournalParams.restored
    ? `${repairView}|${repairLensPaused ? 'paused' : 'active'}`
    : ''

  useEffect(() => {
    setDismissedArrivalKey('')
  }, [journalArrivalKey])

  const journalArrivalContext = initialJournalParams.restored
    ? {
        tone: repairLensPaused ? 'warning' : 'info',
        title: repairLensPaused ? 'Journal review restored, but the repair lens is paused' : 'Journal review restored',
        detail: repairLensPaused
          ? `You came back from Notes with ${repairViewLabel.toLowerCase()} selected, but a direct review filter is taking priority. Clear that filter or reopen the note flow to keep the same repair thread in view.`
          : `You returned from Notes with ${repairViewLabel.toLowerCase()} preserved, so thesis, execution, and rule changes stay attached to the same repair loop instead of resetting to a generic journal pass.`,
        actions: [
          {
            label: repairView === 'completed' ? 'Reopen cleared notes' : 'Reopen repair notes',
            onClick: () =>
              navigate(
                buildNotesReviewLoopUrl(location.search, {
                  completed: repairView,
                  note: repairView === 'completed' ? latestResolvedRepair : null,
                  replaySource: 'journal_repair_loop',
                  replayTitle: repairView === 'completed' ? 'Journal cleared repairs' : 'Journal open repairs',
                  replayStatus: repairView === 'completed' ? 'resolved' : 'open',
                }),
              ),
          },
          ...(repairLensPaused
            ? [
                {
                  label: 'Reapply repair lens',
                  onClick: () => setAttributionFilter('all'),
                  variant: 'subtle',
                },
              ]
            : []),
        ],
      }
    : null

  if (loading) {
    return (
      <LoadingBlock
        label="Loading journal review"
        detail="Pulling reviewed trades, replay artifacts, and repair-loop context so attribution opens with the current evidence."
      />
    )
  }

  return (
    <>
      {error ? (
        <ErrorState
          title="Journal review unavailable"
          description={error}
          actionLabel="Reload journal"
          onAction={loadJournal}
        />
      ) : null}
      {debouncedSearch !== search ? (
        <FeedbackState
          compact
          tone="info"
          eyebrow="Search"
          title="Updating journal search"
          description="Filtering reviewed trades so the current attribution view matches your latest query."
          role="status"
        />
      ) : null}
      {journalArrivalContext && dismissedArrivalKey !== journalArrivalKey ? (
        <WorkflowArrivalBanner
          title={journalArrivalContext.title}
          detail={journalArrivalContext.detail}
          tone={journalArrivalContext.tone}
          actions={journalArrivalContext.actions}
          onDismiss={() => setDismissedArrivalKey(journalArrivalKey)}
        />
      ) : null}
      <WorkflowGuide
        showSteps={false}
        phaseLabel="Phase 4 - Review and repair"
        phaseTone="warning"
        title={
          intradayReview.active
            ? 'Use the journal to explain same-session outcomes, not just to record them.'
            : 'Use the journal to explain outcomes, not just to record them.'
        }
        description={
          intradayReview.active
            ? 'This is where the same-session repair loop separates board quality, fill quality, and cleanup discipline. A useful journal entry should make tomorrow’s intraday rule clearer.'
            : 'This is where the replay loop separates thesis quality, execution quality, and discipline quality. A useful journal entry should make the next rule clearer.'
        }
        steps={buildWorkflowSteps(3)}
        cards={[
          {
            label: 'Use this page for',
            value: intradayReview.active
              ? 'Separate entry quality, execution quality, and cleanup discipline after the trade.'
              : 'Separate thesis, execution, and discipline after the trade.',
            detail: intradayReview.active
              ? 'Read the result through session-aware attribution so the lesson stays tied to the actual intraday failure mode.'
              : 'Read the result through attribution and repair state so the lesson is tied to the actual failure mode.',
          },
          {
            label: 'Best next move',
            value: intradayReview.active
              ? 'Turn repeated same-session mistakes into explicit repair notes and session rules.'
              : 'Turn repeated mistakes into explicit repair notes and rule changes.',
            detail: intradayReview.active
              ? 'The journal is strongest when it feeds the same-session repair loop instead of stopping at post-hoc storytelling.'
              : 'The journal is strongest when it feeds the repair loop, not when it stops at post-hoc storytelling.',
            tone: 'positive',
          },
          {
            label: 'Do not ignore',
            value: 'A green trade can still be a bad process trade.',
            detail: intradayReview.active
              ? 'If the entry was chased, the fill drifted, or the close cleanup was late, the journal should say so even when PnL finished positive.'
              : 'If the thesis, execution path, or sizing rule was wrong, the journal should say so even when PnL finished positive.',
            tone: 'warning',
          },
        ]}
      />
      {intradayReview.active ? (
        <SectionCard
          eyebrow="Same-session review"
          title="Intraday review loop"
          subtitle={intradayReview.guideDetail}
        >
          <section className="metrics-grid">
            {intradayReview.journalCards.map((item) => (
              <MetricCard key={item.label} {...item} />
            ))}
          </section>
        </SectionCard>
      ) : null}
      <section className="metrics-grid">
        {metrics.map((item) => <MetricCard key={item.label} {...item} />)}
      </section>
      <EducationCallout
        topic="journal-review"
        title={intradayReview.active ? 'Review the same-session process, not just the PnL.' : 'Review the thesis, not just the PnL.'}
        body={
          intradayReview.active
            ? 'The journal is now same-session aware. Use it to separate board quality, fill quality, and cleanup discipline instead of collapsing everything into win or loss.'
            : 'The journal is now attribution-aware. Use it to separate idea quality, execution quality, and discipline quality instead of collapsing everything into win or loss.'
        }
        bullets={intradayReview.active
          ? [
              'An opening-range win can still be an opening-range chase review.',
              'A midday loss often teaches patience and session selection more than raw signal direction.',
            ]
          : [
              'A green trade can still be an execution-drift review.',
              'A red trade with a clean fill usually points back to the thesis, not the route.',
            ]}
        linkLabel="Open journal guide"
      />
      <SectionCard
        eyebrow="Repair flow"
        title={intradayReview.active ? intradayReview.labels.repairLoop : 'Repair loop'}
        subtitle={
          intradayReview.active
            ? `Open versus cleared same-session repair notes, so historical review stays aligned with the live desk repair signal. Current focus: ${repairViewLabel}.`
            : `Open versus cleared repair notes, so historical review stays aligned with the live desk repair signal. Current focus: ${repairViewLabel}.`
        }
        actions={(
          <ActionBar compact>
            <Button
              type="button"
              variant={repairView === 'open' ? 'subtle' : 'ghost'}
              size="sm"
              onClick={() => {
                selectRepairView(setRepairView, 'open')
                navigate(
                  buildNotesReviewLoopUrl(location.search, {
                    completed: 'open',
                    replaySource: 'journal_repair_loop',
                    replayTitle: 'Journal open repairs',
                    replayStatus: 'open',
                  }),
                )
              }}
            >
              Open repairs
            </Button>
            <Button
              type="button"
              variant={repairView === 'completed' ? 'subtle' : 'ghost'}
              size="sm"
              onClick={() => {
                selectRepairView(setRepairView, 'completed')
                navigate(
                  buildNotesReviewLoopUrl(location.search, {
                    completed: 'completed',
                    replaySource: 'journal_repair_loop',
                    replayTitle: 'Journal cleared repairs',
                    replayStatus: 'resolved',
                  }),
                )
              }}
            >
              Repairs cleared
            </Button>
            {latestResolvedRepair ? (
              <Button
                type="button"
                variant={repairView === 'completed' ? 'subtle' : 'ghost'}
                size="sm"
                onClick={() => {
                  selectRepairView(setRepairView, 'completed')
                  navigate(
                    buildNotesReviewLoopUrl(location.search, {
                      completed: 'completed',
                      note: latestResolvedRepair,
                      replaySource: 'journal_repair_loop',
                      replayTitle: latestResolvedRepair?.title || 'Latest cleared repair',
                      replayStatus: 'resolved',
                    }),
                  )
                }}
              >
                Open latest clear
              </Button>
            ) : null}
          </ActionBar>
        )}
      >
        <section className="metrics-grid">
          <button
            type="button"
            className="metric-card-button"
            onClick={() => selectRepairView(setRepairView, 'open')}
          >
            <MetricCard
              label="Open repairs"
              value={repairLoopSummary.open_count ?? 0}
              tone={repairView === 'open' ? 'warning' : (repairLoopSummary.open_count ?? 0) > 0 ? 'warning' : 'positive'}
              helper="Active unresolved repair notes still shaping the desk."
            />
          </button>
          <button
            type="button"
            className="metric-card-button"
            onClick={() => selectRepairView(setRepairView, 'completed')}
          >
            <MetricCard
              label="Repairs cleared"
              value={repairLoopSummary.resolved_count ?? 0}
              tone={repairView === 'completed' ? 'positive' : (repairLoopSummary.resolved_count ?? 0) > 0 ? 'positive' : 'neutral'}
              helper="Completed repair notes that were explicitly cleared."
            />
          </button>
          <button
            type="button"
            className="metric-card-button"
            onClick={() => selectRepairView(setRepairView, 'completed')}
            disabled={!latestResolvedRepair}
          >
            <MetricCard
              label="Latest clear"
              value={latestResolvedRepair?.ticker || '--'}
              tone={latestResolvedRepair ? 'positive' : 'neutral'}
              helper={
                latestResolvedRepair
                  ? `${latestResolvedRepair.title || 'Resolved repair'} - ${formatTimestamp(latestResolvedRepair.updated_at)}`
                  : 'No resolved repair note has been recorded yet.'
              }
            />
          </button>
        </section>
      </SectionCard>
      <SectionCard
        eyebrow="Validation layer"
        title={intradayReview.active ? intradayReview.labels.replayEvidence : 'Replay evidence'}
        subtitle={
          intradayReview.active
            ? 'Filtered journal scorecards plus saved intraday boards now feeding same-session replay review.'
            : 'Filtered journal scorecards plus saved-board history now feeding replay review.'
        }
        actions={(
          <ActionBar compact>
            <StatusBadge value={`${validationScorecards.length} scorecards`} />
            <Chip tone="neutral" size="sm">{`${validationBoardHistory.count ?? 0} ${intradayReview.active ? 'saved intraday boards' : 'saved boards'}`}</Chip>
          </ActionBar>
        )}
      >
        <section className="metrics-grid">
          <MetricCard
            label="Paper gate"
            value={promotionGateSummary.label}
            tone={promotionGateSummary.tone}
            helper={promotionGateSummary.action}
          />
          <MetricCard
            label="Replay sample"
            value={`${promotionGateSummary.resolvedCount ?? 0} resolved`}
            tone={promotionGateSummary.tone === 'negative' ? 'negative' : promotionGateSummary.tone === 'warning' ? 'warning' : 'neutral'}
            helper={`${promotionGateSummary.openCount ?? 0} open | ${promotionGateSummary.winRateLabel || '--'} win`}
          />
          <MetricCard
            label="Live drift"
            value={promotionGateSummary.averageAbsSlippageLabel || '--'}
            tone={promotionGateSummary.tone === 'negative' ? 'negative' : promotionGateSummary.tone === 'warning' ? 'warning' : 'positive'}
            helper={`Worst ${promotionGateSummary.worstAbsSlippageLabel || '--'} | ${promotionGateSummary.policySummary || 'Policy unavailable'}`}
          />
        </section>
        <div className="chart-market-panel__footnote">{promotionGateSummary.basis}</div>
        <section className="metrics-grid">
          {validationScorecards.length ? validationScorecards.map((card) => (
            <MetricCard
              key={card.key || card.label}
              label={card.label || 'Validation'}
              value={card.value || '--'}
              tone={card.tone || 'neutral'}
              helper={card.helper || card.detail || ''}
            />
          )) : (
            <EmptyState
              title="No replay scorecards yet"
              description={
                intradayReview.active
                  ? 'Start here by closing intraday trades and saving boards. This section turns them into same-session replay evidence.'
                  : 'Start here by closing reviewed trades and saving boards. This section turns them into replay evidence.'
              }
              actionLabel="Open trades"
              onAction={() => navigate('/trades')}
              secondaryActionLabel="Open watchlist"
              onSecondaryAction={() => navigate('/watchlist')}
            />
          )}
        </section>
        <section className="content-grid">
          <SectionCard eyebrow="Execution lens" title="Route quality" subtitle="Execution-review rollup for the current filtered journal slice.">
            <div className="key-value-grid">
              <div className="key-value-row"><span>Clean fills</span><strong>{validationRouteQuality.clean_fill_count ?? 0}</strong></div>
              <div className="key-value-row"><span>Slipped fills</span><strong>{validationRouteQuality.slipped_fill_count ?? 0}</strong></div>
              <div className="key-value-row"><span>Fragile fills</span><strong>{validationRouteQuality.fragile_fill_count ?? 0}</strong></div>
              <div className="key-value-row"><span>Rejected routes</span><strong>{validationRouteQuality.rejected_route_count ?? 0}</strong></div>
              <div className="key-value-row"><span>Partial fills</span><strong>{validationRouteQuality.partial_fill_count ?? 0}</strong></div>
              <div className="key-value-row"><span>Avg abs slippage</span><strong>{formatBasisPoints(validationRouteQuality.average_abs_slippage_bps)}</strong></div>
              <div className="key-value-row">
                <span>Latest execution review</span>
                <strong>
                  {validationRouteQuality.latest_execution_review
                    ? `${validationRouteQuality.latest_execution_review.ticker || '--'} - ${validationRouteQuality.latest_execution_review.label || 'Review'}`
                    : 'No saved review'}
                </strong>
              </div>
            </div>
          </SectionCard>
          <SectionCard
            eyebrow="Saved context"
            title={intradayReview.active ? intradayReview.labels.savedBoards : 'Saved board history'}
            subtitle={
              intradayReview.active
                ? 'Recent watchlist and compare boards captured as same-session replay evidence.'
                : 'Recent watchlist and compare boards captured as replay evidence.'
            }
          >
            <ListTable>
              <table className="signal-table ui-list-table">
                <caption className="ui-visually-hidden">Saved board history in journal review</caption>
                <thead>
                  <tr>
                    <th scope="col">Board</th>
                    <th scope="col">Leader</th>
                    <th scope="col">Mix</th>
                    <th scope="col">Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {validationBoardItems.length ? validationBoardItems.map((item, index) => (
                    <tr key={item.id || `${item.name || 'snapshot'}-${index}`}>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">{item.board_name || item.name || 'Saved board'}</div>
                          <div className="ui-list-cell__meta">
                            {[item.page ? String(item.page).toUpperCase() : null, item.interval ? String(item.interval).toUpperCase() : null].filter(Boolean).join(' / ') || 'Saved workspace'}
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">{item.leader_ticker || '--'}</div>
                          <div className="ui-list-cell__meta">
                            {item.leader_label || 'Leader snapshot'}
                            {item.leader_score !== null && item.leader_score !== undefined ? ` | ${Number(item.leader_score).toFixed(1)}` : ''}
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">{`${item.promote_count ?? 0} / ${item.review_count ?? 0} / ${item.stand_down_count ?? 0}`}</div>
                          <div className="ui-list-cell__meta">{`${item.candidate_count ?? 0} candidates | ${item.event_window_count ?? 0} event | ${item.fragile_execution_count ?? 0} fragile exec`}</div>
                        </div>
                      </td>
                      <td>{formatTimestamp(item.updated_at)}</td>
                    </tr>
                  )) : (
                    <tr>
                      <td colSpan={4}>
                        <EmptyState
                          title={intradayReview.active ? 'No saved intraday boards yet' : 'No saved boards yet'}
                          description={
                            intradayReview.active
                              ? 'Start here by saving an intraday board from Watchlist or Compare. This section keeps the same-session board history.'
                              : 'Start here by saving a board from Watchlist or Compare. This section keeps the saved-board history.'
                          }
                          actionLabel="Open watchlist"
                          onAction={() => navigate('/watchlist')}
                          secondaryActionLabel="Open compare"
                          onSecondaryAction={() => navigate('/compare')}
                        />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </ListTable>
          </SectionCard>
        </section>
        <section className="content-grid">
          <SectionCard
            eyebrow="Outcome replay"
            title={intradayReview.active ? intradayReview.labels.boardReplay : 'Board replay'}
            subtitle={
              intradayReview.active
                ? 'Saved intraday leaders compared with the first later closed trade on that ticker.'
                : 'Saved board leaders compared with the first later closed trade on that ticker.'
            }
            actions={(
              <ActionBar compact>
                <StatusBadge value={`${boardOutcomeReplay.resolved_count ?? 0} resolved`} />
                <Chip tone="neutral" size="sm">{`${boardOutcomeReplay.open_count ?? 0} awaiting`}</Chip>
              </ActionBar>
            )}
          >
            <ListTable>
              <table className="signal-table ui-list-table">
                <caption className="ui-visually-hidden">Board outcome replay in journal review</caption>
                <thead>
                  <tr>
                    <th scope="col">Leader</th>
                    <th scope="col">Outcome</th>
                    <th scope="col">Review</th>
                    <th scope="col">Saved / resolved</th>
                  </tr>
                </thead>
                <tbody>
                  {boardOutcomeItems.length ? boardOutcomeItems.map((item, index) => (
                    <tr key={`${item.leader_ticker || 'leader'}-${item.saved_at || index}`}>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">{item.leader_ticker || '--'}</div>
                          <div className="ui-list-cell__meta">{item.board_name || 'Saved board'}</div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__badges">
                            <StatusBadge value={item.status_label || 'Replay'} />
                          </div>
                          <div className="ui-list-cell__meta">
                            {item.result_label ? `${item.result_label} | ${formatDollars(item.pnl_dollars)}` : 'No resolved close yet'}
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">{item.attribution_label || item.execution_review_label || 'Awaiting review'}</div>
                          <div className="ui-list-cell__meta">{item.detail || 'Replay detail will show up once the leader resolves.'}</div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__meta">Saved {formatTimestamp(item.saved_at)}</div>
                          <div className="ui-list-cell__meta">Resolved {formatTimestamp(item.resolved_at)}</div>
                        </div>
                      </td>
                    </tr>
                  )) : (
                    <tr>
                      <td colSpan={4}>
                        <EmptyState
                          title={intradayReview.active ? 'No same-session replay yet' : 'No board replay yet'}
                          description={
                            intradayReview.active
                              ? 'Start here by saving an intraday board, then let a leader resolve into a closed trade so this replay view has same-session evidence to show.'
                              : 'Start here by saving a board, then let a leader resolve into a closed trade so this replay view has evidence to show.'
                          }
                          actionLabel="Open trades"
                          onAction={() => navigate('/trades')}
                          secondaryActionLabel="Open watchlist"
                          onSecondaryAction={() => navigate('/watchlist')}
                        />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </ListTable>
          </SectionCard>
          <SectionCard
            eyebrow="Fill drift"
            title="Paper vs live fill drift"
            subtitle="Expected route prices compared with realized fills for journal rows that carry both values."
            actions={(
              <ActionBar compact>
                <Chip tone="neutral" size="sm">{`${paperLiveReplay.count ?? 0} comparable fills`}</Chip>
                <StatusBadge value={`Avg ${formatSignedBasisPoints(paperLiveReplay.average_signed_slippage_bps)}`} />
              </ActionBar>
            )}
          >
            <div className="key-value-grid">
              <div className="key-value-row"><span>Average signed drift</span><strong>{formatSignedBasisPoints(paperLiveReplay.average_signed_slippage_bps)}</strong></div>
              <div className="key-value-row"><span>Average absolute drift</span><strong>{formatBasisPoints(paperLiveReplay.average_abs_slippage_bps)}</strong></div>
              <div className="key-value-row"><span>Worst absolute drift</span><strong>{formatBasisPoints(paperLiveReplay.worst_abs_slippage_bps)}</strong></div>
            </div>
            <ListTable>
              <table className="signal-table ui-list-table">
                <caption className="ui-visually-hidden">Journal paper versus live slippage replay</caption>
                <thead>
                  <tr>
                    <th scope="col">Ticker</th>
                    <th scope="col">Expected</th>
                    <th scope="col">Actual</th>
                    <th scope="col">Delta</th>
                    <th scope="col">Review</th>
                  </tr>
                </thead>
                <tbody>
                  {paperLiveItems.length ? paperLiveItems.map((item, index) => (
                    <tr key={`${item.ticker || 'fill'}-${item.closed_at || index}`}>
                      <td>{item.ticker || '--'}</td>
                      <td>{formatPrice(item.expected_fill_price)}</td>
                      <td>{formatPrice(item.actual_fill_price)}</td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">{formatSignedBasisPoints(item.slippage_bps)}</div>
                          <div className="ui-list-cell__meta">{formatDollars(item.slippage_dollars)}</div>
                        </div>
                      </td>
                      <td>
                        <div className="ui-list-cell">
                          <div className="ui-list-cell__title">{item.execution_review_label || 'Fill review'}</div>
                          <div className="ui-list-cell__meta">{formatTimestamp(item.closed_at)}</div>
                        </div>
                      </td>
                    </tr>
                  )) : (
                    <tr>
                      <td colSpan={5}>
                        <EmptyState
                          title="No comparable fill drift yet"
                          description="Once expected and actual fill prices are both saved on closes, this replay block will quantify the delta."
                        />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </ListTable>
          </SectionCard>
        </section>
      </SectionCard>
      <SectionCard
        eyebrow="Attribution ledger"
        title="Trade journal"
        subtitle={
          repairLens.active
            ? `Closed-trade review with thesis, execution, and post-trade context. ${repairLens.label} is active for ${repairViewLabel.toLowerCase()}.`
            : repairLensPaused
              ? `Closed-trade review with thesis, execution, and post-trade context. ${repairViewLabel} is selected, but direct review filters take priority right now.`
              : 'Closed-trade review with thesis, execution, and post-trade context.'
        }
        actions={(
          <DataToolbar
            searchInputId="journal-search-input"
            searchValue={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search ticker, contract, or setup"
            searchDelayLabel="Search is debounced for smoother journal queries."
            actions={(
              <>
                <SelectField ariaLabel="Filter journal by direction" value={directionFilter} onChange={(event) => setDirectionFilter(event.target.value)} className="data-toolbar__field">
                  <option value="all">All directions</option>
                  <option value="CALL">CALL</option>
                  <option value="PUT">PUT</option>
                </SelectField>
                <SelectField ariaLabel="Filter journal by result" value={resultFilter} onChange={(event) => setResultFilter(event.target.value)} className="data-toolbar__field">
                  <option value="all">All results</option>
                  <option value="win">Wins</option>
                  <option value="loss">Losses</option>
                </SelectField>
                <SelectField ariaLabel="Filter journal by review outcome" value={attributionFilter} onChange={(event) => setAttributionFilter(event.target.value)} className="data-toolbar__field">
                  <option value="all">All reviews</option>
                  <option value="execution">Execution drifts</option>
                  <option value="thesis">Thesis misses</option>
                  <option value="risk">Risk reviews</option>
                  <option value="clean">Clean wins</option>
                  <option value="flat">Flat reviews</option>
                </SelectField>
                <SelectField ariaLabel="Choose repair lens view" value={repairView} onChange={(event) => selectRepairView(setRepairView, event.target.value)} className="data-toolbar__field">
                  <option value="open">Repair lens: open</option>
                  <option value="completed">Repair lens: cleared</option>
                </SelectField>
                {repairLensPaused ? (
                  <Chip tone="neutral" size="sm">
                    Repair lens paused
                  </Chip>
                ) : null}
                <Chip tone="neutral" size="sm">/ focus journal search</Chip>
                <Chip tone="neutral" size="sm">Shift+J jump to review</Chip>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={async () => {
                    try {
                      let blob
                      let filename = 'trade_journal.csv'
                      let successMessage = 'Journal CSV exported.'

                      if (repairLens.active) {
                        blob = buildJournalCsv(displayRows)
                        filename =
                          repairView === 'completed'
                            ? 'trade_journal_repairs_cleared.csv'
                            : 'trade_journal_open_repairs.csv'
                        successMessage = 'Repair-lens CSV exported.'
                      } else {
                        blob = await exportTradeJournalCsv({ search: debouncedSearch, resultFilter, directionFilter, attributionFilter })
                      }

                      const url = window.URL.createObjectURL(new Blob([blob]))
                      const link = document.createElement('a')
                      link.href = url
                      link.setAttribute('download', filename)
                      document.body.appendChild(link)
                      link.click()
                      link.remove()
                      window.URL.revokeObjectURL(url)
                      pushToast(successMessage, 'success')
                    } catch (err) {
                      pushToast(err?.response?.data?.detail || err.message || 'CSV export failed.', 'error')
                    }
                  }}
                >
                  Export CSV
                </Button>
              </>
            )}
          />
        )}
      >
        {repairLensPaused ? (
          <FeedbackState
            compact
            tone="warning"
            eyebrow="Repair lens"
            title="Repair lens paused"
            description={`Repair lens is paused while a direct review filter is active. Switch back to All reviews to reapply the ${repairViewLabel.toLowerCase()} lens.`}
            role="status"
          />
        ) : null}
        {repairLens.active ? (
          <FeedbackState
            compact
            tone="info"
            eyebrow={repairLens.label}
            title={repairView === 'completed' ? 'Resolution lens active' : 'Repair lens active'}
            description={repairLens.detail}
            role="status"
          />
        ) : null}
        {displayRows.length ? (
          <ListTable>
            <table
              ref={journalTableNavigation.containerRef}
              className="signal-table ui-list-table"
              onKeyDown={journalTableNavigation.onKeyDown}
            >
              <caption className="ui-visually-hidden">Journal trade attribution table</caption>
              <thead>
                <tr>
                  <th scope="col">Trade</th>
                  <th scope="col">Thesis</th>
                  <th scope="col">Execution</th>
                  <th scope="col">Review</th>
                  <th scope="col">PnL</th>
                </tr>
              </thead>
              <tbody>
                {displayRows.map((row) => (
                  <tr key={row.key}>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__kicker">{row.instrumentLabel}</div>
                        <div className="ui-list-cell__title">
                          {row.ticker && row.ticker !== '--' ? (
                            <button
                            type="button"
                            className="table-link table-row-action"
                            onClick={() =>
                              navigate(
                                buildDashboardTickerUrl(row.ticker, {
                                  replaySource: 'journal_review',
                                  replayTitle: row.reviewLoopTitle,
                                  replayStatus: row.reviewLoopCompletion === 'completed' ? 'resolved' : 'open',
                                }),
                              )
                            }
                          >
                            {row.ticker}
                          </button>
                          ) : row.ticker}
                        </div>
                        <div className="ui-list-cell__meta">{row.executionTitle}</div>
                        <div className="ui-list-cell__badges">
                          <StatusBadge value={row.sourceLabel} />
                          {row.ticker && row.ticker !== '--' ? (
                            <button
                            type="button"
                            className="table-link"
                            onClick={() =>
                              navigate(
                                buildDashboardTickerUrl(row.ticker, {
                                  replaySource: 'journal_review',
                                  replayTitle: row.reviewLoopTitle,
                                  replayStatus: row.reviewLoopCompletion === 'completed' ? 'resolved' : 'open',
                                }),
                              )
                            }
                          >
                            Open on desk
                          </button>
                          ) : null}
                        </div>
                      </div>
                    </td>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__title">{row.thesisTitle}</div>
                        {row.thesisDetailParts.length ? (
                          <InlineMeta
                            as="div"
                            className="ui-list-cell__meta"
                            items={row.thesisDetailParts}
                          />
                        ) : (
                          <div className="ui-list-cell__meta">No saved thesis context.</div>
                        )}
                        <div className="ui-list-cell__stack">
                          <span>Target {row.targetLabel}</span>
                          <span>Invalidation {row.invalidationLabel}</span>
                          {row.setupScore !== null ? <span>Setup {row.setupScore.toFixed(1)}</span> : null}
                        </div>
                      </div>
                    </td>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__title">
                          <ValueFlow fromLabel="Entry" fromValue={row.entryValue} toLabel="Exit" toValue={row.exitValue} />
                        </div>
                        {row.executionDetailParts.length ? (
                          <InlineMeta
                            as="div"
                            className="ui-list-cell__meta"
                            items={row.executionDetailParts}
                          />
                        ) : (
                          <div className="ui-list-cell__meta">Manual close record</div>
                        )}
                        <div className="ui-list-cell__stack">
                          <span>Opened {row.openedAt}</span>
                          <span>Closed {row.closedAt}</span>
                          <InlineMeta
                            as="span"
                            items={[`${row.contractCount} units`, `Cost ${row.positionCostLabel}`]}
                          />
                          {row.slippageLabel ? <span>Slip {row.slippageLabel}</span> : null}
                        </div>
                        <div className="ui-list-cell__badges">
                          <StatusBadge value={row.executionReviewLabel} tone={row.executionReviewTone} />
                        </div>
                      </div>
                    </td>
                    <td>
                      <div className="ui-list-cell">
                        <div className="ui-list-cell__title">{row.attributionLabel}</div>
                        <div className="ui-list-cell__meta">
                          {row.intradayReview ? `${row.intradayReview.detail} ${row.resultMeta}` : row.resultMeta}
                        </div>
                        <div className="ui-list-cell__stack">
                          <span>Max risk {row.maxRiskLabel}</span>
                          {row.intradayReview ? <span>{row.intradayReview.sessionLabel}</span> : null}
                          {row.eventLabel ? <span>{row.eventLabel}</span> : null}
                          <span>{row.executionReviewDetail}</span>
                        </div>
                        <div className="ui-list-cell__badges">
                          <StatusBadge value={row.attributionLabel} tone={row.attributionTone} />
                          {row.intradayReview ? (
                            <StatusBadge value={row.intradayReview.label} tone={row.intradayReview.tone} />
                          ) : null}
                          {row.reviewTags.map((tag) => (
                            <StatusBadge key={`${row.key}-${tag}`} value={tag} />
                          ))}
                          <button
                            type="button"
                            className="table-link"
                            onClick={() =>
                              navigate(
                                buildNotesReviewLoopUrl(location.search, {
                                  completed: row.reviewLoopCompletion,
                                  note: {
                                    ticker: row.ticker,
                                    title: row.reviewLoopTitle,
                                  },
                                  replaySource: 'journal_review',
                                  replayTitle: row.reviewLoopTitle,
                                  replayStatus: row.reviewLoopCompletion === 'completed' ? 'resolved' : 'open',
                                }),
                              )
                            }
                          >
                            {row.reviewLoopCompletion === 'completed' ? 'Open cleared note' : 'Open repair note'}
                          </button>
                        </div>
                      </div>
                    </td>
                    <td>
                      <div className="ui-list-cell">
                        <div className={`journal-pnl ${row.resultTone === 'positive' ? 'journal-pnl--positive' : row.resultTone === 'negative' ? 'journal-pnl--negative' : ''}`}>
                          {row.pnlLabel}
                        </div>
                        <div className="ui-list-cell__badges">
                          <StatusBadge value={row.resultLabel} />
                        </div>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </ListTable>
        ) : (
          <EmptyState
            title="No journal rows"
            description={
              repairLens.active
                ? 'No journal rows match the active repair lens right now. Change the repair view or reopen the linked repair notes.'
                : 'No journal rows match the current filters yet. Start here with Trades if you need fresh review evidence.'
            }
            actionLabel={repairLens.active ? 'Open repair notes' : 'Open trades'}
            onAction={
              repairLens.active
                ? () =>
                    navigate(
                      buildNotesReviewLoopUrl(location.search, {
                        completed: repairView,
                        replaySource: 'journal_repair_loop',
                        replayTitle: repairLens.label,
                        replayStatus: repairView === 'completed' ? 'resolved' : 'open',
                      }),
                    )
                : () => navigate('/trades')
            }
            secondaryActionLabel={repairLens.active ? 'Reset review filters' : 'Open watchlist'}
            onSecondaryAction={
              repairLens.active
                ? () => {
                    setResultFilter('all')
                    setDirectionFilter('all')
                    setAttributionFilter('all')
                  }
                : () => navigate('/watchlist')
            }
          />
        )}
        <div className="pager-row">
          <Chip tone="neutral" size="sm">
            {repairLens.active
              ? `Showing ${displayRows.length} repair-lens rows from ${rows.length} current rows`
              : `Showing ${displayRows.length} of ${total}`}
          </Chip>
          <ActionBar compact>
            <Button type="button" variant="ghost" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>
              Previous
            </Button>
            <Button type="button" variant="ghost" disabled={(page + 1) * pageSize >= total} onClick={() => setPage((value) => value + 1)}>
              Next
            </Button>
          </ActionBar>
        </div>
      </SectionCard>
    </>
  )
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import ActionBar from '../components/ActionBar'
import Button from '../components/Button'
import ChecklistChip from '../components/ChecklistChip'
import Chip from '../components/Chip'
import DataToolbar from '../components/DataToolbar'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import FilePickerButton from '../components/FilePickerButton'
import { SelectField, TextAreaField, TextField, ToggleField } from '../components/FormFields'
import InlineMeta from '../components/InlineMeta'
import Kicker from '../components/Kicker'
import LoadingBlock from '../components/LoadingBlock'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import WorkflowArrivalBanner from '../components/WorkflowArrivalBanner'
import WorkflowGuide, { buildWorkflowSteps } from '../components/WorkflowGuide'
import { advanceNote, bulkUpdateNotes, createNote, deleteNote, duplicateNote, exportNotes, getFrontendFilters, getNotes, getNotesAgenda, getNotesBoard, getNotesCalendar, getNotesSummary, getRecentNotes, getTradeSummary, importNotes, snoozeNote, updateNote } from '../api/client'
import { usePreferences } from '../context/PreferencesContext'
import { useToast } from '../context/ToastContext'
import usePageActionShortcuts, { focusFirstMatching } from '../hooks/usePageActionShortcuts'
import { buildIntradayReviewLens } from '../utils/intradayReviewModel'

const emptyDraft = { title: '', body: '', ticker: '', tags: '', owner: '', sourceUrl: '', checklist: '', relatedIds: '', blockedByIds: '', priority: 'medium', noteType: 'general', dueAt: '', reminderAt: '', recurrence: 'none', recurrenceEndAt: '', estimateMinutes: 0, spentMinutes: 0 }
const noteTemplates = [
  { label: 'Trade idea', title: 'Trade idea', body: `Setup thesis:
Entry trigger:
Risk to avoid:
Follow-up:`, priority: 'medium', noteType: 'trade_idea' },
  { label: 'Risk review', title: 'Risk review', body: `Position:
Key risk:
Stop / invalidation:
Adjustment plan:`, priority: 'high', noteType: 'risk_review' },
  { label: 'Market note', title: 'Market context', body: `Market regime:
Catalyst:
Impact on watchlist:`, priority: 'low', noteType: 'market_note' },
  { label: 'Operator memory', title: 'Operator memory', body: `Rule:
Context:
What to do:
What to avoid:
Replace when:`, tags: 'memory, operator-memory', owner: 'operator-memory', priority: 'high', noteType: 'market_note' },
  { label: 'Todo', title: 'Trading todo', body: `Task:
Owner:
Needed before entry:`, priority: 'medium', noteType: 'todo' },
  { label: 'UX test finding', title: 'UX test finding', body: `Flow:
Page:
Step:
What happened:
Expected behavior:
Risk introduced:
Suggested fix:
Retest trigger:`, tags: 'ux-test, workstation', owner: 'ux-lane', priority: 'medium', noteType: 'general' },
]
const NOTE_TICKER_PATTERN = /^[A-Z0-9.-]{1,8}$/

function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function friendlyLabel(value = '') {
  return String(value || '').replaceAll('_', ' ')
}

function buildReviewNoteTemplates(attributionSummary) {
  const summary = attributionSummary || {}
  const latestReview = summary.latest_review || null
  const executionReviewCount = Number(summary.execution_review_count || 0)
  const thesisReviewCount = Number(summary.thesis_review_count || 0)
  const riskReviewCount = Number(summary.risk_review_count || 0)
  const templates = []

  if (latestReview?.label) {
    templates.push({
      label: `Latest: ${latestReview.ticker || 'Desk'}`,
      title: `${latestReview.ticker || 'Desk'} ${latestReview.label}`,
      ticker: latestReview.ticker || '',
      tags: 'review-loop, post-trade',
      owner: 'review-loop',
      priority: 'high',
      noteType: 'risk_review',
      body: `Latest review:
${latestReview.label}

Desk note:
${latestReview.detail || 'No extra detail was saved.'}

What to keep:
What to change:
Next-session rule:`,
    })
  }

  if (executionReviewCount > 0) {
    templates.push({
      label: `Execution drift (${executionReviewCount})`,
      title: 'Execution drift review',
      tags: 'execution, review-loop',
      owner: 'execution-lane',
      priority: 'high',
      noteType: 'risk_review',
      body: `Execution review:
Where did the fill drift?
What route or order-type change would have improved it?
What size or urgency rule should change before the next route?`,
    })
  }

  if (thesisReviewCount > 0) {
    templates.push({
      label: `Thesis miss (${thesisReviewCount})`,
      title: 'Thesis miss review',
      tags: 'thesis, review-loop',
      owner: 'research-lane',
      priority: 'medium',
      noteType: 'trade_idea',
      body: `Thesis review:
What was the original setup?
What invalidated it?
What signal or event clue did the desk over-trust?
What should the next version of the setup require?`,
    })
  }

  if (riskReviewCount > 0) {
    templates.push({
      label: `Risk review (${riskReviewCount})`,
      title: 'Risk and sizing review',
      tags: 'risk, sizing, review-loop',
      owner: 'risk-lane',
      priority: 'high',
      noteType: 'risk_review',
      body: `Risk review:
What rule was stretched?
Was the size, stop, or session rule wrong?
What hard lock or preset should change before risking capital again?`,
    })
  }

  return templates.slice(0, 4)
}

function toInputDateTime(value) {
  if (!value) return ''
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return ''
  const year = parsed.getFullYear()
  const month = String(parsed.getMonth() + 1).padStart(2, '0')
  const day = String(parsed.getDate()).padStart(2, '0')
  const hours = String(parsed.getHours()).padStart(2, '0')
  const minutes = String(parsed.getMinutes()).padStart(2, '0')
  return `${year}-${month}-${day}T${hours}:${minutes}`
}

function isValidSourceUrl(value) {
  try {
    const parsed = new URL(String(value || '').trim())
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    return false
  }
}

function toChecklistEntries(value) {
  if (Array.isArray(value)) {
    return value
      .map((item) => String(item?.text || '').trim())
      .filter(Boolean)
  }
  return String(value || '')
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

function buildNoteValidationErrors(noteLike) {
  const errors = {}
  const title = String(noteLike?.title || '').trim()
  const body = String(noteLike?.body || '').trim()
  const ticker = String(noteLike?.ticker || '').trim().toUpperCase()
  const sourceUrl = String(noteLike?.sourceUrl ?? noteLike?.source_url ?? '').trim()
  const dueAt = String(noteLike?.dueAt ?? noteLike?.due_at ?? '').trim()
  const reminderAt = String(noteLike?.reminderAt ?? noteLike?.reminder_at ?? '').trim()
  const estimateMinutes = Number(noteLike?.estimateMinutes ?? noteLike?.estimate_minutes ?? 0)
  const spentMinutes = Number(noteLike?.spentMinutes ?? noteLike?.spent_minutes ?? 0)
  const checklistEntries = toChecklistEntries(noteLike?.checklist)

  if (!title) {
    errors.title = 'Add a short title so this note can be found again.'
  }
  if (!body && !checklistEntries.length) {
    errors.body = 'Add note detail or at least one checklist item so this entry is actionable.'
  }
  if (ticker && !NOTE_TICKER_PATTERN.test(ticker)) {
    errors.ticker = 'Use up to 8 letters, numbers, dots, or dashes.'
  }
  if (sourceUrl && !isValidSourceUrl(sourceUrl)) {
    errors.sourceUrl = 'Enter a full http:// or https:// URL.'
  }
  if (dueAt && reminderAt) {
    const dueTime = new Date(dueAt).getTime()
    const reminderTime = new Date(reminderAt).getTime()
    if (Number.isFinite(dueTime) && Number.isFinite(reminderTime) && reminderTime > dueTime) {
      errors.reminderAt = 'Reminder should happen before the due time.'
    }
  }
  if (!Number.isFinite(estimateMinutes) || estimateMinutes < 0) {
    errors.estimateMinutes = 'Estimate minutes cannot be negative.'
  }
  if (!Number.isFinite(spentMinutes) || spentMinutes < 0) {
    errors.spentMinutes = 'Spent minutes cannot be negative.'
  }

  return errors
}

function parseNotesFocusParams(search) {
  const params = new URLSearchParams(search || '')
  const noteFocus = String(params.get('noteFocus') || '').trim().toLowerCase()
  const noteId = String(params.get('noteId') || '').trim()
  const noteTicker = String(params.get('noteTicker') || '').trim().toUpperCase()
  const noteTag = String(params.get('noteTag') || '').trim().toLowerCase()
  const noteTitle = String(params.get('noteTitle') || '').trim()
  const noteCompletion = String(params.get('noteCompletion') || '').trim().toLowerCase()
  const journalReturn = String(params.get('journalReturn') || '').trim()
  const noteRestored = String(params.get('noteRestored') || '').trim()
  const workflowFrom = String(params.get('workflowFrom') || '').trim().toLowerCase()
  const replaySource = String(params.get('replaySource') || '').trim().toLowerCase()
  const replayTitle = String(params.get('replayTitle') || '').trim()
  const replayStatus = String(params.get('replayStatus') || '').trim().toLowerCase()
  return {
    noteFocus,
    noteId,
    noteTicker,
    noteTag,
    noteTitle,
    noteCompletion,
    journalReturn,
    noteRestored,
    workflowFrom,
    replaySource,
    replayTitle,
    replayStatus,
    hasAny: Boolean(
      noteFocus
        || noteId
        || noteTicker
        || noteTag
        || noteTitle
        || noteCompletion
        || journalReturn
        || noteRestored
        || workflowFrom
        || replaySource
        || replayTitle
        || replayStatus,
    ),
  }
}

function resolveFocusCompletionState(focusParams) {
  const normalized = String(focusParams?.noteCompletion || '').trim().toLowerCase()
  if (normalized === 'open' || normalized === 'completed' || normalized === 'all') {
    return normalized
  }
  return focusParams?.noteFocus === 'review-loop' ? 'open' : 'all'
}

function buildDashboardFocusUrl(search, note, options = {}) {
  const params = new URLSearchParams(search || '')
  const focusParams = parseNotesFocusParams(search)
  params.delete('noteFocus')
  params.delete('noteId')
  params.delete('noteTicker')
  params.delete('noteTag')
  params.delete('noteTitle')
  params.delete('noteCompletion')
  params.delete('noteRestored')
  params.delete('journalReturn')
  params.delete('journalSearch')
  params.delete('journalResult')
  params.delete('journalDirection')
  params.delete('journalAttribution')
  params.delete('journalPage')
  params.delete('journalRepairView')
  params.delete('journalRestored')

  const ticker = String(note?.ticker || '').trim().toUpperCase()
  if (ticker) {
    params.set('ticker', ticker)
  } else {
    params.delete('ticker')
  }

  params.set('workflowFrom', 'notes')
  params.set('notesReturn', '1')
  if (ticker) {
    params.set('notesReturnTicker', ticker)
  } else {
    params.delete('notesReturnTicker')
  }
  if (note?.title) {
    params.set('notesReturnTitle', String(note.title).trim())
  } else {
    params.delete('notesReturnTitle')
  }
  const noteCompletion =
    String(note?.progress_state || '').trim().toLowerCase() === 'done'
    || String(note?.status || '').trim().toLowerCase() === 'done'
    || String(note?.status || '').trim().toLowerCase() === 'completed'
    || Boolean(note?.completed)
      ? 'completed'
      : resolveFocusCompletionState(focusParams)
  params.set('notesReturnCompletion', noteCompletion === 'completed' ? 'completed' : 'open')
  if (focusParams.journalReturn === '1') {
    params.set('notesReturnJournal', '1')
  } else {
    params.delete('notesReturnJournal')
  }

  if (options.resolvedRepair) {
    params.set('repairResolved', '1')
    if (ticker) {
      params.set('repairTicker', ticker)
    } else {
      params.delete('repairTicker')
    }
    if (note?.title) {
      params.set('repairTitle', String(note.title).trim())
    } else {
      params.delete('repairTitle')
    }
  } else {
    params.delete('repairResolved')
    params.delete('repairTicker')
    params.delete('repairTitle')
  }

  const nextQuery = params.toString()
  return `/${nextQuery ? `?${nextQuery}` : ''}`
}

function buildJournalFocusUrl(search) {
  const params = new URLSearchParams(search || '')
  params.delete('noteFocus')
  params.delete('noteId')
  params.delete('noteTicker')
  params.delete('noteTag')
  params.delete('noteTitle')
  params.delete('noteCompletion')
  params.delete('noteRestored')
  params.delete('journalReturn')
  params.set('journalRestored', '1')

  const nextQuery = params.toString()
  return `/journal${nextQuery ? `?${nextQuery}` : ''}`
}

function formatReplayArrivalLabel(source = '', status = '') {
  const normalizedSource = String(source || '').trim().toLowerCase()
  const normalizedStatus = String(status || '').trim().toLowerCase()
  if (normalizedSource === 'board_snapshot') return 'saved board'
  if (normalizedSource === 'board_replay') {
    return normalizedStatus === 'resolved' ? 'resolved board replay' : 'board replay'
  }
  if (normalizedSource === 'live_position') return 'live position review'
  if (normalizedSource === 'journal_review') {
    return normalizedStatus === 'resolved' ? 'cleared journal review' : 'journal review'
  }
  if (normalizedSource === 'journal_repair_loop') {
    return normalizedStatus === 'resolved' ? 'cleared repair flow' : 'open repair flow'
  }
  return 'replay context'
}

function syncNotesRepairParams(search, { active, completionState, focusedNote, focusedNoteId }) {
  const params = new URLSearchParams(search || '')
  if (active) {
    params.set('noteFocus', 'review-loop')
    params.set('noteTag', 'review-loop')
    params.set('noteCompletion', completionState === 'completed' ? 'completed' : 'open')
    if (focusedNoteId) {
      params.set('noteId', String(focusedNoteId))
    } else {
      params.delete('noteId')
    }
    if (focusedNote?.ticker) {
      params.set('noteTicker', String(focusedNote.ticker).trim().toUpperCase())
    } else {
      params.delete('noteTicker')
    }
    if (focusedNote?.title) {
      params.set('noteTitle', String(focusedNote.title).trim())
    } else {
      params.delete('noteTitle')
    }
  } else {
    params.delete('noteFocus')
    params.delete('noteTag')
    params.delete('noteCompletion')
    params.delete('noteId')
    params.delete('noteTicker')
    params.delete('noteTitle')
    params.delete('noteRestored')
  }
  return params
}

export default function NotesPage() {
  const location = useLocation()
  const navigate = useNavigate()
  const focusParams = useMemo(() => parseNotesFocusParams(location.search), [location.search])
  const appliedFocusRef = useRef('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filters, setFilters] = useState({ note_statuses: ['all', 'active', 'archived'], note_priorities: ['all', 'high', 'medium', 'low'], note_sorts: ['updated_desc'], note_types: ['all', 'general'], note_due_states: ['all'], note_completion_states: ['all', 'open', 'completed'] })
  const [notesPayload, setNotesPayload] = useState(null)
  const [tradeSummary, setTradeSummary] = useState(null)
  const [summary, setSummary] = useState(null)
  const [calendar, setCalendar] = useState(null)
  const [recentNotes, setRecentNotes] = useState(null)
  const [agenda, setAgenda] = useState(null)
  const [board, setBoard] = useState(null)
  const [search, setSearch] = useState(() => focusParams.noteTitle || '')
  const [ticker, setTicker] = useState(() => focusParams.noteTicker || '')
  const [status, setStatus] = useState('active')
  const [tag, setTag] = useState(() => focusParams.noteTag || (focusParams.noteFocus === 'review-loop' ? 'review-loop' : ''))
  const [priority, setPriority] = useState('all')
  const [sortBy, setSortBy] = useState('updated_desc')
  const [noteType, setNoteType] = useState('all')
  const [dueState, setDueState] = useState('all')
  const [completionState, setCompletionState] = useState(() => resolveFocusCompletionState(focusParams))
  const [owner, setOwner] = useState('')
  const [hasLink, setHasLink] = useState('all')
  const [checklistState, setChecklistState] = useState('all')
  const [reminderState, setReminderState] = useState('all')
  const [recurrence, setRecurrence] = useState('all')
  const [blockedState, setBlockedState] = useState('all')
const [progressState, setProgressState] = useState('all')
const [pinnedOnly, setPinnedOnly] = useState(false)
const [selectedIds, setSelectedIds] = useState(() => (focusParams.noteId ? [focusParams.noteId] : []))
const [draft, setDraft] = useState(emptyDraft)
const [draftErrors, setDraftErrors] = useState({})
const [editingId, setEditingId] = useState(() => focusParams.noteId || '')
const [editErrors, setEditErrors] = useState({})
const [focusedNoteId, setFocusedNoteId] = useState(() => focusParams.noteId || '')
const [dismissedArrivalKey, setDismissedArrivalKey] = useState('')
  const { preferences } = usePreferences()
  const { pushToast } = useToast()

  usePageActionShortcuts({
    focusInput: () => focusFirstMatching(['#notes-capture-title']),
    focusResult: () => focusFirstMatching(['.note-card button']),
  })

  const loadNotes = useCallback(async () => {
    try {
      setError('')
      const [payload, summaryPayload, tradeSummaryPayload, calendarPayload, recentPayload, agendaPayload, boardPayload] = await Promise.all([
        getNotes({ search, ticker, status, tag, limit: 100, priority, pinnedOnly, sortBy, noteType, dueState, completed: completionState, owner, hasLink, checklistState, reminderState, recurrence, blockedState, progressState }),
        getNotesSummary(),
        getTradeSummary(),
        getNotesCalendar(14, status),
        getRecentNotes(6, status === 'all'),
        getNotesAgenda(7, status),
        getNotesBoard(status),
      ])
      setNotesPayload(payload)
      setSummary(summaryPayload)
      setTradeSummary(tradeSummaryPayload)
      setCalendar(calendarPayload)
      setRecentNotes(recentPayload)
      setAgenda(agendaPayload)
      setBoard(boardPayload)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load notes.')
    } finally {
      setLoading(false)
    }
  }, [search, ticker, status, tag, priority, pinnedOnly, sortBy, noteType, dueState, completionState, owner, hasLink, checklistState, reminderState, recurrence, blockedState, progressState])

  useEffect(() => {
    getFrontendFilters().then(setFilters).catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!focusParams.hasAny) return
    const focusKey = [
      focusParams.noteFocus,
      focusParams.noteId,
      focusParams.noteTicker,
      focusParams.noteTag,
      focusParams.noteTitle,
      focusParams.noteCompletion,
      focusParams.journalReturn,
      focusParams.noteRestored,
    ].join('|')
    if (appliedFocusRef.current === focusKey) return
    appliedFocusRef.current = focusKey

    setStatus('active')
    setTag(focusParams.noteTag || (focusParams.noteFocus === 'review-loop' ? 'review-loop' : ''))
    setTicker(focusParams.noteTicker || '')
    setSearch(focusParams.noteTitle || '')
    setCompletionState(resolveFocusCompletionState(focusParams))
    setSelectedIds(focusParams.noteId ? [focusParams.noteId] : [])
    setEditingId(focusParams.noteId || '')
    setFocusedNoteId(focusParams.noteId || '')
  }, [focusParams])

  useEffect(() => { loadNotes() }, [loadNotes])

  useEffect(() => {
    if (!focusedNoteId) return
    const handle = window.requestAnimationFrame(() => {
      const target = document.getElementById(`note-card-${focusedNoteId}`)
      if (!target) return
      target.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
    return () => window.cancelAnimationFrame(handle)
  }, [focusedNoteId, notesPayload?.items])

  const focusedNote = useMemo(
    () => (notesPayload?.items || []).find((item) => String(item.id) === String(focusedNoteId)) || null,
    [focusedNoteId, notesPayload?.items],
  )
  const repairLensValue = completionState === 'completed' ? 'completed' : 'open'
  const repairLensLabel = repairLensValue === 'completed' ? 'Repairs cleared' : 'Open repairs'
  const repairLensActive = tag === 'review-loop' && (completionState === 'open' || completionState === 'completed')
  const repairLensPaused = !repairLensActive
  const showRepairLensContext = Boolean(tag === 'review-loop' || focusParams.noteFocus === 'review-loop' || focusParams.journalReturn === '1')
  const repairContextRestored = focusParams.noteRestored === '1'

  useEffect(() => {
    if (location.pathname !== '/notes') return
    const nextParams = syncNotesRepairParams(location.search, {
      active: repairLensActive,
      completionState,
      focusedNote,
      focusedNoteId,
    })
    const nextQuery = nextParams.toString()
    const currentQuery = location.search.startsWith('?') ? location.search.slice(1) : location.search
    if (nextQuery === currentQuery) return
    navigate({ pathname: location.pathname, search: nextQuery ? `?${nextQuery}` : '' }, { replace: true })
  }, [completionState, focusedNote, focusedNoteId, location.pathname, location.search, navigate, repairLensActive])

  const metrics = useMemo(() => ([
    { label: 'Open Notes', value: summary?.open_count ?? notesPayload?.open_count ?? 0 },
    { label: 'Completed', value: summary?.completed_count ?? notesPayload?.completed_count ?? 0 },
    { label: 'Overdue', value: summary?.overdue_count ?? notesPayload?.overdue_count ?? 0 },
    { label: 'Due Today', value: summary?.today_count ?? notesPayload?.today_count ?? 0 },
    { label: 'Due Soon', value: summary?.due_soon_count ?? 0 },
    { label: 'Linked', value: summary?.linked_count ?? notesPayload?.linked_count ?? 0 },
    { label: 'Checklist Open', value: summary?.checklist_open_count ?? notesPayload?.checklist_open_count ?? 0 },
    { label: 'Reminders Due', value: summary?.reminder_due_count ?? notesPayload?.reminder_due_count ?? 0 },
    { label: 'Recurring', value: summary?.recurring_count ?? notesPayload?.recurring_count ?? 0 },
    { label: 'Blocked', value: summary?.blocked_count ?? notesPayload?.blocked_count ?? 0 },
    { label: 'Ready', value: summary?.ready_count ?? notesPayload?.ready_count ?? 0 },
    { label: 'In Progress', value: summary?.in_progress_count ?? notesPayload?.progress_counts?.in_progress ?? 0 },
    { label: 'Est. Hours', value: (((summary?.total_estimate_minutes ?? notesPayload?.total_estimate_minutes ?? 0) / 60).toFixed(1)) },
    { label: 'Spent Hours', value: (((summary?.total_spent_minutes ?? notesPayload?.total_spent_minutes ?? 0) / 60).toFixed(1)) },
  ]), [notesPayload, summary])
  const intradayReview = useMemo(
    () =>
      buildIntradayReviewLens({
        tradingStyle: preferences?.tradingStyle,
        preferences,
        notesSummary: summary,
        tradeSummary,
      }),
    [preferences, summary, tradeSummary],
  )
  const baseReviewTemplates = useMemo(
    () => buildReviewNoteTemplates(tradeSummary?.attribution_summary),
    [tradeSummary?.attribution_summary],
  )
  const reviewTemplates = useMemo(
    () => [...(intradayReview.noteTemplates || []), ...baseReviewTemplates],
    [baseReviewTemplates, intradayReview.noteTemplates],
  )
  const latestReview = tradeSummary?.attribution_summary?.latest_review || null
  const starterReviewTemplate = useMemo(
    () => reviewTemplates[0] || noteTemplates.find((item) => item.label === 'Risk review') || noteTemplates[0],
    [reviewTemplates],
  )
  const reviewLoopSummary = summary?.review_loop_summary || { open_count: 0, resolved_count: 0, latest_resolved: null }
  const latestResolvedRepair = reviewLoopSummary.latest_resolved || null
  const latestClearInFocus =
    Boolean(latestResolvedRepair?.id) &&
    String(latestResolvedRepair?.id) === String(focusedNoteId) &&
    repairLensActive &&
    repairLensValue === 'completed'
  const savedNotesSubtitle = repairLensActive
    ? intradayReview.active
      ? `${repairLensLabel} are in focus, so the same-session repair workflow stays visible while you work notes.`
      : `${repairLensLabel} are in focus, so the desk repair workflow stays visible while you work notes.`
    : showRepairLensContext
      ? 'Repair lens is paused while Notes is set to a broader filter view.'
      : 'Pinned notes stay at the top so you can keep active trade context visible.'

  function applyRepairLens(nextLens) {
    setStatus('active')
    setTag('review-loop')
    setCompletionState(nextLens === 'completed' ? 'completed' : 'open')
  }

  function focusLatestResolvedRepair() {
    if (!latestResolvedRepair) return
    setStatus('active')
    setTag('review-loop')
    setCompletionState('completed')
    setTicker(String(latestResolvedRepair.ticker || '').trim().toUpperCase())
    setSearch(String(latestResolvedRepair.title || '').trim())
    if (latestResolvedRepair.id) {
      setSelectedIds([latestResolvedRepair.id])
      setEditingId(latestResolvedRepair.id)
      setFocusedNoteId(latestResolvedRepair.id)
    }
  }

  function applyTemplate(template) {
    setDraft((state) => ({
      ...state,
      title: template.title,
      body: template.body,
      ticker: template.ticker || state.ticker,
      tags: template.tags || state.tags,
      owner: template.owner || state.owner,
      priority: template.priority,
      noteType: template.noteType,
    }))
    setDraftErrors({})
  }

  function updateDraftField(field, value, options = {}) {
    setDraft((state) => ({ ...state, [field]: value }))
    const fieldsToClear = options.clear || [field]
    setDraftErrors((state) => {
      const next = { ...state }
      fieldsToClear.forEach((key) => {
        delete next[key]
      })
      return next
    })
  }

  function clearEditFieldError(noteId, fields) {
    const keys = Array.isArray(fields) ? fields : [fields]
    setEditErrors((state) => {
      if (!state[noteId]) return state
      const nextFields = { ...state[noteId] }
      keys.forEach((key) => {
        delete nextFields[key]
      })
      return {
        ...state,
        [noteId]: nextFields,
      }
    })
  }

  async function handleCreate(event) {
    event.preventDefault()
    const nextErrors = buildNoteValidationErrors(draft)
    if (Object.keys(nextErrors).length) {
      setDraftErrors(nextErrors)
      pushToast('Fix the highlighted note fields and try again.', 'error')
      return
    }
    try {
      await createNote({
        title: draft.title,
        body: draft.body,
        ticker: draft.ticker,
        tags: draft.tags.split(',').map((item) => item.trim()).filter(Boolean),
        owner: draft.owner,
        source_url: draft.sourceUrl || null,
        checklist: draft.checklist.split('\n').map((item) => item.trim()).filter(Boolean).map((text) => ({ text, done: false })),
        related_note_ids: draft.relatedIds.split(',').map((item) => item.trim()).filter(Boolean),
        blocked_by_ids: draft.blockedByIds.split(',').map((item) => item.trim()).filter(Boolean),
        pinned: false,
        priority: draft.priority,
        note_type: draft.noteType,
        due_at: draft.dueAt || null,
        reminder_at: draft.reminderAt || null,
        recurrence: draft.recurrence,
        recurrence_end_at: draft.recurrenceEndAt || null,
        estimate_minutes: Number(draft.estimateMinutes || 0),
        spent_minutes: Number(draft.spentMinutes || 0),
      })
      setDraft(emptyDraft)
      setDraftErrors({})
      pushToast('Note created.', 'success')
      await loadNotes()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to create note.', 'error')
    }
  }

  async function handleQuickUpdate(note, patch, successMessage = 'Note updated.') {
    try {
      await updateNote(note.id, patch)
      pushToast(successMessage, 'success')
      await loadNotes()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to update note.', 'error')
    }
  }

  async function togglePinned(note) { await handleQuickUpdate(note, { pinned: !note.pinned }, note.pinned ? 'Note unpinned.' : 'Note pinned.') }
  async function toggleArchived(note) { await handleQuickUpdate(note, { archived: !note.archived }, note.archived ? 'Note restored.' : 'Note archived.') }
  async function toggleCompleted(note) { await handleQuickUpdate(note, { completed: !note.completed }, note.completed ? 'Marked back to open.' : 'Marked completed.') }

  async function cyclePriority(note) {
    const order = ['low', 'medium', 'high']
    const currentIndex = Math.max(0, order.indexOf(note.priority || 'medium'))
    const nextPriority = order[(currentIndex + 1) % order.length]
    await handleQuickUpdate(note, { priority: nextPriority }, `Priority set to ${nextPriority}.`)
  }

  async function handleDuplicate(note) {
    try {
      await duplicateNote(note.id)
      pushToast('Note duplicated.', 'success')
      await loadNotes()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to duplicate note.', 'error')
    }
  }

  async function handleDelete(note) {
    try {
      await deleteNote(note.id)
      pushToast('Note deleted.', 'info')
      await loadNotes()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to delete note.', 'error')
    }
  }

  async function handleExport() {
    try {
      const payload = await exportNotes()
      downloadJson(`operator_notes_${new Date().toISOString().slice(0, 10)}.json`, payload)
      pushToast('Notes exported.', 'success')
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to export notes.', 'error')
    }
  }

  async function handleImport(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    try {
      const text = await file.text()
      const parsed = JSON.parse(text)
      const items = Array.isArray(parsed?.items) ? parsed.items : []
      const result = await importNotes({ items, mode: 'merge' })
      pushToast(`Imported ${result.imported ?? items.length} notes.`, 'success')
      await loadNotes()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to import notes.', 'error')
    }
  }

  async function handleEditSubmit(event) {
    event.preventDefault()
    const note = notesPayload?.items?.find((item) => item.id === editingId)
    if (!note) return
    const nextErrors = buildNoteValidationErrors(note)
    if (Object.keys(nextErrors).length) {
      setEditErrors((state) => ({ ...state, [note.id]: nextErrors }))
      pushToast('Fix the highlighted note fields and try again.', 'error')
      return
    }
    try {
      await updateNote(note.id, {
        title: note.title,
        body: note.body,
        ticker: note.ticker,
        tags: note.tags,
        priority: note.priority,
        note_type: note.note_type,
        due_at: note.due_at || null,
        reminder_at: note.reminder_at || null,
        recurrence: note.recurrence || 'none',
        recurrence_end_at: note.recurrence_end_at || null,
        completed: note.completed,
        owner: note.owner || '',
        source_url: note.source_url || '',
        checklist: note.checklist || [],
        related_note_ids: note.related_note_ids || [],
        blocked_by_ids: note.blocked_by_ids || [],
        estimate_minutes: Number(note.estimate_minutes || 0),
        spent_minutes: Number(note.spent_minutes || 0),
      })
      setEditingId('')
      setEditErrors((state) => {
        const next = { ...state }
        delete next[note.id]
        return next
      })
      pushToast('Note saved.', 'success')
      await loadNotes()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to save note.', 'error')
    }
  }

  function patchLocalNote(noteId, patch) {
    setNotesPayload((state) => ({
      ...(state || {}),
      items: (state?.items || []).map((item) => item.id === noteId ? { ...item, ...patch } : item),
    }))
  }

  function toggleSelected(noteId) {
    setFocusedNoteId(noteId)
    setSelectedIds((state) => state.includes(noteId) ? state.filter((item) => item !== noteId) : [...state, noteId])
  }

  async function handleBulkAction(action) {
    if (!selectedIds.length) {
      pushToast('Select at least one note first.', 'info')
      return
    }
    try {
      await bulkUpdateNotes({ note_ids: selectedIds, action })
      setSelectedIds([])
      pushToast(`Bulk action applied: ${action}.`, 'success')
      await loadNotes()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to apply bulk action.', 'error')
    }
  }

  async function handleSnooze(note, minutes) {
    try {
      await snoozeNote(note.id, minutes)
      pushToast(`Note snoozed for ${minutes >= 1440 ? '1 day' : minutes >= 60 ? `${Math.round(minutes / 60)} hours` : `${minutes} minutes`}.`, 'success')
      await loadNotes()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to snooze note.', 'error')
    }
  }

  async function handleAdvance(note) {
    try {
      await advanceNote(note.id)
      pushToast('Recurring note advanced.', 'success')
      await loadNotes()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to advance note.', 'error')
    }
  }

  async function handleResolveRepair(note) {
    try {
      await updateNote(note.id, { completed: true })
      setEditingId('')
      pushToast('Repair resolved. Reopening the desk view.', 'success')
      navigate(buildDashboardFocusUrl(location.search, note, { resolvedRepair: true }))
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to resolve repair note.', 'error')
    }
  }

  const notesArrivalKey = [
    focusParams.workflowFrom,
    focusParams.replaySource,
    focusParams.replayTitle,
    focusParams.replayStatus,
    focusParams.noteCompletion,
    focusParams.noteTicker,
    repairLensPaused ? 'paused' : 'active',
  ]
    .filter(Boolean)
    .join('|')

  useEffect(() => {
    setDismissedArrivalKey('')
  }, [notesArrivalKey])

  const notesArrivalContext = (() => {
    const hasReplayContext = Boolean(focusParams.replaySource || focusParams.replayTitle || focusParams.workflowFrom)
    if (!showRepairLensContext && !repairContextRestored && !hasReplayContext) return null

    const actions = []
    const preferredRepairLens =
      String(focusParams.noteCompletion || '').trim().toLowerCase() === 'completed' || repairLensValue === 'completed'
        ? 'completed'
        : 'open'

    if (repairLensPaused) {
      actions.push({
        label: preferredRepairLens === 'completed' ? 'Reapply repairs cleared' : 'Reapply open repairs',
        onClick: () => applyRepairLens(preferredRepairLens),
        variant: 'subtle',
      })
    }

    if (focusParams.journalReturn === '1') {
      actions.push({
        label: 'Back to journal review',
        onClick: () => navigate(buildJournalFocusUrl(location.search)),
      })
    }

    if (focusedNote?.ticker) {
      actions.push({
        label: repairLensActive ? 'Open focused note on desk' : 'Open note on desk',
        onClick: () => navigate(buildDashboardFocusUrl(location.search, focusedNote)),
      })
    } else if (latestResolvedRepair && repairLensValue === 'completed' && !latestClearInFocus) {
      actions.push({
        label: 'Focus latest clear',
        onClick: focusLatestResolvedRepair,
      })
    }

    const replayLabel = formatReplayArrivalLabel(focusParams.replaySource, focusParams.replayStatus)
    const replayReference = focusParams.replayTitle ? `${replayLabel} "${focusParams.replayTitle}"` : replayLabel
    const replayPrefix =
      focusParams.workflowFrom === 'portfolio'
        ? `This repair thread came from ${replayReference} in Portfolio.`
        : focusParams.workflowFrom === 'journal'
          ? `This repair thread came from ${replayReference} in Journal.`
          : hasReplayContext
            ? `This repair thread is still carrying ${replayReference}.`
            : ''

    return {
      tone: repairLensPaused ? 'warning' : 'info',
      title: repairLensPaused
        ? intradayReview.active ? 'Same-session repair context restored, but paused' : 'Repair context restored, but paused'
        : hasReplayContext
          ? `${repairLensLabel} restored from replay`
          : `${repairLensLabel} restored`,
      detail: repairLensPaused
        ? `${replayPrefix} Broader filters are masking the repair lens right now. Reapply the repair view before reopening the desk or closing the loop.`.trim()
        : intradayReview.active
          ? `${replayPrefix} Notes is anchored to ${repairLensLabel.toLowerCase()} so the same-session repair thread stays attached to the right ticker, Journal can reopen without losing context, and resolved notes can go back to the desk cleanly.`.trim()
          : `${replayPrefix} Notes is anchored to ${repairLensLabel.toLowerCase()} so the repair thread stays attached to the right ticker, Journal can reopen without losing context, and resolved notes can go back to the desk cleanly.`.trim(),
      actions,
    }
  })()

  if (loading) {
    return (
      <LoadingBlock
        label="Loading notes workspace"
        detail="Restoring desk memory, repair threads, and upcoming reminders so the repair loop opens with the right context."
      />
    )
  }

  return (
    <>
      {error ? (
        <ErrorState
          title="Notes workspace unavailable"
          description={error}
          actionLabel="Reload notes"
          onAction={loadNotes}
        />
      ) : null}
      <PageIntro
        kicker="Operator notes"
        title="Notes"
        description={
          intradayReview.active
            ? 'Capture same-session repairs, reminders, and execution context without losing the desk state that created them.'
            : 'Capture trade ideas, reminders, and execution context without leaving the platform.'
        }
        helper={
          intradayReview.active
            ? 'Write the same-session repair note first, then use the board and saved notes to reopen the exact intraday context later.'
            : 'Write the note first, then work the board and saved notes when the repair thread or reminder is already in motion.'
        }
        badge="Desk memory"
        actions={(
          <ActionBar compact>
            <Chip tone="neutral" size="sm">/ focus title</Chip>
            <Chip tone="neutral" size="sm">Shift+J jump to notes</Chip>
          </ActionBar>
        )}
      />
      {notesArrivalContext && dismissedArrivalKey !== notesArrivalKey ? (
        <WorkflowArrivalBanner
          title={notesArrivalContext.title}
          detail={notesArrivalContext.detail}
          tone={notesArrivalContext.tone}
          actions={notesArrivalContext.actions}
          onDismiss={() => setDismissedArrivalKey(notesArrivalKey)}
        />
      ) : null}
      <WorkflowGuide
        showSteps={false}
        phaseLabel="Phase 4 - Review and repair"
        phaseTone="warning"
        title={
          intradayReview.active
            ? 'Use notes to preserve same-session desk memory and reopen the right repair context.'
            : 'Use notes to preserve desk memory and reopen the right review context.'
        }
        description={
          intradayReview.active
            ? 'This page should keep same-session repairs, late cleanup rules, and route fixes attached to the right ticker instead of turning into a generic scratchpad.'
            : 'This page should help you keep repairs, reminders, and follow-ups attached to the right ticker and workflow state instead of turning into a generic scratchpad.'
        }
        steps={buildWorkflowSteps(3)}
        cards={[
          {
            label: 'Use this page for',
            value: intradayReview.active
              ? 'Capture what the desk must change before the next intraday session.'
              : 'Capture what the desk needs to remember next session.',
            detail: intradayReview.active
              ? 'Notes are strongest when they hold same-session repairs, blockers, and route changes that would otherwise be lost between pages.'
              : 'Notes are strongest when they hold repairs, blockers, and follow-up context that would otherwise be lost between pages.',
          },
          {
            label: 'Best next move',
            value: intradayReview.active
              ? 'Reconnect same-session repairs back to journal or dashboard while the context is still fresh.'
              : 'Reconnect open repairs back to journal or dashboard when they are actionable.',
            detail: intradayReview.active
              ? 'The right note should reopen the exact intraday review state, not just store text.'
              : 'The right note should reopen the right review state, not just store text.',
            tone: 'positive',
          },
          {
            label: 'Do not ignore',
            value: 'Open repairs should stay visible until the rule change is clear.',
            detail: intradayReview.active
              ? 'If a note cannot tell you what changed before the next open, who owns it, or which session rule it fixes, it is probably just hiding unfinished work.'
              : 'If a note cannot tell you what changed, who owns it, or where to return, it is probably just hiding unfinished work.',
            tone: 'warning',
          },
        ]}
      />
      {intradayReview.active ? (
        <SectionCard
          eyebrow="Same-session review"
          title="Intraday repair queue"
          subtitle={intradayReview.guideDetail}
        >
          <section className="metrics-grid">
            {intradayReview.notesCards.map((item) => (
              <MetricCard key={item.label} {...item} />
            ))}
          </section>
        </SectionCard>
      ) : null}
      <section className="metrics-grid">{metrics.map((item) => <MetricCard key={item.label} {...item} />)}</section>
      <section className="content-grid">
        <SectionCard eyebrow="Write" title="Capture note" subtitle="Create a quick note tied to a ticker, workflow, or setup.">
          <div className="tag-chip-row">
            {noteTemplates.map((template) => (
              <Chip key={template.label} as="button" type="button" tone="neutral" className="tag-chip" onClick={() => applyTemplate(template)}>
                {template.label}
              </Chip>
            ))}
          </div>
          {reviewTemplates.length ? (
            <>
              <div className="workspace-summary-card">
                <span>{intradayReview.active ? 'Same-session repair prompts' : 'Repair loop prompts'}</span>
                <strong>
                  {latestReview?.ticker
                    ? `${latestReview.ticker} ${latestReview.label}`
                    : `${reviewTemplates.length} prompt${reviewTemplates.length === 1 ? '' : 's'} ready`}
                </strong>
              </div>
              {latestReview?.detail ? (
                <p className="ui-note">{latestReview.detail}</p>
              ) : null}
              <div className="tag-chip-row">
                {reviewTemplates.map((template) => (
                  <Chip key={template.label} as="button" type="button" tone="warning" className="tag-chip" onClick={() => applyTemplate(template)}>
                    {template.label}
                  </Chip>
                ))}
              </div>
            </>
          ) : null}
          <form className="analysis-form analysis-form--wide" onSubmit={handleCreate}>
            <TextField id="notes-capture-title" label="Title" hint="Use a short name that will still make sense when this note is reopened later." error={draftErrors.title} required ariaLabel="Note title" value={draft.title} onChange={(e) => updateDraftField('title', e.target.value)} placeholder="Note title" maxLength={120} />
            <TextField label="Ticker" hint="Optional, but useful when this note should reopen on the desk." error={draftErrors.ticker} ariaLabel="Ticker" value={draft.ticker} onChange={(e) => updateDraftField('ticker', e.target.value.toUpperCase())} placeholder="Ticker" maxLength={8} />
            <TextField label="Tags" hint="Comma-separated tags help this note show up in the right repair lane." ariaLabel="Tags" value={draft.tags} onChange={(e) => updateDraftField('tags', e.target.value)} placeholder="tags, comma-separated" />
            <TextField label="Owner or lane" hint="Use this when the note belongs to a specific person or operating lane." ariaLabel="Owner or lane" value={draft.owner} onChange={(e) => updateDraftField('owner', e.target.value)} placeholder="Owner / lane" maxLength={40} />
            <TextField label="Source URL" hint="Optional source link for filings, docs, or screenshots. Use a full http or https URL." error={draftErrors.sourceUrl} ariaLabel="Source URL" value={draft.sourceUrl} onChange={(e) => updateDraftField('sourceUrl', e.target.value)} placeholder="Source URL" maxLength={300} />
            <TextField label="Related note IDs" hint="Comma-separated note IDs this entry should stay attached to." ariaLabel="Related note IDs" value={draft.relatedIds} onChange={(e) => updateDraftField('relatedIds', e.target.value)} placeholder="Related note IDs, comma-separated" />
            <TextField label="Blocked by note IDs" hint="Use this when the next action depends on another note finishing first." ariaLabel="Blocked by note IDs" value={draft.blockedByIds} onChange={(e) => updateDraftField('blockedByIds', e.target.value)} placeholder="Blocked by note IDs, comma-separated" />
            <SelectField label="Priority" hint="Set how visible this note should be in the working lanes." ariaLabel="Draft note priority" value={draft.priority} onChange={(e) => updateDraftField('priority', e.target.value)}>{(filters.note_priorities || ['all', 'high', 'medium', 'low']).filter((item) => item !== 'all').map((option) => <option key={option} value={option}>{option}</option>)}</SelectField>
            <SelectField label="Note type" hint="Choose the closest note type so the desk can group it correctly." ariaLabel="Draft note type" value={draft.noteType} onChange={(e) => updateDraftField('noteType', e.target.value)}>{(filters.note_types || ['all', 'general']).filter((item) => item !== 'all').map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
            <TextField label="Due at" hint="Optional due time for deadline-driven notes." ariaLabel="Draft due date and time" type="datetime-local" value={draft.dueAt} onChange={(e) => updateDraftField('dueAt', e.target.value)} />
            <TextField label="Reminder at" hint="Reminder should happen before the due time when both are set." error={draftErrors.reminderAt} ariaLabel="Draft reminder date and time" type="datetime-local" value={draft.reminderAt} onChange={(e) => updateDraftField('reminderAt', e.target.value)} />
            <SelectField label="Recurrence" hint="Use recurrence for reminders that should keep coming back until cleared." ariaLabel="Draft recurrence" value={draft.recurrence} onChange={(e) => updateDraftField('recurrence', e.target.value)}>{(filters.note_recurrences || ['all','none','daily','weekly','weekdays','monthly']).filter((item) => item !== 'all').map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
            <TextField label="Recurrence end" hint="Optional end date for repeating notes." ariaLabel="Draft recurrence end date and time" type="datetime-local" value={draft.recurrenceEndAt} onChange={(e) => updateDraftField('recurrenceEndAt', e.target.value)} />
            <TextField label="Estimate minutes" hint="Optional expected effort for this task or review." error={draftErrors.estimateMinutes} ariaLabel="Estimated minutes" type="number" min={0} step={5} value={draft.estimateMinutes} onChange={(e) => updateDraftField('estimateMinutes', e.target.value)} placeholder="Estimate minutes" />
            <TextField label="Spent minutes" hint="Optional time already spent on this note or repair thread." error={draftErrors.spentMinutes} ariaLabel="Spent minutes" type="number" min={0} step={5} value={draft.spentMinutes} onChange={(e) => updateDraftField('spentMinutes', e.target.value)} placeholder="Spent minutes" />
            <Button type="submit" variant="solid">Save note</Button>
          </form>
          <TextAreaField label="Body" hint="Capture the actual context, rule change, or follow-up the desk should remember." error={draftErrors.body} required ariaLabel="Note body" inputClassName="notes-textarea" value={draft.body} onChange={(e) => updateDraftField('body', e.target.value, { clear: ['body'] })} placeholder="Execution details, market context, risks, or follow-up tasks..." rows={8} />
          <TextAreaField label="Checklist" hint="Optional checklist items, one per line. A checklist can satisfy the actionability requirement even if the body stays short." ariaLabel="Note checklist" inputClassName="notes-textarea" value={draft.checklist} onChange={(e) => updateDraftField('checklist', e.target.value, { clear: ['body'] })} placeholder="Checklist items, one per line" rows={4} />
        </SectionCard>
        <SectionCard eyebrow="Find" title="Filters" subtitle="Narrow down notes by ticker, status, due state, or type." actions={(<ActionBar compact><Button type="button" variant="ghost" size="sm" onClick={loadNotes}>Refresh</Button><Button type="button" variant="ghost" size="sm" onClick={handleExport}>Export</Button><FilePickerButton accept="application/json" onFileSelect={handleImport}>Import</FilePickerButton><Button type="button" variant="subtle" size="sm" onClick={() => handleBulkAction('complete')}>Complete selected</Button><Button type="button" variant="subtle" size="sm" onClick={() => handleBulkAction('archive')}>Archive selected</Button></ActionBar>)}>
          <DataToolbar
            searchValue={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search notes"
            actions={(<>
              <TextField ariaLabel="Filter notes by ticker" value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="Ticker" maxLength={8} />
              <SelectField ariaLabel="Filter notes by status" value={status} onChange={(e) => setStatus(e.target.value)}>{(filters.note_statuses || ['all', 'active', 'archived']).map((option) => <option key={option} value={option}>{option}</option>)}</SelectField>
              <SelectField ariaLabel="Filter notes by priority" value={priority} onChange={(e) => setPriority(e.target.value)}>{(filters.note_priorities || ['all', 'high', 'medium', 'low']).map((option) => <option key={option} value={option}>{option}</option>)}</SelectField>
              <SelectField ariaLabel="Filter notes by type" value={noteType} onChange={(e) => setNoteType(e.target.value)}>{(filters.note_types || ['all', 'general']).map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
              <SelectField ariaLabel="Filter notes by due state" value={dueState} onChange={(e) => setDueState(e.target.value)}>{(filters.note_due_states || ['all']).map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
              <SelectField ariaLabel="Choose repair lens" value={repairLensValue} onChange={(e) => applyRepairLens(e.target.value)}>
                <option value="open">Repair lens: open</option>
                <option value="completed">Repair lens: cleared</option>
              </SelectField>
              <SelectField ariaLabel="Filter notes by completion" value={completionState} onChange={(e) => setCompletionState(e.target.value)}>{(filters.note_completion_states || ['all', 'open', 'completed']).map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
              <SelectField ariaLabel="Sort notes" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>{(filters.note_sorts || ['updated_desc']).map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
              <SelectField ariaLabel="Filter notes by linked records" value={hasLink} onChange={(e) => setHasLink(e.target.value)}>{(filters.note_link_filters || ['all','yes','no']).map((option) => <option key={option} value={option}>{option === 'all' ? 'All links' : option === 'yes' ? 'With link' : 'No link'}</option>)}</SelectField>
              <SelectField ariaLabel="Filter notes by checklist state" value={checklistState} onChange={(e) => setChecklistState(e.target.value)}>{(filters.note_checklist_states || ['all','none','open','done']).map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
              <SelectField ariaLabel="Filter notes by reminder state" value={reminderState} onChange={(e) => setReminderState(e.target.value)}>{(filters.note_reminder_states || ['all','none','scheduled','today','due','upcoming']).map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
              <SelectField ariaLabel="Filter notes by recurrence" value={recurrence} onChange={(e) => setRecurrence(e.target.value)}>{(filters.note_recurrences || ['all','none','daily','weekly','weekdays','monthly']).map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
              <SelectField ariaLabel="Filter notes by blocked state" value={blockedState} onChange={(e) => setBlockedState(e.target.value)}>{(filters.note_blocked_states || ['all','ready','blocked']).map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
              <SelectField ariaLabel="Filter notes by progress state" value={progressState} onChange={(e) => setProgressState(e.target.value)}>{(filters.note_progress_states || ['all','not_started','planned','in_progress','done']).map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
              <TextField ariaLabel="Filter notes by tag" value={tag} onChange={(e) => setTag(e.target.value.toLowerCase())} placeholder="Tag" />
              <TextField ariaLabel="Filter notes by owner" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="Owner" maxLength={40} />
              <ToggleField label="Pinned only" checked={pinnedOnly} onChange={(e) => setPinnedOnly(e.target.checked)} />
            </>)}
          />
          {showRepairLensContext ? (
            <>
              {repairContextRestored ? (
                <div className="workspace-summary-card">
                  <span>{intradayReview.active ? 'Same-session repair context restored' : 'Repair context restored'}</span>
                  <strong>{repairLensPaused ? 'Paused until filters narrow back down' : `${repairLensLabel} restored`}</strong>
                </div>
              ) : null}
              <div className="workspace-summary-card">
                <span>Repair lens</span>
                <strong>{repairLensPaused ? 'Paused' : repairLensLabel}</strong>
              </div>
              <div className="tag-chip-row">
                <Chip tone={repairLensPaused ? 'warning' : 'positive'} className="tag-chip">
                  {repairLensPaused ? 'Repair lens paused' : repairLensLabel}
                </Chip>
              </div>
              <p className="ui-note">
                {repairLensPaused
                  ? 'Journal repair context is paused while Notes is using broader filters. Reapply the repair lens to return to repair notes.'
                  : `Notes is focused on ${repairLensLabel.toLowerCase()} so the repair workflow stays aligned with Journal.`}
              </p>
            </>
          ) : null}
          <div className="metrics-grid">
            <button
              type="button"
              className="metric-card-button"
              onClick={() => applyRepairLens('open')}
            >
              <MetricCard label="Open repairs" value={reviewLoopSummary.open_count ?? 0} />
            </button>
            <button
              type="button"
              className="metric-card-button"
              onClick={() => applyRepairLens('completed')}
            >
              <MetricCard label="Repairs cleared" value={reviewLoopSummary.resolved_count ?? 0} />
            </button>
          </div>
          <div className="workspace-summary-card">
            <span>Latest clear</span>
            <strong>
              {latestResolvedRepair
                ? `${latestResolvedRepair.ticker || 'Desk'} - ${latestResolvedRepair.title || 'Resolved repair note'}`
                : 'No cleared repair notes yet'}
            </strong>
          </div>
          {latestResolvedRepair ? (
            <ActionBar compact>
              <Button type="button" variant="ghost" size="sm" onClick={focusLatestResolvedRepair}>
                Focus latest clear
              </Button>
            </ActionBar>
          ) : null}
          <div className="tag-chip-row">
            {(notesPayload?.tags || []).slice(0, 10).map((item) => (
              <Chip key={item.tag} as="button" type="button" tone="neutral" className="tag-chip" onClick={() => setTag(item.tag)}>
                {item.tag} ({item.count})
              </Chip>
            ))}
          </div>
          <div className="tag-chip-row">
            {(notesPayload?.owners || []).slice(0, 8).map((item) => (
              <Chip key={item.owner} as="button" type="button" tone="neutral" className="tag-chip" onClick={() => setOwner(item.owner)}>
                {item.owner} ({item.count})
              </Chip>
            ))}
          </div>
        </SectionCard>
      </section>
      <section className="content-grid">
      <SectionCard eyebrow="Recent memory" title="Recent notes" subtitle="Most recently updated operator notes for quick recall.">
        <div className="notes-grid">
          {(recentNotes?.items || []).map((item) => (
            <article key={item.id} className="note-card">
              <div className="note-card__head"><div><Kicker as="div"><InlineMeta items={[item.ticker || 'General', friendlyLabel(item.note_type || 'general')]} /></Kicker><h3>{item.title}</h3></div></div>
              <p>{item.body || 'No additional detail provided.'}</p>
              <div className="tag-chip-row">
                {item.reminder_at ? <Chip tone="warning" className="tag-chip">Reminder {new Date(item.reminder_at).toLocaleString()}</Chip> : null}
                {item.due_at ? <Chip tone="warning" className="tag-chip">Due {new Date(item.due_at).toLocaleString()}</Chip> : null}
                <Chip as="button" type="button" tone="neutral" className="tag-chip" onClick={() => setSearch(item.title || item.ticker || "")}>Open in list</Chip>
              </div>
            </article>
          ))}
          {!recentNotes?.items?.length ? (
            <EmptyState
              title="No recent notes"
              description="Start here with one repair note or trade idea so this page begins keeping desk memory."
              actionLabel={`Start ${starterReviewTemplate?.label || 'note'}`}
              onAction={() => applyTemplate(starterReviewTemplate)}
              secondaryActionLabel="Open journal review"
              onSecondaryAction={() => navigate(buildJournalFocusUrl(location.search))}
            />
          ) : null}
        </div>
      </SectionCard>
      <SectionCard eyebrow="Time-sensitive" title="Agenda" subtitle="Upcoming reminders and due items across the next week.">
        <div className="notes-grid">
          {(agenda?.items || []).slice(0, 10).map((item) => (
            <article key={`${item.id}-${item.agenda_kind}-${item.agenda_at}`} className="note-card">
              <div className="note-card__head"><div><Kicker as="div"><InlineMeta items={[item.ticker || 'General', friendlyLabel(item.agenda_kind || 'agenda')]} /></Kicker><h3>{item.title}</h3></div></div>
              <p><InlineMeta items={[new Date(item.agenda_at).toLocaleString(), item.recurrence && item.recurrence !== 'none' ? friendlyLabel(item.recurrence) : null]} /></p>
              <div className="tag-chip-row">
                <Chip as="button" type="button" tone="neutral" className="tag-chip" onClick={() => setSearch(item.title || item.ticker || '')}>Open in list</Chip>
                {item.recurrence && item.recurrence !== 'none' ? <Chip as="button" type="button" tone="positive" className="tag-chip" onClick={() => handleAdvance(item)}>Advance</Chip> : null}
              </div>
            </article>
          ))}
          {!agenda?.items?.length ? <EmptyState title="No agenda items" description="There are no reminders or due items scheduled for the next week." /> : null}
        </div>
      </SectionCard>
      <SectionCard eyebrow="Operating lanes" title="Board" subtitle="Operational note lanes for blocked, ready, urgent, and reminder-driven work.">
        <div className="notes-grid">
          {(board?.columns || []).map((column) => (
            <article key={column.key} className="note-card">
              <div className="note-card__head"><div><Kicker as="div">{column.label}</Kicker><h3>{column.count}</h3></div></div>
              <div className="tag-chip-row">
                {(column.items || []).slice(0, 6).map((item) => (
                  <Chip key={`${column.key}_${item.id}`} as="button" type="button" tone="neutral" className="tag-chip" onClick={() => setSearch(item.title || item.ticker || '')}>
                    {item.ticker || 'General'} - {item.title}
                  </Chip>
                ))}
              </div>
            </article>
          ))}
          {!board?.columns?.length ? (
            <EmptyState
              title="No board lanes"
              description="Start here with one repair note, then this board becomes the blocked, ready, and urgent work queue."
              actionLabel={`Start ${starterReviewTemplate?.label || 'note'}`}
              onAction={() => applyTemplate(starterReviewTemplate)}
              secondaryActionLabel="Open desk"
              onSecondaryAction={() => navigate('/')}
            />
          ) : null}
        </div>
      </SectionCard>
      <SectionCard eyebrow="Deadlines" title="Due calendar" subtitle="Upcoming note deadlines across the next two weeks.">
        <div className="notes-grid">
          {(calendar?.groups || []).map((group) => (
            <article key={group.date} className="note-card">
              <div className="note-card__head"><div><Kicker as="div">{new Date(`${group.date}T00:00:00`).toLocaleDateString()}</Kicker><h3>{group.count} due</h3></div></div>
              <div className="tag-chip-row">
                {group.items.slice(0, 6).map((item) => (
                  <Chip key={item.id} as="button" type="button" tone="neutral" className="tag-chip" onClick={() => setSearch(item.title || item.ticker || '')}>
                    {item.ticker || 'General'} - {item.title}
                  </Chip>
                ))}
              </div>
            </article>
          ))}
          {!calendar?.groups?.length ? <EmptyState title="No upcoming dates" description="There are no dated notes scheduled across the next two weeks." /> : null}
        </div>
      </SectionCard>
      </section>
      <SectionCard eyebrow="Working memory" title="Saved notes" subtitle={savedNotesSubtitle}>
        {latestClearInFocus ? (
          <>
            <div className="workspace-summary-card">
              <span>Latest clear in focus</span>
              <strong>
                {latestResolvedRepair?.ticker || 'Desk'} - {latestResolvedRepair?.title || 'Resolved repair note'}
              </strong>
            </div>
            <p className="ui-note">
              This saved-notes view is anchored to the most recently cleared repair note, so you can confirm the resolved follow-up before moving on.
            </p>
          </>
        ) : null}
        <div className="notes-grid">
          {(notesPayload?.items || []).map((note) => {
            const isEditing = editingId === note.id
            const noteErrors = editErrors[note.id] || {}
            const noteTags = Array.isArray(note.tags) ? note.tags : []
            const isReviewLoopNote =
              noteTags.includes('review-loop') || String(note.owner || '').trim().toLowerCase() === 'review-loop'
            const canOpenOnDashboard = Boolean(String(note.ticker || '').trim())
            const isFocusedReviewLoopNote =
              focusParams.noteFocus === 'review-loop' &&
              (!focusParams.noteId || String(focusParams.noteId) === String(note.id))
            const canReturnToJournalReview =
              isFocusedReviewLoopNote &&
              isReviewLoopNote &&
              focusParams.journalReturn === '1'
            return (
              <article
                id={`note-card-${note.id}`}
                key={note.id}
                className={`note-card ${note.pinned ? 'note-card--pinned' : ''} ${note.due_state === 'overdue' ? 'note-card--overdue' : ''} ${note.completed ? 'note-card--completed' : ''} ${focusedNoteId === note.id ? 'note-card--focused' : ''}`}
              >
                <div className="note-card__head">
                  <div>
                    <ToggleField label="Select" className="toggle-inline" checked={selectedIds.includes(note.id)} onChange={() => toggleSelected(note.id)} />
                    <Kicker as="div">
                      <InlineMeta
                        items={[
                          note.ticker || 'General',
                          friendlyLabel(note.note_type),
                          note.priority || 'medium',
                          note.owner || null,
                        ]}
                      />
                    </Kicker>
                    {!isEditing ? <h3>{note.title}</h3> : null}
                  </div>
                  <div className="note-card__actions">
                    <Button type="button" variant="ghost" size="sm" className="desk-action" onClick={() => togglePinned(note)}>{note.pinned ? 'Unpin' : 'Pin'}</Button>
                    <Button type="button" variant="ghost" size="sm" className="desk-action" onClick={() => cyclePriority(note)}>Priority</Button>
                    <Button type="button" variant="ghost" size="sm" className="desk-action" onClick={() => toggleCompleted(note)}>{note.completed ? 'Reopen' : 'Complete'}</Button>
                    {canReturnToJournalReview ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="desk-action"
                        onClick={() => navigate(buildJournalFocusUrl(location.search))}
                      >
                        Back to journal review
                      </Button>
                    ) : null}
                    {isReviewLoopNote && canOpenOnDashboard ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="desk-action"
                        onClick={() => navigate(buildDashboardFocusUrl(location.search, note))}
                      >
                        {isFocusedReviewLoopNote ? 'Back to desk' : 'Open on dashboard'}
                      </Button>
                    ) : null}
                    {isFocusedReviewLoopNote && !note.completed ? (
                      <Button
                        type="button"
                        variant="solid"
                        size="sm"
                        className="desk-action"
                        onClick={() => handleResolveRepair(note)}
                      >
                        Resolve repair and reopen desk
                      </Button>
                    ) : null}
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="desk-action"
                      onClick={() => {
                        setFocusedNoteId(note.id)
                        setEditingId(isEditing ? '' : note.id)
                        clearEditFieldError(note.id, Object.keys(noteErrors))
                      }}
                    >
                      {isEditing ? 'Cancel' : 'Edit'}
                    </Button>
                    <Button type="button" variant="ghost" size="sm" className="desk-action" onClick={() => handleDuplicate(note)}>Duplicate</Button>
                    <Button type="button" variant="subtle" size="sm" className="desk-action" onClick={() => toggleArchived(note)}>{note.archived ? 'Restore' : 'Archive'}</Button>
                    <Button type="button" variant="subtle" size="sm" className="desk-action" onClick={() => handleDelete(note)}>Delete</Button>
                  </div>
                </div>
                {!isEditing ? (
                  <>
                    <p>{note.body || 'No additional detail provided.'}</p>
                    <div className="tag-chip-row">
                      <Chip tone={note.due_state === "overdue" ? "negative" : note.due_state === "due_today" ? "warning" : "neutral"} className={`tag-chip note-status-chip note-status-chip--${note.due_state || "none"}`}>{friendlyLabel(note.due_state || "none")}</Chip>
                      <Chip tone={note.blocked_state === "blocked" ? "negative" : "neutral"} className="tag-chip">{friendlyLabel(note.blocked_state || "ready")}</Chip>
                      {note.due_at ? <Chip tone="warning" className="tag-chip">Due {new Date(note.due_at).toLocaleString()}</Chip> : null}
                      {note.reminder_at ? <Chip tone="warning" className="tag-chip">Reminder {new Date(note.reminder_at).toLocaleString()}</Chip> : null}
                      {note.owner ? <Chip tone="neutral" className="tag-chip">Owner {note.owner}</Chip> : null}
                      <Chip tone="neutral" className="tag-chip">{friendlyLabel(note.progress_state || "not_started")}</Chip>
                      <Chip tone="neutral" className="tag-chip">{note.progress_percent ?? 0}% progress</Chip>
                      {note.estimate_minutes ? <Chip tone="neutral" className="tag-chip">Est. {note.estimate_minutes}m</Chip> : null}
                      {note.spent_minutes ? <Chip tone="neutral" className="tag-chip">Spent {note.spent_minutes}m</Chip> : null}
                      {(note.related_note_ids || []).length ? <Chip tone="neutral" className="tag-chip">Related {(note.related_note_ids || []).length}</Chip> : null}
                      {(note.blocked_by_ids || []).length ? <Chip tone="negative" className="tag-chip">Blocked by {(note.blocked_by_ids || []).length}</Chip> : null}
                      {note.source_url ? <Chip as="a" tone="neutral" className="tag-chip" href={note.source_url} target="_blank" rel="noreferrer">Open source</Chip> : null}
                      {(note.tags || []).map((item) => <Chip key={item} tone="neutral" className="tag-chip">#{item}</Chip>)}
                    </div>
                    <div className="workspace-summary-card"><span>Progress</span><strong>{note.progress_percent ?? 0}% - {friendlyLabel(note.progress_state || "not_started")}</strong></div>
                    {note.checklist_progress?.total ? <div className="workspace-summary-card"><span>Checklist</span><strong>{note.checklist_progress.done}/{note.checklist_progress.total} complete</strong></div> : null}
                    {note.checklist_progress?.total ? <div className="tag-chip-row">{(note.checklist || []).map((item, index) => <ChecklistChip key={`${note.id}_${index}`} done={item.done} className="tag-chip">{item.text}</ChecklistChip>)}</div> : null}
                    <div className="tag-chip-row">{(filters.note_snooze_presets || []).map((preset) => <Chip key={preset.label} as="button" type="button" tone="neutral" className="tag-chip" onClick={() => handleSnooze(note, preset.minutes)}>Snooze {preset.label}</Chip>)}</div>
                  </>
                ) : (
                  <form className="note-edit-grid" onSubmit={handleEditSubmit}>
                    <TextField label="Title" hint="Keep the saved title clear enough to reopen later without extra context." error={noteErrors.title} required ariaLabel="Note title" value={note.title || ''} onChange={(e) => { patchLocalNote(note.id, { title: e.target.value }); clearEditFieldError(note.id, 'title') }} placeholder="Title" maxLength={120} />
                    <TextField label="Ticker" hint="Optional desk symbol for reopening this note on the chart later." error={noteErrors.ticker} ariaLabel="Note ticker" value={note.ticker || ''} onChange={(e) => { patchLocalNote(note.id, { ticker: e.target.value.toUpperCase() }); clearEditFieldError(note.id, 'ticker') }} placeholder="Ticker" maxLength={8} />
                    <TextField label="Owner" hint="Use the current owner or operating lane when this note has a clear home." ariaLabel="Note owner" value={note.owner || ''} onChange={(e) => patchLocalNote(note.id, { owner: e.target.value })} placeholder="Owner" maxLength={40} />
                    <TextField label="Source URL" hint="Optional source link for docs, filings, or screenshots." error={noteErrors.sourceUrl} ariaLabel="Note source URL" value={note.source_url || ''} onChange={(e) => { patchLocalNote(note.id, { source_url: e.target.value }); clearEditFieldError(note.id, 'sourceUrl') }} placeholder="Source URL" maxLength={300} />
                    <SelectField label="Priority" hint="Priority controls how loudly this note should surface in the board." ariaLabel="Note priority" value={note.priority || 'medium'} onChange={(e) => patchLocalNote(note.id, { priority: e.target.value })}>{(filters.note_priorities || ['all', 'high', 'medium', 'low']).filter((item) => item !== 'all').map((option) => <option key={option} value={option}>{option}</option>)}</SelectField>
                    <SelectField label="Note type" hint="Keep the type aligned with the desk behavior this note should support." ariaLabel="Note type" value={note.note_type || 'general'} onChange={(e) => patchLocalNote(note.id, { note_type: e.target.value })}>{(filters.note_types || ['all', 'general']).filter((item) => item !== 'all').map((option) => <option key={option} value={option}>{friendlyLabel(option)}</option>)}</SelectField>
                    <TextField label="Due at" hint="Optional deadline for when this note should stop waiting." ariaLabel="Note due date and time" type="datetime-local" value={toInputDateTime(note.due_at)} onChange={(e) => patchLocalNote(note.id, { due_at: e.target.value || null })} />
                    <TextField label="Reminder at" hint="Reminder should happen before the due time when both are set." error={noteErrors.reminderAt} ariaLabel="Note reminder date and time" type="datetime-local" value={toInputDateTime(note.reminder_at)} onChange={(e) => { patchLocalNote(note.id, { reminder_at: e.target.value || null }); clearEditFieldError(note.id, 'reminderAt') }} />
                    <TextField label="Estimate minutes" hint="Optional effort estimate for this note or repair thread." error={noteErrors.estimateMinutes} ariaLabel="Note estimated minutes" type="number" min={0} step={5} value={note.estimate_minutes || 0} onChange={(e) => { patchLocalNote(note.id, { estimate_minutes: e.target.value }); clearEditFieldError(note.id, 'estimateMinutes') }} placeholder="Estimate minutes" />
                    <TextField label="Spent minutes" hint="Optional time already invested in this note or fix." error={noteErrors.spentMinutes} ariaLabel="Note spent minutes" type="number" min={0} step={5} value={note.spent_minutes || 0} onChange={(e) => { patchLocalNote(note.id, { spent_minutes: e.target.value }); clearEditFieldError(note.id, 'spentMinutes') }} placeholder="Spent minutes" />
                    <TextField label="Tags" hint="Comma-separated tags help keep this note discoverable." ariaLabel="Note tags" value={(note.tags || []).join(', ')} onChange={(e) => patchLocalNote(note.id, { tags: e.target.value.split(',').map((item) => item.trim()).filter(Boolean) })} placeholder="tags, comma-separated" />
                    <TextField label="Related note IDs" hint="Optional linked notes that should stay attached to this thread." ariaLabel="Related note IDs" value={(note.related_note_ids || []).join(', ')} onChange={(e) => patchLocalNote(note.id, { related_note_ids: e.target.value.split(',').map((item) => item.trim()).filter(Boolean) })} placeholder="Related note IDs" />
                    <TextField label="Blocked by note IDs" hint="Use blockers when this note depends on another note closing first." ariaLabel="Blocked by note IDs" value={(note.blocked_by_ids || []).join(', ')} onChange={(e) => patchLocalNote(note.id, { blocked_by_ids: e.target.value.split(',').map((item) => item.trim()).filter(Boolean) })} placeholder="Blocked by note IDs" />
                    <TextAreaField label="Checklist" hint="Optional checklist items, one per line." ariaLabel="Note checklist" inputClassName="notes-textarea" value={(note.checklist || []).map((item) => item.text).join('\n')} onChange={(e) => { patchLocalNote(note.id, { checklist: e.target.value.split('\n').map((item) => item.trim()).filter(Boolean).map((line, index) => ({ text: line, done: note.checklist?.[index]?.done || false })) }); clearEditFieldError(note.id, 'body') }} rows={4} placeholder="Checklist items, one per line" />
                    <TextAreaField label="Body" hint="Keep enough detail here to explain what changed or what must happen next." error={noteErrors.body} required ariaLabel="Note body" inputClassName="notes-textarea" value={note.body || ''} onChange={(e) => { patchLocalNote(note.id, { body: e.target.value }); clearEditFieldError(note.id, 'body') }} rows={6} />
                    <div className="note-card__actions"><Button type="submit" variant="solid" size="sm">Save changes</Button></div>
                  </form>
                )}
                <div className="workspace-summary-card"><span>Updated</span><strong>{note.updated_at ? new Date(note.updated_at).toLocaleString() : '—'}</strong></div>
              </article>
            )
          })}
          {!notesPayload?.items?.length ? (
            <EmptyState
              title="No notes matched"
              description="Widen the filters or start here with one repair-thread note so this saved memory lane has something to reopen later."
              actionLabel={`Start ${starterReviewTemplate?.label || 'note'}`}
              onAction={() => applyTemplate(starterReviewTemplate)}
              secondaryActionLabel="Open desk"
              onSecondaryAction={() => navigate('/')}
            />
          ) : null}
        </div>
      </SectionCard>
    </>
  )
}


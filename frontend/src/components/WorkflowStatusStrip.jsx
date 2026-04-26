import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router-dom'
import { usePreferences } from '../context/PreferencesContext'
import Button from './Button'
import Chip from './Chip'

const AUTO_COLLAPSE_MS = 3000
const WORKFLOW_LABELS = [
  'Find signal',
  'Qualify',
  'Act safely',
  'Review and repair',
]

function titleCase(value = '') {
  return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

function buildDashboardTickerUrl(ticker = '') {
  const normalized = String(ticker || '').trim().toUpperCase()
  if (!normalized) return '/'
  const params = new URLSearchParams()
  params.set('ticker', normalized)
  return `/?${params.toString()}`
}

function buildNotesRepairUrl({ completion = 'open' } = {}) {
  const params = new URLSearchParams()
  params.set('noteFocus', 'review-loop')
  params.set('noteTag', 'review-loop')
  params.set('noteCompletion', completion === 'completed' ? 'completed' : 'open')
  params.set('journalReturn', '1')
  return `/notes?${params.toString()}`
}

function buildWorkflowStatusModel(location) {
  const params = new URLSearchParams(location.search || '')
  const workflowFrom = String(params.get('workflowFrom') || '').trim().toLowerCase()
  const focusTicker =
    String(params.get('focusTicker') || params.get('compareFocusTicker') || params.get('noteTicker') || params.get('ticker') || '')
      .trim()
      .toUpperCase()
  const noteCompletion = String(params.get('noteCompletion') || '').trim().toLowerCase()
  const journalRepairView = String(params.get('journalRepairView') || '').trim().toLowerCase()
  const noteFocus = String(params.get('noteFocus') || '').trim().toLowerCase()
  const journalRestored = String(params.get('journalRestored') || '').trim() === '1'
  const workflowAutoload = String(params.get('workflowAutoload') || '').trim() === '1'

  const base = {
    pageLabel: 'Desk',
    phaseIndex: 2,
    title: 'Act on one setup at a time.',
    detail: 'Keep the workstation oriented around the clearest next move instead of jumping between pages without context.',
    chips: [],
    actions: [],
  }

  if (location.pathname === '/watchlist') {
    return {
      ...base,
      pageLabel: 'Watchlist',
      phaseIndex: 0,
      title: 'Find the strongest candidates first.',
      detail: 'Use the liquid board to narrow the field before anything reaches compare or the desk.',
      chips: ['Board scan'],
      actions: [{ label: 'Open compare', to: '/compare' }],
    }
  }

  if (location.pathname === '/compare') {
    return {
      ...base,
      pageLabel: 'Compare',
      phaseIndex: 1,
      title: 'Qualify the leaders before they hit the desk.',
      detail: workflowAutoload
        ? 'A board handoff is active here, so compare should keep the incoming candidates together and send only the best leader forward.'
        : 'Use compare to narrow the board into one cleaner candidate before moving into action.',
      chips: [
        workflowAutoload ? 'Board handoff' : null,
        workflowFrom ? `${titleCase(workflowFrom)} source` : null,
        focusTicker ? `${focusTicker} focus` : null,
      ].filter(Boolean),
      actions: [
        { label: focusTicker ? `Open ${focusTicker} on desk` : 'Open desk', to: buildDashboardTickerUrl(focusTicker) },
        { label: 'Back to watchlist', to: '/watchlist', variant: 'ghost' },
      ],
    }
  }

  if (location.pathname === '/alerts') {
    return {
      ...base,
      pageLabel: 'Alerts',
      phaseIndex: 1,
      title: 'Triage interruptions without losing the board.',
      detail: 'Alerts should point you toward the next surface that needs attention, not replace the workstation hierarchy.',
      chips: ['Alert triage'],
      actions: [
        { label: 'Open compare', to: '/compare' },
        { label: 'Open trades', to: '/trades', variant: 'ghost' },
      ],
    }
  }

  if (location.pathname === '/') {
    return {
      ...base,
      pageLabel: 'Desk',
      phaseIndex: 2,
      title: 'Use the desk to act safely on one live idea.',
      detail: 'This is the act-safely surface, so the next move should usually be risk management or review rather than a new branch of work.',
      chips: [focusTicker ? `${focusTicker} loaded` : 'Live ticket'],
      actions: [
        { label: 'Open trades', to: '/trades' },
        { label: 'Open journal', to: '/journal', variant: 'ghost' },
      ],
    }
  }

  if (location.pathname === '/trades') {
    return {
      ...base,
      pageLabel: 'Trades',
      phaseIndex: 2,
      title: 'Manage live risk before searching for the next idea.',
      detail: 'Use this surface to reduce complexity, verify fills, and hand the position into review when the trade story is complete.',
      chips: ['Open risk'],
      actions: [
        { label: 'Open journal', to: '/journal' },
        { label: 'Open repair notes', to: buildNotesRepairUrl({ completion: 'open' }), variant: 'ghost' },
      ],
    }
  }

  if (location.pathname === '/portfolio') {
    return {
      ...base,
      pageLabel: 'Portfolio',
      phaseIndex: 2,
      title: 'Check exposure before adding new risk.',
      detail: 'Portfolio should confirm whether the desk can support more action or needs cleanup first.',
      chips: ['Exposure check'],
      actions: [
        { label: 'Open trades', to: '/trades' },
        { label: 'Open journal', to: '/journal', variant: 'ghost' },
      ],
    }
  }

  if (location.pathname === '/journal') {
    const completion = journalRepairView === 'completed' ? 'completed' : 'open'
    return {
      ...base,
      pageLabel: 'Journal',
      phaseIndex: 3,
      title: 'Turn results into repair language.',
      detail: journalRestored
        ? 'The journal has restored repair-loop context, so you can keep the same repair thread alive instead of restarting the review from scratch.'
        : 'Use the journal to separate thesis, execution, and discipline so the next rule is clearer than the last result.',
      chips: [
        journalRestored ? 'Review restored' : null,
        completion === 'completed' ? 'Repairs cleared' : 'Open repairs',
      ].filter(Boolean),
      actions: [
        { label: completion === 'completed' ? 'Open cleared notes' : 'Open repair notes', to: buildNotesRepairUrl({ completion }) },
        { label: 'Back to desk', to: '/', variant: 'ghost' },
      ],
    }
  }

  if (location.pathname === '/notes') {
    const completion = noteCompletion === 'completed' ? 'completed' : 'open'
    return {
      ...base,
      pageLabel: 'Notes',
      phaseIndex: 3,
      title: 'Keep the repair thread attached to the right setup.',
      detail: noteFocus === 'review-loop'
        ? 'Notes is running as part of the repair loop, so this page should preserve what changes before you reopen the desk or return to Journal.'
        : 'Use notes to preserve the next rule, blocker, or follow-up so the repair loop survives page changes.',
      chips: [
        noteFocus === 'review-loop' ? 'Repair loop' : null,
        completion === 'completed' ? 'Repairs cleared' : noteFocus === 'review-loop' ? 'Open repairs' : null,
        focusTicker ? `${focusTicker} context` : null,
      ].filter(Boolean),
      actions: [
        { label: focusTicker ? `Open ${focusTicker} on desk` : 'Open journal', to: focusTicker ? buildDashboardTickerUrl(focusTicker) : '/journal' },
        { label: 'Back to journal', to: '/journal', variant: 'ghost' },
      ],
    }
  }

  if (location.pathname === '/settings') {
    return {
      ...base,
      pageLabel: 'Settings',
      phaseIndex: 2,
      title: 'Tune the workstation rules before the next route.',
      detail: 'Settings should support the rest of the workflow by clarifying defaults, not become a separate operating mode.',
      chips: ['Desk policy'],
      actions: [
        { label: 'Back to desk', to: '/' },
        { label: 'Open trades', to: '/trades', variant: 'ghost' },
      ],
    }
  }

  if (location.pathname === '/education') {
    return {
      ...base,
      pageLabel: 'Guide',
      phaseIndex: 3,
      title: 'Learn in context, then return to the workstation.',
      detail: 'Education is most useful when it supports the current workflow and sends you back to the live desk with a clearer next move.',
      chips: ['Reference'],
      actions: [
        { label: 'Back to desk', to: '/' },
        { label: 'Open watchlist', to: '/watchlist', variant: 'ghost' },
      ],
    }
  }

  return base
}

export default function WorkflowStatusStrip() {
  const { preferences } = usePreferences()
  const location = useLocation()
  const navigate = useNavigate()
  const titleId = useId()
  const collapseTimerRef = useRef(null)
  const [collapsed, setCollapsed] = useState(false)
  const model = useMemo(
    () => buildWorkflowStatusModel(location),
    [location.pathname, location.search],
  )

  const clearCollapseTimer = () => {
    if (collapseTimerRef.current) {
      window.clearTimeout(collapseTimerRef.current)
      collapseTimerRef.current = null
    }
  }

  const scheduleCollapse = () => {
    clearCollapseTimer()
    collapseTimerRef.current = window.setTimeout(() => {
      setCollapsed(true)
    }, AUTO_COLLAPSE_MS)
  }

  useEffect(() => {
    if (!preferences?.showWorkflowStatusStrip) return undefined
    setCollapsed(false)
    scheduleCollapse()
    return () => clearCollapseTimer()
  }, [preferences?.showWorkflowStatusStrip, location.pathname, location.search])

  if (!model || !preferences?.showWorkflowStatusStrip) return null

  const strip = (
    <section
      className={`ui-panel ui-panel--section workflow-status-strip ${
        collapsed ? 'workflow-status-strip--collapsed' : ''
      }`}
      aria-labelledby={titleId}
      onMouseEnter={() => {
        clearCollapseTimer()
        setCollapsed(false)
      }}
      onMouseLeave={scheduleCollapse}
      onFocusCapture={() => {
        clearCollapseTimer()
        setCollapsed(false)
      }}
      onBlurCapture={() => {
        scheduleCollapse()
      }}
    >
      <button
        type="button"
        className="workflow-status-strip__peek"
        aria-label={`Expand workflow status for ${model.pageLabel}`}
        onClick={() => {
          clearCollapseTimer()
          setCollapsed(false)
        }}
      >
        <span className="workflow-status-strip__peek-phase">{model.phaseIndex + 1}</span>
        <span className="workflow-status-strip__peek-label">{model.pageLabel}</span>
      </button>
      <div className="workflow-status-strip__header">
        <div className="workflow-status-strip__copy">
          <div className="ui-kicker">Workflow status</div>
          <strong className="workflow-status-strip__title" id={titleId}>{model.title}</strong>
          <p className="workflow-status-strip__detail">{model.detail}</p>
        </div>
        <div className="workflow-status-strip__meta">
          <Chip tone="neutral" size="sm" active>
            {model.pageLabel}
          </Chip>
          <Chip tone="warning" size="sm">
            Phase {model.phaseIndex + 1}
          </Chip>
          {model.chips.map((item) => (
            <Chip key={item} tone="neutral" size="sm">
              {item}
            </Chip>
          ))}
        </div>
      </div>
      <ol className="workflow-status-strip__steps" aria-label="Workflow status steps">
        {WORKFLOW_LABELS.map((label, index) => (
          <li
            key={label}
            className={`workflow-status-strip__step ${
              index === model.phaseIndex
                ? 'workflow-status-strip__step--active'
                : index < model.phaseIndex
                  ? 'workflow-status-strip__step--covered'
                  : ''
            }`}
            aria-current={index === model.phaseIndex ? 'step' : undefined}
          >
            <span className="workflow-status-strip__step-index">{index + 1}</span>
            <span className="workflow-status-strip__step-label">{label}</span>
          </li>
        ))}
      </ol>
      {model.actions.length ? (
        <div className="workflow-status-strip__actions" aria-label="Workflow actions">
          {model.actions.map((action) => (
            <Button
              key={action.label}
              type="button"
              variant={action.variant || 'subtle'}
              size="sm"
              onClick={() => navigate(action.to)}
            >
              {action.label}
            </Button>
          ))}
        </div>
      ) : null}
    </section>
  )

  if (typeof document === 'undefined') {
    return strip
  }

  return createPortal(strip, document.body)
}

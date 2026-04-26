import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  deleteWorkspace,
  duplicateWorkspace,
  exportWorkspaces,
  getFrontendFilters,
  getSavedWorkspaces,
  importWorkspaces,
  updateWorkspace,
} from '../api/client'
import Button from '../components/Button'
import Chip from '../components/Chip'
import DataToolbar from '../components/DataToolbar'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import FeedbackState from '../components/FeedbackState'
import FilePickerButton from '../components/FilePickerButton'
import { SelectField, ToggleField } from '../components/FormFields'
import Kicker from '../components/Kicker'
import LoadingBlock from '../components/LoadingBlock'
import MetricCard from '../components/MetricCard'
import PageIntro from '../components/PageIntro'
import SectionCard from '../components/SectionCard'
import useDebouncedValue from '../hooks/useDebouncedValue'
import { usePreferences } from '../context/PreferencesContext'
import { useToast } from '../context/ToastContext'

export default function WorkspacesPage() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [payload, setPayload] = useState({ items: [], count: 0, tag_counts: {} })
  const [filters, setFilters] = useState({ workspace_pages: ['all'] })
  const [search, setSearch] = useState('')
  const [pageFilter, setPageFilter] = useState('all')
  const [sortBy, setSortBy] = useState('updated_desc')
  const [pinnedOnly, setPinnedOnly] = useState(false)
  const [tagFilter, setTagFilter] = useState('')
  const [workspaceActionState, setWorkspaceActionState] = useState(null)
  const [pendingDeleteWorkspace, setPendingDeleteWorkspace] = useState(null)
  const [exportBusy, setExportBusy] = useState(false)
  const [importBusy, setImportBusy] = useState(false)
  const [deleteBusyId, setDeleteBusyId] = useState('')
  const debouncedSearch = useDebouncedValue(search, 250)
  const { applyPreferences } = usePreferences()
  const { pushToast } = useToast()
  const navigate = useNavigate()

  const load = useCallback(async () => {
    try {
      setError('')
      const [workspacePayload, filterPayload] = await Promise.all([
        getSavedWorkspaces({ search: debouncedSearch, page: pageFilter, pinnedOnly, tag: tagFilter, sortBy }),
        getFrontendFilters(),
      ])
      setPayload(workspacePayload)
      setFilters(filterPayload)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message || 'Failed to load saved workspaces.')
    } finally {
      setLoading(false)
    }
  }, [debouncedSearch, pageFilter, pinnedOnly, sortBy, tagFilter])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (!pendingDeleteWorkspace?.id) return
    const stillExists = (payload?.items || []).some((item) => item.id === pendingDeleteWorkspace.id)
    if (!stillExists) {
      setPendingDeleteWorkspace(null)
    }
  }, [payload?.items, pendingDeleteWorkspace])

  const metrics = useMemo(() => [
    { label: 'Saved presets', value: payload?.count ?? 0 },
    { label: 'Pinned', value: (payload?.items || []).filter((item) => item.pinned).length },
    { label: 'Tagged', value: Object.keys(payload?.tag_counts || {}).length },
    { label: 'Last Sync', value: new Date().toLocaleTimeString() },
  ], [payload])

  async function handleDelete(item) {
    if (!item?.id) return
    if (pendingDeleteWorkspace?.id !== item.id) {
      setPendingDeleteWorkspace({ id: item.id, name: item.name })
      setWorkspaceActionState({
        tone: 'warning',
        title: `Delete ${item.name}?`,
        description:
          'Deleting a saved preset removes this desk or board configuration from the shared list. Export it first if you may need to restore it later.',
      })
      return
    }
    try {
      setDeleteBusyId(item.id)
      await deleteWorkspace(item.id)
      setPendingDeleteWorkspace(null)
      setWorkspaceActionState({
        tone: 'info',
        title: 'Preset deleted',
        description: `${item.name} was removed from the saved preset list.`,
      })
      pushToast('Preset deleted.', 'success')
      await load()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to delete preset.', 'error')
      setWorkspaceActionState({
        tone: 'negative',
        title: 'Preset delete failed',
        description: err?.response?.data?.detail || err.message || 'Failed to delete preset.',
      })
    } finally {
      setDeleteBusyId('')
    }
  }

  async function handleDuplicate(id) {
    try {
      await duplicateWorkspace(id)
      setPendingDeleteWorkspace(null)
      setWorkspaceActionState(null)
      pushToast('Preset duplicated.', 'success')
      await load()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to duplicate preset.', 'error')
    }
  }

  async function handleTogglePin(item) {
    try {
      await updateWorkspace(item.id, { pinned: !item.pinned })
      pushToast(item.pinned ? 'Workspace unpinned.' : 'Workspace pinned.', 'success')
      await load()
    } catch (err) {
      pushToast(err?.response?.data?.detail || err.message || 'Failed to update workspace.', 'error')
    }
  }

  async function handleExport() {
    try {
      setExportBusy(true)
      setWorkspaceActionState({
        tone: 'info',
        title: 'Export downloads a JSON preset snapshot',
        description: 'Use export for backup or migration. Imported JSON merges incoming items into the current saved preset list.',
      })
      const data = await exportWorkspaces()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = 'saved_workspaces.json'
      anchor.click()
      URL.revokeObjectURL(url)
      setWorkspaceActionState({
        tone: 'positive',
        title: 'Preset export ready',
        description: 'Downloaded saved_workspaces.json. Keep it as a backup or import it into another operations environment later.',
      })
      pushToast('Preset export created.', 'success')
    } catch (err) {
      setWorkspaceActionState({
        tone: 'negative',
        title: 'Preset export failed',
        description: err?.response?.data?.detail || err.message || 'Failed to export presets.',
      })
      pushToast(err?.response?.data?.detail || err.message || 'Failed to export presets.', 'error')
    } finally {
      setExportBusy(false)
    }
  }

  async function handleImport(event) {
    const file = event.target.files?.[0]
    if (!file) return
    try {
      setImportBusy(true)
      if (!/\.json$/i.test(file.name) && file.type && file.type !== 'application/json') {
        throw new Error('Choose a JSON preset export before importing.')
      }
      const text = await file.text()
      const parsed = JSON.parse(text)
      const items = Array.isArray(parsed)
        ? parsed
        : Array.isArray(parsed?.items)
          ? parsed.items
          : null
      if (!items) {
        throw new Error('Import file must contain a JSON object with an items array.')
      }
      if (!items.length) {
        setWorkspaceActionState({
          tone: 'warning',
          title: 'Preset import is empty',
          description: 'That JSON file does not contain any preset items to merge.',
        })
        pushToast('Preset import file is empty.', 'warning')
        return
      }
      await importWorkspaces({ items, mode: 'merge' })
      setPendingDeleteWorkspace(null)
      setWorkspaceActionState({
        tone: 'positive',
        title: 'Preset import merged',
        description: `Merged ${items.length} preset${items.length === 1 ? '' : 's'} into the saved operations list. Review duplicates or stale presets before applying them live.`,
      })
      pushToast('Presets imported.', 'success')
      await load()
    } catch (err) {
      setWorkspaceActionState({
        tone: 'negative',
        title: 'Preset import failed',
        description: err?.response?.data?.detail || err.message || 'Failed to import presets.',
      })
      pushToast(err?.response?.data?.detail || err.message || 'Failed to import presets.', 'error')
    } finally {
      setImportBusy(false)
      event.target.value = ''
    }
  }

  function handleApply(item) {
    setPendingDeleteWorkspace(null)
    setWorkspaceActionState(null)
    const p = item?.payload || {}
    applyPreferences({
      defaultTicker: p.ticker || p.defaultTicker || 'SPY',
      defaultInterval: p.interval || p.defaultInterval || '5m',
      defaultHorizon: Number(p.horizon || p.defaultHorizon || 5),
      watchlistTickers: p.tickers || p.watchlistTickers || 'SPY,QQQ,NVDA,TSLA,AAPL,MSFT',
      autoRefreshWatchlist: Boolean(p.autoRefresh ?? p.autoRefreshWatchlist ?? true),
    })
    pushToast(`Applied preset ${item.name}.`, 'success')
    navigate(item.page === 'watchlist' ? '/watchlist' : item.page === 'compare' ? '/compare' : '/')
  }

  if (loading) {
    return (
      <LoadingBlock
        label="Loading saved presets"
        detail="Pulling reusable operations, desk, and board presets so pinned layouts open with current metadata."
      />
    )
  }

  return (
    <>
      {error ? (
        <ErrorState
          title="Saved presets unavailable"
          description={error}
          actionLabel="Reload presets"
          onAction={load}
        />
      ) : null}
      <PageIntro
        kicker="Saved presets"
        title="Reuse operations, desk, and board layouts"
        description="Apply, pin, export, and import reusable preset states without leaving platform operations."
        badge={`${payload?.count ?? 0} saved presets`}
        actions={(
          <Button type="button" variant="subtle" onClick={load}>
            Refresh presets
          </Button>
        )}
      />
      <section className="metrics-grid">
        {metrics.map((item) => <MetricCard key={item.label} {...item} />)}
      </section>
      <SectionCard
        title="Saved presets"
        subtitle="Reusable operations, dashboard, and watchlist presets stored by the backend."
        actions={(
          <DataToolbar
            searchValue={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search presets"
            actions={(
              <>
                <SelectField ariaLabel="Filter presets by page" value={pageFilter} onChange={(e) => setPageFilter(e.target.value)}>
                  {(filters.workspace_pages || ['all']).map((option) => (
                    <option key={option} value={option}>{option}</option>
                  ))}
                </SelectField>
                <SelectField ariaLabel="Sort presets" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
                  <option value="updated_desc">recent</option>
                  <option value="name_asc">name</option>
                  <option value="page_asc">page</option>
                </SelectField>
                <SelectField ariaLabel="Filter presets by tag" value={tagFilter} onChange={(e) => setTagFilter(e.target.value)}>
                  <option value="">all tags</option>
                  {Object.keys(payload?.tag_counts || {}).sort().map((tag) => (
                    <option key={tag} value={tag}>{tag}</option>
                  ))}
                </SelectField>
                <ToggleField label="Pinned only" checked={pinnedOnly} onChange={(e) => setPinnedOnly(e.target.checked)} />
                <Button type="button" variant="ghost" size="sm" onClick={handleExport} disabled={exportBusy}>
                  {exportBusy ? 'Exporting...' : 'Export JSON'}
                </Button>
                <FilePickerButton accept="application/json" onFileSelect={handleImport} disabled={importBusy}>
                  {importBusy ? 'Importing...' : 'Import JSON'}
                </FilePickerButton>
              </>
            )}
          />
        )}
      >
        <FeedbackState
          compact
          tone={workspaceActionState?.tone || 'neutral'}
          title={workspaceActionState?.title || 'Exports download JSON; imports merge into saved presets'}
          description={
            workspaceActionState?.description ||
            'Export creates a backup JSON file. Importing JSON merges incoming presets into the current saved list, so review duplicates before applying them.'
          }
          actions={
            pendingDeleteWorkspace
              ? [
                  {
                    label: deleteBusyId === pendingDeleteWorkspace.id ? 'Deleting...' : 'Delete workspace',
                    onAction: () => {
                      const item = (payload?.items || []).find((entry) => entry.id === pendingDeleteWorkspace.id)
                      if (item) {
                        void handleDelete(item)
                      }
                    },
                    variant: 'solid',
                    disabled: deleteBusyId === pendingDeleteWorkspace.id,
                  },
                  {
                    label: 'Cancel',
                    onAction: () => {
                      setPendingDeleteWorkspace(null)
                      setWorkspaceActionState(null)
                    },
                    variant: 'ghost',
                  },
                ]
              : []
          }
        />
        <div className="workspace-list">
          {(payload?.items || []).map((item) => (
            <article className="workspace-item" key={item.id}>
              <div>
                <Kicker as="div">{item.page}{item.pinned ? ' - pinned' : ''}</Kicker>
                <h3>{item.name}</h3>
                <p>{item.notes || 'No notes saved for this preset.'}</p>
                <div className="workspace-item__meta">Updated {item.updated_at || '--'}</div>
                <div className="workspace-tags">
                  {(item.tags || []).map((tag) => (
                    <Chip key={tag} tone="neutral" size="sm">
                      {tag}
                    </Chip>
                  ))}
                </div>
              </div>
              <div className="workspace-item__actions">
                <Button type="button" variant="ghost" size="sm" onClick={() => handleApply(item)}>Apply</Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => handleTogglePin(item)}>
                  {item.pinned ? 'Unpin' : 'Pin'}
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => handleDuplicate(item.id)}>Duplicate</Button>
                <Button
                  type="button"
                  variant="subtle"
                  size="sm"
                  onClick={() => handleDelete(item)}
                  disabled={deleteBusyId === item.id}
                >
                  {deleteBusyId === item.id
                    ? 'Deleting...'
                    : pendingDeleteWorkspace?.id === item.id
                      ? 'Confirm delete'
                      : 'Delete preset'}
                </Button>
              </div>
            </article>
          ))}
          {!(payload?.items || []).length ? (
            <EmptyState
              title="No saved presets yet"
              description="Start here by saving one operations, desk, or board layout, then come back to reapply it, duplicate it, or export it as backup JSON."
              actionLabel="Open dashboard"
              onAction={() => navigate('/')}
              secondaryActionLabel="Open watchlist"
              onSecondaryAction={() => navigate('/watchlist')}
            />
          ) : null}
        </div>
      </SectionCard>
    </>
  )
}

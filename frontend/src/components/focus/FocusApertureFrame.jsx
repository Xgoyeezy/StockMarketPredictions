import { useMemo } from 'react'
import { useLocation } from 'react-router-dom'
import { usePreferences } from '../../context/PreferencesContext'
import {
  DEFAULT_VISUAL_FOCUS_MODE,
  FULL_CONSOLE_FOCUS_MODE,
  buildDecisionRibbonModel,
  buildFocusBlockers,
  buildFocusRailItems,
  normalizeFocusRailKey,
  normalizePinnedFocusRails,
  normalizeVisualFocusMode,
} from '../../utils/focusApertureModel'
import DecisionRibbon from './DecisionRibbon'
import FocusPanel from './FocusPanel'
import FocusRail from './FocusRail'

function joinClasses(...values) {
  return values.filter(Boolean).join(' ')
}

export default function FocusApertureFrame({
  children,
  currentPage,
  activeAccountProfile,
}) {
  const { preferences, setPreference } = usePreferences()
  const location = useLocation()
  const focusMode = normalizeVisualFocusMode(preferences?.visualFocusMode)
  const expandedFocusRail = normalizeFocusRailKey(preferences?.expandedFocusRail)
  const pinnedFocusRails = normalizePinnedFocusRails(preferences?.pinnedFocusRails)
  const focusBlockers = useMemo(
    () => buildFocusBlockers({
      preferences,
      activeAccountProfile,
    }),
    [activeAccountProfile, preferences],
  )

  const ribbonModel = useMemo(
    () => buildDecisionRibbonModel({
      location,
      preferences,
      activeAccountProfile,
      currentPage,
      blockers: focusBlockers,
    }),
    [activeAccountProfile, currentPage, focusBlockers, location.pathname, location.search, preferences],
  )

  const railItems = useMemo(
    () => buildFocusRailItems({
      location,
      preferences,
      expandedFocusRail,
      pinnedFocusRails,
      visualFocusMode: focusMode,
      blockers: focusBlockers,
    }),
    [expandedFocusRail, focusBlockers, focusMode, location.pathname, location.search, pinnedFocusRails, preferences],
  )

  function handleRailToggle(key) {
    const normalizedKey = normalizeFocusRailKey(key)
    if (!normalizedKey) return
    setPreference('expandedFocusRail', expandedFocusRail === normalizedKey ? '' : normalizedKey)
  }

  function handleToggleFocusMode() {
    setPreference(
      'visualFocusMode',
      focusMode === DEFAULT_VISUAL_FOCUS_MODE ? FULL_CONSOLE_FOCUS_MODE : DEFAULT_VISUAL_FOCUS_MODE,
    )
  }

  return (
    <div
      className={joinClasses(
        'focus-aperture',
        `focus-aperture--${focusMode}`,
      )}
      data-focus-mode={focusMode}
      data-testid="focus-aperture-frame"
    >
      <DecisionRibbon model={ribbonModel} focusMode={focusMode} onToggleFocusMode={handleToggleFocusMode} />
      <div className="focus-aperture__body">
        <FocusRail items={railItems} onToggle={handleRailToggle} />
        <FocusPanel>{children}</FocusPanel>
      </div>
    </div>
  )
}

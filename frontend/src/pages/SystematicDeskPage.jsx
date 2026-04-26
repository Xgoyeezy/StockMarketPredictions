import StrategyDeskWorkspace, { SYSTEMATIC_DESK_KEY } from '../components/StrategyDeskWorkspace'

export default function SystematicDeskPage() {
  return (
    <StrategyDeskWorkspace
      focusedDeskKey={SYSTEMATIC_DESK_KEY}
      pageTitle="Systematic Equities"
      pageDescription="Dedicated systematic desk for cross-sectional, event-aware, and paper-routable equity workflows."
      pageHelper="Focused systematic workflow with desk runtime, options automation, allocator, risk, and latest execution context."
    />
  )
}

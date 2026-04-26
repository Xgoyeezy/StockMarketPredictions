const DEFAULT_AUTONOMY_BOARD = ['SPY', 'QQQ', 'AAPL', 'MSFT', 'NVDA', 'AMD']

function toNumber(value, fallback = 0) {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : fallback
}

function humanizeStatus(value, fallback = '--') {
  const normalized = String(value || '').trim()
  if (!normalized) return fallback
  return normalized
    .split('_')
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
}

export function buildTradeAutomationForm(snapshot) {
  const settings = snapshot?.settings || {}
  return {
    enabled: Boolean(settings.enabled),
    executionIntent: settings.execution_intent || 'broker_paper',
    tickers: Array.isArray(settings.tickers) ? settings.tickers.join(', ') : 'SPY, QQQ, AAPL, MSFT',
    interval: settings.interval || '5m',
    horizon: String(settings.horizon ?? 5),
    cycleIntervalSeconds: String(settings.cycle_interval_seconds ?? 60),
    cooldownMinutes: String(settings.cooldown_minutes ?? 20),
    accountSize: String(snapshot?.effective_funds ?? settings.account_size ?? 10000),
    actualFunds: String(snapshot?.actual_funds ?? settings.account_size ?? 10000),
    effectiveFundsMultiplier: String(settings.effective_funds_multiplier ?? snapshot?.effective_funds_multiplier ?? 1.0),
    riskPercent: String(settings.risk_percent ?? 0.50),
    autoTradeEquities: settings.auto_trade_equities !== false,
    autoTradeListedOptions: Boolean(settings.auto_trade_listed_options),
    orderType: settings.order_type || 'limit',
    timeInForce: settings.time_in_force || 'day_ext',
    regularHoursOnly: settings.regular_hours_only === true,
    autoSyncOrders: settings.auto_sync_orders !== false,
    autoManagePositions: settings.auto_manage_positions !== false,
    autoFlattenBeforeClose: settings.auto_flatten_before_close !== false,
    flattenBeforeCloseMinutes: String(settings.flatten_before_close_minutes ?? 15),
    maxOpenPositions: String(settings.max_open_positions ?? 6),
    cycleEntryRankLimit: String(settings.cycle_entry_rank_limit ?? 2),
    maxNotionalPerTrade: String(settings.max_notional_per_trade ?? 2500),
    maxTotalOpenNotional: String(settings.max_total_open_notional ?? 5000),
    maxGrossLeverage: String(settings.max_gross_leverage ?? 1.5),
    maxSinglePositionPct: String(settings.max_single_position_pct ?? 12),
    maxCorrelatedBucketPct: String(settings.max_correlated_bucket_pct ?? 35),
    minEdgeToCostRatio: String(settings.min_edge_to_cost_ratio ?? 2.5),
    allowPyramiding: settings.allow_pyramiding !== false,
    requireLiquidityFields: settings.require_liquidity_fields !== false,
    maxDailyLossR: String(settings.max_daily_loss_r ?? 2),
    maxConsecutiveLosses: String(settings.max_consecutive_losses ?? 3),
    maxDailyEntries: String(settings.max_daily_entries ?? 3),
    maxDailyEntriesPerSymbol: String(settings.max_daily_entries_per_symbol ?? 1),
    maxErrorStreak: String(settings.max_error_streak ?? 3),
    longOnly: settings.long_only !== false,
    equitiesOnly: settings.equities_only !== false,
    fractionalSharesOnly: Boolean(settings.fractional_shares_only),
    useFastModel: settings.use_fast_model !== false,
    aiDailyReviewEnabled: settings.ai_daily_review_enabled !== false,
    aiAutoAdjustEnabled: settings.ai_auto_adjust_enabled !== false,
    aiAdjustLiveEnabled: settings.ai_adjust_live_enabled !== false,
    aiReviewMinTrades: String(settings.ai_review_min_trades ?? 3),
    aiMaxDailySettingChanges: String(settings.ai_max_daily_setting_changes ?? 4),
    aiMaxStepPct: String(settings.ai_max_step_pct ?? 20),
    accuracyCalibrationEnabled: settings.accuracy_calibration_enabled !== false,
    accuracyCalibrationApplyToLive: Boolean(settings.accuracy_calibration_apply_to_live),
    accuracyCalibrationMinSamples: String(settings.accuracy_calibration_min_samples ?? 20),
    accuracyCalibrationStaleAfterSessions: String(settings.accuracy_calibration_stale_after_sessions ?? 5),
    accuracyCalibrationMaxCandidatePenalty: String(settings.accuracy_calibration_max_candidate_penalty ?? 25),
    dailyObjectiveEnabled: settings.daily_objective_enabled !== false,
    dailyProfitTargetPct: String(settings.daily_profit_target_pct ?? 1.0),
    dailyProfitTargetDollars: String(settings.daily_profit_target_dollars ?? 1000),
    dailyLossBudgetPct: String(settings.daily_loss_budget_pct ?? 0.5),
    dailyObjectiveApplyToLive: Boolean(settings.daily_objective_apply_to_live),
    lossContainmentEnabled: settings.loss_containment_enabled !== false,
    lossContainmentApplyToLive: Boolean(settings.loss_containment_apply_to_live),
    lossContainmentAutoClosePaper: settings.loss_containment_auto_close_paper !== false,
    lossContainmentAutoCloseLive: Boolean(settings.loss_containment_auto_close_live),
    lossContainmentMaxOpenHeatPct: String(settings.loss_containment_max_open_heat_pct ?? 0.35),
    lossContainmentMaxPositionLossR: String(settings.loss_containment_max_position_loss_r ?? 0.5),
    lossContainmentMaxPositionMaePct: String(settings.loss_containment_max_position_mae_pct ?? 0.35),
    lossContainmentProfitProtectTriggerR: String(settings.loss_containment_profit_protect_trigger_r ?? 0.75),
    lossContainmentProfitProtectFloorR: String(settings.loss_containment_profit_protect_floor_r ?? 0.15),
    lossContainmentTimeStopMinutes: String(settings.loss_containment_time_stop_minutes ?? 45),
    lossContainmentStaleQuoteSeconds: String(settings.loss_containment_stale_quote_seconds ?? 120),
    exitWatchdogEnabled: settings.exit_watchdog_enabled !== false,
    exitWatchdogApplyToLive: Boolean(settings.exit_watchdog_apply_to_live),
    exitWatchdogMaxConfirmationSeconds: String(settings.exit_watchdog_max_confirmation_seconds ?? 60),
    exitWatchdogMaxPartialMinutes: String(settings.exit_watchdog_max_partial_minutes ?? 5),
    exitWatchdogBlockEntriesOnUnconfirmedExit: settings.exit_watchdog_block_entries_on_unconfirmed_exit !== false,
    stateControlEnabled: settings.state_control_enabled !== false,
    stateControlAutoThrottleEnabled: settings.state_control_auto_throttle_enabled !== false,
    stateControlAutoHaltEnabled: settings.state_control_auto_halt_enabled !== false,
    stateControlWatchScore: String(settings.state_control_watch_score ?? 75),
    stateControlDeriskScore: String(settings.state_control_derisk_score ?? 55),
    stateControlHaltScore: String(settings.state_control_halt_score ?? 30),
    stateControlRecoveryCycles: String(settings.state_control_recovery_cycles ?? 2),
    paperCanaryEnabled: settings.paper_canary_enabled !== false,
    paperCanaryAutoReviewEnabled: settings.paper_canary_auto_review_enabled !== false,
    paperCanaryWindowSessions: String(settings.paper_canary_window_sessions ?? 5),
    paperCanaryRequiredCleanSessions: String(settings.paper_canary_required_clean_sessions ?? 3),
    paperOrderLifecycleCanaryEnabled: settings.paper_order_lifecycle_canary_enabled !== false,
    paperOrderLifecycleAutoSubmitEnabled: Boolean(settings.paper_order_lifecycle_auto_submit_enabled),
    paperOrderLifecycleWindowSessions: String(settings.paper_order_lifecycle_window_sessions ?? 5),
    paperOrderLifecycleRequiredCleanSessions: String(settings.paper_order_lifecycle_required_clean_sessions ?? 3),
    livePilotSoakEnabled: Boolean(settings.live_pilot_soak_enabled),
    livePilotMaxNotional: String(settings.live_pilot_max_notional ?? 10),
    livePilotSymbol: String(settings.live_pilot_symbol || 'SPY'),
    livePilotApprovalTtlMinutes: String(settings.live_pilot_approval_ttl_minutes ?? 15),
    livePilotCancelTimeoutSeconds: String(settings.live_pilot_cancel_timeout_seconds ?? 30),
    livePilotCanaryEnabled: settings.live_pilot_canary_enabled !== false,
    livePilotCanaryAutoReviewEnabled: settings.live_pilot_canary_auto_review_enabled !== false,
    livePilotCanaryWindowSessions: String(settings.live_pilot_canary_window_sessions ?? 5),
    livePilotCanaryRequiredCleanSessions: String(settings.live_pilot_canary_required_clean_sessions ?? 3),
    livePilotExpansionEnabled: Boolean(settings.live_pilot_expansion_enabled),
    livePilotExpansionMaxNotional: String(settings.live_pilot_expansion_max_notional ?? 25),
    livePilotExpansionMaxDailyOrders: String(settings.live_pilot_expansion_max_daily_orders ?? 1),
    livePilotExpansionApprovalTtlMinutes: String(settings.live_pilot_expansion_approval_ttl_minutes ?? 10),
    livePilotExpansionRequireLimit: settings.live_pilot_expansion_require_limit !== false,
    livePilotExpansionAllowAutonomousEntries: Boolean(settings.live_pilot_expansion_allow_autonomous_entries),
    livePilotExpansionCanaryEnabled: settings.live_pilot_expansion_canary_enabled !== false,
    livePilotExpansionCanaryAutoReviewEnabled: settings.live_pilot_expansion_canary_auto_review_enabled !== false,
    livePilotExpansionCanaryWindowSessions: String(settings.live_pilot_expansion_canary_window_sessions ?? 5),
    livePilotExpansionCanaryRequiredCleanSessions: String(settings.live_pilot_expansion_canary_required_clean_sessions ?? 3),
    livePilotWindowEnabled: Boolean(settings.live_pilot_window_enabled),
    livePilotWindowMaxNotional: String(settings.live_pilot_window_max_notional ?? 50),
    livePilotWindowMaxSessionOrders: String(settings.live_pilot_window_max_session_orders ?? 1),
    livePilotWindowApprovalTtlMinutes: String(settings.live_pilot_window_approval_ttl_minutes ?? 10),
    livePilotWindowDurationMinutes: String(settings.live_pilot_window_duration_minutes ?? 60),
    livePilotWindowRequireLimit: settings.live_pilot_window_require_limit !== false,
    livePilotWindowCanaryEnabled: settings.live_pilot_window_canary_enabled !== false,
    livePilotWindowCanaryAutoReviewEnabled: settings.live_pilot_window_canary_auto_review_enabled !== false,
    livePilotWindowCanaryWindowSessions: String(settings.live_pilot_window_canary_window_sessions ?? 5),
    livePilotWindowCanaryRequiredCleanSessions: String(settings.live_pilot_window_canary_required_clean_sessions ?? 3),
    livePilotPromotionReportEnabled: settings.live_pilot_promotion_report_enabled !== false,
    livePilotPromotionReportAutoReviewEnabled: settings.live_pilot_promotion_report_auto_review_enabled !== false,
    livePilotPromotionRequiredWindowCleanSessions: String(settings.live_pilot_promotion_required_window_clean_sessions ?? 3),
    livePilotPromotionStaleAfterDays: String(settings.live_pilot_promotion_stale_after_days ?? 2),
    limitedLiveRolloutEnabled: Boolean(settings.limited_live_rollout_enabled),
    limitedLiveRolloutMaxNotional: String(settings.limited_live_rollout_max_notional ?? 100),
    limitedLiveRolloutMaxSessionOrders: String(settings.limited_live_rollout_max_session_orders ?? 1),
    limitedLiveRolloutDurationMinutes: String(settings.limited_live_rollout_duration_minutes ?? 60),
    limitedLiveRolloutRequireLimit: settings.limited_live_rollout_require_limit !== false,
    limitedLiveRolloutApprovalTtlMinutes: String(settings.limited_live_rollout_approval_ttl_minutes ?? 10),
    limitedLiveRolloutAutoExpandEnabled: Boolean(settings.limited_live_rollout_auto_expand_enabled),
    limitedLiveRolloutCanaryEnabled: settings.limited_live_rollout_canary_enabled !== false,
    limitedLiveRolloutCanaryAutoReviewEnabled: settings.limited_live_rollout_canary_auto_review_enabled !== false,
    limitedLiveRolloutCanaryWindowSessions: String(settings.limited_live_rollout_canary_window_sessions ?? 5),
    limitedLiveRolloutCanaryRequiredCleanSessions: String(settings.limited_live_rollout_canary_required_clean_sessions ?? 3),
    limitedLiveRolloutCanaryStaleAfterDays: String(settings.limited_live_rollout_canary_stale_after_days ?? 2),
    limitedLiveCapExpansionReportEnabled: settings.limited_live_cap_expansion_report_enabled !== false,
    limitedLiveCapExpansionReportAutoReviewEnabled: settings.limited_live_cap_expansion_report_auto_review_enabled !== false,
    limitedLiveCapExpansionRequiredCleanSessions: String(settings.limited_live_cap_expansion_required_clean_sessions ?? 3),
    limitedLiveCapExpansionStaleAfterDays: String(settings.limited_live_cap_expansion_stale_after_days ?? 2),
    limitedLiveCapExpansionTargetMaxNotional: String(settings.limited_live_cap_expansion_target_max_notional ?? 250),
    limitedLiveCapExpansionEnabled: Boolean(settings.limited_live_cap_expansion_enabled),
    limitedLiveCapExpansionMaxNotional: String(settings.limited_live_cap_expansion_max_notional ?? 250),
    limitedLiveCapExpansionDurationMinutes: String(settings.limited_live_cap_expansion_duration_minutes ?? 60),
    limitedLiveCapExpansionApprovalTtlMinutes: String(settings.limited_live_cap_expansion_approval_ttl_minutes ?? 10),
    limitedLiveCapExpansionMaxSessionOrders: String(settings.limited_live_cap_expansion_max_session_orders ?? 1),
    limitedLiveCapExpansionRequireLimit: settings.limited_live_cap_expansion_require_limit !== false,
    limitedLiveCapExpansionAutoExpandEnabled: Boolean(settings.limited_live_cap_expansion_auto_expand_enabled),
    limitedLiveCapExpansionCanaryEnabled: settings.limited_live_cap_expansion_canary_enabled !== false,
    limitedLiveCapExpansionCanaryAutoReviewEnabled: settings.limited_live_cap_expansion_canary_auto_review_enabled !== false,
    limitedLiveCapExpansionCanaryWindowSessions: String(settings.limited_live_cap_expansion_canary_window_sessions ?? 5),
    limitedLiveCapExpansionCanaryRequiredCleanSessions: String(settings.limited_live_cap_expansion_canary_required_clean_sessions ?? 3),
    limitedLiveCapExpansionCanaryStaleAfterDays: String(settings.limited_live_cap_expansion_canary_stale_after_days ?? 2),
    limitedLiveNextTierCapReportEnabled: settings.limited_live_next_tier_cap_report_enabled !== false,
    limitedLiveNextTierCapReportAutoReviewEnabled: settings.limited_live_next_tier_cap_report_auto_review_enabled !== false,
    limitedLiveNextTierCapRequiredCleanSessions: String(settings.limited_live_next_tier_cap_required_clean_sessions ?? 3),
    limitedLiveNextTierCapStaleAfterDays: String(settings.limited_live_next_tier_cap_stale_after_days ?? 2),
    limitedLiveNextTierCapTargetMaxNotional: String(settings.limited_live_next_tier_cap_target_max_notional ?? 500),
    limitedLiveNextTierCapEnabled: Boolean(settings.limited_live_next_tier_cap_enabled),
    limitedLiveNextTierCapMaxNotional: String(settings.limited_live_next_tier_cap_max_notional ?? 500),
    limitedLiveNextTierCapDurationMinutes: String(settings.limited_live_next_tier_cap_duration_minutes ?? 60),
    limitedLiveNextTierCapApprovalTtlMinutes: String(settings.limited_live_next_tier_cap_approval_ttl_minutes ?? 10),
    limitedLiveNextTierCapMaxSessionOrders: String(settings.limited_live_next_tier_cap_max_session_orders ?? 1),
    limitedLiveNextTierCapRequireLimit: settings.limited_live_next_tier_cap_require_limit !== false,
    limitedLiveNextTierCapAutoExpandEnabled: Boolean(settings.limited_live_next_tier_cap_auto_expand_enabled),
    limitedLiveNextTierCapCanaryEnabled: settings.limited_live_next_tier_cap_canary_enabled !== false,
    limitedLiveNextTierCapCanaryAutoReviewEnabled: settings.limited_live_next_tier_cap_canary_auto_review_enabled !== false,
    limitedLiveNextTierCapCanaryWindowSessions: String(settings.limited_live_next_tier_cap_canary_window_sessions ?? 5),
    limitedLiveNextTierCapCanaryRequiredCleanSessions: String(settings.limited_live_next_tier_cap_canary_required_clean_sessions ?? 3),
    limitedLiveNextTierCapCanaryStaleAfterDays: String(settings.limited_live_next_tier_cap_canary_stale_after_days ?? 2),
    limitedLiveHigherCapReportEnabled: settings.limited_live_higher_cap_report_enabled !== false,
    limitedLiveHigherCapReportAutoReviewEnabled: settings.limited_live_higher_cap_report_auto_review_enabled !== false,
    limitedLiveHigherCapRequiredCleanSessions: String(settings.limited_live_higher_cap_required_clean_sessions ?? 3),
    limitedLiveHigherCapStaleAfterDays: String(settings.limited_live_higher_cap_stale_after_days ?? 2),
    limitedLiveHigherCapTargetMaxNotional: String(settings.limited_live_higher_cap_target_max_notional ?? 1000),
    limitedLiveOperatorChecklistRequired: settings.limited_live_operator_checklist_required !== false,
  }
}

export function buildTradeAutomationPayload(form) {
  return {
    enabled: Boolean(form.enabled),
    execution_intent: form.executionIntent,
    tickers: String(form.tickers || '')
      .split(/[\n,]+/)
      .map((value) => value.trim().toUpperCase())
      .filter(Boolean),
    interval: form.interval,
    horizon: Number(form.horizon || 5),
    cycle_interval_seconds: Number(form.cycleIntervalSeconds || 60),
    cooldown_minutes: Number(form.cooldownMinutes || 0),
    effective_funds_multiplier: Number(form.effectiveFundsMultiplier || 1.0),
    risk_percent: Number(form.riskPercent || 0.50),
    auto_trade_equities: Boolean(form.autoTradeEquities),
    auto_trade_listed_options: Boolean(form.autoTradeListedOptions),
    order_type: form.orderType,
    time_in_force: form.timeInForce,
    regular_hours_only: Boolean(form.regularHoursOnly),
    auto_sync_orders: Boolean(form.autoSyncOrders),
    auto_manage_positions: Boolean(form.autoManagePositions),
    auto_flatten_before_close: Boolean(form.autoFlattenBeforeClose),
    flatten_before_close_minutes: Number(form.flattenBeforeCloseMinutes || 15),
    max_open_positions: Number(form.maxOpenPositions || 1),
    cycle_entry_rank_limit: Number(form.cycleEntryRankLimit || 2),
    max_notional_per_trade: Number(form.maxNotionalPerTrade || 2500),
    max_total_open_notional: Number(form.maxTotalOpenNotional || 5000),
    max_gross_leverage: Number(form.maxGrossLeverage || 1.5),
    max_single_position_pct: Number(form.maxSinglePositionPct || 12),
    max_correlated_bucket_pct: Number(form.maxCorrelatedBucketPct || 35),
    min_edge_to_cost_ratio: Number(form.minEdgeToCostRatio || 2.5),
    allow_pyramiding: Boolean(form.allowPyramiding),
    require_liquidity_fields: Boolean(form.requireLiquidityFields),
    max_daily_loss_r: Number(form.maxDailyLossR || 2),
    max_consecutive_losses: Number(form.maxConsecutiveLosses || 3),
    max_daily_entries: Number(form.maxDailyEntries || 3),
    max_daily_entries_per_symbol: Number(form.maxDailyEntriesPerSymbol || 1),
    max_error_streak: Number(form.maxErrorStreak || 3),
    long_only: Boolean(form.longOnly),
    equities_only: false,
    fractional_shares_only: Boolean(form.fractionalSharesOnly),
    use_fast_model: Boolean(form.useFastModel),
    ai_daily_review_enabled: Boolean(form.aiDailyReviewEnabled),
    ai_auto_adjust_enabled: Boolean(form.aiAutoAdjustEnabled),
    ai_adjust_live_enabled: Boolean(form.aiAdjustLiveEnabled),
    ai_review_min_trades: Number(form.aiReviewMinTrades || 3),
    ai_max_daily_setting_changes: Number(form.aiMaxDailySettingChanges || 4),
    ai_max_step_pct: Number(form.aiMaxStepPct || 20),
    accuracy_calibration_enabled: Boolean(form.accuracyCalibrationEnabled),
    accuracy_calibration_apply_to_live: Boolean(form.accuracyCalibrationApplyToLive),
    accuracy_calibration_min_samples: Number(form.accuracyCalibrationMinSamples || 20),
    accuracy_calibration_stale_after_sessions: Number(form.accuracyCalibrationStaleAfterSessions || 5),
    accuracy_calibration_max_candidate_penalty: Number(form.accuracyCalibrationMaxCandidatePenalty || 25),
    daily_objective_enabled: Boolean(form.dailyObjectiveEnabled),
    daily_profit_target_pct: Number(form.dailyProfitTargetPct || 1.0),
    daily_profit_target_dollars: Number(form.dailyProfitTargetDollars || 1000),
    daily_loss_budget_pct: Number(form.dailyLossBudgetPct || 0.5),
    daily_objective_apply_to_live: Boolean(form.dailyObjectiveApplyToLive),
    loss_containment_enabled: Boolean(form.lossContainmentEnabled),
    loss_containment_apply_to_live: Boolean(form.lossContainmentApplyToLive),
    loss_containment_auto_close_paper: Boolean(form.lossContainmentAutoClosePaper),
    loss_containment_auto_close_live: Boolean(form.lossContainmentAutoCloseLive),
    loss_containment_max_open_heat_pct: Number(form.lossContainmentMaxOpenHeatPct || 0.35),
    loss_containment_max_position_loss_r: Number(form.lossContainmentMaxPositionLossR || 0.5),
    loss_containment_max_position_mae_pct: Number(form.lossContainmentMaxPositionMaePct || 0.35),
    loss_containment_profit_protect_trigger_r: Number(form.lossContainmentProfitProtectTriggerR || 0.75),
    loss_containment_profit_protect_floor_r: Number(form.lossContainmentProfitProtectFloorR || 0.15),
    loss_containment_time_stop_minutes: Number(form.lossContainmentTimeStopMinutes || 45),
    loss_containment_stale_quote_seconds: Number(form.lossContainmentStaleQuoteSeconds || 120),
    exit_watchdog_enabled: Boolean(form.exitWatchdogEnabled),
    exit_watchdog_apply_to_live: Boolean(form.exitWatchdogApplyToLive),
    exit_watchdog_max_confirmation_seconds: Number(form.exitWatchdogMaxConfirmationSeconds || 60),
    exit_watchdog_max_partial_minutes: Number(form.exitWatchdogMaxPartialMinutes || 5),
    exit_watchdog_block_entries_on_unconfirmed_exit: Boolean(form.exitWatchdogBlockEntriesOnUnconfirmedExit),
    state_control_enabled: Boolean(form.stateControlEnabled),
    state_control_auto_throttle_enabled: Boolean(form.stateControlAutoThrottleEnabled),
    state_control_auto_halt_enabled: Boolean(form.stateControlAutoHaltEnabled),
    state_control_watch_score: Number(form.stateControlWatchScore || 75),
    state_control_derisk_score: Number(form.stateControlDeriskScore || 55),
    state_control_halt_score: Number(form.stateControlHaltScore || 30),
    state_control_recovery_cycles: Number(form.stateControlRecoveryCycles || 2),
    paper_canary_enabled: Boolean(form.paperCanaryEnabled),
    paper_canary_auto_review_enabled: Boolean(form.paperCanaryAutoReviewEnabled),
    paper_canary_window_sessions: Number(form.paperCanaryWindowSessions || 5),
    paper_canary_required_clean_sessions: Number(form.paperCanaryRequiredCleanSessions || 3),
    paper_order_lifecycle_canary_enabled: Boolean(form.paperOrderLifecycleCanaryEnabled),
    paper_order_lifecycle_auto_submit_enabled: Boolean(form.paperOrderLifecycleAutoSubmitEnabled),
    paper_order_lifecycle_window_sessions: Number(form.paperOrderLifecycleWindowSessions || 5),
    paper_order_lifecycle_required_clean_sessions: Number(form.paperOrderLifecycleRequiredCleanSessions || 3),
    live_pilot_soak_enabled: Boolean(form.livePilotSoakEnabled),
    live_pilot_max_notional: Number(form.livePilotMaxNotional || 10),
    live_pilot_symbol: String(form.livePilotSymbol || 'SPY').trim().toUpperCase(),
    live_pilot_approval_ttl_minutes: Number(form.livePilotApprovalTtlMinutes || 15),
    live_pilot_cancel_timeout_seconds: Number(form.livePilotCancelTimeoutSeconds || 30),
    live_pilot_canary_enabled: Boolean(form.livePilotCanaryEnabled),
    live_pilot_canary_auto_review_enabled: Boolean(form.livePilotCanaryAutoReviewEnabled),
    live_pilot_canary_window_sessions: Number(form.livePilotCanaryWindowSessions || 5),
    live_pilot_canary_required_clean_sessions: Number(form.livePilotCanaryRequiredCleanSessions || 3),
    live_pilot_expansion_enabled: Boolean(form.livePilotExpansionEnabled),
    live_pilot_expansion_max_notional: Number(form.livePilotExpansionMaxNotional || 25),
    live_pilot_expansion_max_daily_orders: Number(form.livePilotExpansionMaxDailyOrders || 1),
    live_pilot_expansion_approval_ttl_minutes: Number(form.livePilotExpansionApprovalTtlMinutes || 10),
    live_pilot_expansion_require_limit: true,
    live_pilot_expansion_allow_autonomous_entries: false,
    live_pilot_expansion_canary_enabled: Boolean(form.livePilotExpansionCanaryEnabled),
    live_pilot_expansion_canary_auto_review_enabled: Boolean(form.livePilotExpansionCanaryAutoReviewEnabled),
    live_pilot_expansion_canary_window_sessions: Number(form.livePilotExpansionCanaryWindowSessions || 5),
    live_pilot_expansion_canary_required_clean_sessions: Number(form.livePilotExpansionCanaryRequiredCleanSessions || 3),
    live_pilot_window_enabled: Boolean(form.livePilotWindowEnabled),
    live_pilot_window_max_notional: Number(form.livePilotWindowMaxNotional || 50),
    live_pilot_window_max_session_orders: Number(form.livePilotWindowMaxSessionOrders || 1),
    live_pilot_window_approval_ttl_minutes: Number(form.livePilotWindowApprovalTtlMinutes || 10),
    live_pilot_window_duration_minutes: Number(form.livePilotWindowDurationMinutes || 60),
    live_pilot_window_require_limit: true,
    live_pilot_window_canary_enabled: Boolean(form.livePilotWindowCanaryEnabled),
    live_pilot_window_canary_auto_review_enabled: Boolean(form.livePilotWindowCanaryAutoReviewEnabled),
    live_pilot_window_canary_window_sessions: Number(form.livePilotWindowCanaryWindowSessions || 5),
    live_pilot_window_canary_required_clean_sessions: Number(form.livePilotWindowCanaryRequiredCleanSessions || 3),
    live_pilot_promotion_report_enabled: Boolean(form.livePilotPromotionReportEnabled),
    live_pilot_promotion_report_auto_review_enabled: Boolean(form.livePilotPromotionReportAutoReviewEnabled),
    live_pilot_promotion_required_window_clean_sessions: Number(form.livePilotPromotionRequiredWindowCleanSessions || 3),
    live_pilot_promotion_stale_after_days: Number(form.livePilotPromotionStaleAfterDays || 2),
    limited_live_rollout_enabled: Boolean(form.limitedLiveRolloutEnabled),
    limited_live_rollout_max_notional: Number(form.limitedLiveRolloutMaxNotional || 100),
    limited_live_rollout_max_session_orders: Number(form.limitedLiveRolloutMaxSessionOrders || 1),
    limited_live_rollout_duration_minutes: Number(form.limitedLiveRolloutDurationMinutes || 60),
    limited_live_rollout_require_limit: true,
    limited_live_rollout_approval_ttl_minutes: Number(form.limitedLiveRolloutApprovalTtlMinutes || 10),
    limited_live_rollout_auto_expand_enabled: false,
    limited_live_rollout_canary_enabled: Boolean(form.limitedLiveRolloutCanaryEnabled),
    limited_live_rollout_canary_auto_review_enabled: Boolean(form.limitedLiveRolloutCanaryAutoReviewEnabled),
    limited_live_rollout_canary_window_sessions: Number(form.limitedLiveRolloutCanaryWindowSessions || 5),
    limited_live_rollout_canary_required_clean_sessions: Number(form.limitedLiveRolloutCanaryRequiredCleanSessions || 3),
    limited_live_rollout_canary_stale_after_days: Number(form.limitedLiveRolloutCanaryStaleAfterDays || 2),
    limited_live_cap_expansion_report_enabled: Boolean(form.limitedLiveCapExpansionReportEnabled),
    limited_live_cap_expansion_report_auto_review_enabled: Boolean(form.limitedLiveCapExpansionReportAutoReviewEnabled),
    limited_live_cap_expansion_required_clean_sessions: Number(form.limitedLiveCapExpansionRequiredCleanSessions || 3),
    limited_live_cap_expansion_stale_after_days: Number(form.limitedLiveCapExpansionStaleAfterDays || 2),
    limited_live_cap_expansion_target_max_notional: Number(form.limitedLiveCapExpansionTargetMaxNotional || 250),
    limited_live_cap_expansion_enabled: Boolean(form.limitedLiveCapExpansionEnabled),
    limited_live_cap_expansion_max_notional: Number(form.limitedLiveCapExpansionMaxNotional || 250),
    limited_live_cap_expansion_duration_minutes: Number(form.limitedLiveCapExpansionDurationMinutes || 60),
    limited_live_cap_expansion_approval_ttl_minutes: Number(form.limitedLiveCapExpansionApprovalTtlMinutes || 10),
    limited_live_cap_expansion_max_session_orders: Number(form.limitedLiveCapExpansionMaxSessionOrders || 1),
    limited_live_cap_expansion_require_limit: true,
    limited_live_cap_expansion_auto_expand_enabled: false,
    limited_live_cap_expansion_canary_enabled: Boolean(form.limitedLiveCapExpansionCanaryEnabled),
    limited_live_cap_expansion_canary_auto_review_enabled: Boolean(form.limitedLiveCapExpansionCanaryAutoReviewEnabled),
    limited_live_cap_expansion_canary_window_sessions: Number(form.limitedLiveCapExpansionCanaryWindowSessions || 5),
    limited_live_cap_expansion_canary_required_clean_sessions: Number(form.limitedLiveCapExpansionCanaryRequiredCleanSessions || 3),
    limited_live_cap_expansion_canary_stale_after_days: Number(form.limitedLiveCapExpansionCanaryStaleAfterDays || 2),
    limited_live_next_tier_cap_report_enabled: Boolean(form.limitedLiveNextTierCapReportEnabled),
    limited_live_next_tier_cap_report_auto_review_enabled: Boolean(form.limitedLiveNextTierCapReportAutoReviewEnabled),
    limited_live_next_tier_cap_required_clean_sessions: Number(form.limitedLiveNextTierCapRequiredCleanSessions || 3),
    limited_live_next_tier_cap_stale_after_days: Number(form.limitedLiveNextTierCapStaleAfterDays || 2),
    limited_live_next_tier_cap_target_max_notional: Number(form.limitedLiveNextTierCapTargetMaxNotional || 500),
    limited_live_next_tier_cap_enabled: Boolean(form.limitedLiveNextTierCapEnabled),
    limited_live_next_tier_cap_max_notional: Number(form.limitedLiveNextTierCapMaxNotional || 500),
    limited_live_next_tier_cap_duration_minutes: Number(form.limitedLiveNextTierCapDurationMinutes || 60),
    limited_live_next_tier_cap_approval_ttl_minutes: Number(form.limitedLiveNextTierCapApprovalTtlMinutes || 10),
    limited_live_next_tier_cap_max_session_orders: Number(form.limitedLiveNextTierCapMaxSessionOrders || 1),
    limited_live_next_tier_cap_require_limit: true,
    limited_live_next_tier_cap_auto_expand_enabled: false,
    limited_live_next_tier_cap_canary_enabled: Boolean(form.limitedLiveNextTierCapCanaryEnabled),
    limited_live_next_tier_cap_canary_auto_review_enabled: Boolean(form.limitedLiveNextTierCapCanaryAutoReviewEnabled),
    limited_live_next_tier_cap_canary_window_sessions: Number(form.limitedLiveNextTierCapCanaryWindowSessions || 5),
    limited_live_next_tier_cap_canary_required_clean_sessions: Number(form.limitedLiveNextTierCapCanaryRequiredCleanSessions || 3),
    limited_live_next_tier_cap_canary_stale_after_days: Number(form.limitedLiveNextTierCapCanaryStaleAfterDays || 2),
    limited_live_higher_cap_report_enabled: Boolean(form.limitedLiveHigherCapReportEnabled),
    limited_live_higher_cap_report_auto_review_enabled: Boolean(form.limitedLiveHigherCapReportAutoReviewEnabled),
    limited_live_higher_cap_required_clean_sessions: Number(form.limitedLiveHigherCapRequiredCleanSessions || 3),
    limited_live_higher_cap_stale_after_days: Number(form.limitedLiveHigherCapStaleAfterDays || 2),
    limited_live_higher_cap_target_max_notional: Number(form.limitedLiveHigherCapTargetMaxNotional || 1000),
    limited_live_operator_checklist_required: Boolean(form.limitedLiveOperatorChecklistRequired),
  }
}

export function buildTradeAutomationPresetPayload(key, snapshot) {
  const currentSettings = snapshot?.settings || {}
  const effectiveFunds = toNumber(snapshot?.effective_funds, toNumber(currentSettings.account_size, 100000))
  const base = {
    tickers: DEFAULT_AUTONOMY_BOARD,
    interval: '5m',
    horizon: 5,
    cycle_interval_seconds: 60,
    cooldown_minutes: 20,
    effective_funds_multiplier: toNumber(snapshot?.settings?.effective_funds_multiplier, 1.0),
    risk_percent: 0.50,
    auto_trade_equities: true,
    auto_trade_listed_options: false,
    order_type: 'limit',
    time_in_force: 'day_ext',
    regular_hours_only: false,
    auto_sync_orders: true,
    auto_manage_positions: true,
    auto_flatten_before_close: true,
    flatten_before_close_minutes: 15,
    max_open_positions: 6,
    cycle_entry_rank_limit: 2,
    max_notional_per_trade: Math.max(effectiveFunds * 0.12, 100.0),
    max_total_open_notional: Math.max(effectiveFunds * 0.25, 100.0),
    max_gross_leverage: 1.5,
    max_single_position_pct: 12,
    max_correlated_bucket_pct: 35,
    min_edge_to_cost_ratio: 3.0,
    allow_pyramiding: true,
    require_liquidity_fields: true,
    max_daily_loss_r: 2,
    max_consecutive_losses: 3,
    max_daily_entries: 3,
    max_daily_entries_per_symbol: 1,
    max_error_streak: 3,
    long_only: true,
    equities_only: false,
    fractional_shares_only: Boolean(currentSettings.fractional_shares_only),
    use_fast_model: true,
    ai_daily_review_enabled: currentSettings.ai_daily_review_enabled !== false,
    ai_auto_adjust_enabled: currentSettings.ai_auto_adjust_enabled !== false,
    ai_adjust_live_enabled: currentSettings.ai_adjust_live_enabled !== false,
    ai_review_min_trades: Number(currentSettings.ai_review_min_trades ?? 3),
    ai_max_daily_setting_changes: Number(currentSettings.ai_max_daily_setting_changes ?? 4),
    ai_max_step_pct: Number(currentSettings.ai_max_step_pct ?? 20),
    accuracy_calibration_enabled: currentSettings.accuracy_calibration_enabled !== false,
    accuracy_calibration_apply_to_live: Boolean(currentSettings.accuracy_calibration_apply_to_live),
    accuracy_calibration_min_samples: Number(currentSettings.accuracy_calibration_min_samples ?? 20),
    accuracy_calibration_stale_after_sessions: Number(currentSettings.accuracy_calibration_stale_after_sessions ?? 5),
    accuracy_calibration_max_candidate_penalty: Number(currentSettings.accuracy_calibration_max_candidate_penalty ?? 25),
    daily_objective_enabled: currentSettings.daily_objective_enabled !== false,
    daily_profit_target_pct: Number(currentSettings.daily_profit_target_pct ?? 1.0),
    daily_profit_target_dollars: Number(currentSettings.daily_profit_target_dollars ?? 1000),
    daily_loss_budget_pct: Number(currentSettings.daily_loss_budget_pct ?? 0.5),
    daily_objective_apply_to_live: Boolean(currentSettings.daily_objective_apply_to_live),
    loss_containment_enabled: currentSettings.loss_containment_enabled !== false,
    loss_containment_apply_to_live: Boolean(currentSettings.loss_containment_apply_to_live),
    loss_containment_auto_close_paper: currentSettings.loss_containment_auto_close_paper !== false,
    loss_containment_auto_close_live: Boolean(currentSettings.loss_containment_auto_close_live),
    loss_containment_max_open_heat_pct: Number(currentSettings.loss_containment_max_open_heat_pct ?? 0.35),
    loss_containment_max_position_loss_r: Number(currentSettings.loss_containment_max_position_loss_r ?? 0.5),
    loss_containment_max_position_mae_pct: Number(currentSettings.loss_containment_max_position_mae_pct ?? 0.35),
    loss_containment_profit_protect_trigger_r: Number(currentSettings.loss_containment_profit_protect_trigger_r ?? 0.75),
    loss_containment_profit_protect_floor_r: Number(currentSettings.loss_containment_profit_protect_floor_r ?? 0.15),
    loss_containment_time_stop_minutes: Number(currentSettings.loss_containment_time_stop_minutes ?? 45),
    loss_containment_stale_quote_seconds: Number(currentSettings.loss_containment_stale_quote_seconds ?? 120),
    exit_watchdog_enabled: currentSettings.exit_watchdog_enabled !== false,
    exit_watchdog_apply_to_live: Boolean(currentSettings.exit_watchdog_apply_to_live),
    exit_watchdog_max_confirmation_seconds: Number(currentSettings.exit_watchdog_max_confirmation_seconds ?? 60),
    exit_watchdog_max_partial_minutes: Number(currentSettings.exit_watchdog_max_partial_minutes ?? 5),
    exit_watchdog_block_entries_on_unconfirmed_exit: currentSettings.exit_watchdog_block_entries_on_unconfirmed_exit !== false,
    state_control_enabled: currentSettings.state_control_enabled !== false,
    state_control_auto_throttle_enabled: currentSettings.state_control_auto_throttle_enabled !== false,
    state_control_auto_halt_enabled: currentSettings.state_control_auto_halt_enabled !== false,
    state_control_watch_score: Number(currentSettings.state_control_watch_score ?? 75),
    state_control_derisk_score: Number(currentSettings.state_control_derisk_score ?? 55),
    state_control_halt_score: Number(currentSettings.state_control_halt_score ?? 30),
    state_control_recovery_cycles: Number(currentSettings.state_control_recovery_cycles ?? 2),
    paper_canary_enabled: currentSettings.paper_canary_enabled !== false,
    paper_canary_auto_review_enabled: currentSettings.paper_canary_auto_review_enabled !== false,
    paper_canary_window_sessions: Number(currentSettings.paper_canary_window_sessions ?? 5),
    paper_canary_required_clean_sessions: Number(currentSettings.paper_canary_required_clean_sessions ?? 3),
    paper_order_lifecycle_canary_enabled: currentSettings.paper_order_lifecycle_canary_enabled !== false,
    paper_order_lifecycle_auto_submit_enabled: Boolean(currentSettings.paper_order_lifecycle_auto_submit_enabled),
    paper_order_lifecycle_window_sessions: Number(currentSettings.paper_order_lifecycle_window_sessions ?? 5),
    paper_order_lifecycle_required_clean_sessions: Number(currentSettings.paper_order_lifecycle_required_clean_sessions ?? 3),
    live_pilot_soak_enabled: Boolean(currentSettings.live_pilot_soak_enabled),
    live_pilot_max_notional: Number(currentSettings.live_pilot_max_notional ?? 10),
    live_pilot_symbol: String(currentSettings.live_pilot_symbol || 'SPY').trim().toUpperCase(),
    live_pilot_approval_ttl_minutes: Number(currentSettings.live_pilot_approval_ttl_minutes ?? 15),
    live_pilot_cancel_timeout_seconds: Number(currentSettings.live_pilot_cancel_timeout_seconds ?? 30),
    live_pilot_canary_enabled: currentSettings.live_pilot_canary_enabled !== false,
    live_pilot_canary_auto_review_enabled: currentSettings.live_pilot_canary_auto_review_enabled !== false,
    live_pilot_canary_window_sessions: Number(currentSettings.live_pilot_canary_window_sessions ?? 5),
    live_pilot_canary_required_clean_sessions: Number(currentSettings.live_pilot_canary_required_clean_sessions ?? 3),
    live_pilot_expansion_enabled: Boolean(currentSettings.live_pilot_expansion_enabled),
    live_pilot_expansion_max_notional: Number(currentSettings.live_pilot_expansion_max_notional ?? 25),
    live_pilot_expansion_max_daily_orders: Number(currentSettings.live_pilot_expansion_max_daily_orders ?? 1),
    live_pilot_expansion_approval_ttl_minutes: Number(currentSettings.live_pilot_expansion_approval_ttl_minutes ?? 10),
    live_pilot_expansion_require_limit: true,
    live_pilot_expansion_allow_autonomous_entries: false,
    live_pilot_expansion_canary_enabled: currentSettings.live_pilot_expansion_canary_enabled !== false,
    live_pilot_expansion_canary_auto_review_enabled: currentSettings.live_pilot_expansion_canary_auto_review_enabled !== false,
    live_pilot_expansion_canary_window_sessions: Number(currentSettings.live_pilot_expansion_canary_window_sessions ?? 5),
    live_pilot_expansion_canary_required_clean_sessions: Number(currentSettings.live_pilot_expansion_canary_required_clean_sessions ?? 3),
    live_pilot_window_enabled: Boolean(currentSettings.live_pilot_window_enabled),
    live_pilot_window_max_notional: Number(currentSettings.live_pilot_window_max_notional ?? 50),
    live_pilot_window_max_session_orders: Number(currentSettings.live_pilot_window_max_session_orders ?? 1),
    live_pilot_window_approval_ttl_minutes: Number(currentSettings.live_pilot_window_approval_ttl_minutes ?? 10),
    live_pilot_window_duration_minutes: Number(currentSettings.live_pilot_window_duration_minutes ?? 60),
    live_pilot_window_require_limit: true,
    live_pilot_window_canary_enabled: currentSettings.live_pilot_window_canary_enabled !== false,
    live_pilot_window_canary_auto_review_enabled: currentSettings.live_pilot_window_canary_auto_review_enabled !== false,
    live_pilot_window_canary_window_sessions: Number(currentSettings.live_pilot_window_canary_window_sessions ?? 5),
    live_pilot_window_canary_required_clean_sessions: Number(currentSettings.live_pilot_window_canary_required_clean_sessions ?? 3),
  }

  if (key === 'prep') {
    return {
      ...base,
      execution_intent: 'desk',
      auto_manage_positions: false,
      auto_flatten_before_close: false,
      order_type: 'limit',
      time_in_force: 'day_ext',
      regular_hours_only: false,
      max_open_positions: 1,
      max_notional_per_trade: Math.min(Math.max(effectiveFunds * 0.05, 100.0), 5000),
      max_total_open_notional: Math.min(Math.max(effectiveFunds * 0.05, 100.0), 5000),
    }
  }

  if (key === 'paper') {
    return {
      ...base,
      execution_intent: 'broker_paper',
      order_type: 'limit',
      time_in_force: 'day_ext',
      regular_hours_only: false,
      max_open_positions: 2,
      max_notional_per_trade: Math.max(effectiveFunds * 0.12, 100.0),
      max_total_open_notional: Math.max(effectiveFunds * 0.25, 100.0),
    }
  }

  if (key === 'pre_market') {
    return {
      ...base,
      execution_intent: 'broker_paper',
      cooldown_minutes: 45,
      risk_percent: 0.25,
      max_open_positions: 2,
      max_notional_per_trade: Math.max(effectiveFunds * 0.06, 100.0),
      max_total_open_notional: Math.max(effectiveFunds * 0.12, 100.0),
      min_edge_to_cost_ratio: 4.0,
      max_spread_bps: 12.5,
      max_daily_entries: 2,
      allow_pyramiding: false,
      require_liquidity_fields: true,
    }
  }

  if (key === 'after_hours') {
    return {
      ...base,
      execution_intent: 'broker_paper',
      cooldown_minutes: 60,
      risk_percent: 0.15,
      max_open_positions: 1,
      max_notional_per_trade: Math.max(effectiveFunds * 0.04, 100.0),
      max_total_open_notional: Math.max(effectiveFunds * 0.08, 100.0),
      min_edge_to_cost_ratio: 5.0,
      max_spread_bps: 10.0,
      max_daily_entries: 1,
      max_daily_entries_per_symbol: 1,
      allow_pyramiding: false,
      require_liquidity_fields: true,
    }
  }

  return {
    ...base,
    execution_intent: 'broker_live',
    order_type: 'limit',
    time_in_force: 'day_ext',
    regular_hours_only: false,
    max_open_positions: 2,
    max_notional_per_trade: Math.max(effectiveFunds * 0.1, 100.0),
    max_total_open_notional: Math.max(effectiveFunds * 0.2, 100.0),
  }
}

export function buildRankedEntryGateModel(rolloutReadiness = {}) {
  const gate = rolloutReadiness?.ranked_entry_rollout || {}
  if (!Object.keys(gate).length && !Object.keys(rolloutReadiness || {}).length) {
    return null
  }
  const accepted = Boolean(gate.accepted)
  const status = String(gate.status || '').trim().toLowerCase() || 'missing'
  const tone = accepted ? 'positive' : status === 'rejected' || status === 'invalid' ? 'negative' : 'warning'
  return {
    accepted,
    tone,
    title: accepted
      ? `Ranked-entry gate: ${gate.candidate_label || gate.promotion_candidate_label || gate.candidate_key || 'Accepted'}`
      : `Ranked-entry gate: ${gate.candidate_key || 'Validation only'}`,
    description:
      String(gate.basis || gate.detail || rolloutReadiness.basis || rolloutReadiness.detail || '').trim() ||
      'Ranked-entry validation data is not available yet.',
    metrics: [
      { label: 'Baseline', value: String(gate.baseline_key || 'A') },
      { label: 'Candidate', value: String(gate.candidate_key || 'M') },
      { label: 'Gate', value: accepted ? 'Accepted' : 'Blocked', tone: accepted ? 'positive' : 'negative' },
      { label: 'Scenarios', value: gate.scenario_count != null ? String(gate.scenario_count) : '--' },
    ],
  }
}

export function buildValidationSampleModel(snapshot = {}) {
  const rolloutReadiness = snapshot?.rollout_readiness || {}
  const metrics = rolloutReadiness?.metrics || {}
  const gate = rolloutReadiness?.ranked_entry_rollout || {}
  const routeIntegrity = rolloutReadiness?.current_route_validation_integrity || gate.current_route_validation_integrity || {}
  const fillCount = toNumber(metrics.current_route_fill_count ?? gate.current_route_fill_count, 0)
  const closeCount = toNumber(metrics.current_route_closed_trade_count ?? gate.current_route_closed_trade_count, 0)
  const sampleStatus = String(metrics.current_route_sample_status || routeIntegrity.current_route_sample_status || gate.current_route_sample_status || '').trim().toLowerCase()
  const coverageStatus = String(metrics.mark_to_market_coverage_status || routeIntegrity.mark_to_market_coverage_status || gate.mark_to_market_coverage_status || '').trim().toLowerCase()
  const consistencyStatus = String(metrics.ledger_snapshot_consistency || routeIntegrity.ledger_snapshot_consistency || gate.ledger_snapshot_consistency || '').trim().toLowerCase()
  const metricsSource = String(metrics.metrics_source || routeIntegrity.metrics_source || gate.metrics_source || '').trim().toLowerCase()

  if (!sampleStatus && !coverageStatus && !consistencyStatus && !fillCount && !closeCount) {
    return null
  }

  if (sampleStatus !== 'sufficient') {
    return {
      tone: 'warning',
      title: 'Validation sample: Collecting sample',
      description: `Current-route evidence is still thin at ${fillCount} directional fills and ${closeCount} closed trades. Keep broker-live locked while paper collection runs.`,
      metrics: [
        { label: 'Current-route fills', value: String(fillCount) },
        { label: 'Current-route closes', value: String(closeCount) },
        { label: 'Coverage', value: humanizeStatus(coverageStatus, '--') },
        { label: 'Consistency', value: humanizeStatus(consistencyStatus, '--') },
      ],
    }
  }

  if (coverageStatus && coverageStatus !== 'complete') {
    return {
      tone: 'warning',
      title: 'Validation sample: Snapshot coverage incomplete',
      description: `The current-route sample is large enough, but mark-to-market coverage is still ${humanizeStatus(coverageStatus).toLowerCase()}. Validation remains ledger-driven until snapshot coverage catches up.`,
      metrics: [
        { label: 'Current-route fills', value: String(fillCount) },
        { label: 'Current-route closes', value: String(closeCount) },
        { label: 'Coverage', value: humanizeStatus(coverageStatus) },
        { label: 'Source', value: humanizeStatus(metricsSource, 'Event Ledger') },
      ],
    }
  }

  if (consistencyStatus && consistencyStatus !== 'consistent') {
    return {
      tone: consistencyStatus === 'inconsistent' ? 'negative' : 'warning',
      title: 'Validation sample: Snapshot consistency pending',
      description: `Ledger and snapshot agreement is still ${humanizeStatus(consistencyStatus).toLowerCase()}. Rerun validation only after the accounting view stabilizes.`,
      metrics: [
        { label: 'Current-route fills', value: String(fillCount) },
        { label: 'Current-route closes', value: String(closeCount) },
        { label: 'Consistency', value: humanizeStatus(consistencyStatus) },
        { label: 'Source', value: humanizeStatus(metricsSource, 'Event Ledger') },
      ],
    }
  }

  return {
    tone: 'positive',
    title: 'Validation sample: Ready to rerun validation',
    description: `Current-route evidence and snapshot integrity are aligned. The next paper export can now be used as the promotion decision artifact.`,
    metrics: [
      { label: 'Current-route fills', value: String(fillCount) },
      { label: 'Current-route closes', value: String(closeCount) },
      { label: 'Coverage', value: humanizeStatus(coverageStatus, 'Complete') },
      { label: 'Consistency', value: humanizeStatus(consistencyStatus, 'Consistent') },
    ],
  }
}

export function buildCollectionPhaseModel(snapshot = {}) {
  const collectionPhase = snapshot?.collection_phase || {}
  const rolloutReadiness = snapshot?.rollout_readiness || {}
  const runtime = snapshot?.runtime || {}
  const metrics = rolloutReadiness?.metrics || {}

  const active = Boolean(
    collectionPhase.collection_phase_active ??
      rolloutReadiness.collection_phase_active ??
      runtime.collection_phase_active
  )
  const rawLabel = String(
    collectionPhase.collection_phase_label ||
      rolloutReadiness.collection_phase_label ||
      runtime.collection_phase_label ||
      '',
  ).trim()
  const rawDetail = String(
    collectionPhase.collection_phase_detail ||
      rolloutReadiness.collection_phase_detail ||
      runtime.collection_phase_detail ||
      '',
  ).trim()
  const fillCount = toNumber(
    collectionPhase.current_route_fill_count ??
      metrics.current_route_fill_count ??
      runtime.current_route_fill_count,
    0,
  )
  const closeCount = toNumber(
    collectionPhase.current_route_closed_trade_count ??
      metrics.current_route_closed_trade_count ??
      runtime.current_route_closed_trade_count,
    0,
  )
  const coverageStatus = String(
    collectionPhase.mark_to_market_coverage_status ||
      metrics.mark_to_market_coverage_status ||
      runtime.mark_to_market_coverage_status ||
      '',
  ).trim().toLowerCase()
  const consistencyStatus = String(
    collectionPhase.ledger_snapshot_consistency ||
      metrics.ledger_snapshot_consistency ||
      runtime.ledger_snapshot_consistency ||
      '',
  ).trim().toLowerCase()
  const lastCollectionBlocker = String(
    collectionPhase.last_collection_blocker ||
      rolloutReadiness.last_collection_blocker ||
      runtime.last_collection_blocker ||
      '',
  ).trim().toLowerCase()
  const reconciliationStatus = String(
    collectionPhase.current_route_reconciliation_status ||
      rolloutReadiness.current_route_reconciliation_status ||
      metrics.current_route_reconciliation_status ||
      runtime.current_route_reconciliation_status ||
      '',
  ).trim().toLowerCase()
  const orphanOrderEventCount = toNumber(
    collectionPhase.current_route_orphan_order_event_count ??
      rolloutReadiness.current_route_orphan_order_event_count ??
      metrics.current_route_orphan_order_event_count ??
      runtime.current_route_orphan_order_event_count,
    0,
  )
  const lastSubmittedCurrentRouteOrderAt =
    collectionPhase.last_submitted_current_route_order_at ||
    metrics.last_submitted_current_route_order_at ||
    runtime.last_submitted_current_route_order_at ||
    null
  const lastCurrentRouteFillAt =
    collectionPhase.last_current_route_fill_at ||
    metrics.last_current_route_fill_at ||
    runtime.last_current_route_fill_at ||
    null
  const lastCurrentRouteCloseAt =
    collectionPhase.last_current_route_close_at ||
    metrics.last_current_route_close_at ||
    runtime.last_current_route_close_at ||
    null
  const lastValidationRerunAt =
    collectionPhase.last_validation_rerun_at ||
    rolloutReadiness.last_validation_rerun_at ||
    runtime.last_validation_rerun_at ||
    null
  const lastValidationRerunCycleId =
    collectionPhase.last_validation_rerun_cycle_id ||
    rolloutReadiness.last_validation_rerun_cycle_id ||
    runtime.last_validation_rerun_cycle_id ||
    null
  const autoValidationRerunEnabled = Boolean(
    collectionPhase.auto_validation_rerun_enabled ??
      rolloutReadiness.auto_validation_rerun_enabled ??
      runtime.auto_validation_rerun_enabled ??
      true,
  )

  if (
    !rawLabel &&
    !rawDetail &&
    !fillCount &&
    !closeCount &&
    !coverageStatus &&
    !consistencyStatus &&
    !lastCollectionBlocker &&
    !reconciliationStatus &&
    !orphanOrderEventCount &&
    !lastValidationRerunAt &&
    !lastValidationRerunCycleId
  ) {
    return null
  }

  const label = rawLabel || (active ? 'Collecting sample' : 'Ready for rollout review')
  const detail = rawDetail || 'Collection-phase telemetry is not available yet.'

  let tone = active ? 'warning' : 'positive'
  if (/blocked/i.test(label) || consistencyStatus === 'inconsistent') tone = 'negative'
  if (/rerunning/i.test(label)) tone = 'warning'
  if (/persistence/i.test(label)) tone = 'negative'
  if (/reconcile/i.test(label) || orphanOrderEventCount > 0) tone = 'negative'
  if (/ready/i.test(label) && !active) tone = 'positive'

  return {
    active,
    tone,
    title: `Collection phase: ${label}`,
    description: detail,
    metrics: [
      { label: 'Current-route fills', value: String(fillCount) },
      { label: 'Current-route closes', value: String(closeCount) },
      { label: 'Coverage', value: humanizeStatus(coverageStatus, '--') },
      { label: 'Consistency', value: humanizeStatus(consistencyStatus, '--') },
      { label: 'Blocker', value: humanizeStatus(lastCollectionBlocker, '--') },
      { label: 'Reconcile', value: humanizeStatus(reconciliationStatus, '--') },
      { label: 'Orphan events', value: String(orphanOrderEventCount) },
      { label: 'Auto rerun', value: autoValidationRerunEnabled ? 'Enabled' : 'Disabled' },
      {
        label: 'Last rerun',
        value: lastValidationRerunAt ? new Date(lastValidationRerunAt).toLocaleString() : '--',
      },
      {
        label: 'Rerun cycle',
        value: lastValidationRerunCycleId || '--',
      },
      {
        label: 'Last submit',
        value: lastSubmittedCurrentRouteOrderAt ? new Date(lastSubmittedCurrentRouteOrderAt).toLocaleString() : '--',
      },
      {
        label: 'Last fill',
        value: lastCurrentRouteFillAt ? new Date(lastCurrentRouteFillAt).toLocaleString() : '--',
      },
      {
        label: 'Last close',
        value: lastCurrentRouteCloseAt ? new Date(lastCurrentRouteCloseAt).toLocaleString() : '--',
      },
    ],
  }
}

export function buildAutomationTelemetrySnapshot(snapshot = {}) {
  const runtime = snapshot?.runtime || {}
  return {
    candidate: runtime?.last_candidate || null,
    rejection: runtime?.last_rejection || null,
    pathEvaluations: Array.isArray(runtime?.last_path_evaluations) ? runtime.last_path_evaluations : [],
  }
}

export function buildAiReviewModel(snapshot = {}) {
  const aiReview = snapshot?.ai_review || {}
  const lastReview = aiReview?.last_review || {}
  const scores = lastReview?.objective_scores || {}
  const appliedChanges = Array.isArray(lastReview?.applied_changes) ? lastReview.applied_changes : []
  const skippedChanges = Array.isArray(lastReview?.skipped_changes) ? lastReview.skipped_changes : []
  const summary = aiReview?.current_journal_summary || {}
  const lastReviewAt = aiReview?.last_review_at || lastReview?.reviewed_at || null
  const overall = Number(scores.overall_score)
  const tone = !aiReview.enabled
    ? 'neutral'
    : Number.isFinite(overall) && overall >= 75
      ? 'positive'
      : Number.isFinite(overall) && overall < 50
        ? 'negative'
        : appliedChanges.length || skippedChanges.length
          ? 'warning'
          : 'neutral'
  return {
    enabled: Boolean(aiReview.enabled),
    autoAdjustEnabled: Boolean(aiReview.auto_adjust_enabled),
    adjustLiveEnabled: Boolean(aiReview.adjust_live_enabled),
    reviewWindowOpen: Boolean(aiReview.review_window_open),
    sessionDay: aiReview.review_session_day || lastReview?.session_day || '--',
    relatedNoteId: aiReview.related_note_id || lastReview?.note_id || null,
    lastReviewAt,
    appliedChanges,
    skippedChanges,
    title: aiReview.enabled ? 'AI review loop' : 'AI review disabled',
    description:
      lastReview?.summary ||
      (summary.observation_count
        ? `${summary.observation_count} observation(s) collected for ${aiReview.review_session_day || 'today'}.`
        : 'Waiting for automation cycles to build the daily note.'),
    tone,
    metrics: [
      { label: 'Session', value: aiReview.review_session_day || '--' },
      { label: 'Review window', value: aiReview.review_window_open ? 'Open' : 'Waiting' },
      { label: 'Observations', value: String(summary.observation_count ?? 0) },
      { label: 'Good / bad', value: `${summary.good_count ?? 0}/${summary.bad_count ?? 0}` },
      { label: 'Overall score', value: Number.isFinite(overall) ? overall.toFixed(0) : '--' },
      { label: 'Applied', value: String(appliedChanges.length) },
      { label: 'Skipped', value: String(skippedChanges.length) },
      { label: 'Last review', value: lastReviewAt ? new Date(lastReviewAt).toLocaleString() : '--' },
    ],
  }
}

export function buildControlPlaneModel(snapshot = {}) {
  const controlPlane = snapshot?.control_plane || {}
  const state = String(controlPlane.state || 'healthy').trim().toLowerCase()
  const score = toNumber(controlPlane.score, NaN)
  const componentScores = controlPlane.component_scores || {}
  const triggeredSignals = Array.isArray(controlPlane.triggered_signals) ? controlPlane.triggered_signals : []
  const activeOverrides = Array.isArray(controlPlane.active_overrides) ? controlPlane.active_overrides : []
  const runtimeOverrides = Array.isArray(controlPlane.active_runtime_overrides) ? controlPlane.active_runtime_overrides : []
  const shadowReport = controlPlane.shadow_validation || {}
  const shadowStatus = String(shadowReport.status || '').trim().toLowerCase()
  const shadowScenarios = Array.isArray(shadowReport.scenarios) ? shadowReport.scenarios : []
  const shadowTone =
    shadowStatus === 'pass'
      ? 'positive'
      : shadowStatus === 'fail'
        ? 'negative'
        : shadowStatus === 'not_run'
          ? 'neutral'
          : 'warning'
  const shadowValidation =
    shadowStatus && shadowStatus !== 'not_run'
      ? {
          status: shadowStatus,
          tone: shadowTone,
          title: `Shadow validation: ${humanizeStatus(shadowStatus)}`,
          description:
            shadowStatus === 'pass'
              ? `All ${shadowReport.scenario_count ?? shadowScenarios.length} state-control scenarios passed without mutating live settings.`
              : `${shadowReport.failed_count ?? 0} of ${shadowReport.scenario_count ?? shadowScenarios.length} state-control scenarios need review.`,
          noteId: shadowReport.note_id || null,
          lastRunAt: shadowReport.evaluated_at || shadowReport.last_run_at || null,
          scenarios: shadowScenarios,
          metrics: [
            { label: 'Status', value: humanizeStatus(shadowStatus), tone: shadowTone },
            { label: 'Scenarios', value: String(shadowReport.scenario_count ?? shadowScenarios.length) },
            { label: 'Passed', value: String(shadowReport.passed_count ?? 0), tone: shadowStatus === 'pass' ? 'positive' : 'neutral' },
            { label: 'Failed', value: String(shadowReport.failed_count ?? 0), tone: Number(shadowReport.failed_count || 0) ? 'negative' : 'positive' },
            { label: 'Worst state', value: humanizeStatus(shadowReport.worst_state, 'Healthy') },
            { label: 'Overlays', value: String(shadowReport.expected_overlay_count ?? 0) },
            { label: 'Safety lock', value: shadowReport.safety_lock_expected ? 'Expected' : 'No' },
            { label: 'Last shadow', value: shadowReport.evaluated_at ? new Date(shadowReport.evaluated_at).toLocaleString() : '--' },
          ],
        }
      : null
  const lastTransition = controlPlane.last_transition || null
  const tone =
    !controlPlane.enabled
      ? 'neutral'
      : state === 'halt'
        ? 'negative'
        : state === 'de_risk'
          ? 'warning'
          : state === 'watch'
            ? 'warning'
            : 'positive'
  const title = controlPlane.enabled
    ? `State control: ${humanizeStatus(state, 'Healthy')}`
    : 'State control disabled'
  const topSignal = triggeredSignals[0]
  return {
    enabled: Boolean(controlPlane.enabled),
    autoThrottleEnabled: Boolean(controlPlane.auto_throttle_enabled),
    autoHaltEnabled: Boolean(controlPlane.auto_halt_enabled),
    state,
    score,
    tone,
    title,
    description:
      topSignal?.detail ||
      (controlPlane.evaluated_at
        ? `Last evaluated ${new Date(controlPlane.evaluated_at).toLocaleString()}.`
        : 'Waiting for the next automation cycle or manual review.'),
    relatedNoteId: controlPlane.related_note_id || null,
    lastTransition,
    activeOverrides,
    runtimeOverrides,
    triggeredSignals,
    shadowValidation,
    manualActionRequired: Boolean(controlPlane.manual_action_required),
    metrics: [
      { label: 'State', value: humanizeStatus(state, 'Healthy'), tone },
      { label: 'Score', value: Number.isFinite(score) ? score.toFixed(0) : '--', tone },
      { label: 'Data', value: componentScores.data_integrity != null ? Number(componentScores.data_integrity).toFixed(0) : '--' },
      { label: 'Alpha', value: componentScores.alpha_efficacy != null ? Number(componentScores.alpha_efficacy).toFixed(0) : '--' },
      { label: 'Execution', value: componentScores.execution_quality != null ? Number(componentScores.execution_quality).toFixed(0) : '--' },
      { label: 'Market', value: componentScores.market_state != null ? Number(componentScores.market_state).toFixed(0) : '--' },
      { label: 'Overrides', value: String(activeOverrides.length || runtimeOverrides.length) },
      { label: 'Manual action', value: controlPlane.manual_action_required ? 'Required' : 'No' },
    ],
  }
}

export function buildPaperOrderLifecycleCanaryModel(snapshot = {}) {
  const canary = snapshot?.paper_order_lifecycle_canary || {}
  const status = String(canary.status || 'not_run').trim().toLowerCase()
  const sessions = Array.isArray(canary.sessions) ? canary.sessions : []
  const blockers = Array.isArray(canary.blockers) ? canary.blockers : []
  const warnings = Array.isArray(canary.warnings) ? canary.warnings : []
  const noteCoverage = canary.note_coverage || {}
  const cleanCount = toNumber(canary.clean_session_count, 0)
  const requiredClean = toNumber(canary.required_clean_sessions, 3)
  const windowCount = toNumber(canary.window_session_count, sessions.length)
  const autoSubmitEnabled = Boolean(canary.auto_submit_enabled)
  const nextEligibleRunAt = canary.next_eligible_run_at || null
  const lastScheduledRunAt = canary.last_scheduled_run_at || null
  const lastAutoSubmitAt = canary.last_auto_submit_at || null
  const tone =
    canary.enabled === false
      ? 'neutral'
      : status === 'ready'
        ? 'positive'
        : status === 'blocked'
          ? 'negative'
          : status === 'not_run'
            ? 'neutral'
            : 'warning'
  const description =
    status === 'ready'
      ? `${cleanCount}/${requiredClean} paper lifecycle sessions are clean.`
      : status === 'blocked'
        ? `${blockers.length || 1} lifecycle canary blocker${blockers.length === 1 ? '' : 's'} must clear before promotion review.`
        : status === 'collecting'
          ? `${cleanCount}/${requiredClean} clean lifecycle sessions collected.`
          : 'Run lifecycle canary review after paper-only order lifecycle soak evidence exists across sessions.'
  return {
    status,
    tone,
    title: canary.enabled === false ? 'Lifecycle canary disabled' : `Lifecycle canary: ${humanizeStatus(status, 'Not run')}`,
    description,
    enabled: canary.enabled !== false,
    autoSubmitEnabled,
    sessions,
    blockers,
    warnings,
    evidenceWindow: canary.evidence_window || {},
    relatedNoteId: canary.related_note_id || canary.note_id || null,
    lastRunAt: canary.evaluated_at || canary.last_run_at || null,
    lastScheduledRunAt,
    nextEligibleRunAt,
    lastAutoSubmitAt,
    latestSoakStatus: canary.latest_soak_status || 'missing',
    latestTerminalState: canary.latest_terminal_state || null,
    latestBrokerOrderId: canary.latest_broker_order_id || null,
    latestLocalOrderId: canary.latest_local_order_id || null,
    latestReconciliationStatus: canary.latest_reconciliation_status || 'missing',
    manualActionRequired: Boolean(canary.manual_action_required || blockers.length),
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Clean days', value: `${cleanCount}/${requiredClean}`, tone: cleanCount >= requiredClean ? 'positive' : 'warning' },
      { label: 'Evidence days', value: String(windowCount) },
      { label: 'Latest soak', value: humanizeStatus(canary.latest_soak_status, 'Missing') },
      { label: 'Terminal', value: humanizeStatus(canary.latest_terminal_state, '--'), tone: canary.latest_terminal_state ? 'positive' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(canary.latest_reconciliation_status, 'Missing'), tone: canary.latest_reconciliation_status === 'clean' ? 'positive' : canary.latest_reconciliation_status === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Auto submit', value: autoSubmitEnabled ? 'On' : 'Off', tone: autoSubmitEnabled ? 'warning' : 'neutral' },
      { label: 'Notes', value: `${toNumber(noteCoverage.covered, 0)}/${toNumber(noteCoverage.required, 0)}` },
      { label: 'Broker order', value: canary.latest_broker_order_id ? String(canary.latest_broker_order_id).slice(0, 12) : '--' },
      { label: 'Local order', value: canary.latest_local_order_id ? String(canary.latest_local_order_id).slice(0, 12) : '--' },
      { label: 'Next review', value: nextEligibleRunAt ? new Date(nextEligibleRunAt).toLocaleString() : '--' },
      { label: 'Last scheduled', value: lastScheduledRunAt ? new Date(lastScheduledRunAt).toLocaleString() : '--' },
      { label: 'Last auto submit', value: lastAutoSubmitAt ? new Date(lastAutoSubmitAt).toLocaleString() : '--' },
      { label: 'Note', value: canary.related_note_id || canary.note_id ? 'Linked' : '--' },
      { label: 'Manual action', value: canary.manual_action_required || blockers.length ? 'Required' : 'No', tone: blockers.length ? 'negative' : 'neutral' },
    ],
  }
}

export function buildPaperCanaryModel(snapshot = {}) {
  const canary = snapshot?.paper_canary || {}
  const status = String(canary.status || 'not_run').trim().toLowerCase()
  const sessions = Array.isArray(canary.sessions) ? canary.sessions : []
  const blockers = Array.isArray(canary.blockers) ? canary.blockers : []
  const warnings = Array.isArray(canary.warnings) ? canary.warnings : []
  const pnl = canary.pnl_summary || {}
  const slippage = canary.slippage_summary || {}
  const aiCoverage = canary.ai_review_coverage || {}
  const noteCoverage = canary.note_coverage || {}
  const lifecycleCanary = canary.paper_order_lifecycle_canary || {}
  const cleanCount = toNumber(canary.clean_session_count, 0)
  const requiredClean = toNumber(canary.required_clean_sessions, 3)
  const windowCount = toNumber(canary.window_session_count, sessions.length)
  const shadowPassRate = toNumber(canary.shadow_pass_rate, 0)
  const noteRatio = toNumber(noteCoverage.ratio, 0)
  const autoReviewEnabled = canary.auto_review_enabled !== false
  const nextEligibleRunAt = canary.next_eligible_run_at || null
  const lastScheduledRunAt = canary.last_scheduled_run_at || null
  const runSource = String(canary.run_source || '').trim().toLowerCase()
  const skippedReason = String(canary.skipped_reason || '').trim()
  const tone =
    canary.enabled === false
      ? 'neutral'
      : status === 'ready'
      ? 'positive'
      : status === 'blocked'
        ? 'negative'
        : status === 'not_run'
          ? 'neutral'
          : 'warning'
  const description =
    status === 'ready'
      ? `${cleanCount}/${requiredClean} clean paper sessions are ready for broker-live review.`
      : status === 'blocked'
        ? `${blockers.length} blocker${blockers.length === 1 ? '' : 's'} must clear before promotion review.`
        : status === 'collecting'
          ? `${cleanCount}/${requiredClean} clean paper sessions collected.`
          : autoReviewEnabled && nextEligibleRunAt
            ? `Waiting for scheduled paper canary review at ${new Date(nextEligibleRunAt).toLocaleString()}.`
            : 'Run the paper canary review after paper automation has daily AI, state-control, and shadow evidence.'
  return {
    status,
    tone,
    title: canary.enabled === false ? 'Paper canary disabled' : `Paper canary: ${humanizeStatus(status, 'Not run')}`,
    description,
    enabled: canary.enabled !== false,
    autoReviewEnabled,
    runSource,
    skippedReason,
    nextEligibleRunAt,
    lastScheduledRunAt,
    evidenceWindow: canary.evidence_window || {},
    relatedNoteId: canary.related_note_id || canary.note_id || null,
    lastRunAt: canary.evaluated_at || canary.last_run_at || null,
    sessions,
    blockers,
    warnings,
    manualActionRequired: Boolean(canary.manual_action_required),
    settingsChangedDuringWindow: Boolean(canary.settings_changed_during_window),
    lifecycleCanary,
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Clean days', value: `${cleanCount}/${requiredClean}`, tone: cleanCount >= requiredClean ? 'positive' : 'warning' },
      { label: 'Evidence days', value: String(windowCount) },
      { label: 'Worst state', value: humanizeStatus(canary.worst_state, 'Healthy') },
      { label: 'Shadow pass', value: `${(shadowPassRate * 100).toFixed(0)}%`, tone: shadowPassRate >= 1 && windowCount ? 'positive' : 'warning' },
      { label: 'AI coverage', value: `${toNumber(aiCoverage.covered, 0)}/${toNumber(aiCoverage.required, 0)}` },
      { label: 'Note coverage', value: `${(noteRatio * 100).toFixed(0)}%`, tone: noteRatio >= 1 && windowCount ? 'positive' : 'warning' },
      { label: 'Closed PnL', value: `$${toNumber(pnl.realized_pnl, 0).toFixed(2)}` },
      { label: 'Closed trades', value: String(toNumber(pnl.closed_trade_count, 0)) },
      { label: 'Avg slippage', value: slippage.average_abs_bps == null ? '--' : `${toNumber(slippage.average_abs_bps, 0).toFixed(1)} bps` },
      { label: 'Worst slippage', value: slippage.worst_abs_bps == null ? '--' : `${toNumber(slippage.worst_abs_bps, 0).toFixed(1)} bps` },
      { label: 'Lifecycle canary', value: humanizeStatus(lifecycleCanary.status, 'Missing'), tone: lifecycleCanary.status === 'ready' ? 'positive' : lifecycleCanary.status === 'blocked' ? 'negative' : 'warning' },
      { label: 'Auto review', value: autoReviewEnabled ? 'On' : 'Off', tone: autoReviewEnabled ? 'positive' : 'neutral' },
      { label: 'Run source', value: runSource ? humanizeStatus(runSource) : '--' },
      { label: 'Next review', value: nextEligibleRunAt ? new Date(nextEligibleRunAt).toLocaleString() : '--' },
      { label: 'Last scheduled', value: lastScheduledRunAt ? new Date(lastScheduledRunAt).toLocaleString() : '--' },
      { label: 'Note', value: canary.related_note_id || canary.note_id ? 'Linked' : '--' },
      { label: 'Manual action', value: canary.manual_action_required ? 'Required' : 'No' },
    ],
  }
}

export function buildLivePilotReadinessModel(snapshot = {}) {
  const readiness = snapshot?.live_pilot_readiness || {}
  const status = String(readiness.status || 'not_run').trim().toLowerCase()
  const blockers = Array.isArray(readiness.blockers) ? readiness.blockers : []
  const warnings = Array.isArray(readiness.warnings) ? readiness.warnings : []
  const operatorActions = Array.isArray(readiness.required_operator_actions)
    ? readiness.required_operator_actions
    : []
  const paperEvidence = readiness.paper_evidence || {}
  const liveRoute = readiness.live_route_config || {}
  const tone =
    status === 'ready_to_request_approval'
      ? 'positive'
      : status === 'blocked'
        ? 'negative'
        : status === 'warning'
          ? 'warning'
          : 'neutral'
  const paperCanary = paperEvidence.paper_canary || {}
  const lifecycleCanary = paperEvidence.lifecycle_canary || {}
  const paperBroker = paperEvidence.paper_broker_reconciliation || {}
  const stateControl = paperEvidence.state_control || {}
  const shadowValidation = paperEvidence.shadow_validation || {}
  const description =
    status === 'ready_to_request_approval'
      ? 'Paper evidence is clean and the live pilot can be sent for manual approval.'
      : status === 'blocked'
        ? `${blockers.length || 1} blocker${blockers.length === 1 ? '' : 's'} must clear before live pilot approval.`
        : status === 'warning'
          ? `${warnings.length || 1} readiness warning${warnings.length === 1 ? '' : 's'} need operator review before approval.`
          : 'Run the live pilot readiness review after paper canary and lifecycle evidence are current.'
  return {
    status,
    tone,
    title: `Live pilot readiness: ${humanizeStatus(status, 'Not run')}`,
    description,
    blockers,
    warnings,
    operatorActions,
    paperEvidence,
    liveRoute,
    relatedNoteId: readiness.related_note_id || readiness.note_id || null,
    evaluatedAt: readiness.evaluated_at || readiness.last_run_at || null,
    manualActionRequired: Boolean(readiness.manual_action_required || blockers.length || operatorActions.length),
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Paper canary', value: humanizeStatus(readiness.paper_evidence_status, 'Missing'), tone: readiness.paper_evidence_status === 'ready' ? 'positive' : 'warning' },
      { label: 'Lifecycle', value: humanizeStatus(readiness.lifecycle_canary_status, 'Missing'), tone: readiness.lifecycle_canary_status === 'ready' ? 'positive' : 'warning' },
      { label: 'Paper broker', value: humanizeStatus(readiness.paper_broker_reconciliation_status, 'Missing'), tone: readiness.paper_broker_reconciliation_status === 'clean' ? 'positive' : readiness.paper_broker_reconciliation_status === 'blocked' ? 'negative' : 'warning' },
      { label: 'State control', value: humanizeStatus(readiness.state_control_status, 'Unknown'), tone: readiness.state_control_status === 'halt' ? 'negative' : readiness.state_control_status === 'healthy' ? 'positive' : 'warning' },
      { label: 'Shadow', value: humanizeStatus(readiness.shadow_validation_status, 'Missing'), tone: readiness.shadow_validation_status === 'pass' ? 'positive' : 'warning' },
      { label: 'Live gate', value: humanizeStatus(readiness.broker_live_gate_status, 'Unknown'), tone: readiness.broker_live_gate_status === 'open' ? 'positive' : 'negative' },
      { label: 'Safety locks', value: humanizeStatus(readiness.safety_lock_status, 'Unknown'), tone: readiness.safety_lock_status === 'clear' ? 'positive' : 'negative' },
      { label: 'Live config', value: liveRoute.credentials_configured ? 'Configured' : 'Missing', tone: liveRoute.credentials_configured ? 'positive' : 'negative' },
      { label: 'Live server', value: liveRoute.server_live_trading_enabled ? 'Enabled' : 'Disabled', tone: liveRoute.server_live_trading_enabled ? 'positive' : 'negative' },
      { label: 'Paper days', value: `${toNumber(paperCanary.clean_session_count, 0)}/${toNumber(paperCanary.required_clean_sessions, 0)}` },
      { label: 'Lifecycle days', value: `${toNumber(lifecycleCanary.clean_session_count, 0)}/${toNumber(lifecycleCanary.required_clean_sessions, 0)}` },
      { label: 'Broker matches', value: paperBroker.matched_count == null ? '--' : String(paperBroker.matched_count) },
      { label: 'State score', value: stateControl.score == null ? '--' : String(toNumber(stateControl.score, 0).toFixed(0)) },
      { label: 'Shadow fails', value: shadowValidation.failed_count == null ? '--' : String(toNumber(shadowValidation.failed_count, 0)) },
      { label: 'Blockers', value: String(blockers.length), tone: blockers.length ? 'negative' : 'positive' },
      { label: 'Warnings', value: String(warnings.length), tone: warnings.length ? 'warning' : 'neutral' },
      { label: 'Actions', value: String(operatorActions.length), tone: operatorActions.length ? 'warning' : 'neutral' },
      { label: 'Note', value: readiness.related_note_id || readiness.note_id ? 'Linked' : '--' },
    ],
  }
}

export function buildLivePilotSoakModel(snapshot = {}) {
  const soak = snapshot?.live_pilot_soak || {}
  const status = String(soak.status || 'not_run').trim().toLowerCase()
  const blockers = Array.isArray(soak.blockers) ? soak.blockers : []
  const warnings = Array.isArray(soak.warnings) ? soak.warnings : []
  const fillEvidence = soak.fill_evidence || {}
  const cancelEvidence = soak.cancel_evidence || {}
  const closeEvidence = soak.close_evidence || {}
  const reconciliationStatus = String(soak.reconciliation_status || 'not_run').trim().toLowerCase()
  const approvalStatus = String(soak.approval_status || 'missing').trim().toLowerCase()
  const tone =
    status === 'completed' || status === 'approved'
      ? 'positive'
      : status === 'blocked'
        ? 'negative'
        : status === 'warning'
          ? 'warning'
          : 'neutral'
  const description =
    status === 'approved'
      ? `Approval is fresh until ${soak.approval_expires_at ? new Date(soak.approval_expires_at).toLocaleString() : 'expiry'}. Run remains a separate manual action.`
      : status === 'completed'
        ? 'One tiny broker-live limit order was submitted, canceled or closed, and local reconciliation was recorded.'
        : status === 'blocked'
          ? `${blockers.length || 1} live pilot blocker${blockers.length === 1 ? '' : 's'} require manual review before any live order test.`
          : status === 'warning'
            ? `${warnings.length || 1} live pilot warning${warnings.length === 1 ? '' : 's'} recorded; no live automation gates were changed.`
            : 'Prepare the manual tiny live pilot soak after live readiness is clean.'
  return {
    status,
    tone,
    title: `Tiny live pilot: ${humanizeStatus(status, 'Not run')}`,
    description,
    approvalStatus,
    approvalExpiresAt: soak.approval_expires_at || null,
    symbol: soak.symbol || soak.settings?.live_pilot_symbol || 'SPY',
    notionalCap: soak.notional_cap ?? soak.settings?.live_pilot_max_notional ?? 10,
    referencePrice: soak.reference_price ?? null,
    limitPrice: soak.limit_price ?? null,
    quantity: soak.quantity ?? null,
    currentStep: soak.current_step || 'idle',
    checkedAt: soak.checked_at || soak.last_run_at || null,
    relatedNoteId: soak.related_note_id || soak.note_id || null,
    brokerOrderId: soak.broker_order_id || null,
    brokerStatus: soak.broker_status || null,
    localOrderId: soak.local_order_id || null,
    localTradeId: soak.local_trade_id || null,
    terminalState: soak.terminal_state || null,
    reconciliationStatus,
    fillEvidence,
    cancelEvidence,
    closeEvidence,
    blockers,
    warnings,
    manualActionRequired: Boolean(soak.manual_action_required || blockers.length),
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Approval', value: humanizeStatus(approvalStatus, 'Missing'), tone: approvalStatus === 'approved' ? 'positive' : approvalStatus === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Symbol', value: soak.symbol || soak.settings?.live_pilot_symbol || 'SPY' },
      { label: 'Cap', value: `$${Number(soak.notional_cap ?? soak.settings?.live_pilot_max_notional ?? 10).toFixed(2)}` },
      { label: 'Limit', value: soak.limit_price == null ? '--' : `$${Number(soak.limit_price).toFixed(2)}` },
      { label: 'Qty', value: soak.quantity == null ? '--' : String(soak.quantity) },
      { label: 'Terminal', value: humanizeStatus(soak.terminal_state, '--'), tone: soak.terminal_state ? 'positive' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(reconciliationStatus, 'Not run'), tone: reconciliationStatus === 'clean' ? 'positive' : reconciliationStatus === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Broker order', value: soak.broker_order_id ? String(soak.broker_order_id).slice(0, 12) : '--' },
      { label: 'Local order', value: soak.local_order_id ? String(soak.local_order_id).slice(0, 12) : '--' },
      { label: 'Cancel', value: cancelEvidence?.canceled ? 'Recorded' : '--', tone: cancelEvidence?.canceled ? 'positive' : 'neutral' },
      { label: 'Close', value: closeEvidence?.closed ? 'Recorded' : '--', tone: closeEvidence?.closed ? 'positive' : 'neutral' },
      { label: 'Expires', value: soak.approval_expires_at ? new Date(soak.approval_expires_at).toLocaleString() : '--' },
      { label: 'Note', value: soak.related_note_id || soak.note_id ? 'Linked' : '--' },
      { label: 'Manual action', value: soak.manual_action_required || blockers.length ? 'Required' : 'No', tone: blockers.length ? 'negative' : 'neutral' },
    ],
  }
}

export function buildLivePilotCanaryModel(snapshot = {}) {
  const canary = snapshot?.live_pilot_canary || {}
  const status = String(canary.status || 'not_run').trim().toLowerCase()
  const sessions = Array.isArray(canary.sessions) ? canary.sessions : []
  const blockers = Array.isArray(canary.blockers) ? canary.blockers : []
  const warnings = Array.isArray(canary.warnings) ? canary.warnings : []
  const noteCoverage = canary.note_coverage || {}
  const cleanCount = toNumber(canary.clean_session_count, 0)
  const requiredClean = toNumber(canary.required_clean_sessions, 3)
  const windowCount = toNumber(canary.window_session_count, sessions.length)
  const autoReviewEnabled = canary.auto_review_enabled !== false
  const runSource = String(canary.run_source || '').trim().toLowerCase()
  const nextEligibleRunAt = canary.next_eligible_run_at || null
  const lastScheduledRunAt = canary.last_scheduled_run_at || null
  const tone =
    canary.enabled === false
      ? 'neutral'
      : status === 'ready'
        ? 'positive'
        : status === 'blocked'
          ? 'negative'
          : status === 'not_run'
            ? 'neutral'
            : 'warning'
  const description =
    status === 'ready'
      ? `${cleanCount}/${requiredClean} tiny live pilot sessions are clean.`
      : status === 'blocked'
        ? `${blockers.length || 1} live pilot canary blocker${blockers.length === 1 ? '' : 's'} must clear before any live expansion.`
        : status === 'collecting'
          ? `${cleanCount}/${requiredClean} clean tiny live sessions collected.`
          : autoReviewEnabled && nextEligibleRunAt
            ? `Waiting for scheduled live canary review at ${new Date(nextEligibleRunAt).toLocaleString()}.`
            : 'Run the live pilot canary after manual tiny live soak evidence exists across sessions.'
  return {
    status,
    tone,
    title: canary.enabled === false ? 'Live canary disabled' : `Live canary: ${humanizeStatus(status, 'Not run')}`,
    description,
    enabled: canary.enabled !== false,
    autoReviewEnabled,
    runSource,
    skippedReason: String(canary.skipped_reason || '').trim(),
    nextEligibleRunAt,
    lastScheduledRunAt,
    evidenceWindow: canary.evidence_window || {},
    relatedNoteId: canary.related_note_id || canary.note_id || null,
    lastRunAt: canary.evaluated_at || canary.last_run_at || null,
    sessions,
    blockers,
    warnings,
    manualActionRequired: Boolean(canary.manual_action_required || blockers.length),
    latestSoakStatus: canary.latest_soak_status || 'missing',
    latestTerminalState: canary.latest_terminal_state || null,
    latestBrokerOrderId: canary.latest_broker_order_id || null,
    latestLocalOrderId: canary.latest_local_order_id || null,
    latestReconciliationStatus: canary.latest_reconciliation_status || 'missing',
    liveReadinessStatus: canary.live_readiness_status || 'missing',
    brokerLiveGateStatus: canary.broker_live_gate_status || 'unknown',
    safetyLockStatus: canary.safety_lock_status || 'unknown',
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Clean days', value: `${cleanCount}/${requiredClean}`, tone: cleanCount >= requiredClean ? 'positive' : 'warning' },
      { label: 'Evidence days', value: String(windowCount) },
      { label: 'Latest soak', value: humanizeStatus(canary.latest_soak_status, 'Missing') },
      { label: 'Terminal', value: humanizeStatus(canary.latest_terminal_state, '--'), tone: canary.latest_terminal_state ? 'positive' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(canary.latest_reconciliation_status, 'Missing'), tone: canary.latest_reconciliation_status === 'clean' ? 'positive' : canary.latest_reconciliation_status === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Readiness', value: humanizeStatus(canary.live_readiness_status, 'Missing'), tone: canary.live_readiness_status === 'ready_to_request_approval' ? 'positive' : canary.live_readiness_status === 'blocked' ? 'negative' : 'warning' },
      { label: 'Live gate', value: humanizeStatus(canary.broker_live_gate_status, 'Unknown'), tone: canary.broker_live_gate_status === 'open' ? 'positive' : 'negative' },
      { label: 'Safety locks', value: humanizeStatus(canary.safety_lock_status, 'Unknown'), tone: canary.safety_lock_status === 'clear' ? 'positive' : 'negative' },
      { label: 'Notes', value: `${toNumber(noteCoverage.covered, 0)}/${toNumber(noteCoverage.required, 0)}` },
      { label: 'Broker order', value: canary.latest_broker_order_id ? String(canary.latest_broker_order_id).slice(0, 12) : '--' },
      { label: 'Local order', value: canary.latest_local_order_id ? String(canary.latest_local_order_id).slice(0, 12) : '--' },
      { label: 'Auto review', value: autoReviewEnabled ? 'On' : 'Off', tone: autoReviewEnabled ? 'positive' : 'neutral' },
      { label: 'Run source', value: runSource ? humanizeStatus(runSource) : '--' },
      { label: 'Next review', value: nextEligibleRunAt ? new Date(nextEligibleRunAt).toLocaleString() : '--' },
      { label: 'Last scheduled', value: lastScheduledRunAt ? new Date(lastScheduledRunAt).toLocaleString() : '--' },
      { label: 'Note', value: canary.related_note_id || canary.note_id ? 'Linked' : '--' },
      { label: 'Manual action', value: canary.manual_action_required || blockers.length ? 'Required' : 'No', tone: blockers.length ? 'negative' : 'neutral' },
    ],
  }
}

export function buildLivePilotExpansionModel(snapshot = {}) {
  const expansion = snapshot?.live_pilot_expansion || {}
  const status = String(expansion.status || 'not_run').trim().toLowerCase()
  const blockers = Array.isArray(expansion.blockers) ? expansion.blockers : []
  const warnings = Array.isArray(expansion.warnings) ? expansion.warnings : []
  const candidate = expansion.selected_candidate || {}
  const approvalStatus = String(expansion.approval_status || 'missing').trim().toLowerCase()
  const reconciliationStatus = String(expansion.reconciliation_status || 'not_run').trim().toLowerCase()
  const cancelEvidence = expansion.cancel_evidence || {}
  const closeEvidence = expansion.close_evidence || {}
  const tone =
    status === 'completed' || status === 'approved'
      ? 'positive'
      : status === 'blocked'
        ? 'negative'
        : status === 'warning'
          ? 'warning'
          : 'neutral'
  const symbol = expansion.symbol || candidate.ticker || candidate.symbol || '--'
  const description =
    status === 'approved'
      ? `Expansion approval is fresh until ${expansion.approval_expires_at ? new Date(expansion.approval_expires_at).toLocaleString() : 'expiry'}. Run remains a separate manual action.`
      : status === 'completed'
        ? 'One operator-approved live limit order was submitted, canceled or closed, and reconciled.'
        : status === 'blocked'
          ? `${blockers.length || 1} live expansion blocker${blockers.length === 1 ? '' : 's'} must clear before any expansion order.`
          : 'Prepare a capped live pilot expansion after the live canary is ready and a clean candidate exists.'
  return {
    status,
    tone,
    title: `Live pilot expansion: ${humanizeStatus(status, 'Not run')}`,
    description,
    approvalStatus,
    approvalExpiresAt: expansion.approval_expires_at || null,
    selectedCandidate: candidate,
    symbol,
    side: expansion.side || 'buy',
    notionalCap: expansion.notional_cap ?? expansion.settings?.live_pilot_expansion_max_notional ?? 25,
    dailyOrderCap: expansion.daily_order_cap ?? expansion.settings?.live_pilot_expansion_max_daily_orders ?? 1,
    referencePrice: expansion.reference_price ?? null,
    limitPrice: expansion.limit_price ?? null,
    quantity: expansion.quantity ?? null,
    estimatedNotional: expansion.estimated_notional ?? null,
    currentStep: expansion.current_step || 'idle',
    checkedAt: expansion.checked_at || expansion.last_run_at || null,
    relatedNoteId: expansion.related_note_id || expansion.note_id || null,
    brokerOrderId: expansion.broker_order_id || null,
    brokerStatus: expansion.broker_status || null,
    localOrderId: expansion.local_order_id || null,
    localTradeId: expansion.local_trade_id || null,
    terminalState: expansion.terminal_state || null,
    reconciliationStatus,
    blockers,
    warnings,
    manualActionRequired: Boolean(expansion.manual_action_required || blockers.length),
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Approval', value: humanizeStatus(approvalStatus, 'Missing'), tone: approvalStatus === 'approved' ? 'positive' : approvalStatus === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Candidate', value: symbol },
      { label: 'Rank', value: candidate.portfolio_rank == null ? '--' : String(candidate.portfolio_rank) },
      { label: 'Cap', value: `$${Number(expansion.notional_cap ?? expansion.settings?.live_pilot_expansion_max_notional ?? 25).toFixed(2)}` },
      { label: 'Daily cap', value: String(expansion.daily_order_cap ?? expansion.settings?.live_pilot_expansion_max_daily_orders ?? 1) },
      { label: 'Limit', value: expansion.limit_price == null ? '--' : `$${Number(expansion.limit_price).toFixed(2)}` },
      { label: 'Qty', value: expansion.quantity == null ? '--' : String(expansion.quantity) },
      { label: 'Terminal', value: humanizeStatus(expansion.terminal_state, '--'), tone: expansion.terminal_state ? 'positive' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(reconciliationStatus, 'Not run'), tone: reconciliationStatus === 'clean' ? 'positive' : reconciliationStatus === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Broker order', value: expansion.broker_order_id ? String(expansion.broker_order_id).slice(0, 12) : '--' },
      { label: 'Local order', value: expansion.local_order_id ? String(expansion.local_order_id).slice(0, 12) : '--' },
      { label: 'Cancel', value: cancelEvidence?.canceled ? 'Recorded' : '--', tone: cancelEvidence?.canceled ? 'positive' : 'neutral' },
      { label: 'Close', value: closeEvidence?.closed ? 'Recorded' : '--', tone: closeEvidence?.closed ? 'positive' : 'neutral' },
      { label: 'Expires', value: expansion.approval_expires_at ? new Date(expansion.approval_expires_at).toLocaleString() : '--' },
      { label: 'Note', value: expansion.related_note_id || expansion.note_id ? 'Linked' : '--' },
      { label: 'Manual action', value: expansion.manual_action_required || blockers.length ? 'Required' : 'No', tone: blockers.length ? 'negative' : 'neutral' },
    ],
  }
}

export function buildLivePilotExpansionCanaryModel(snapshot = {}) {
  const canary = snapshot?.live_pilot_expansion_canary || {}
  const status = String(canary.status || 'not_run').trim().toLowerCase()
  const sessions = Array.isArray(canary.sessions) ? canary.sessions : []
  const blockers = Array.isArray(canary.blockers) ? canary.blockers : []
  const warnings = Array.isArray(canary.warnings) ? canary.warnings : []
  const noteCoverage = canary.note_coverage || {}
  const cleanCount = toNumber(canary.clean_session_count, 0)
  const requiredClean = toNumber(canary.required_clean_sessions, 3)
  const windowCount = toNumber(canary.window_session_count, sessions.length)
  const autoReviewEnabled = canary.auto_review_enabled !== false
  const runSource = String(canary.run_source || '').trim().toLowerCase()
  const nextEligibleRunAt = canary.next_eligible_run_at || null
  const lastScheduledRunAt = canary.last_scheduled_run_at || null
  const candidateEvidence = canary.candidate_evidence || {}
  const pnlSummary = canary.pnl_summary || {}
  const slippageSummary = canary.slippage_summary || {}
  const tone =
    canary.enabled === false
      ? 'neutral'
      : status === 'ready'
        ? 'positive'
        : status === 'blocked'
          ? 'negative'
          : status === 'not_run'
            ? 'neutral'
            : 'warning'
  const description =
    status === 'ready'
      ? `${cleanCount}/${requiredClean} capped live expansion sessions are clean.`
      : status === 'blocked'
        ? `${blockers.length || 1} live expansion canary blocker${blockers.length === 1 ? '' : 's'} must clear before any supervised live window.`
        : status === 'collecting'
          ? `${cleanCount}/${requiredClean} clean capped live expansion sessions collected.`
          : autoReviewEnabled && nextEligibleRunAt
            ? `Waiting for scheduled live expansion canary review at ${new Date(nextEligibleRunAt).toLocaleString()}.`
            : 'Run the expansion canary after operator-approved live expansion evidence exists across sessions.'
  const latestSymbol = canary.latest_symbol || candidateEvidence.ticker || candidateEvidence.symbol || '--'
  const averageSlippage = slippageSummary.average_abs_bps
  const realizedPnl = pnlSummary.realized_pnl
  return {
    status,
    tone,
    title: canary.enabled === false ? 'Live expansion canary disabled' : `Live expansion canary: ${humanizeStatus(status, 'Not run')}`,
    description,
    enabled: canary.enabled !== false,
    autoReviewEnabled,
    runSource,
    skippedReason: String(canary.skipped_reason || '').trim(),
    nextEligibleRunAt,
    lastScheduledRunAt,
    evidenceWindow: canary.evidence_window || {},
    relatedNoteId: canary.related_note_id || canary.note_id || null,
    lastRunAt: canary.evaluated_at || canary.last_run_at || null,
    sessions,
    blockers,
    warnings,
    manualActionRequired: Boolean(canary.manual_action_required || blockers.length),
    latestExpansionStatus: canary.latest_expansion_status || 'missing',
    latestTerminalState: canary.latest_terminal_state || null,
    latestBrokerOrderId: canary.latest_broker_order_id || null,
    latestLocalOrderId: canary.latest_local_order_id || null,
    latestReconciliationStatus: canary.latest_reconciliation_status || 'missing',
    liveReadinessStatus: canary.live_readiness_status || 'missing',
    brokerLiveGateStatus: canary.broker_live_gate_status || 'unknown',
    safetyLockStatus: canary.safety_lock_status || 'unknown',
    candidateEvidence,
    pnlSummary,
    slippageSummary,
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Clean days', value: `${cleanCount}/${requiredClean}`, tone: cleanCount >= requiredClean ? 'positive' : 'warning' },
      { label: 'Evidence days', value: String(windowCount) },
      { label: 'Latest expansion', value: humanizeStatus(canary.latest_expansion_status, 'Missing') },
      { label: 'Terminal', value: humanizeStatus(canary.latest_terminal_state, '--'), tone: canary.latest_terminal_state ? 'positive' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(canary.latest_reconciliation_status, 'Missing'), tone: canary.latest_reconciliation_status === 'clean' ? 'positive' : canary.latest_reconciliation_status === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Candidate', value: latestSymbol },
      { label: 'Avg slip', value: averageSlippage == null ? '--' : `${Number(averageSlippage).toFixed(1)} bps`, tone: Number(averageSlippage) > 50 ? 'warning' : 'neutral' },
      { label: 'Realized PnL', value: realizedPnl == null ? '--' : `$${Number(realizedPnl).toFixed(2)}` },
      { label: 'Readiness', value: humanizeStatus(canary.live_readiness_status, 'Missing'), tone: canary.live_readiness_status === 'ready_to_request_approval' ? 'positive' : canary.live_readiness_status === 'blocked' ? 'negative' : 'warning' },
      { label: 'Live gate', value: humanizeStatus(canary.broker_live_gate_status, 'Unknown'), tone: canary.broker_live_gate_status === 'open' ? 'positive' : 'negative' },
      { label: 'Safety locks', value: humanizeStatus(canary.safety_lock_status, 'Unknown'), tone: canary.safety_lock_status === 'clear' ? 'positive' : 'negative' },
      { label: 'Notes', value: `${toNumber(noteCoverage.covered, 0)}/${toNumber(noteCoverage.required, 0)}` },
      { label: 'Broker order', value: canary.latest_broker_order_id ? String(canary.latest_broker_order_id).slice(0, 12) : '--' },
      { label: 'Local order', value: canary.latest_local_order_id ? String(canary.latest_local_order_id).slice(0, 12) : '--' },
      { label: 'Auto review', value: autoReviewEnabled ? 'On' : 'Off', tone: autoReviewEnabled ? 'positive' : 'neutral' },
      { label: 'Run source', value: runSource ? humanizeStatus(runSource) : '--' },
      { label: 'Next review', value: nextEligibleRunAt ? new Date(nextEligibleRunAt).toLocaleString() : '--' },
      { label: 'Last scheduled', value: lastScheduledRunAt ? new Date(lastScheduledRunAt).toLocaleString() : '--' },
      { label: 'Note', value: canary.related_note_id || canary.note_id ? 'Linked' : '--' },
      { label: 'Manual action', value: canary.manual_action_required || blockers.length ? 'Required' : 'No', tone: blockers.length ? 'negative' : 'neutral' },
    ],
  }
}

export function buildLivePilotWindowCanaryModel(snapshot = {}) {
  const canary = snapshot?.live_pilot_window_canary || {}
  const status = String(canary.status || 'not_run').trim().toLowerCase()
  const sessions = Array.isArray(canary.sessions) ? canary.sessions : []
  const blockers = Array.isArray(canary.blockers) ? canary.blockers : []
  const warnings = Array.isArray(canary.warnings) ? canary.warnings : []
  const noteCoverage = canary.note_coverage || {}
  const cleanCount = toNumber(canary.clean_session_count, 0)
  const requiredClean = toNumber(canary.required_clean_sessions, 3)
  const windowCount = toNumber(canary.window_session_count, sessions.length)
  const autoReviewEnabled = canary.auto_review_enabled !== false
  const runSource = String(canary.run_source || '').trim().toLowerCase()
  const nextEligibleRunAt = canary.next_eligible_run_at || null
  const lastScheduledRunAt = canary.last_scheduled_run_at || null
  const candidateEvidence = canary.candidate_evidence || {}
  const pnlSummary = canary.pnl_summary || {}
  const slippageSummary = canary.slippage_summary || {}
  const tone =
    canary.enabled === false
      ? 'neutral'
      : status === 'ready'
        ? 'positive'
        : status === 'blocked'
          ? 'negative'
          : status === 'not_run'
            ? 'neutral'
            : 'warning'
  const description =
    status === 'ready'
      ? `${cleanCount}/${requiredClean} supervised live pilot sessions are clean.`
      : status === 'blocked'
        ? `${blockers.length || 1} supervised live pilot canary blocker${blockers.length === 1 ? '' : 's'} must clear before broader live review.`
        : status === 'collecting'
          ? `${cleanCount}/${requiredClean} clean supervised live pilot sessions collected.`
          : autoReviewEnabled && nextEligibleRunAt
            ? `Waiting for scheduled supervised live pilot canary review at ${new Date(nextEligibleRunAt).toLocaleString()}.`
            : 'Run the supervised live pilot canary after one-trade live window evidence exists across sessions.'
  const latestSymbol = canary.latest_symbol || candidateEvidence.ticker || candidateEvidence.symbol || '--'
  const averageSlippage = slippageSummary.average_abs_bps
  const realizedPnl = pnlSummary.realized_pnl
  return {
    status,
    tone,
    title: canary.enabled === false ? 'Supervised live canary disabled' : `Supervised live canary: ${humanizeStatus(status, 'Not run')}`,
    description,
    enabled: canary.enabled !== false,
    autoReviewEnabled,
    runSource,
    skippedReason: String(canary.skipped_reason || '').trim(),
    nextEligibleRunAt,
    lastScheduledRunAt,
    evidenceWindow: canary.evidence_window || {},
    relatedNoteId: canary.related_note_id || canary.note_id || null,
    lastRunAt: canary.evaluated_at || canary.last_run_at || null,
    sessions,
    blockers,
    warnings,
    manualActionRequired: Boolean(canary.manual_action_required || blockers.length),
    latestWindowStatus: canary.latest_window_status || 'missing',
    latestTerminalState: canary.latest_terminal_state || null,
    latestBrokerOrderId: canary.latest_broker_order_id || null,
    latestLocalOrderId: canary.latest_local_order_id || null,
    latestLocalTradeId: canary.latest_local_trade_id || null,
    latestReconciliationStatus: canary.latest_reconciliation_status || 'missing',
    liveReadinessStatus: canary.live_readiness_status || 'missing',
    brokerLiveGateStatus: canary.broker_live_gate_status || 'unknown',
    safetyLockStatus: canary.safety_lock_status || 'unknown',
    candidateEvidence,
    pnlSummary,
    slippageSummary,
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Clean days', value: `${cleanCount}/${requiredClean}`, tone: cleanCount >= requiredClean ? 'positive' : 'warning' },
      { label: 'Evidence days', value: String(windowCount) },
      { label: 'Latest window', value: humanizeStatus(canary.latest_window_status, 'Missing') },
      { label: 'Terminal', value: humanizeStatus(canary.latest_terminal_state, '--'), tone: canary.latest_terminal_state ? 'positive' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(canary.latest_reconciliation_status, 'Missing'), tone: canary.latest_reconciliation_status === 'clean' ? 'positive' : canary.latest_reconciliation_status === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Candidate', value: latestSymbol },
      { label: 'Avg slip', value: averageSlippage == null ? '--' : `${Number(averageSlippage).toFixed(1)} bps`, tone: Number(averageSlippage) > 50 ? 'warning' : 'neutral' },
      { label: 'Realized PnL', value: realizedPnl == null ? '--' : `$${Number(realizedPnl).toFixed(2)}` },
      { label: 'Readiness', value: humanizeStatus(canary.live_readiness_status, 'Missing'), tone: canary.live_readiness_status === 'ready_to_request_approval' ? 'positive' : canary.live_readiness_status === 'blocked' ? 'negative' : 'warning' },
      { label: 'Live gate', value: humanizeStatus(canary.broker_live_gate_status, 'Unknown'), tone: canary.broker_live_gate_status === 'open' ? 'positive' : 'negative' },
      { label: 'Safety locks', value: humanizeStatus(canary.safety_lock_status, 'Unknown'), tone: canary.safety_lock_status === 'clear' ? 'positive' : 'negative' },
      { label: 'Notes', value: `${toNumber(noteCoverage.covered, 0)}/${toNumber(noteCoverage.required, 0)}` },
      { label: 'Broker order', value: canary.latest_broker_order_id ? String(canary.latest_broker_order_id).slice(0, 12) : '--' },
      { label: 'Local order', value: canary.latest_local_order_id ? String(canary.latest_local_order_id).slice(0, 12) : '--' },
      { label: 'Auto review', value: autoReviewEnabled ? 'On' : 'Off', tone: autoReviewEnabled ? 'positive' : 'neutral' },
      { label: 'Run source', value: runSource ? humanizeStatus(runSource) : '--' },
      { label: 'Next review', value: nextEligibleRunAt ? new Date(nextEligibleRunAt).toLocaleString() : '--' },
      { label: 'Last scheduled', value: lastScheduledRunAt ? new Date(lastScheduledRunAt).toLocaleString() : '--' },
      { label: 'Note', value: canary.related_note_id || canary.note_id ? 'Linked' : '--' },
      { label: 'Manual action', value: canary.manual_action_required || blockers.length ? 'Required' : 'No', tone: blockers.length ? 'negative' : 'neutral' },
    ],
  }
}

export function buildLivePilotPromotionReportModel(snapshot = {}) {
  const report = snapshot?.live_pilot_promotion_report || {}
  const status = String(report.status || 'not_run').trim().toLowerCase()
  const blockers = Array.isArray(report.blockers) ? report.blockers : []
  const warnings = Array.isArray(report.warnings) ? report.warnings : []
  const actions = Array.isArray(report.required_operator_actions) ? report.required_operator_actions : []
  const evidence = report.evidence_summaries && typeof report.evidence_summaries === 'object' ? report.evidence_summaries : {}
  const progress = report.clean_session_progress || {}
  const clean = toNumber(progress.clean, 0)
  const required = toNumber(progress.required, toNumber(report.required_window_clean_sessions, 3))
  const pnlSummary = report.pnl_summary || {}
  const slippageSummary = report.slippage_summary || {}
  const orderEvents = report.order_event_summary || {}
  const autoReviewEnabled = report.auto_review_enabled !== false
  const nextEligibleRunAt = report.next_eligible_run_at || null
  const lastScheduledRunAt = report.last_scheduled_run_at || null
  const tone =
    report.enabled === false
      ? 'neutral'
      : status === 'ready_to_request_limited_live_rollout'
        ? 'positive'
        : status === 'blocked'
          ? 'negative'
          : status === 'needs_operator_review'
            ? 'warning'
            : 'neutral'
  const description =
    status === 'ready_to_request_limited_live_rollout'
      ? 'All required evidence is clean. The next step is a separate operator-approved limited-live rollout request.'
      : status === 'needs_operator_review'
        ? `${warnings.length || 1} promotion warning${warnings.length === 1 ? '' : 's'} need operator review before a rollout request.`
        : status === 'blocked'
          ? `${blockers.length || 1} promotion blocker${blockers.length === 1 ? '' : 's'} must clear before limited-live rollout review.`
          : autoReviewEnabled && nextEligibleRunAt
            ? `Waiting for scheduled promotion review at ${new Date(nextEligibleRunAt).toLocaleString()}.`
            : 'Run the live pilot promotion report after supervised live canary evidence is ready.'
  const evidenceRows = Object.values(evidence).filter(Boolean)
  return {
    status,
    tone,
    title: report.enabled === false ? 'Promotion report disabled' : `Promotion report: ${humanizeStatus(status, 'Not run')}`,
    description,
    enabled: report.enabled !== false,
    autoReviewEnabled,
    runSource: report.run_source || null,
    skippedReason: String(report.skipped_reason || '').trim(),
    nextEligibleRunAt,
    lastScheduledRunAt,
    relatedNoteId: report.related_note_id || report.note_id || null,
    lastRunAt: report.evaluated_at || report.last_run_at || null,
    evidenceRows,
    blockers,
    warnings,
    operatorActions: actions,
    manualActionRequired: Boolean(report.manual_action_required || blockers.length || warnings.length),
    brokerLiveGateStatus: report.broker_live_gate_status || 'unknown',
    safetyLockStatus: report.safety_lock_status || 'unknown',
    latestWindowStatus: report.latest_window_status || 'missing',
    latestWindowTerminalState: report.latest_window_terminal_state || null,
    latestWindowReconciliationStatus: report.latest_window_reconciliation_status || 'missing',
    latestBrokerOrderId: report.latest_broker_order_id || null,
    latestLocalOrderId: report.latest_local_order_id || null,
    pnlSummary,
    slippageSummary,
    orderEvents,
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Supervised days', value: `${clean}/${required}`, tone: clean >= required ? 'positive' : 'warning' },
      { label: 'Live gate', value: humanizeStatus(report.broker_live_gate_status, 'Unknown'), tone: report.broker_live_gate_status === 'open' ? 'positive' : 'negative' },
      { label: 'Safety locks', value: humanizeStatus(report.safety_lock_status, 'Unknown'), tone: report.safety_lock_status === 'clear' ? 'positive' : 'negative' },
      { label: 'Latest window', value: humanizeStatus(report.latest_window_status, 'Missing') },
      { label: 'Terminal', value: humanizeStatus(report.latest_window_terminal_state, '--'), tone: report.latest_window_terminal_state ? 'positive' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(report.latest_window_reconciliation_status, 'Missing'), tone: report.latest_window_reconciliation_status === 'clean' ? 'positive' : report.latest_window_reconciliation_status === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Worst slip', value: slippageSummary.worst_abs_bps == null ? '--' : `${Number(slippageSummary.worst_abs_bps).toFixed(1)} bps`, tone: Number(slippageSummary.worst_abs_bps || 0) > 100 ? 'negative' : Number(slippageSummary.worst_abs_bps || 0) > 50 ? 'warning' : 'neutral' },
      { label: 'Realized PnL', value: pnlSummary.realized_pnl == null ? '--' : `$${Number(pnlSummary.realized_pnl).toFixed(2)}` },
      { label: 'Order events', value: String(toNumber(orderEvents.count, 0)), tone: toNumber(orderEvents.failed_count, 0) ? 'negative' : 'neutral' },
      { label: 'Evidence', value: `${evidenceRows.filter((item) => item.ready).length}/${evidenceRows.length}` },
      { label: 'Auto review', value: autoReviewEnabled ? 'On' : 'Off', tone: autoReviewEnabled ? 'positive' : 'neutral' },
      { label: 'Run source', value: report.run_source ? humanizeStatus(report.run_source) : '--' },
      { label: 'Next review', value: nextEligibleRunAt ? new Date(nextEligibleRunAt).toLocaleString() : '--' },
      { label: 'Last scheduled', value: lastScheduledRunAt ? new Date(lastScheduledRunAt).toLocaleString() : '--' },
      { label: 'Note', value: report.related_note_id || report.note_id ? 'Linked' : '--' },
      { label: 'Manual action', value: report.manual_action_required || blockers.length || warnings.length ? 'Required' : 'No', tone: blockers.length ? 'negative' : warnings.length ? 'warning' : 'neutral' },
    ],
  }
}

export function buildLimitedLiveRolloutGateModel(snapshot = {}) {
  const gate = snapshot?.limited_live_rollout_gate || {}
  const status = String(gate.status || 'not_prepared').trim().toLowerCase()
  const blockers = Array.isArray(gate.blockers) ? gate.blockers : []
  const warnings = Array.isArray(gate.warnings) ? gate.warnings : []
  const caps = gate.caps && typeof gate.caps === 'object' ? gate.caps : {}
  const evidence = gate.candidate_order_evidence && typeof gate.candidate_order_evidence === 'object'
    ? gate.candidate_order_evidence
    : {}
  const candidate = evidence.candidate || gate.selected_candidate || {}
  const orders = Array.isArray(evidence.orders) ? evidence.orders : []
  const approvalState = String(gate.approval_state || 'missing').trim().toLowerCase()
  const rolloutActive = Boolean(gate.rollout_active)
  const consumed = toNumber(gate.consumed_order_count, 0)
  const maxOrders = toNumber(caps.max_session_orders ?? gate.max_session_orders, 1)
  const maxNotional = toNumber(caps.max_notional ?? gate.notional_cap, 100)
  const tone =
    gate.enabled === false
      ? 'neutral'
      : status === 'active'
        ? 'positive'
        : status === 'approved'
          ? 'warning'
          : status === 'rolled_back'
            ? 'neutral'
            : status === 'blocked'
              ? 'negative'
              : 'neutral'
  const description =
    status === 'active'
      ? `Runtime live allowance is active until ${gate.rollout_expires_at ? new Date(gate.rollout_expires_at).toLocaleString() : 'expiry'} with ${Math.max(0, maxOrders - consumed)} order remaining.`
      : status === 'approved'
        ? `Approval is fresh until ${gate.approval_expires_at ? new Date(gate.approval_expires_at).toLocaleString() : 'expiry'}. Activation remains a separate manual action.`
        : status === 'rolled_back'
          ? 'The runtime allowance has been rolled back and live routing must be prepared again.'
          : status === 'blocked'
            ? `${blockers.length || 1} rollout blocker${blockers.length === 1 ? '' : 's'} must clear before activation.`
            : 'Prepare a limited-live rollout gate after the promotion report is ready.'
  return {
    status,
    tone,
    title: gate.enabled === false ? 'Limited-live rollout disabled' : `Limited-live gate: ${humanizeStatus(status, 'Not prepared')}`,
    description,
    enabled: gate.enabled !== false,
    approvalState,
    approvalExpiresAt: gate.approval_expires_at || null,
    rolloutActive,
    rolloutExpiresAt: gate.rollout_expires_at || null,
    rollbackState: gate.rollback_state || 'inactive',
    rollbackReason: gate.rollback_reason || '',
    relatedNoteId: gate.related_note_id || gate.note_id || null,
    blockers,
    warnings,
    candidate,
    orders,
    manualActionRequired: Boolean(gate.manual_action_required || blockers.length),
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not prepared'), tone },
      { label: 'Approval', value: humanizeStatus(approvalState, 'Missing'), tone: approvalState === 'approved' ? 'warning' : approvalState === 'consumed' ? 'positive' : 'neutral' },
      { label: 'Active', value: rolloutActive ? 'Yes' : 'No', tone: rolloutActive ? 'positive' : 'neutral' },
      { label: 'Cap', value: `$${maxNotional.toFixed(2)}` },
      { label: 'Orders', value: `${consumed}/${maxOrders}`, tone: consumed >= maxOrders ? 'warning' : 'neutral' },
      { label: 'Limit only', value: caps.require_limit === false ? 'No' : 'Yes', tone: caps.require_limit === false ? 'negative' : 'positive' },
      { label: 'Candidate', value: candidate.ticker || candidate.symbol || '--' },
      { label: 'Broker order', value: orders[0]?.broker_order_id || '--' },
      { label: 'Local order', value: orders[0]?.order_id || '--' },
      { label: 'Rollback', value: humanizeStatus(gate.rollback_state, '--') },
      { label: 'Note', value: gate.related_note_id || gate.note_id ? 'Linked' : '--' },
    ],
  }
}

export function buildLimitedLiveRolloutCanaryModel(snapshot = {}) {
  const canary = snapshot?.limited_live_rollout_canary || {}
  const status = String(canary.status || 'not_run').trim().toLowerCase()
  const sessions = Array.isArray(canary.sessions) ? canary.sessions : []
  const blockers = Array.isArray(canary.blockers) ? canary.blockers : []
  const warnings = Array.isArray(canary.warnings) ? canary.warnings : []
  const noteCoverage = canary.note_coverage || {}
  const cleanCount = toNumber(canary.clean_session_count, 0)
  const requiredClean = toNumber(canary.required_clean_sessions, 3)
  const windowCount = toNumber(canary.window_session_count, sessions.length)
  const consumed = toNumber(canary.consumed_order_count, 0)
  const autoReviewEnabled = canary.auto_review_enabled !== false
  const runSource = String(canary.run_source || '').trim().toLowerCase()
  const nextEligibleRunAt = canary.next_eligible_run_at || null
  const lastScheduledRunAt = canary.last_scheduled_run_at || null
  const pnlSummary = canary.pnl_summary || {}
  const slippageSummary = canary.slippage_summary || {}
  const tone =
    canary.enabled === false
      ? 'neutral'
      : status === 'ready_for_operator_review'
        ? 'positive'
        : status === 'blocked'
          ? 'negative'
          : status === 'warning'
            ? 'warning'
            : 'neutral'
  const description =
    status === 'ready_for_operator_review'
      ? `${cleanCount}/${requiredClean} limited-live rollout sessions are clean.`
      : status === 'blocked'
        ? `${blockers.length || 1} limited-live rollout canary blocker${blockers.length === 1 ? '' : 's'} must clear before cap expansion review.`
        : status === 'warning'
          ? `${warnings.length || 1} limited-live rollout canary warning${warnings.length === 1 ? '' : 's'} need operator review.`
          : status === 'collecting'
            ? `${cleanCount}/${requiredClean} clean limited-live rollout sessions collected.`
            : autoReviewEnabled && nextEligibleRunAt
              ? `Waiting for scheduled limited-live rollout canary review at ${new Date(nextEligibleRunAt).toLocaleString()}.`
              : 'Run the limited-live rollout canary after capped live rollout evidence exists across sessions.'
  return {
    status,
    tone,
    title: canary.enabled === false ? 'Limited-live canary disabled' : `Limited-live canary: ${humanizeStatus(status, 'Not run')}`,
    description,
    enabled: canary.enabled !== false,
    autoReviewEnabled,
    runSource,
    scheduledStatus: canary.scheduled_status || '',
    skippedReason: String(canary.skipped_reason || '').trim(),
    nextEligibleRunAt,
    lastScheduledRunAt,
    evidenceWindow: canary.evidence_window || {},
    relatedNoteId: canary.related_note_id || canary.note_id || null,
    lastRunAt: canary.evaluated_at || canary.last_run_at || null,
    sessions,
    blockers,
    warnings,
    manualActionRequired: Boolean(canary.manual_action_required || blockers.length || warnings.length),
    latestRolloutStatus: canary.latest_rollout_status || 'missing',
    latestTerminalState: canary.latest_terminal_state || null,
    latestReconciliationStatus: canary.latest_reconciliation_status || 'missing',
    latestBrokerOrderId: canary.latest_broker_order_id || null,
    latestLocalOrderId: canary.latest_local_order_id || null,
    consumedOrderCount: consumed,
    brokerGateStatus: canary.broker_gate_status || canary.broker_live_gate_status || 'unknown',
    safetyLockStatus: canary.safety_lock_status || 'unknown',
    promotionStatus: canary.promotion_status || 'missing',
    pnlSummary,
    slippageSummary,
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Clean days', value: `${cleanCount}/${requiredClean}`, tone: cleanCount >= requiredClean ? 'positive' : 'warning' },
      { label: 'Evidence days', value: String(windowCount) },
      { label: 'Consumed', value: String(consumed), tone: consumed ? 'positive' : 'warning' },
      { label: 'Latest rollout', value: humanizeStatus(canary.latest_rollout_status, 'Missing') },
      { label: 'Terminal', value: humanizeStatus(canary.latest_terminal_state, '--'), tone: canary.latest_terminal_state ? 'positive' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(canary.latest_reconciliation_status, 'Missing'), tone: canary.latest_reconciliation_status === 'clean' ? 'positive' : canary.latest_reconciliation_status === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Worst slip', value: slippageSummary.worst_abs_bps == null ? '--' : `${Number(slippageSummary.worst_abs_bps).toFixed(1)} bps`, tone: Number(slippageSummary.worst_abs_bps || 0) > 100 ? 'negative' : Number(slippageSummary.worst_abs_bps || 0) > 50 ? 'warning' : 'neutral' },
      { label: 'Realized PnL', value: pnlSummary.realized_pnl == null ? '--' : `$${Number(pnlSummary.realized_pnl).toFixed(2)}` },
      { label: 'Promotion', value: humanizeStatus(canary.promotion_status, 'Missing'), tone: canary.promotion_status === 'ready_to_request_limited_live_rollout' ? 'positive' : 'warning' },
      { label: 'Live gate', value: humanizeStatus(canary.broker_gate_status || canary.broker_live_gate_status, 'Unknown'), tone: (canary.broker_gate_status || canary.broker_live_gate_status) === 'open' ? 'positive' : 'negative' },
      { label: 'Safety locks', value: humanizeStatus(canary.safety_lock_status, 'Unknown'), tone: canary.safety_lock_status === 'clear' ? 'positive' : 'negative' },
      { label: 'Notes', value: `${toNumber(noteCoverage.covered, 0)}/${toNumber(noteCoverage.required, 0)}` },
      { label: 'Broker order', value: canary.latest_broker_order_id ? String(canary.latest_broker_order_id).slice(0, 12) : '--' },
      { label: 'Local order', value: canary.latest_local_order_id ? String(canary.latest_local_order_id).slice(0, 12) : '--' },
      { label: 'Auto review', value: autoReviewEnabled ? 'On' : 'Off', tone: autoReviewEnabled ? 'positive' : 'neutral' },
      { label: 'Run source', value: runSource ? humanizeStatus(runSource) : '--' },
      { label: 'Next review', value: nextEligibleRunAt ? new Date(nextEligibleRunAt).toLocaleString() : '--' },
      { label: 'Last scheduled', value: lastScheduledRunAt ? new Date(lastScheduledRunAt).toLocaleString() : '--' },
      { label: 'Note', value: canary.related_note_id || canary.note_id ? 'Linked' : '--' },
    ],
  }
}

export function buildLimitedLiveCapExpansionReportModel(snapshot = {}) {
  const report = snapshot?.limited_live_cap_expansion_report || {}
  const status = String(report.status || 'not_run').trim().toLowerCase()
  const blockers = Array.isArray(report.blockers) ? report.blockers : []
  const warnings = Array.isArray(report.warnings) ? report.warnings : []
  const actions = Array.isArray(report.required_operator_actions) ? report.required_operator_actions : []
  const evidence = report.evidence_summaries && typeof report.evidence_summaries === 'object' ? report.evidence_summaries : {}
  const progress = report.clean_session_progress || {}
  const clean = toNumber(progress.clean, 0)
  const required = toNumber(progress.required, toNumber(report.required_clean_sessions, 3))
  const currentCap = toNumber(report.current_max_notional, 100)
  const targetCap = toNumber(report.target_max_notional, 250)
  const recommendedCap = toNumber(report.recommended_next_max_notional, targetCap)
  const pnlSummary = report.pnl_summary || {}
  const slippageSummary = report.slippage_summary || {}
  const orderEvents = report.order_event_summary || {}
  const autoReviewEnabled = report.auto_review_enabled !== false
  const nextEligibleRunAt = report.next_eligible_run_at || null
  const lastScheduledRunAt = report.last_scheduled_run_at || null
  const tone =
    report.enabled === false
      ? 'neutral'
      : status === 'ready_to_request_cap_expansion'
        ? 'positive'
        : status === 'blocked'
          ? 'negative'
          : status === 'needs_operator_review'
            ? 'warning'
            : 'neutral'
  const description =
    status === 'ready_to_request_cap_expansion'
      ? `Limited-live evidence is clean. Recommended next cap is $${recommendedCap.toFixed(2)}.`
      : status === 'needs_operator_review'
        ? `${warnings.length || 1} cap expansion warning${warnings.length === 1 ? '' : 's'} need operator review.`
        : status === 'blocked'
          ? `${blockers.length || 1} cap expansion blocker${blockers.length === 1 ? '' : 's'} must clear before a higher cap request.`
          : autoReviewEnabled && nextEligibleRunAt
            ? `Waiting for scheduled cap expansion review at ${new Date(nextEligibleRunAt).toLocaleString()}.`
            : 'Run the cap expansion report after the limited-live rollout canary is ready.'
  const evidenceRows = Object.values(evidence).filter(Boolean)
  return {
    status,
    tone,
    title: report.enabled === false ? 'Cap expansion report disabled' : `Cap expansion: ${humanizeStatus(status, 'Not run')}`,
    description,
    enabled: report.enabled !== false,
    autoReviewEnabled,
    runSource: report.run_source || null,
    scheduledStatus: report.scheduled_status || '',
    skippedReason: String(report.skipped_reason || '').trim(),
    nextEligibleRunAt,
    lastScheduledRunAt,
    relatedNoteId: report.related_note_id || report.note_id || null,
    lastRunAt: report.evaluated_at || report.last_run_at || null,
    evidenceRows,
    blockers,
    warnings,
    operatorActions: actions,
    manualActionRequired: Boolean(report.manual_action_required || blockers.length || warnings.length),
    currentCap,
    targetCap,
    recommendedCap,
    brokerLiveGateStatus: report.broker_live_gate_status || report.broker_gate_status || 'unknown',
    safetyLockStatus: report.safety_lock_status || 'unknown',
    stateControlStatus: report.state_control_status || 'unknown',
    promotionStatus: report.promotion_status || 'missing',
    rolloutCanaryStatus: report.rollout_canary_status || 'missing',
    latestReconciliationStatus: report.latest_reconciliation_status || 'missing',
    consumedOrderCount: toNumber(report.consumed_order_count, 0),
    pnlSummary,
    slippageSummary,
    orderEvents,
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Current cap', value: `$${currentCap.toFixed(2)}` },
      { label: 'Target cap', value: `$${targetCap.toFixed(2)}` },
      { label: 'Recommended', value: `$${recommendedCap.toFixed(2)}`, tone: recommendedCap > currentCap ? 'positive' : 'neutral' },
      { label: 'Clean days', value: `${clean}/${required}`, tone: clean >= required ? 'positive' : 'warning' },
      { label: 'Rollout canary', value: humanizeStatus(report.rollout_canary_status, 'Missing'), tone: report.rollout_canary_status === 'ready_for_operator_review' ? 'positive' : 'warning' },
      { label: 'Promotion', value: humanizeStatus(report.promotion_status, 'Missing'), tone: report.promotion_status === 'ready_to_request_limited_live_rollout' ? 'positive' : 'warning' },
      { label: 'Live gate', value: humanizeStatus(report.broker_live_gate_status || report.broker_gate_status, 'Unknown'), tone: (report.broker_live_gate_status || report.broker_gate_status) === 'open' ? 'positive' : 'negative' },
      { label: 'Safety locks', value: humanizeStatus(report.safety_lock_status, 'Unknown'), tone: report.safety_lock_status === 'clear' ? 'positive' : 'negative' },
      { label: 'State control', value: humanizeStatus(report.state_control_status, 'Unknown'), tone: report.state_control_status === 'halt' ? 'negative' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(report.latest_reconciliation_status, 'Missing'), tone: report.latest_reconciliation_status === 'clean' ? 'positive' : report.latest_reconciliation_status === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Consumed', value: String(toNumber(report.consumed_order_count, 0)) },
      { label: 'Worst slip', value: slippageSummary.worst_abs_bps == null ? '--' : `${Number(slippageSummary.worst_abs_bps).toFixed(1)} bps`, tone: Number(slippageSummary.worst_abs_bps || 0) > 100 ? 'negative' : Number(slippageSummary.worst_abs_bps || 0) > 50 ? 'warning' : 'neutral' },
      { label: 'Realized PnL', value: pnlSummary.realized_pnl == null ? '--' : `$${Number(pnlSummary.realized_pnl).toFixed(2)}`, tone: Number(pnlSummary.realized_pnl || 0) < 0 ? 'negative' : 'neutral' },
      { label: 'Order events', value: String(toNumber(orderEvents.count, 0)), tone: toNumber(orderEvents.failed_count, 0) || toNumber(orderEvents.cap_breach_count, 0) ? 'negative' : 'neutral' },
      { label: 'Evidence', value: `${evidenceRows.filter((item) => item.ready).length}/${evidenceRows.length}` },
      { label: 'Auto review', value: autoReviewEnabled ? 'On' : 'Off', tone: autoReviewEnabled ? 'positive' : 'neutral' },
      { label: 'Run source', value: report.run_source ? humanizeStatus(report.run_source) : '--' },
      { label: 'Next review', value: nextEligibleRunAt ? new Date(nextEligibleRunAt).toLocaleString() : '--' },
      { label: 'Note', value: report.related_note_id || report.note_id ? 'Linked' : '--' },
    ],
  }
}

export function buildLimitedLiveCapExpansionGateModel(snapshot = {}) {
  const gate = snapshot?.limited_live_cap_expansion_gate || {}
  const status = String(gate.status || 'not_prepared').trim().toLowerCase()
  const blockers = Array.isArray(gate.blockers) ? gate.blockers : []
  const warnings = Array.isArray(gate.warnings) ? gate.warnings : []
  const caps = gate.caps || {}
  const approvalState = String(gate.approval_state || 'missing').trim().toLowerCase()
  const expansionActive = Boolean(gate.expansion_active)
  const currentCap = toNumber(caps.current_max_notional ?? gate.current_max_notional, 100)
  const expandedCap = toNumber(caps.expanded_max_notional ?? gate.expanded_max_notional, 250)
  const consumed = toNumber(gate.consumed_order_count, 0)
  const maxOrders = toNumber(caps.max_session_orders ?? gate.max_session_orders, 1)
  const tone =
    gate.enabled === false
      ? 'neutral'
      : status === 'active'
        ? 'positive'
        : status === 'approved'
          ? 'warning'
          : status === 'blocked'
            ? 'negative'
            : status === 'rolled_back'
              ? 'neutral'
              : 'neutral'
  const description =
    status === 'active'
      ? `Runtime expanded cap is active until ${gate.expansion_expires_at ? new Date(gate.expansion_expires_at).toLocaleString() : 'expiry'} with ${Math.max(0, maxOrders - consumed)} order remaining.`
      : status === 'approved'
        ? `Approval is fresh until ${gate.approval_expires_at ? new Date(gate.approval_expires_at).toLocaleString() : 'expiry'}. Activation remains a separate manual action.`
        : status === 'rolled_back'
          ? 'Runtime expanded-cap allowance was rolled back.'
          : status === 'blocked'
            ? `${blockers.length || 1} cap expansion gate blocker${blockers.length === 1 ? '' : 's'} must clear before activation.`
            : 'Prepare the cap expansion gate after the cap expansion report is ready and a base limited-live rollout allowance is active.'
  return {
    status,
    tone,
    title: gate.enabled === false ? 'Cap expansion gate disabled' : `Cap expansion gate: ${humanizeStatus(status, 'Not prepared')}`,
    description,
    enabled: gate.enabled !== false,
    approvalState,
    approvalExpiresAt: gate.approval_expires_at || null,
    expansionActive,
    expansionExpiresAt: gate.expansion_expires_at || null,
    rollbackState: gate.rollback_state || 'inactive',
    rollbackReason: gate.rollback_reason || '',
    relatedNoteId: gate.related_note_id || gate.note_id || null,
    blockers,
    warnings,
    currentCap,
    expandedCap,
    consumedOrderCount: consumed,
    maxSessionOrders: maxOrders,
    orders: Array.isArray(gate?.candidate_order_evidence?.orders) ? gate.candidate_order_evidence.orders : [],
    metrics: [
      { label: 'Approval', value: humanizeStatus(approvalState, 'Missing'), tone: approvalState === 'approved' ? 'warning' : approvalState === 'consumed' ? 'positive' : 'neutral' },
      { label: 'Active', value: expansionActive ? 'Yes' : 'No', tone: expansionActive ? 'positive' : 'neutral' },
      { label: 'Current cap', value: `$${currentCap.toFixed(2)}` },
      { label: 'Expanded cap', value: `$${expandedCap.toFixed(2)}`, tone: expandedCap > currentCap ? 'positive' : 'neutral' },
      { label: 'Orders', value: `${consumed}/${maxOrders}`, tone: consumed >= maxOrders ? 'warning' : 'neutral' },
      { label: 'Limit only', value: caps.require_limit === false ? 'No' : 'Yes', tone: caps.require_limit === false ? 'negative' : 'positive' },
      { label: 'Rollback', value: humanizeStatus(gate.rollback_state, 'Inactive') },
      { label: 'Note', value: gate.related_note_id || gate.note_id ? 'Linked' : '--' },
    ],
  }
}

export function buildLimitedLiveCapExpansionCanaryModel(snapshot = {}) {
  const canary = snapshot?.limited_live_cap_expansion_canary || {}
  const status = String(canary.status || 'not_run').trim().toLowerCase()
  const sessions = Array.isArray(canary.sessions) ? canary.sessions : []
  const blockers = Array.isArray(canary.blockers) ? canary.blockers : []
  const warnings = Array.isArray(canary.warnings) ? canary.warnings : []
  const noteCoverage = canary.note_coverage || {}
  const cleanCount = toNumber(canary.clean_session_count, 0)
  const requiredClean = toNumber(canary.required_clean_sessions, 3)
  const windowCount = toNumber(canary.window_session_count, sessions.length)
  const consumed = toNumber(canary.consumed_order_count, 0)
  const currentCap = toNumber(canary.current_max_notional, 100)
  const expandedCap = toNumber(canary.expanded_max_notional, 250)
  const autoReviewEnabled = canary.auto_review_enabled !== false
  const runSource = String(canary.run_source || '').trim().toLowerCase()
  const nextEligibleRunAt = canary.next_eligible_run_at || null
  const lastScheduledRunAt = canary.last_scheduled_run_at || null
  const pnlSummary = canary.pnl_summary || {}
  const slippageSummary = canary.slippage_summary || {}
  const tone =
    canary.enabled === false
      ? 'neutral'
      : status === 'ready_for_operator_review'
        ? 'positive'
        : status === 'blocked'
          ? 'negative'
          : status === 'warning'
            ? 'warning'
            : 'neutral'
  const description =
    status === 'ready_for_operator_review'
      ? `${cleanCount}/${requiredClean} expanded-cap sessions are clean.`
      : status === 'blocked'
        ? `${blockers.length || 1} expanded-cap canary blocker${blockers.length === 1 ? '' : 's'} must clear before a higher-cap review.`
        : status === 'warning'
          ? `${warnings.length || 1} expanded-cap canary warning${warnings.length === 1 ? '' : 's'} need operator review.`
          : status === 'collecting'
            ? `${cleanCount}/${requiredClean} clean expanded-cap sessions collected.`
            : autoReviewEnabled && nextEligibleRunAt
              ? `Waiting for scheduled expanded-cap canary review at ${new Date(nextEligibleRunAt).toLocaleString()}.`
              : 'Run the expanded-cap canary after cap-expansion gate evidence exists across sessions.'
  return {
    status,
    tone,
    title: canary.enabled === false ? 'Expanded-cap canary disabled' : `Expanded-cap canary: ${humanizeStatus(status, 'Not run')}`,
    description,
    enabled: canary.enabled !== false,
    autoReviewEnabled,
    runSource,
    scheduledStatus: canary.scheduled_status || '',
    skippedReason: String(canary.skipped_reason || '').trim(),
    nextEligibleRunAt,
    lastScheduledRunAt,
    evidenceWindow: canary.evidence_window || {},
    relatedNoteId: canary.related_note_id || canary.note_id || null,
    lastRunAt: canary.evaluated_at || canary.last_run_at || null,
    sessions,
    blockers,
    warnings,
    manualActionRequired: Boolean(canary.manual_action_required || blockers.length || warnings.length),
    latestGateStatus: canary.latest_gate_status || canary.latest_rollout_status || 'missing',
    latestTerminalState: canary.latest_terminal_state || null,
    latestReconciliationStatus: canary.latest_reconciliation_status || 'missing',
    latestBrokerOrderId: canary.latest_broker_order_id || null,
    latestLocalOrderId: canary.latest_local_order_id || null,
    currentCap,
    expandedCap,
    consumedOrderCount: consumed,
    brokerGateStatus: canary.broker_gate_status || canary.broker_live_gate_status || 'unknown',
    safetyLockStatus: canary.safety_lock_status || 'unknown',
    stateControlStatus: canary.state_control_status || 'unknown',
    capExpansionReportStatus: canary.cap_expansion_report_status || 'missing',
    rolloutCanaryStatus: canary.rollout_canary_status || 'missing',
    pnlSummary,
    slippageSummary,
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Clean days', value: `${cleanCount}/${requiredClean}`, tone: cleanCount >= requiredClean ? 'positive' : 'warning' },
      { label: 'Evidence days', value: String(windowCount) },
      { label: 'Consumed', value: String(consumed), tone: consumed ? 'positive' : 'warning' },
      { label: 'Current cap', value: `$${currentCap.toFixed(2)}` },
      { label: 'Expanded cap', value: `$${expandedCap.toFixed(2)}`, tone: expandedCap > currentCap ? 'positive' : 'neutral' },
      { label: 'Latest gate', value: humanizeStatus(canary.latest_gate_status || canary.latest_rollout_status, 'Missing') },
      { label: 'Terminal', value: humanizeStatus(canary.latest_terminal_state, '--'), tone: canary.latest_terminal_state ? 'positive' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(canary.latest_reconciliation_status, 'Missing'), tone: canary.latest_reconciliation_status === 'clean' ? 'positive' : canary.latest_reconciliation_status === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Worst slip', value: slippageSummary.worst_abs_bps == null ? '--' : `${Number(slippageSummary.worst_abs_bps).toFixed(1)} bps`, tone: Number(slippageSummary.worst_abs_bps || 0) > 100 ? 'negative' : Number(slippageSummary.worst_abs_bps || 0) > 50 ? 'warning' : 'neutral' },
      { label: 'Realized PnL', value: pnlSummary.realized_pnl == null ? '--' : `$${Number(pnlSummary.realized_pnl).toFixed(2)}`, tone: Number(pnlSummary.realized_pnl || 0) < 0 ? 'negative' : 'neutral' },
      { label: 'Cap report', value: humanizeStatus(canary.cap_expansion_report_status, 'Missing'), tone: canary.cap_expansion_report_status === 'ready_to_request_cap_expansion' ? 'positive' : 'warning' },
      { label: 'Live gate', value: humanizeStatus(canary.broker_gate_status || canary.broker_live_gate_status, 'Unknown'), tone: (canary.broker_gate_status || canary.broker_live_gate_status) === 'open' ? 'positive' : 'negative' },
      { label: 'Safety locks', value: humanizeStatus(canary.safety_lock_status, 'Unknown'), tone: canary.safety_lock_status === 'clear' ? 'positive' : 'negative' },
      { label: 'State control', value: humanizeStatus(canary.state_control_status, 'Unknown'), tone: canary.state_control_status === 'halt' ? 'negative' : 'neutral' },
      { label: 'Notes', value: `${toNumber(noteCoverage.covered, 0)}/${toNumber(noteCoverage.required, 0)}` },
      { label: 'Broker order', value: canary.latest_broker_order_id ? String(canary.latest_broker_order_id).slice(0, 12) : '--' },
      { label: 'Local order', value: canary.latest_local_order_id ? String(canary.latest_local_order_id).slice(0, 12) : '--' },
      { label: 'Auto review', value: autoReviewEnabled ? 'On' : 'Off', tone: autoReviewEnabled ? 'positive' : 'neutral' },
      { label: 'Run source', value: runSource ? humanizeStatus(runSource) : '--' },
      { label: 'Next review', value: nextEligibleRunAt ? new Date(nextEligibleRunAt).toLocaleString() : '--' },
      { label: 'Last scheduled', value: lastScheduledRunAt ? new Date(lastScheduledRunAt).toLocaleString() : '--' },
      { label: 'Note', value: canary.related_note_id || canary.note_id ? 'Linked' : '--' },
    ],
  }
}

export function buildLimitedLiveNextTierCapReportModel(snapshot = {}) {
  const report = snapshot?.limited_live_next_tier_cap_report || {}
  const status = String(report.status || 'not_run').trim().toLowerCase()
  const blockers = Array.isArray(report.blockers) ? report.blockers : []
  const warnings = Array.isArray(report.warnings) ? report.warnings : []
  const actions = Array.isArray(report.required_operator_actions) ? report.required_operator_actions : []
  const progress = report.clean_session_progress || {}
  const evidence = report.evidence_summaries || {}
  const evidenceRows = Object.entries(evidence).map(([key, value]) => ({ key, ...(value || {}) }))
  const currentCap = toNumber(report.current_max_notional, 250)
  const recommendedCap = toNumber(report.recommended_next_max_notional, toNumber(report.target_max_notional, 500))
  const targetCap = toNumber(report.target_max_notional, 500)
  const clean = toNumber(progress.clean, 0)
  const required = toNumber(progress.required ?? report.required_clean_sessions, 3)
  const autoReviewEnabled = report.auto_review_enabled !== false
  const runSource = String(report.run_source || '').trim().toLowerCase()
  const nextEligibleRunAt = report.next_eligible_run_at || null
  const lastScheduledRunAt = report.last_scheduled_run_at || null
  const pnlSummary = report.pnl_summary || {}
  const slippageSummary = report.slippage_summary || {}
  const orderEvents = report.order_event_summary || {}
  const tone =
    report.enabled === false
      ? 'neutral'
      : status === 'ready_to_request_next_tier_cap'
        ? 'positive'
        : status === 'blocked'
          ? 'negative'
          : status === 'needs_operator_review'
            ? 'warning'
            : 'neutral'
  const description =
    status === 'ready_to_request_next_tier_cap'
      ? `Evidence is clean for requesting a ${recommendedCap.toFixed(2)} limited-live cap.`
      : status === 'blocked'
        ? `${blockers.length || 1} next-tier cap blocker${blockers.length === 1 ? '' : 's'} must clear before a larger-cap request.`
        : status === 'needs_operator_review'
          ? `${warnings.length || 1} next-tier cap warning${warnings.length === 1 ? '' : 's'} need operator review.`
          : autoReviewEnabled && nextEligibleRunAt
            ? `Waiting for scheduled next-tier cap review at ${new Date(nextEligibleRunAt).toLocaleString()}.`
            : 'Run the next-tier cap report after the expanded-cap canary is ready.'
  return {
    status,
    tone,
    title: report.enabled === false ? 'Next-tier cap report disabled' : `Next-tier cap: ${humanizeStatus(status, 'Not run')}`,
    description,
    enabled: report.enabled !== false,
    autoReviewEnabled,
    runSource,
    scheduledStatus: report.scheduled_status || '',
    skippedReason: String(report.skipped_reason || '').trim(),
    nextEligibleRunAt,
    lastScheduledRunAt,
    relatedNoteId: report.related_note_id || report.note_id || null,
    lastRunAt: report.evaluated_at || report.last_run_at || null,
    evidenceRows,
    blockers,
    warnings,
    operatorActions: actions,
    manualActionRequired: Boolean(report.manual_action_required || blockers.length || warnings.length),
    currentCap,
    targetCap,
    recommendedCap,
    brokerLiveGateStatus: report.broker_live_gate_status || report.broker_gate_status || 'unknown',
    safetyLockStatus: report.safety_lock_status || 'unknown',
    stateControlStatus: report.state_control_status || 'unknown',
    capExpansionCanaryStatus: report.cap_expansion_canary_status || 'missing',
    capExpansionReportStatus: report.cap_expansion_report_status || 'missing',
    capExpansionGateStatus: report.cap_expansion_gate_status || 'missing',
    latestReconciliationStatus: report.latest_reconciliation_status || 'missing',
    consumedOrderCount: toNumber(report.consumed_order_count, 0),
    pnlSummary,
    slippageSummary,
    orderEvents,
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Current cap', value: `$${currentCap.toFixed(2)}` },
      { label: 'Target cap', value: `$${targetCap.toFixed(2)}` },
      { label: 'Recommended', value: `$${recommendedCap.toFixed(2)}`, tone: recommendedCap > currentCap ? 'positive' : 'warning' },
      { label: 'Clean days', value: `${clean}/${required}`, tone: clean >= required ? 'positive' : 'warning' },
      { label: 'Expansion canary', value: humanizeStatus(report.cap_expansion_canary_status, 'Missing'), tone: report.cap_expansion_canary_status === 'ready_for_operator_review' ? 'positive' : 'warning' },
      { label: 'Expansion report', value: humanizeStatus(report.cap_expansion_report_status, 'Missing'), tone: report.cap_expansion_report_status === 'ready_to_request_cap_expansion' ? 'positive' : 'warning' },
      { label: 'Expansion gate', value: humanizeStatus(report.cap_expansion_gate_status, 'Missing') },
      { label: 'Live gate', value: humanizeStatus(report.broker_live_gate_status || report.broker_gate_status, 'Unknown'), tone: (report.broker_live_gate_status || report.broker_gate_status) === 'open' ? 'positive' : 'negative' },
      { label: 'Safety locks', value: humanizeStatus(report.safety_lock_status, 'Unknown'), tone: report.safety_lock_status === 'clear' ? 'positive' : 'negative' },
      { label: 'State control', value: humanizeStatus(report.state_control_status, 'Unknown'), tone: report.state_control_status === 'halt' ? 'negative' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(report.latest_reconciliation_status, 'Missing'), tone: report.latest_reconciliation_status === 'clean' ? 'positive' : report.latest_reconciliation_status === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Consumed', value: String(toNumber(report.consumed_order_count, 0)) },
      { label: 'Worst slip', value: slippageSummary.worst_abs_bps == null ? '--' : `${Number(slippageSummary.worst_abs_bps).toFixed(1)} bps`, tone: Number(slippageSummary.worst_abs_bps || 0) > 100 ? 'negative' : Number(slippageSummary.worst_abs_bps || 0) > 50 ? 'warning' : 'neutral' },
      { label: 'Realized PnL', value: pnlSummary.realized_pnl == null ? '--' : `$${Number(pnlSummary.realized_pnl).toFixed(2)}`, tone: Number(pnlSummary.realized_pnl || 0) < 0 ? 'negative' : 'neutral' },
      { label: 'Order events', value: String(toNumber(orderEvents.count, 0)), tone: toNumber(orderEvents.failed_count, 0) || toNumber(orderEvents.cap_breach_count, 0) ? 'negative' : 'neutral' },
      { label: 'Evidence', value: `${evidenceRows.filter((item) => item.ready).length}/${evidenceRows.length}` },
      { label: 'Auto review', value: autoReviewEnabled ? 'On' : 'Off', tone: autoReviewEnabled ? 'positive' : 'neutral' },
      { label: 'Run source', value: runSource ? humanizeStatus(runSource) : '--' },
      { label: 'Next review', value: nextEligibleRunAt ? new Date(nextEligibleRunAt).toLocaleString() : '--' },
      { label: 'Note', value: report.related_note_id || report.note_id ? 'Linked' : '--' },
    ],
  }
}

export function buildLimitedLiveNextTierCapGateModel(snapshot = {}) {
  const gate = snapshot?.limited_live_next_tier_cap_gate || {}
  const status = String(gate.status || 'not_prepared').trim().toLowerCase()
  const blockers = Array.isArray(gate.blockers) ? gate.blockers : []
  const warnings = Array.isArray(gate.warnings) ? gate.warnings : []
  const caps = gate.caps || {}
  const approvalState = String(gate.approval_state || 'missing').trim().toLowerCase()
  const nextTierActive = Boolean(gate.next_tier_cap_active ?? gate.expansion_active)
  const currentCap = toNumber(caps.current_max_notional ?? gate.current_max_notional, 250)
  const nextCap = toNumber(caps.next_max_notional ?? caps.expanded_max_notional ?? gate.next_max_notional ?? gate.expanded_max_notional, 500)
  const consumed = toNumber(gate.consumed_order_count, 0)
  const maxOrders = toNumber(caps.max_session_orders ?? gate.max_session_orders, 1)
  const tone =
    gate.enabled === false
      ? 'neutral'
      : status === 'active'
        ? 'positive'
        : status === 'approved'
          ? 'warning'
          : status === 'blocked'
            ? 'negative'
            : status === 'rolled_back'
              ? 'neutral'
              : 'neutral'
  const description =
    status === 'active'
      ? `Runtime next-tier cap is active until ${gate.next_tier_cap_expires_at || gate.expansion_expires_at ? new Date(gate.next_tier_cap_expires_at || gate.expansion_expires_at).toLocaleString() : 'expiry'} with ${Math.max(0, maxOrders - consumed)} order remaining.`
      : status === 'approved'
        ? `Approval is fresh until ${gate.approval_expires_at ? new Date(gate.approval_expires_at).toLocaleString() : 'expiry'}. Activation remains a separate manual action.`
        : status === 'rolled_back'
          ? 'Runtime next-tier cap allowance was rolled back.'
          : status === 'blocked'
            ? `${blockers.length || 1} next-tier cap gate blocker${blockers.length === 1 ? '' : 's'} must clear before activation.`
            : 'Prepare the next-tier cap gate after the next-tier cap report is ready and base rollout plus cap-expansion allowances are active.'
  return {
    status,
    tone,
    title: gate.enabled === false ? 'Next-tier cap gate disabled' : `Next-tier cap gate: ${humanizeStatus(status, 'Not prepared')}`,
    description,
    enabled: gate.enabled !== false,
    approvalState,
    approvalExpiresAt: gate.approval_expires_at || null,
    nextTierActive,
    nextTierExpiresAt: gate.next_tier_cap_expires_at || gate.expansion_expires_at || null,
    rollbackState: gate.rollback_state || 'inactive',
    rollbackReason: gate.rollback_reason || '',
    relatedNoteId: gate.related_note_id || gate.note_id || null,
    blockers,
    warnings,
    currentCap,
    nextCap,
    consumedOrderCount: consumed,
    maxSessionOrders: maxOrders,
    orders: Array.isArray(gate?.candidate_order_evidence?.orders) ? gate.candidate_order_evidence.orders : [],
    metrics: [
      { label: 'Approval', value: humanizeStatus(approvalState, 'Missing'), tone: approvalState === 'approved' ? 'warning' : approvalState === 'consumed' ? 'positive' : 'neutral' },
      { label: 'Active', value: nextTierActive ? 'Yes' : 'No', tone: nextTierActive ? 'positive' : 'neutral' },
      { label: 'Current cap', value: `$${currentCap.toFixed(2)}` },
      { label: 'Next cap', value: `$${nextCap.toFixed(2)}`, tone: nextCap > currentCap ? 'positive' : 'neutral' },
      { label: 'Orders', value: `${consumed}/${maxOrders}`, tone: consumed >= maxOrders ? 'warning' : 'neutral' },
      { label: 'Limit only', value: caps.require_limit === false ? 'No' : 'Yes', tone: caps.require_limit === false ? 'negative' : 'positive' },
      { label: 'Base rollout', value: gate.base_rollout_id ? 'Linked' : '--' },
      { label: 'Cap expansion', value: gate.base_cap_expansion_id ? 'Linked' : '--' },
      { label: 'Rollback', value: humanizeStatus(gate.rollback_state, 'Inactive') },
      { label: 'Note', value: gate.related_note_id || gate.note_id ? 'Linked' : '--' },
    ],
  }
}

export function buildLivePilotWindowModel(snapshot = {}) {
  const pilot = snapshot?.live_pilot_window || {}
  const status = String(pilot.status || 'not_run').trim().toLowerCase()
  const blockers = Array.isArray(pilot.blockers) ? pilot.blockers : []
  const warnings = Array.isArray(pilot.warnings) ? pilot.warnings : []
  const candidate = pilot.selected_candidate || {}
  const approvalStatus = String(pilot.approval_status || 'missing').trim().toLowerCase()
  const reconciliationStatus = String(pilot.reconciliation_status || 'not_run').trim().toLowerCase()
  const terminalState = String(pilot.terminal_state || '').trim().toLowerCase()
  const positionEvidence = pilot.position_evidence || {}
  const cancelEvidence = pilot.cancel_evidence || {}
  const exitEvidence = pilot.exit_evidence || {}
  const symbol = pilot.symbol || candidate.ticker || candidate.symbol || '--'
  const tone =
    status === 'completed'
      ? 'positive'
      : status === 'approved' || status === 'entered'
        ? 'warning'
        : status === 'blocked'
          ? 'negative'
          : status === 'warning'
            ? 'warning'
            : 'neutral'
  const description =
    status === 'approved'
      ? `Approval is fresh until ${pilot.approval_expires_at ? new Date(pilot.approval_expires_at).toLocaleString() : 'expiry'}. Entry remains a separate manual action.`
      : status === 'entered'
        ? `One capped live pilot order is ${humanizeStatus(terminalState || 'working')}; use the manual exit/cancel action to make it terminal.`
        : status === 'completed'
          ? 'The supervised live pilot order reached a terminal cancel or close state and reconciliation was recorded.'
          : status === 'blocked'
            ? `${blockers.length || 1} supervised live pilot blocker${blockers.length === 1 ? '' : 's'} must clear before entry.`
            : 'Prepare a supervised one-trade live pilot window after live expansion canary readiness is clean.'
  return {
    status,
    tone,
    title: `Supervised live pilot: ${humanizeStatus(status, 'Not run')}`,
    description,
    approvalStatus,
    approvalExpiresAt: pilot.approval_expires_at || null,
    windowExpiresAt: pilot.window_expires_at || null,
    selectedCandidate: candidate,
    symbol,
    side: pilot.side || 'buy',
    notionalCap: pilot.notional_cap ?? pilot.settings?.live_pilot_window_max_notional ?? 50,
    sessionOrderCap: pilot.session_order_cap ?? pilot.settings?.live_pilot_window_max_session_orders ?? 1,
    referencePrice: pilot.reference_price ?? null,
    limitPrice: pilot.limit_price ?? null,
    quantity: pilot.quantity ?? null,
    estimatedNotional: pilot.estimated_notional ?? null,
    currentStep: pilot.current_step || 'idle',
    checkedAt: pilot.checked_at || pilot.last_run_at || null,
    relatedNoteId: pilot.related_note_id || pilot.note_id || null,
    brokerOrderId: pilot.broker_order_id || null,
    brokerStatus: pilot.broker_status || null,
    localOrderId: pilot.local_order_id || null,
    localTradeId: pilot.local_trade_id || null,
    terminalState: pilot.terminal_state || null,
    positionEvidence,
    cancelEvidence,
    exitEvidence,
    reconciliationStatus,
    blockers,
    warnings,
    manualActionRequired: Boolean(pilot.manual_action_required || blockers.length),
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Approval', value: humanizeStatus(approvalStatus, 'Missing'), tone: approvalStatus === 'approved' ? 'positive' : approvalStatus === 'blocked' ? 'negative' : 'neutral' },
      { label: 'Candidate', value: symbol },
      { label: 'Rank', value: candidate.portfolio_rank == null ? '--' : String(candidate.portfolio_rank) },
      { label: 'Cap', value: `$${Number(pilot.notional_cap ?? pilot.settings?.live_pilot_window_max_notional ?? 50).toFixed(2)}` },
      { label: 'Limit', value: pilot.limit_price == null ? '--' : `$${Number(pilot.limit_price).toFixed(2)}` },
      { label: 'Qty', value: pilot.quantity == null ? '--' : String(pilot.quantity) },
      { label: 'Terminal', value: humanizeStatus(pilot.terminal_state, '--'), tone: terminalState === 'canceled' || terminalState === 'closed' ? 'positive' : terminalState ? 'warning' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(reconciliationStatus, 'Not run'), tone: reconciliationStatus === 'clean' ? 'positive' : reconciliationStatus === 'blocked' ? 'negative' : reconciliationStatus === 'working' || reconciliationStatus === 'open' ? 'warning' : 'neutral' },
      { label: 'Broker order', value: pilot.broker_order_id ? String(pilot.broker_order_id).slice(0, 12) : '--' },
      { label: 'Local order', value: pilot.local_order_id ? String(pilot.local_order_id).slice(0, 12) : '--' },
      { label: 'Position', value: positionEvidence?.state ? humanizeStatus(positionEvidence.state) : '--', tone: positionEvidence?.state === 'open' ? 'warning' : 'neutral' },
      { label: 'Cancel', value: cancelEvidence?.canceled ? 'Recorded' : '--', tone: cancelEvidence?.canceled ? 'positive' : 'neutral' },
      { label: 'Exit', value: exitEvidence?.closed ? 'Recorded' : '--', tone: exitEvidence?.closed ? 'positive' : 'neutral' },
      { label: 'Approval expires', value: pilot.approval_expires_at ? new Date(pilot.approval_expires_at).toLocaleString() : '--' },
      { label: 'Window expires', value: pilot.window_expires_at ? new Date(pilot.window_expires_at).toLocaleString() : '--' },
      { label: 'Note', value: pilot.related_note_id || pilot.note_id ? 'Linked' : '--' },
      { label: 'Manual action', value: pilot.manual_action_required || blockers.length ? 'Required' : 'No', tone: blockers.length || status === 'entered' ? 'warning' : 'neutral' },
    ],
  }
}

export function buildPaperBrokerReconciliationModel(snapshot = {}) {
  const reconciliation = snapshot?.paper_broker_reconciliation || {}
  const status = String(reconciliation.status || 'not_run').trim().toLowerCase()
  const blockers = Array.isArray(reconciliation.blockers) ? reconciliation.blockers : []
  const warnings = Array.isArray(reconciliation.warnings) ? reconciliation.warnings : []
  const orphanBrokerCount = toNumber(reconciliation.orphan_broker_order_count, 0)
  const orphanLocalCount = toNumber(reconciliation.orphan_local_order_count, 0)
  const positionMismatchCount = toNumber(reconciliation.position_mismatch_count, 0)
  const fillMismatchCount = toNumber(reconciliation.fill_mismatch_count, 0)
  const mismatchCount = orphanBrokerCount + orphanLocalCount + positionMismatchCount + fillMismatchCount
  const brokerAvailable = Boolean(reconciliation.broker_available)
  const equitySnapshot = reconciliation.equity_snapshot || {}
  const tone =
    status === 'clean'
      ? 'positive'
      : status === 'blocked'
        ? 'negative'
        : status === 'warning'
          ? 'warning'
          : 'neutral'
  const description =
    status === 'clean'
      ? 'Broker-paper orders, fills, positions, and local ledger evidence agree.'
      : status === 'blocked'
        ? `${blockers.length} reconciliation blocker${blockers.length === 1 ? '' : 's'} must clear before paper promotion readiness.`
        : status === 'warning'
          ? `${warnings.length || 1} broker-paper warning${warnings.length === 1 ? '' : 's'} recorded; no live gates were changed.`
          : 'Run broker-paper reconciliation after paper order activity to compare broker state with the local ledger.'
  return {
    status,
    tone,
    title: `Paper broker: ${humanizeStatus(status, 'Not run')}`,
    description,
    checkedAt: reconciliation.checked_at || reconciliation.last_run_at || null,
    lastRunAt: reconciliation.last_run_at || reconciliation.checked_at || null,
    lastScheduledRunAt: reconciliation.last_scheduled_run_at || null,
    runSource: reconciliation.run_source || null,
    relatedNoteId: reconciliation.related_note_id || reconciliation.note_id || null,
    blockers,
    warnings,
    orphanBrokerOrders: Array.isArray(reconciliation.orphan_broker_orders) ? reconciliation.orphan_broker_orders : [],
    orphanLocalOrders: Array.isArray(reconciliation.orphan_local_orders) ? reconciliation.orphan_local_orders : [],
    positionMismatches: Array.isArray(reconciliation.position_mismatches) ? reconciliation.position_mismatches : [],
    fillMismatches: Array.isArray(reconciliation.fill_mismatches) ? reconciliation.fill_mismatches : [],
    manualActionRequired: blockers.length > 0,
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Matched', value: String(toNumber(reconciliation.matched_count, 0)), tone: status === 'clean' ? 'positive' : 'neutral' },
      { label: 'Mismatches', value: String(mismatchCount), tone: mismatchCount ? 'negative' : 'positive' },
      { label: 'Broker orphan', value: String(orphanBrokerCount), tone: orphanBrokerCount ? 'negative' : 'positive' },
      { label: 'Local orphan', value: String(orphanLocalCount), tone: orphanLocalCount ? 'negative' : 'positive' },
      { label: 'Position mismatch', value: String(positionMismatchCount), tone: positionMismatchCount ? 'negative' : 'positive' },
      { label: 'Fill mismatch', value: String(fillMismatchCount), tone: fillMismatchCount ? 'negative' : 'positive' },
      { label: 'Ledger', value: humanizeStatus(reconciliation.ledger_consistency, 'Unknown'), tone: reconciliation.ledger_consistency === 'consistent' ? 'positive' : mismatchCount || blockers.length ? 'negative' : 'neutral' },
      { label: 'Broker read', value: brokerAvailable ? 'Available' : 'Unavailable', tone: brokerAvailable ? 'positive' : 'warning' },
      { label: 'Equity snapshot', value: humanizeStatus(equitySnapshot.status, 'Missing'), tone: equitySnapshot.status === 'matched' ? 'positive' : equitySnapshot.status === 'drift' ? 'warning' : 'neutral' },
      { label: 'Run source', value: reconciliation.run_source ? humanizeStatus(reconciliation.run_source) : '--' },
      { label: 'Last check', value: reconciliation.checked_at ? new Date(reconciliation.checked_at).toLocaleString() : '--' },
      { label: 'Note', value: reconciliation.related_note_id || reconciliation.note_id ? 'Linked' : '--' },
    ],
  }
}

export function buildPaperOrderLifecycleSoakModel(snapshot = {}) {
  const soak = snapshot?.paper_order_lifecycle_soak || {}
  const status = String(soak.status || 'not_run').trim().toLowerCase()
  const blockers = Array.isArray(soak.blockers) ? soak.blockers : []
  const warnings = Array.isArray(soak.warnings) ? soak.warnings : []
  const fillEvidence = soak.fill_evidence || {}
  const cancelEvidence = soak.cancel_evidence || {}
  const closeEvidence = soak.close_evidence || {}
  const reconciliationStatus = String(soak.reconciliation_status || 'not_run').trim().toLowerCase()
  const tone =
    status === 'completed'
      ? 'positive'
      : status === 'blocked'
        ? 'negative'
        : status === 'warning'
          ? 'warning'
          : 'neutral'
  const description =
    status === 'completed'
      ? 'One tiny broker-paper lifecycle was submitted, terminal evidence was recorded, and reconciliation agreed.'
      : status === 'blocked'
        ? `${blockers.length || 1} lifecycle blocker${blockers.length === 1 ? '' : 's'} require manual review before paper promotion readiness.`
        : status === 'warning'
          ? `${warnings.length || 1} lifecycle warning${warnings.length === 1 ? '' : 's'} recorded; no live gates were changed.`
          : 'Run a controlled paper-only order lifecycle soak to prove submit, sync, cancel or close, reconciliation, and Notes evidence.'
  return {
    status,
    tone,
    title: `Paper order lifecycle: ${humanizeStatus(status, 'Not run')}`,
    description,
    currentStep: soak.current_step || 'idle',
    checkedAt: soak.checked_at || soak.last_run_at || null,
    lastRunAt: soak.last_run_at || soak.checked_at || null,
    relatedNoteId: soak.related_note_id || soak.note_id || null,
    brokerOrderId: soak.broker_order_id || null,
    brokerStatus: soak.broker_status || null,
    localOrderId: soak.local_order_id || null,
    localTradeId: soak.local_trade_id || null,
    terminalState: soak.terminal_state || null,
    reconciliationStatus,
    fillEvidence,
    cancelEvidence,
    closeEvidence,
    blockers,
    warnings,
    manualActionRequired: Boolean(soak.manual_action_required || blockers.length),
    metrics: [
      { label: 'Status', value: humanizeStatus(status, 'Not run'), tone },
      { label: 'Step', value: humanizeStatus(soak.current_step, 'Idle') },
      { label: 'Terminal', value: humanizeStatus(soak.terminal_state, '--'), tone: soak.terminal_state ? 'positive' : 'neutral' },
      { label: 'Reconcile', value: humanizeStatus(reconciliationStatus, 'Not run'), tone: reconciliationStatus === 'clean' ? 'positive' : reconciliationStatus === 'blocked' ? 'negative' : reconciliationStatus === 'warning' ? 'warning' : 'neutral' },
      { label: 'Broker order', value: soak.broker_order_id ? String(soak.broker_order_id).slice(0, 12) : '--' },
      { label: 'Local order', value: soak.local_order_id ? String(soak.local_order_id).slice(0, 12) : '--' },
      { label: 'Broker status', value: humanizeStatus(soak.broker_status, '--') },
      { label: 'Fill', value: Object.keys(fillEvidence || {}).length ? 'Recorded' : '--', tone: Object.keys(fillEvidence || {}).length ? 'positive' : 'neutral' },
      { label: 'Cancel', value: cancelEvidence?.canceled ? 'Recorded' : '--', tone: cancelEvidence?.canceled ? 'positive' : 'neutral' },
      { label: 'Close', value: closeEvidence?.closed ? 'Recorded' : '--', tone: closeEvidence?.closed ? 'positive' : 'neutral' },
      { label: 'Note', value: soak.related_note_id || soak.note_id ? 'Linked' : '--' },
      { label: 'Manual action', value: soak.manual_action_required || blockers.length ? 'Required' : 'No', tone: blockers.length ? 'negative' : 'neutral' },
    ],
  }
}

export function buildOptionAutomationDiagnostics(snapshot = {}, optionsSnapshot = null) {
  const runtime = snapshot?.runtime || {}
  const validation = optionsSnapshot?.validation_artifact || optionsSnapshot?.lifecycle?.validation_artifact || {}
  const optionExecution = snapshot?.option_execution || runtime?.last_option_execution || {}
  const lastOptionEntry = snapshot?.last_option_entry || runtime?.last_option_entry || null
  const lastOptionExit = snapshot?.last_option_exit || runtime?.last_option_exit || null
  const lastOptionsCycleAt = snapshot?.last_options_cycle_at || runtime?.last_options_cycle_at || null
  const openOptionCount = toNumber(snapshot?.open_option_position_count ?? runtime?.open_option_position_count, 0)
  const sellReadyCount = toNumber(snapshot?.sell_ready_option_count ?? runtime?.sell_ready_option_count, 0)
  const cleanCycleCount = toNumber(optionsSnapshot?.clean_cycle_count ?? validation?.clean_cycle_count, 0)
  const requiredCleanCycles = toNumber(optionsSnapshot?.required_clean_cycles ?? validation?.required_clean_cycles, 5)
  const workingOrderCount = toNumber(optionsSnapshot?.working_order_count ?? validation?.working_order_count, 0)
  const scanStatus = String(snapshot?.last_options_scan_status || runtime?.last_options_scan_status || '').trim()
  const scheduledBlocker = String(snapshot?.last_options_blocker || runtime?.last_options_blocker || '').trim()
  const readinessState = String(optionsSnapshot?.readiness_state || validation?.readiness_state || '').trim()
  const readinessLabel = String(optionsSnapshot?.readiness_label || validation?.readiness_label || '').trim()
  const status = String(snapshot?.option_scan_status || optionExecution.option_scan_status || '').trim()
  const blockReason = String(snapshot?.option_block_reason || optionExecution.option_block_reason || '').trim()
  const selectedContract = String(optionExecution.selected_contract || '').trim()
  const quoteAge = toNumber(snapshot?.option_quote_age_seconds ?? optionExecution.option_quote_age_seconds, NaN)
  const spreadPct = toNumber(snapshot?.option_spread_pct ?? optionExecution.option_spread_pct, NaN)
  const score = toNumber(snapshot?.option_contract_score ?? optionExecution.option_contract_score, NaN)
  if (
    !status &&
    !selectedContract &&
    !blockReason &&
    !scanStatus &&
    !scheduledBlocker &&
    !readinessState &&
    !lastOptionEntry &&
    !lastOptionExit &&
    !lastOptionsCycleAt &&
    !openOptionCount &&
    !sellReadyCount
  ) return null

  const tone =
    scheduledBlocker || blockReason
      ? 'warning'
      : readinessState === 'ready' || status === 'fresh' || status === 'replaced' || scanStatus === 'completed'
      ? 'positive'
      : readinessState === 'blocked' || status === 'blocked'
        ? 'warning'
        : 'neutral'
  const title =
    scheduledBlocker
      ? 'Scheduled options blocked'
      : readinessLabel
        ? `Scheduled options ${readinessLabel}`
      : lastOptionEntry || lastOptionExit || lastOptionsCycleAt
        ? 'Scheduled options runtime'
        : status === 'replaced'
          ? 'Option contract refreshed'
          : status === 'blocked'
            ? 'Option contract blocked'
            : status === 'fresh'
              ? 'Option quote fresh'
              : 'Option scan'
  return {
    tone,
    title,
    description:
      optionExecution.detail ||
      (scheduledBlocker
        ? scheduledBlocker
          : blockReason
            ? `Blocked by ${humanizeStatus(blockReason)}.`
            : readinessLabel
              ? `Current scheduled options readiness: ${readinessLabel}.`
            : scanStatus
              ? `Latest scheduled options scan status: ${humanizeStatus(scanStatus)}.`
              : 'Latest option automation quote diagnostics are available.'),
    metrics: [
      { label: 'Readiness', value: readinessLabel || humanizeStatus(readinessState, '--') },
      { label: 'Clean cycles', value: `${cleanCycleCount}/${requiredCleanCycles || 5}` },
      { label: 'Contract', value: selectedContract || lastOptionEntry?.contract_symbol || '--' },
      { label: 'Scan', value: humanizeStatus(scanStatus || status, '--') },
      { label: 'Open options', value: String(openOptionCount) },
      { label: 'Working orders', value: String(workingOrderCount) },
      { label: 'Sell ready', value: String(sellReadyCount) },
      { label: 'Clean entries', value: String(toNumber(validation?.clean_entry_count, 0)) },
      { label: 'Clean exits', value: String(toNumber(validation?.clean_exit_count, 0)) },
      { label: 'Blocked exits', value: String(toNumber(validation?.blocked_exit_count, 0)) },
      { label: 'Last broker sync', value: validation?.last_broker_sync_at ? new Date(validation.last_broker_sync_at).toLocaleString() : '--' },
      { label: 'Score', value: Number.isFinite(score) ? score.toFixed(0) : '--' },
      { label: 'Quote age', value: Number.isFinite(quoteAge) ? `${quoteAge.toFixed(1)}s` : '--' },
      { label: 'Spread', value: Number.isFinite(spreadPct) ? `${(spreadPct * 100).toFixed(1)}%` : '--' },
      { label: 'Last cycle', value: lastOptionsCycleAt ? new Date(lastOptionsCycleAt).toLocaleString() : '--' },
      { label: 'Last entry', value: lastOptionEntry?.at ? new Date(lastOptionEntry.at).toLocaleString() : '--' },
      { label: 'Last exit', value: lastOptionExit?.at ? new Date(lastOptionExit.at).toLocaleString() : '--' },
    ],
    lastRefreshAt: snapshot?.last_option_refresh_at || optionExecution.last_option_refresh_at || null,
    blockReason: scheduledBlocker || blockReason || (validation?.blockers || [])[0] || '',
    nextStep: validation?.next_step || optionsSnapshot?.next_step || '',
  }
}

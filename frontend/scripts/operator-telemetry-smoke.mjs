import assert from 'node:assert/strict'

import {
  buildAutomationTelemetrySnapshot,
  buildCollectionPhaseModel,
  buildControlPlaneModel,
  buildLivePilotCanaryModel,
  buildLivePilotExpansionCanaryModel,
  buildLivePilotExpansionModel,
  buildLivePilotReadinessModel,
  buildLivePilotSoakModel,
  buildLivePilotWindowModel,
  buildPaperBrokerReconciliationModel,
  buildPaperCanaryModel,
  buildPaperOrderLifecycleCanaryModel,
  buildPaperOrderLifecycleSoakModel,
  buildRankedEntryGateModel,
  buildTradeAutomationForm,
  buildTradeAutomationPayload,
  buildValidationSampleModel,
} from '../src/utils/tradeAutomationModel.js'
import {
  buildSessionAwareFreshness,
  buildSessionAwareFreshnessAlert,
} from '../src/utils/marketFreshnessModel.js'
import { buildSignalTelemetry } from '../src/utils/signalTelemetry.js'
import { resolveAccountProfileTradingContext } from '../src/utils/accountProfileModel.js'
import {
  getPublicSiteBranding,
  getPublicSiteContact,
  getPublicSitePage,
} from '../src/utils/publicSiteModel.js'

const form = buildTradeAutomationForm({
  settings: {
    enabled: true,
    execution_intent: 'broker_live',
    tickers: ['SPY', 'QQQ'],
    max_gross_leverage: 1.5,
    max_single_position_pct: 12,
    max_correlated_bucket_pct: 35,
    min_edge_to_cost_ratio: 2.5,
    allow_pyramiding: true,
    require_liquidity_fields: true,
    cycle_entry_rank_limit: 2,
    live_pilot_expansion_canary_enabled: true,
    live_pilot_expansion_canary_auto_review_enabled: true,
    live_pilot_expansion_canary_window_sessions: 5,
    live_pilot_expansion_canary_required_clean_sessions: 3,
    live_pilot_window_enabled: true,
    live_pilot_window_max_notional: 50,
    live_pilot_window_max_session_orders: 1,
    live_pilot_window_approval_ttl_minutes: 10,
    live_pilot_window_duration_minutes: 60,
  },
})

assert.equal(form.executionIntent, 'broker_live')
assert.equal(form.maxGrossLeverage, '1.5')
assert.equal(form.maxSinglePositionPct, '12')
assert.equal(form.maxCorrelatedBucketPct, '35')
assert.equal(form.minEdgeToCostRatio, '2.5')
assert.equal(form.cycleEntryRankLimit, '2')
assert.equal(form.allowPyramiding, true)
assert.equal(form.requireLiquidityFields, true)
assert.equal(form.livePilotExpansionCanaryEnabled, true)
assert.equal(form.livePilotExpansionCanaryAutoReviewEnabled, true)
assert.equal(form.livePilotExpansionCanaryWindowSessions, '5')
assert.equal(form.livePilotExpansionCanaryRequiredCleanSessions, '3')
assert.equal(form.livePilotWindowEnabled, true)
assert.equal(form.livePilotWindowMaxNotional, '50')
assert.equal(form.livePilotWindowMaxSessionOrders, '1')
assert.equal(form.livePilotWindowApprovalTtlMinutes, '10')
assert.equal(form.livePilotWindowDurationMinutes, '60')

const payload = buildTradeAutomationPayload(form)
assert.equal(payload.max_gross_leverage, 1.5)
assert.equal(payload.max_single_position_pct, 12)
assert.equal(payload.max_correlated_bucket_pct, 35)
assert.equal(payload.min_edge_to_cost_ratio, 2.5)
assert.equal(payload.cycle_entry_rank_limit, 2)
assert.equal(payload.allow_pyramiding, true)
assert.equal(payload.require_liquidity_fields, true)
assert.equal(payload.live_pilot_expansion_canary_enabled, true)
assert.equal(payload.live_pilot_expansion_canary_auto_review_enabled, true)
assert.equal(payload.live_pilot_expansion_canary_window_sessions, 5)
assert.equal(payload.live_pilot_expansion_canary_required_clean_sessions, 3)
assert.equal(payload.live_pilot_window_enabled, true)
assert.equal(payload.live_pilot_window_max_notional, 50)
assert.equal(payload.live_pilot_window_max_session_orders, 1)
assert.equal(payload.live_pilot_window_approval_ttl_minutes, 10)
assert.equal(payload.live_pilot_window_duration_minutes, 60)

const gate = buildRankedEntryGateModel({
  ranked_entry_rollout: {
    accepted: false,
    status: 'rejected',
    basis: 'Candidate drawdown 12.00% exceeds the allowed 11.50% ceiling.',
    baseline_key: 'A',
    candidate_key: 'M',
    scenario_count: 16,
  },
})

assert.equal(gate.tone, 'negative')
assert.equal(gate.metrics[0].value, 'A')
assert.equal(gate.metrics[1].value, 'M')
assert.equal(gate.metrics[2].value, 'Blocked')

const telemetrySnapshot = buildAutomationTelemetrySnapshot({
  runtime: {
    last_candidate: { ticker: 'NVDA', portfolio_rank: 1 },
    last_rejection: { reason: 'correlated_bucket_cap' },
    last_path_evaluations: [{ instrument_type: 'equity', ticker: 'NVDA' }],
  },
})

assert.equal(telemetrySnapshot.candidate.ticker, 'NVDA')
assert.equal(telemetrySnapshot.rejection.reason, 'correlated_bucket_cap')
assert.equal(telemetrySnapshot.pathEvaluations.length, 1)

const validationSample = buildValidationSampleModel({
  rollout_readiness: {
    metrics: {
      current_route_fill_count: 6,
      current_route_closed_trade_count: 2,
      current_route_sample_status: 'insufficient',
      mark_to_market_coverage_status: 'partial_window',
      ledger_snapshot_consistency: 'unavailable',
      metrics_source: 'event_ledger',
    },
  },
})

assert.equal(validationSample.tone, 'warning')
assert.equal(validationSample.title, 'Validation sample: Collecting sample')
assert.equal(validationSample.metrics[0].value, '6')
assert.equal(validationSample.metrics[1].value, '2')

const nestedValidationSample = buildValidationSampleModel({
  rollout_readiness: {
    metrics: {
      current_route_fill_count: 12,
      current_route_closed_trade_count: 6,
    },
    current_route_validation_integrity: {
      current_route_sample_status: 'sufficient',
      mark_to_market_coverage_status: 'complete',
      ledger_snapshot_consistency: 'consistent',
      metrics_source: 'mark_to_market',
    },
  },
})

assert.equal(nestedValidationSample.tone, 'positive')
assert.equal(nestedValidationSample.title, 'Validation sample: Ready to rerun validation')
assert.equal(nestedValidationSample.metrics[2].value, 'Complete')

const collectionPhase = buildCollectionPhaseModel({
  rollout_readiness: {
    collection_phase_active: true,
    collection_phase_label: 'Rerunning validation',
    collection_phase_detail: 'The latest qualifying paper cycle triggered an automatic validation export.',
    last_collection_blocker: '',
    current_route_reconciliation_status: 'clean',
    current_route_orphan_order_event_count: 0,
    last_submitted_current_route_order_at: '2026-04-23T14:30:00+00:00',
    last_current_route_fill_at: '2026-04-23T14:31:00+00:00',
    last_current_route_close_at: '2026-04-23T14:34:00+00:00',
    last_validation_rerun_at: '2026-04-23T14:35:00+00:00',
    last_validation_rerun_cycle_id: 'cycle-123',
    auto_validation_rerun_enabled: true,
    metrics: {
      current_route_fill_count: 10,
      current_route_closed_trade_count: 5,
      mark_to_market_coverage_status: 'complete',
      ledger_snapshot_consistency: 'consistent',
    },
  },
})

assert.equal(collectionPhase.active, true)
assert.equal(collectionPhase.tone, 'warning')
assert.equal(collectionPhase.title, 'Collection phase: Rerunning validation')
assert.equal(collectionPhase.metrics[0].value, '10')
assert.equal(collectionPhase.metrics[1].value, '5')
assert.equal(collectionPhase.metrics[4].value, '--')
assert.equal(collectionPhase.metrics[5].value, 'Clean')
assert.equal(collectionPhase.metrics[6].value, '0')
assert.equal(collectionPhase.metrics[7].value, 'Enabled')
assert.equal(collectionPhase.metrics[9].value, 'cycle-123')

const controlPlane = buildControlPlaneModel({
  control_plane: {
    enabled: true,
    state: 'de_risk',
    score: 47,
    component_scores: {
      data_integrity: 94,
      alpha_efficacy: 48,
      execution_quality: 42,
      market_state: 65,
    },
    triggered_signals: [
      {
        component: 'execution_quality',
        signal: 'average_slippage_drift',
        detail: 'Average absolute slippage is 55.0 bps.',
      },
    ],
    active_overrides: [
      {
        field: 'risk_percent',
        before: 0.5,
        effective: 0.25,
        reason: 'De-risk state applies a 0.50 new-risk multiplier.',
      },
      {
        field: 'order_type',
        before: 'market',
        effective: 'limit',
        reason: 'De-risk state requires limit routing for new entries.',
      },
    ],
    shadow_validation: {
      status: 'pass',
      evaluated_at: '2026-04-24T14:05:00+00:00',
      scenario_count: 6,
      passed_count: 6,
      failed_count: 0,
      worst_state: 'halt',
      expected_overlay_count: 7,
      safety_lock_expected: true,
      note_id: 'shadow-note-1',
      scenarios: [
        {
          id: 'slippage_spread_weakness',
          label: 'Slippage Spread Weakness',
          status: 'pass',
          state: 'de_risk',
          score: 47,
          active_overrides: [{ field: 'order_type', before: 'market', effective: 'limit' }],
          detail: 'Execution weakness forced limit routing and throttled entries.',
        },
      ],
    },
  },
})

assert.equal(controlPlane.state, 'de_risk')
assert.equal(controlPlane.metrics[1].value, '47')
assert.equal(controlPlane.activeOverrides.length, 2)
assert.equal(controlPlane.shadowValidation.tone, 'positive')
assert.equal(controlPlane.shadowValidation.metrics[1].value, '6')
assert.equal(controlPlane.shadowValidation.metrics[4].value, 'Halt')
assert.equal(controlPlane.shadowValidation.scenarios[0].state, 'de_risk')

const paperBrokerReconciliation = buildPaperBrokerReconciliationModel({
  paper_broker_reconciliation: {
    status: 'blocked',
    checked_at: '2026-04-24T20:15:00+00:00',
    run_source: 'scheduled',
    matched_count: 4,
    orphan_broker_order_count: 1,
    orphan_local_order_count: 0,
    position_mismatch_count: 1,
    fill_mismatch_count: 0,
    ledger_consistency: 'inconsistent',
    broker_available: true,
    equity_snapshot: { status: 'drift' },
    related_note_id: 'paper-broker-note-1',
    blockers: [{ key: 'orphan_broker_order', detail: 'One broker order is missing locally.' }],
    warnings: [],
  },
})

assert.equal(paperBrokerReconciliation.tone, 'negative')
assert.equal(paperBrokerReconciliation.metrics[1].value, '4')
assert.equal(paperBrokerReconciliation.metrics[2].value, '2')
assert.equal(paperBrokerReconciliation.metrics[8].value, 'Available')
assert.equal(paperBrokerReconciliation.metrics[9].value, 'Drift')
assert.equal(paperBrokerReconciliation.relatedNoteId, 'paper-broker-note-1')

const paperOrderLifecycleSoak = buildPaperOrderLifecycleSoakModel({
  paper_order_lifecycle_soak: {
    status: 'completed',
    current_step: 'reconciliation',
    checked_at: '2026-04-24T20:18:00+00:00',
    broker_order_id: 'broker-entry-1',
    broker_status: 'canceled',
    local_order_id: 'local-order-1',
    terminal_state: 'canceled',
    reconciliation_status: 'clean',
    cancel_evidence: { canceled: true, broker_order_id: 'broker-entry-1' },
    related_note_id: 'paper-order-lifecycle-note-1',
    blockers: [],
    warnings: [],
  },
})

assert.equal(paperOrderLifecycleSoak.tone, 'positive')
assert.equal(paperOrderLifecycleSoak.metrics[2].value, 'Canceled')
assert.equal(paperOrderLifecycleSoak.metrics[3].value, 'Clean')
assert.equal(paperOrderLifecycleSoak.metrics[8].value, 'Recorded')
assert.equal(paperOrderLifecycleSoak.relatedNoteId, 'paper-order-lifecycle-note-1')

const paperOrderLifecycleCanary = buildPaperOrderLifecycleCanaryModel({
  paper_order_lifecycle_canary: {
    status: 'ready',
    enabled: true,
    auto_submit_enabled: false,
    clean_session_count: 3,
    required_clean_sessions: 3,
    window_session_count: 3,
    latest_soak_status: 'completed',
    latest_terminal_state: 'canceled',
    latest_broker_order_id: 'broker-entry-1',
    latest_local_order_id: 'local-order-1',
    latest_reconciliation_status: 'clean',
    note_coverage: { covered: 3, required: 3, ratio: 1 },
    related_note_id: 'lifecycle-canary-note-1',
    evaluated_at: '2026-04-24T20:22:00+00:00',
    next_eligible_run_at: '2026-04-27T20:10:00+00:00',
    sessions: [
      {
        session_day: '2026-04-24',
        status: 'clean',
        clean: true,
        lifecycle_soak: { status: 'completed', terminal_state: 'canceled', broker_order_id: 'broker-entry-1' },
        paper_broker_reconciliation: { status: 'clean' },
        ledger: { unresolved_count: 0 },
        blockers: [],
      },
    ],
    blockers: [],
    warnings: [],
  },
})

assert.equal(paperOrderLifecycleCanary.tone, 'positive')
assert.equal(paperOrderLifecycleCanary.metrics[1].value, '3/3')
assert.equal(paperOrderLifecycleCanary.metrics[5].value, 'Clean')
assert.equal(paperOrderLifecycleCanary.metrics[6].value, 'Off')
assert.equal(paperOrderLifecycleCanary.relatedNoteId, 'lifecycle-canary-note-1')

const paperCanary = buildPaperCanaryModel({
  paper_canary: {
    status: 'ready',
    enabled: true,
    auto_review_enabled: true,
    clean_session_count: 3,
    required_clean_sessions: 3,
    window_session_count: 3,
    worst_state: 'watch',
    shadow_pass_rate: 1,
    ai_review_coverage: { covered: 3, required: 3, ratio: 1 },
    note_coverage: { covered: 3, required: 3, ratio: 1 },
    pnl_summary: { closed_trade_count: 4, realized_pnl: 124.5 },
    slippage_summary: { sample_count: 4, average_abs_bps: 8.25, worst_abs_bps: 18.5 },
    paper_order_lifecycle_canary: {
      status: 'ready',
      clean_session_count: 3,
      required_clean_sessions: 3,
      latest_soak_status: 'completed',
      latest_reconciliation_status: 'clean',
    },
    blockers: [],
    warnings: [{ key: 'closed_trade_sample_missing', detail: 'One quiet session.' }],
    manual_action_required: false,
    run_source: 'scheduled',
    evaluated_at: '2026-04-24T20:20:00+00:00',
    last_scheduled_run_at: '2026-04-24T20:20:00+00:00',
    next_eligible_run_at: '2026-04-27T20:10:00+00:00',
    evidence_window: {
      start_session_day: '2026-04-20',
      end_session_day: '2026-04-24',
      configured_session_count: 5,
      evidence_session_count: 3,
    },
    related_note_id: 'paper-canary-note-1',
    sessions: [
      {
        session_day: '2026-04-24',
        status: 'clean',
        clean: true,
        state_control: { state: 'watch' },
        shadow_validation: { status: 'pass' },
        paper_broker_reconciliation: { status: 'clean' },
        paper_order_lifecycle_soak: { status: 'completed', terminal_state: 'canceled' },
        pnl: { realized_pnl: 124.5 },
        slippage: { average_abs_bps: 8.25 },
        blockers: [],
      },
    ],
  },
})

assert.equal(paperCanary.tone, 'positive')
assert.equal(paperCanary.metrics[1].value, '3/3')
assert.equal(paperCanary.metrics[4].value, '100%')
assert.equal(paperCanary.metrics[9].value, '8.3 bps')
assert.equal(paperCanary.metrics[11].value, 'Ready')
assert.equal(paperCanary.metrics[12].value, 'On')
assert.equal(paperCanary.metrics[13].value, 'Scheduled')
assert.equal(paperCanary.sessions[0].state_control.state, 'watch')
assert.equal(paperCanary.nextEligibleRunAt, '2026-04-27T20:10:00+00:00')

const livePilotReadiness = buildLivePilotReadinessModel({
  live_pilot_readiness: {
    status: 'ready_to_request_approval',
    paper_evidence_status: 'ready',
    lifecycle_canary_status: 'ready',
    paper_broker_reconciliation_status: 'clean',
    state_control_status: 'healthy',
    shadow_validation_status: 'pass',
    broker_live_gate_status: 'open',
    safety_lock_status: 'clear',
    evaluated_at: '2026-04-24T20:30:00+00:00',
    related_note_id: 'live-readiness-note-1',
    paper_evidence: {
      paper_canary: { status: 'ready', clean_session_count: 3, required_clean_sessions: 3 },
      lifecycle_canary: { status: 'ready', clean_session_count: 3, required_clean_sessions: 3 },
      paper_broker_reconciliation: { status: 'clean', matched_count: 4 },
      state_control: { status: 'healthy', state: 'healthy', score: 91 },
      shadow_validation: { status: 'pass', scenario_count: 6, failed_count: 0 },
    },
    live_route_config: {
      execution_intent: 'broker_live',
      credentials_configured: true,
      server_live_trading_enabled: true,
      rollout_allows_live: true,
      enabled: false,
      armed: false,
      kill_switch: false,
    },
    required_operator_actions: [{ key: 'operator_approval_required', detail: 'Manual approval required.' }],
    blockers: [],
    warnings: [],
  },
})

assert.equal(livePilotReadiness.tone, 'positive')
assert.equal(livePilotReadiness.metrics[1].value, 'Ready')
assert.equal(livePilotReadiness.metrics[6].value, 'Open')
assert.equal(livePilotReadiness.metrics[7].value, 'Clear')
assert.equal(livePilotReadiness.metrics[10].value, '3/3')
assert.equal(livePilotReadiness.relatedNoteId, 'live-readiness-note-1')

const livePilotSoak = buildLivePilotSoakModel({
  live_pilot_soak: {
    status: 'approved',
    approval_status: 'approved',
    approval_expires_at: '2026-04-24T20:45:00+00:00',
    symbol: 'SPY',
    notional_cap: 10,
    reference_price: 500,
    limit_price: 475,
    quantity: 0.001,
    reconciliation_status: 'not_run',
    related_note_id: 'live-soak-note-1',
    blockers: [],
    warnings: [],
  },
})

assert.equal(livePilotSoak.tone, 'positive')
assert.equal(livePilotSoak.metrics[1].value, 'Approved')
assert.equal(livePilotSoak.metrics[2].value, 'SPY')
assert.equal(livePilotSoak.metrics[4].value, '$475.00')
assert.equal(livePilotSoak.relatedNoteId, 'live-soak-note-1')

const livePilotCanary = buildLivePilotCanaryModel({
  live_pilot_canary: {
    status: 'ready',
    enabled: true,
    auto_review_enabled: true,
    clean_session_count: 3,
    required_clean_sessions: 3,
    window_session_count: 3,
    latest_soak_status: 'completed',
    latest_terminal_state: 'canceled',
    latest_broker_order_id: 'live-broker-entry-1',
    latest_local_order_id: 'live-local-order-1',
    latest_reconciliation_status: 'clean',
    live_readiness_status: 'ready_to_request_approval',
    broker_live_gate_status: 'open',
    safety_lock_status: 'clear',
    note_coverage: { covered: 3, required: 3, ratio: 1 },
    related_note_id: 'live-canary-note-1',
    evaluated_at: '2026-04-24T20:45:00+00:00',
    next_eligible_run_at: '2026-04-27T20:10:00+00:00',
    run_source: 'scheduled',
    sessions: [
      {
        session_day: '2026-04-24',
        status: 'clean',
        clean: true,
        live_pilot_soak: {
          status: 'completed',
          terminal_state: 'canceled',
          broker_order_id: 'live-broker-entry-1',
          reconciliation_status: 'clean',
        },
        live_pilot_readiness: { status: 'ready_to_request_approval' },
        blockers: [],
      },
    ],
    blockers: [],
    warnings: [],
  },
})

assert.equal(livePilotCanary.tone, 'positive')
assert.equal(livePilotCanary.metrics[1].value, '3/3')
assert.equal(livePilotCanary.metrics[5].value, 'Clean')
assert.equal(livePilotCanary.metrics[6].value, 'Ready To Request Approval')
assert.equal(livePilotCanary.metrics[7].value, 'Open')
assert.equal(livePilotCanary.relatedNoteId, 'live-canary-note-1')

const livePilotExpansion = buildLivePilotExpansionModel({
  live_pilot_expansion: {
    status: 'approved',
    approval_status: 'approved',
    approval_expires_at: '2026-04-24T20:55:00+00:00',
    selected_candidate: {
      ticker: 'SPY',
      portfolio_rank: 1,
      alpha_score: 91,
      execution_score: 82,
      edge_to_cost_ratio: 3.2,
    },
    symbol: 'SPY',
    side: 'buy',
    notional_cap: 25,
    daily_order_cap: 1,
    limit_price: 475,
    quantity: 0.001,
    reconciliation_status: 'not_run',
    related_note_id: 'live-expansion-note-1',
    blockers: [],
    warnings: [],
  },
})

assert.equal(livePilotExpansion.tone, 'positive')
assert.equal(livePilotExpansion.metrics[1].value, 'Approved')
assert.equal(livePilotExpansion.metrics[2].value, 'SPY')
assert.equal(livePilotExpansion.metrics[4].value, '$25.00')
assert.equal(livePilotExpansion.metrics[15].value, 'Linked')
assert.equal(livePilotExpansion.relatedNoteId, 'live-expansion-note-1')

const livePilotExpansionCanary = buildLivePilotExpansionCanaryModel({
  live_pilot_expansion_canary: {
    status: 'ready',
    enabled: true,
    auto_review_enabled: true,
    clean_session_count: 3,
    required_clean_sessions: 3,
    window_session_count: 3,
    latest_expansion_status: 'completed',
    latest_terminal_state: 'canceled',
    latest_broker_order_id: 'live-expansion-broker-1',
    latest_local_order_id: 'live-expansion-local-1',
    latest_reconciliation_status: 'clean',
    latest_symbol: 'SPY',
    live_readiness_status: 'ready_to_request_approval',
    broker_live_gate_status: 'open',
    safety_lock_status: 'clear',
    candidate_evidence: { ticker: 'SPY', auto_entry_eligible: true, edge_to_cost_ratio: 3.2 },
    pnl_summary: { realized_pnl: 0 },
    slippage_summary: { sample_count: 1, average_abs_bps: 8.5, worst_abs_bps: 8.5 },
    note_coverage: { covered: 3, required: 3, ratio: 1 },
    related_note_id: 'live-expansion-canary-note-1',
    evaluated_at: '2026-04-24T21:05:00+00:00',
    next_eligible_run_at: '2026-04-27T20:10:00+00:00',
    run_source: 'scheduled',
    sessions: [
      {
        session_day: '2026-04-24',
        status: 'clean',
        clean: true,
        live_pilot_expansion: {
          status: 'completed',
          terminal_state: 'canceled',
          broker_order_id: 'live-expansion-broker-1',
          reconciliation_status: 'clean',
        },
        candidate: { ticker: 'SPY', auto_entry_eligible: true },
        slippage: { abs_bps: 8.5 },
        live_pilot_readiness: { status: 'ready_to_request_approval' },
        blockers: [],
      },
    ],
    blockers: [],
    warnings: [],
  },
})

assert.equal(livePilotExpansionCanary.tone, 'positive')
assert.equal(livePilotExpansionCanary.metrics[1].value, '3/3')
assert.equal(livePilotExpansionCanary.metrics[5].value, 'Clean')
assert.equal(livePilotExpansionCanary.metrics[6].value, 'SPY')
assert.equal(livePilotExpansionCanary.metrics[7].value, '8.5 bps')
assert.equal(livePilotExpansionCanary.metrics[9].value, 'Ready To Request Approval')
assert.equal(livePilotExpansionCanary.relatedNoteId, 'live-expansion-canary-note-1')

const livePilotWindow = buildLivePilotWindowModel({
  live_pilot_window: {
    status: 'entered',
    approval_status: 'consumed',
    approval_expires_at: '2026-04-24T21:15:00+00:00',
    window_expires_at: '2026-04-24T22:05:00+00:00',
    selected_candidate: {
      ticker: 'SPY',
      portfolio_rank: 1,
      alpha_score: 91,
      execution_score: 82,
      edge_to_cost_ratio: 3.2,
    },
    symbol: 'SPY',
    side: 'buy',
    notional_cap: 50,
    session_order_cap: 1,
    limit_price: 500,
    quantity: 0.1,
    terminal_state: 'open',
    broker_order_id: 'live-window-broker-1',
    local_order_id: 'live-window-local-1',
    position_evidence: { state: 'open', broker_order_id: 'live-window-broker-1' },
    reconciliation_status: 'open',
    related_note_id: 'live-window-note-1',
    blockers: [],
    warnings: [],
    manual_action_required: true,
  },
})

assert.equal(livePilotWindow.tone, 'warning')
assert.equal(livePilotWindow.metrics[2].value, 'SPY')
assert.equal(livePilotWindow.metrics[4].value, '$50.00')
assert.equal(livePilotWindow.metrics[7].value, 'Open')
assert.equal(livePilotWindow.metrics[8].value, 'Open')
assert.equal(livePilotWindow.relatedNoteId, 'live-window-note-1')

const signalTelemetry = buildSignalTelemetry({
  alpha_score: 91,
  execution_score: 82,
  portfolio_score: 88,
  edge_to_cost_ratio: 3.4,
  portfolio_rank: 1,
  proxy_correlation_bucket: 'mega_cap_tech',
  auto_entry_eligible: false,
  reject_reason: 'Bucket crowded.',
})

assert.deepEqual(signalTelemetry.rankingSummary, ['Alpha 91.0', 'Exec 82.0', 'Port 88.0'])
assert.deepEqual(signalTelemetry.automationSummary, ['Edge/cost 3.4x', 'Rank #1', 'Mega Cap Tech'])
assert.equal(signalTelemetry.eligibilityLabel, 'Auto entry blocked')
assert.equal(signalTelemetry.rejectionSummary, 'Bucket crowded.')

const premarketFreshness = buildSessionAwareFreshness({
  freshness: {
    ticker: 'SPY',
    interval: '5m',
    status: 'stale',
    session: 'premarket',
    latest_bar_age_minutes: 827.8,
    message: 'Latest 5m bar for SPY is 827.8 minutes old, which exceeds the stale threshold.',
  },
  sessionModel: {
    phase: 'premarket',
    label: 'Premarket planning',
    regularHoursOnly: true,
  },
})

assert.equal(premarketFreshness.status, 'awaiting_regular_session')
assert.equal(premarketFreshness.feed_expected, false)
assert.match(premarketFreshness.message, /waiting for core-session bars/i)

const premarketAlert = buildSessionAwareFreshnessAlert({ freshness: premarketFreshness })
assert.equal(premarketAlert, null)

const liveLagAlert = buildSessionAwareFreshnessAlert({
  freshness: buildSessionAwareFreshness({
    freshness: {
      ticker: 'SPY',
      interval: '5m',
      status: 'stale',
      session: 'regular',
      latest_bar_age_minutes: 18,
    },
    sessionModel: {
      phase: 'morning_session',
      label: 'Morning drive',
      regularHoursOnly: true,
    },
  }),
})
assert.equal(liveLagAlert.title, 'Market data lag detected')
assert.equal(liveLagAlert.tone, 'negative')

const extendedHoursFreshness = buildSessionAwareFreshness({
  freshness: {
    ticker: 'SPY',
    interval: '5m',
    status: 'stale',
    session: 'after_hours',
    latest_bar_age_minutes: 42,
  },
  sessionModel: {
    phase: 'after_hours',
    label: 'After-hours session',
    regularHoursOnly: false,
  },
})

const lockedBrokerageContext = resolveAccountProfileTradingContext({
  activeAccountProfile: 'brokerage',
  defaultExecutionIntent: 'broker_live',
  primaryBrokerageLinkedAccountId: '',
  linkedAccounts: [],
})

assert.equal(lockedBrokerageContext.effectiveAccountTargetType, 'linked_client')
assert.equal(lockedBrokerageContext.profileTradingLockedReason.includes('Bind a primary brokerage account'), true)
assert.equal(lockedBrokerageContext.executionRouteOverride.locked, true)

const boundBrokerageContext = resolveAccountProfileTradingContext({
  activeAccountProfile: 'brokerage',
  defaultExecutionIntent: 'broker_live',
  primaryBrokerageLinkedAccountId: 'acct-1',
  linkedAccounts: [
    {
      id: 'acct-1',
      label: 'Brokerage Main',
      connection_status: 'connected',
      token_health: 'healthy',
      relink_required: false,
      account_environment: 'live',
    },
  ],
})

assert.equal(boundBrokerageContext.effectiveAccountTargetType, 'linked_client')
assert.equal(boundBrokerageContext.effectiveLinkedAccountId, 'acct-1')
assert.equal(boundBrokerageContext.accountTargetLocked, true)
assert.equal(boundBrokerageContext.profileTradingLockedReason, '')
assert.equal(boundBrokerageContext.executionRouteOverride.label, 'Brokerage account')
assert.equal(extendedHoursFreshness.status, 'stale')

const publicConnectPage = getPublicSitePage('/connect')
assert.equal(publicConnectPage.key, 'connect')
assert.equal(publicConnectPage.title, 'Personal Connection Notes')

const publicTermsPage = getPublicSitePage('/terms')
assert.equal(publicTermsPage.key, 'terms')

const publicPrivacyPage = getPublicSitePage('/privacy')
assert.equal(publicPrivacyPage.key, 'privacy')

const publicBranding = getPublicSiteBranding()
assert.equal(publicBranding.name, 'Personal Trading Research Desk')

const publicContact = getPublicSiteContact()
assert.equal(publicContact.type, 'placeholder')

const weekendFreshness = buildSessionAwareFreshness({
  freshness: {
    ticker: 'SPY',
    interval: '5m',
    status: 'stale',
    session: 'weekend',
    latest_bar_age_minutes: 2600,
  },
  sessionModel: {
    phase: 'weekend',
    label: 'Weekend prep',
    regularHoursOnly: true,
  },
})
assert.equal(weekendFreshness.status, 'awaiting_regular_session')
assert.match(weekendFreshness.message, /weekend/i)

console.log('operator telemetry smoke passed')

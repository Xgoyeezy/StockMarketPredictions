from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from backend import stock_direction_model as sdm
from backend.services.event_calendar_service import load_macro_events
from backend.services.market_service import build_watchlist, get_defaults
from backend.services.portfolio_service import get_open_trades
from backend.services.serialization import serialize_value
from backend.schemas import WatchlistRequest


def _safe_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_event_context(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def get_alerts_snapshot(
    limit: int = 12,
    min_severity: str = 'all',
    search: str = '',
    source: str = 'all',
    *,
    current_user: Any | None = None,
    db: Any | None = None,
) -> dict[str, Any]:
    defaults = get_defaults()
    alert_tickers = defaults['default_scan_tickers'][:6]
    watchlist_request = WatchlistRequest(
        tickers=alert_tickers,
        interval=defaults['default_interval'],
        horizon=defaults['default_horizon'],
        limit=6,
        sort_by='setup_score',
        descending=True,
        include_contract_lookup=False,
        include_event_lookup=False,
        include_alignment=False,
        use_fast_model=True,
    )
    watchlist_payload = build_watchlist(watchlist_request)
    watchlist_rows = watchlist_payload.get('results') or watchlist_payload.get('rows') or []

    trade_payload = get_open_trades(limit=50, offset=0, search='', db=db, current_user=current_user)
    trade_rows = trade_payload.get('open_trades', [])
    monitor_rows = trade_payload.get('monitor', [])

    alerts: list[dict[str, Any]] = []

    for row in watchlist_rows:
        decision = str(row.get('trade_decision', '') or '').upper()
        score = _safe_float(row.get('setup_score')) or 0.0
        verdict = str(row.get('verdict', '') or '')
        event_risk = bool(row.get('event_risk', False))
        event_label = str(row.get('event_label', '') or '').strip()
        event_reason = str(row.get('event_reason', '') or '').strip()
        event_context = _normalize_event_context(row.get('event_context'))
        event_trade_posture = str(event_context.get('trade_posture') or '').strip().lower()
        event_summary = str(event_context.get('summary') or '').strip()
        market_regime = str(row.get('market_regime', '') or '').strip()
        regime_strength_score = _safe_float(row.get('regime_strength_score'))
        severity = 'high' if decision == 'VALID TRADE' and score >= 80 else 'medium' if decision == 'VALID TRADE' else 'low'
        if event_risk and severity not in {'critical', 'high'}:
            severity = 'high'
        elif event_trade_posture == 'caution' and severity == 'low':
            severity = 'medium'
        if decision == 'VALID TRADE' or score >= 75:
            message = f"Decision: {decision or 'REVIEW'} | score {score:.1f}"
            if event_risk or event_trade_posture == 'caution':
                message = event_summary or event_reason or event_label or "Known event window is active."
            alerts.append({
                'source': 'watchlist',
                'severity': severity,
                'ticker': row.get('ticker', ''),
                'title': f"{row.get('ticker', '')} {verdict or 'setup'}",
                'message': f"Decision: {decision or 'REVIEW'} · score {score:.1f}",
                'context': {
                    'decision': decision or 'REVIEW',
                    'setup_score': score,
                    'target_price': row.get('target_price') or row.get('expected_underlying_target'),
                    'stop_loss': row.get('stop_loss'),
                    'event_risk': event_risk,
                    'event_label': event_label,
                    'event_reason': event_reason,
                    'event_context': event_context,
                    'event_window_label': str(event_context.get('event_window_label') or '').strip(),
                    'event_severity': str(event_context.get('event_severity') or '').strip(),
                    'trade_posture': str(event_context.get('trade_posture') or '').strip(),
                    'event_session_label': str(event_context.get('session_label') or '').strip(),
                    'event_summary': event_summary,
                    'market_regime': market_regime,
                    'regime_strength_score': regime_strength_score,
                    'forecast_confidence': _safe_float(row.get('forecast_confidence')),
                    'resolved_count': _safe_float(row.get('resolved_count')),
                    'empirical_hit_rate': _safe_float(row.get('empirical_hit_rate')),
                    'average_error': _safe_float(row.get('average_error')),
                    'average_probability_up': _safe_float(row.get('average_probability_up')),
                    'calibration_scope': str(row.get('calibration_scope') or '').strip(),
                    'best_regime': str(row.get('best_regime') or '').strip(),
                    'best_regime_hit_rate': _safe_float(row.get('best_regime_hit_rate')),
                    'best_regime_edge': _safe_float(row.get('best_regime_edge')),
                    'best_regime_resolved_count': _safe_float(row.get('best_regime_resolved_count')),
                    'weakest_regime': str(row.get('weakest_regime') or '').strip(),
                    'weakest_regime_hit_rate': _safe_float(row.get('weakest_regime_hit_rate')),
                    'weakest_regime_edge': _safe_float(row.get('weakest_regime_edge')),
                    'weakest_regime_resolved_count': _safe_float(row.get('weakest_regime_resolved_count')),
                    'best_session': str(row.get('best_session') or '').strip(),
                    'best_session_hit_rate': _safe_float(row.get('best_session_hit_rate')),
                    'best_session_edge': _safe_float(row.get('best_session_edge')),
                    'best_session_resolved_count': _safe_float(row.get('best_session_resolved_count')),
                    'weakest_session': str(row.get('weakest_session') or '').strip(),
                    'weakest_session_hit_rate': _safe_float(row.get('weakest_session_hit_rate')),
                    'weakest_session_edge': _safe_float(row.get('weakest_session_edge')),
                    'weakest_session_resolved_count': _safe_float(row.get('weakest_session_resolved_count')),
                    'best_event_window': str(row.get('best_event_window') or '').strip(),
                    'best_event_window_hit_rate': _safe_float(row.get('best_event_window_hit_rate')),
                    'best_event_window_edge': _safe_float(row.get('best_event_window_edge')),
                    'best_event_window_resolved_count': _safe_float(row.get('best_event_window_resolved_count')),
                    'weakest_event_window': str(row.get('weakest_event_window') or '').strip(),
                    'weakest_event_window_hit_rate': _safe_float(row.get('weakest_event_window_hit_rate')),
                    'weakest_event_window_edge': _safe_float(row.get('weakest_event_window_edge')),
                    'weakest_event_window_resolved_count': _safe_float(row.get('weakest_event_window_resolved_count')),
                    'best_driver': str(row.get('best_driver') or '').strip(),
                    'best_driver_helpful_rate': _safe_float(row.get('best_driver_helpful_rate')),
                    'best_driver_average_signed_impact': _safe_float(row.get('best_driver_average_signed_impact')),
                    'best_driver_resolved_count': _safe_float(row.get('best_driver_resolved_count')),
                    'weakest_driver': str(row.get('weakest_driver') or '').strip(),
                    'weakest_driver_helpful_rate': _safe_float(row.get('weakest_driver_helpful_rate')),
                    'weakest_driver_average_signed_impact': _safe_float(row.get('weakest_driver_average_signed_impact')),
                    'weakest_driver_resolved_count': _safe_float(row.get('weakest_driver_resolved_count')),
                    'technical_confidence_component': _safe_float(row.get('technical_confidence_component')),
                    'news_confidence_component': _safe_float(row.get('news_confidence_component')),
                    'regime_confidence_component': _safe_float(row.get('regime_confidence_component')),
                    'journal_probability_shift': _safe_float(row.get('journal_probability_shift')),
                    'news_probability_shift': _safe_float(row.get('news_probability_shift')),
                    'event_probability_shift': _safe_float(row.get('event_probability_shift')),
                    'spread_pct': _safe_float(row.get('spread_pct')),
                    'volume': _safe_float(row.get('volume')),
                    'open_interest': _safe_float(row.get('open_interest')),
                },
            })

    for index, row in enumerate(monitor_rows):
        action = str(row.get('monitor_action', '') or '').upper()
        pnl = _safe_float(row.get('pnl_dollars')) or 0.0
        ticker = row.get('ticker') or (trade_rows[index].get('ticker') if index < len(trade_rows) else '')
        if action in {'STOP HIT', 'EXIT FULLY NOW', 'SELL MORE NOW', 'SELL 50% NOW', 'TIME STOP', 'DATA ISSUE'}:
            severity = (
                'critical'
                if action == 'STOP HIT'
                else 'high'
                if action in {'EXIT FULLY NOW', 'SELL MORE NOW', 'SELL 50% NOW', 'TIME STOP'}
                else 'medium'
            )
            alerts.append({
                'source': 'trade_monitor',
                'severity': severity,
                'ticker': ticker,
                'title': f"{ticker or 'Open trade'} {action}",
                'message': f"PnL: {pnl:.2f}",
                'context': {
                    'monitor_action': action,
                    'pnl_dollars': pnl,
                    'current_underlying_price': row.get('current_underlying_price') or row.get('current_underlying'),
                    'trade_age_days': row.get('trade_age_days'),
                    'active_stop_price': row.get('active_stop_price'),
                    'next_target_price': row.get('next_target_price'),
                    'current_exit_stage': row.get('current_exit_stage'),
                    'exit_reason': row.get('exit_reason'),
                },
            })

    for event in load_macro_events(limit=6):
        event_date = event.get('event_date')
        days_until = None
        if event_date:
            try:
                days_until = (datetime.fromisoformat(str(event_date)).date() - date.today()).days
            except ValueError:
                days_until = None
        severity = 'high' if days_until is not None and days_until <= 3 else 'medium'
        alerts.append({
            'source': 'macro_calendar',
            'severity': severity,
            'ticker': '',
            'title': event.get('title', 'Macro Event'),
            'message': f"Scheduled for {event_date}" if event_date else 'Scheduled macro event',
            'context': {'event_date': event_date, 'days_until': days_until},
        })

    severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    if str(min_severity).lower() != 'all':
        threshold = severity_order.get(str(min_severity).lower(), 3)
        alerts = [row for row in alerts if severity_order.get(str(row.get('severity')).lower(), 3) <= threshold]
    source_filter = str(source or 'all').strip().lower()
    if source_filter != 'all':
        alerts = [row for row in alerts if str(row.get('source', '')).lower() == source_filter]
    needle = str(search or '').strip().lower()
    if needle:
        alerts = [
            row for row in alerts
            if needle in str(row.get('title', '')).lower()
            or needle in str(row.get('message', '')).lower()
            or needle in str(row.get('ticker', '')).lower()
            or needle in str(row.get('source', '')).lower()
        ]
    alerts = sorted(alerts, key=lambda item: (severity_order.get(str(item.get('severity')).lower(), 99), str(item.get('ticker') or ''), str(item.get('title') or '')))
    limited = alerts[:max(1, int(limit))]

    severity_counts = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
    source_counts: dict[str, int] = {}
    for row in alerts:
        sev = str(row.get('severity', 'low')).lower()
        src = str(row.get('source', 'other')).lower()
        if sev in severity_counts:
            severity_counts[sev] += 1
        source_counts[src] = source_counts.get(src, 0) + 1

    return {
        'count': len(limited),
        'total': len(alerts),
        'severity_counts': severity_counts,
        'source_counts': source_counts,
        'alerts': serialize_value(limited),
        'macro_events': serialize_value(load_macro_events(limit=6)),
        'generated_at': datetime.utcnow().isoformat() + 'Z',
    }

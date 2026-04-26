from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from hft.backtest.metrics import compute_equity_curve, compute_max_drawdown, compute_sharpe
from hft.backtest.reports import build_read_only_bridge, write_html_report, write_json_report
from hft.execution.simulator import ExecutionSimulator
from hft.latency.model import LatencyModel, LatencyProfile
from hft.features.microstructure import MicrostructureFeatureSnapshot
from hft.inventory.inventory import InventoryManager, InventoryState
from hft.market_data.schemas import MarketEvent
from hft.monitoring.alerts import AlertRecord, evaluate_alerts
from hft.order_book.book import OrderBook
from hft.optimization.types import QueueCalibrationArtifact
from hft.pnl.attribution import PnLAttributionEngine, PnLAttributionSnapshot
from hft.risk.checks import HFTRiskEngine
from hft.risk.limits import HFTLimitConfig
from hft.strategies.base import HFTStrategy
from hft.utils.ids import NamedIdPool
from hft.utils.logging import JsonlLogger

try:
    import duckdb
except Exception:  # pragma: no cover
    duckdb = None


@dataclass(frozen=True)
class ReplayRunConfig:
    seed: int
    symbol: str
    strategy_name: str
    latency_profile: LatencyProfile
    risk_limits: HFTLimitConfig
    replay_window: tuple[int, int] | None = None
    strategy_parameters: dict[str, Any] = field(default_factory=dict)
    fee_model: dict[str, float] = field(default_factory=dict)
    queue_calibration: QueueCalibrationArtifact | None = None
    analysis_sections: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayRunResult:
    run_id: str
    symbol: str
    strategy_name: str
    orders: list[Any]
    fills: list[Any]
    event_log: list[dict[str, Any]]
    inventory_path: list[InventoryState]
    pnl_path: list[float]
    metrics: dict[str, Any]
    inventory_snapshot: InventoryState
    attribution_snapshot: PnLAttributionSnapshot
    strategy_health: Any
    alerts: list[AlertRecord]
    output_dir: str
    equity_curve: list[float]
    bridge_export: dict[str, Any]
    analysis_sections: dict[str, Any] = field(default_factory=dict)
    final_book_snapshot: Any | None = None
    latency_summary: dict[str, Any] = field(default_factory=dict)


class ReplayBacktestEngine:
    def __init__(self, *, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.ids = NamedIdPool()

    def run(self, *, events: list[MarketEvent], strategy: HFTStrategy, config: ReplayRunConfig) -> ReplayRunResult:
        rng = random.Random(config.seed)
        symbol = config.symbol.upper()
        run_id = self.ids.next("run")
        run_dir = self.base_dir / "replay" / f"run_id={run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        logger = JsonlLogger(run_dir / "event_log.jsonl")

        book = OrderBook(symbol)
        risk_engine = HFTRiskEngine(limits=config.risk_limits)
        latency_model = LatencyModel(profile=config.latency_profile, rng=random.Random(config.seed))
        simulator = ExecutionSimulator(
            risk_engine=risk_engine,
            latency_model=latency_model,
            maker_rebate_per_share=float(config.fee_model.get("maker_rebate_per_share", 0.0015)),
            taker_fee_per_share=float(config.fee_model.get("taker_fee_per_share", 0.0030)),
            seed=config.seed,
            queue_calibration=config.queue_calibration,
        )
        max_inventory = config.risk_limits.for_symbol(symbol).max_inventory
        inventory = InventoryManager(symbol=symbol, max_inventory=max_inventory)
        attribution = PnLAttributionEngine(
            symbol=symbol,
            strategy_name=strategy.strategy_name,
            maker_rebate_per_share=float(config.fee_model.get("maker_rebate_per_share", 0.0015)),
            taker_fee_per_share=float(config.fee_model.get("taker_fee_per_share", 0.0030)),
            cancel_cost_per_order=float(config.fee_model.get("cancel_cost_per_order", 0.0001)),
        )

        inventory_path: list[InventoryState] = []
        pnl_path: list[float] = []
        event_log: list[dict[str, Any]] = []
        active_symbol_limits = config.risk_limits.for_symbol(symbol)
        last_features: MicrostructureFeatureSnapshot | None = None

        for event in sorted(events):
            if config.replay_window and not (config.replay_window[0] <= event.exchange_ts_ns <= config.replay_window[1]):
                continue
            strategy.on_market_event(event)
            book.apply_event(event)
            book_snapshot = book.snapshot()
            try:
                features = strategy.compute_features(book)
            except TypeError:
                # fallback for strategies that accept snapshot-like objects
                features = strategy.compute_features(book_snapshot)
            last_features = features
            risk_engine.update_runtime_state(
                event,
                latest_spread=book_snapshot.spread,
                latest_volatility=getattr(features, "short_term_volatility", 0.0),
                inventory=inventory.position,
                notional_exposure=inventory.position * book_snapshot.mid_price,
                realized_pnl=inventory.realized_pnl,
            )

            visible_depth = {}
            for level in book_snapshot.depth_by_level["bids"] + book_snapshot.depth_by_level["asks"]:
                visible_depth[level.price] = level.size
            fills = simulator.process_market_event(
                event,
                best_bid=book_snapshot.best_bid,
                best_ask=book_snapshot.best_ask,
                visible_depth_by_price=visible_depth,
                imbalance=book_snapshot.order_book_imbalance,
            )
            for fill in fills:
                pre_mid = book_snapshot.mid_price
                post_state = inventory.update_from_fill(fill)
                inventory_path.append(post_state)
                attribution.record_fill(fill, mid_price_before=pre_mid, mid_price_after=book.get_mid_price())
                pnl_path.append(post_state.realized_pnl + post_state.unrealized_pnl)
                risk_engine.update_runtime_state(
                    inventory=inventory.position,
                    notional_exposure=inventory.position * book.get_mid_price(),
                    realized_pnl=inventory.realized_pnl,
                    fill_count=risk_engine.state.fill_count + 1,
                )

            fair_value_estimate = strategy.estimate_fair_value(features)
            market_state = type(
                "MarketState",
                (),
                {"timestamp_ns": event.receive_ts_ns, "features": features, "fair_value": fair_value_estimate},
            )
            for cancel in strategy.generate_cancels(simulator.get_active_orders(), market_state):
                decision = simulator.cancel_order(cancel.order_id, timestamp_ns=event.receive_ts_ns)
                if decision.allowed:
                    attribution.record_cancel()

            kill_decision = risk_engine.should_kill_strategy()
            if kill_decision.allowed:
                inventory_state = inventory.mark_to_market(book_snapshot.mid_price, timestamp_ns=event.receive_ts_ns)
                for quote in strategy.generate_quotes(features, inventory_state):
                    order = simulator.create_limit_order(
                        symbol=symbol,
                        strategy_name=strategy.strategy_name,
                        side=quote.side,
                        price=quote.price,
                        quantity=min(quote.quantity, active_symbol_limits.max_order_size),
                        decision_timestamp=event.receive_ts_ns + latency_model.sample_ns("strategy_compute_ns"),
                        quote_width=quote.quote_width,
                        metadata=dict(quote.metadata or {}),
                    )
                    decision, placed_order = simulator.submit_order(order)
                    event_record = {
                        "event_id": event.event_id,
                        "timestamp_ns": event.receive_ts_ns,
                        "action": "submit_order",
                        "order_id": placed_order.order_id,
                        "side": placed_order.side,
                        "price": placed_order.price,
                        "quantity": placed_order.quantity,
                        "risk_allowed": decision.allowed,
                        "risk_reason": decision.reason,
                    }
                    event_log.append(event_record)
                    logger.emit(event_record)
            else:
                event_record = {
                    "event_id": event.event_id,
                    "timestamp_ns": event.receive_ts_ns,
                    "action": "kill_switch_pause",
                    "risk_reason": kill_decision.reason,
                }
                event_log.append(event_record)
                logger.emit(event_record)

            if not inventory_path:
                inventory_state = inventory.mark_to_market(book_snapshot.mid_price, timestamp_ns=event.receive_ts_ns)
                inventory_path.append(inventory_state)
                pnl_path.append(inventory_state.realized_pnl + inventory_state.unrealized_pnl)

        final_inventory = inventory.mark_to_market(book.get_mid_price(), timestamp_ns=book.last_timestamp_ns)
        attribution_snapshot = attribution.snapshot(
            realized_pnl=final_inventory.realized_pnl,
            unrealized_pnl=final_inventory.unrealized_pnl,
        )
        equity_curve = compute_equity_curve(pnl_path)
        returns = [equity_curve[idx] - equity_curve[idx - 1] for idx in range(1, len(equity_curve))]
        fill_rate = len(simulator.get_fills()) / max(len(simulator.get_active_orders()) + len(simulator.get_fills()), 1)
        cancel_rate = attribution.cancel_costs / max(attribution.cancel_cost_per_order, 1e-9)
        metrics = {
            "pnl": final_inventory.realized_pnl + final_inventory.unrealized_pnl,
            "realized_pnl": final_inventory.realized_pnl,
            "unrealized_pnl": final_inventory.unrealized_pnl,
            "sharpe_ratio": compute_sharpe(returns),
            "max_drawdown": compute_max_drawdown(equity_curve),
            "fill_rate": fill_rate,
            "cancel_rate": cancel_rate,
            "message_rate": float(len(risk_engine.state.message_count_window)),
            "average_spread_captured": attribution.spread_capture / max(len(simulator.get_fills()), 1),
            "adverse_selection_cost": attribution.adverse_selection,
            "inventory_exposure": abs(final_inventory.position),
            "holding_time_ns": final_inventory.inventory_age_ns,
            "hit_rate": 1.0 if metrics_positive(final_inventory.realized_pnl + final_inventory.unrealized_pnl) else 0.0,
            "queue_position_estimate": 0.5,
            "latency_sensitivity": config.latency_profile.profile_type,
        }
        strategy_health = strategy.publish_risk_state()
        alerts = evaluate_alerts(
            risk_reason=strategy_health.risk_state,
            inventory_skew=final_inventory.inventory_skew,
            realized_pnl=final_inventory.realized_pnl,
            message_rate=metrics["message_rate"],
        )
        result = ReplayRunResult(
            run_id=run_id,
            symbol=symbol,
            strategy_name=strategy.strategy_name,
            orders=simulator.get_orders(),
            fills=simulator.get_fills(),
            event_log=event_log,
            inventory_path=inventory_path,
            pnl_path=pnl_path,
            metrics=metrics,
            inventory_snapshot=final_inventory,
            attribution_snapshot=attribution_snapshot,
            strategy_health=strategy_health,
            alerts=alerts,
            output_dir=str(run_dir),
            equity_curve=equity_curve,
            bridge_export={},
            analysis_sections=dict(config.analysis_sections),
            final_book_snapshot=book.snapshot(),
            latency_summary=simulator.get_latency_summary(),
        )
        bridge = build_read_only_bridge(result)
        result.bridge_export = bridge
        write_json_report(run_dir / "metrics_summary.json", metrics)
        write_json_report(run_dir / "bridge_export.json", bridge)
        write_json_report(run_dir / "config_snapshot.json", asdict(config))
        write_json_report(run_dir / "fills.json", {"items": result.fills})
        write_json_report(run_dir / "inventory_path.json", {"items": result.inventory_path})
        write_json_report(run_dir / "pnl_path.json", {"items": result.pnl_path})
        write_json_report(run_dir / "latency_summary.json", result.latency_summary)
        write_html_report(run_dir / "report.html", result)
        self._write_duckdb_summary(run_dir=run_dir, result=result)
        return result

    def _write_duckdb_summary(self, *, run_dir: Path, result: ReplayRunResult) -> None:
        if duckdb is None:
            return
        db_path = self.base_dir / "hft.duckdb"
        conn = duckdb.connect(str(db_path))
        conn.execute(
            """
            create table if not exists replay_runs (
              run_id varchar,
              symbol varchar,
              strategy_name varchar,
              pnl double,
              realized_pnl double,
              unrealized_pnl double,
              fill_rate double,
              max_drawdown double,
              created_at varchar
            )
            """
        )
        conn.execute(
            """
            insert into replay_runs values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.run_id,
                result.symbol,
                result.strategy_name,
                result.metrics["pnl"],
                result.metrics["realized_pnl"],
                result.metrics["unrealized_pnl"],
                result.metrics["fill_rate"],
                result.metrics["max_drawdown"],
                run_dir.name,
            ],
        )
        conn.close()


def metrics_positive(value: float) -> bool:
    return float(value) > 0.0

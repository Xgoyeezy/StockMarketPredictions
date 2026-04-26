from __future__ import annotations

import random
from dataclasses import asdict
from statistics import mean
from typing import Any

import numpy as np

from hft.execution.queue_model import QueuePositionModel
from hft.execution.simulator import LatencyProfile
from hft.market_data.schemas import MarketEvent
from hft.optimization.types import (
    FillCalibrationReport,
    LatencyCalibrationArtifact,
    QueueCalibrationArtifact,
)
from hft.order_book.book import OrderBook


def calibrate_latency_from_events(
    events: list[MarketEvent],
    *,
    stage_samples: dict[str, list[int]] | None = None,
) -> LatencyCalibrationArtifact:
    stage_samples = dict(stage_samples or {})
    market_data_latencies = [event.latency_ns for event in events] or [0]
    market_data_ns = int(np.median(market_data_latencies))
    strategy_samples = stage_samples.get("strategy_compute_ns") or [max(int(market_data_ns * 0.35), 1)]
    submit_samples = stage_samples.get("order_submit_ns") or [max(int(market_data_ns * 0.55), 1)]
    ack_samples = stage_samples.get("exchange_ack_ns") or [max(int(market_data_ns * 0.30), 1)]
    cancel_samples = stage_samples.get("cancel_ns") or [max(int(market_data_ns * 0.45), 1)]
    fill_samples = stage_samples.get("fill_ns") or [max(int(market_data_ns * 0.20), 1)]
    artifact = LatencyCalibrationArtifact(
        market_data_ns=market_data_ns,
        strategy_compute_ns=int(np.median(strategy_samples)),
        order_submit_ns=int(np.median(submit_samples)),
        exchange_ack_ns=int(np.median(ack_samples)),
        cancel_ns=int(np.median(cancel_samples)),
        fill_ns=int(np.median(fill_samples)),
        stage_error_by_name={
            "market_data_ns": _mean_abs_error(market_data_latencies, market_data_ns),
            "strategy_compute_ns": _mean_abs_error(strategy_samples, int(np.median(strategy_samples))),
            "order_submit_ns": _mean_abs_error(submit_samples, int(np.median(submit_samples))),
            "exchange_ack_ns": _mean_abs_error(ack_samples, int(np.median(ack_samples))),
            "cancel_ns": _mean_abs_error(cancel_samples, int(np.median(cancel_samples))),
            "fill_ns": _mean_abs_error(fill_samples, int(np.median(fill_samples))),
        },
    )
    return artifact


def latency_artifact_to_profile(artifact: LatencyCalibrationArtifact) -> LatencyProfile:
    return LatencyProfile(
        "fixed",
        {
            "market_data_ns": artifact.market_data_ns,
            "strategy_compute_ns": artifact.strategy_compute_ns,
            "order_submit_ns": artifact.order_submit_ns,
            "exchange_ack_ns": artifact.exchange_ack_ns,
            "cancel_ns": artifact.cancel_ns,
            "fill_ns": artifact.fill_ns,
        },
    )


def build_queue_observations(events: list[MarketEvent]) -> list[dict[str, float]]:
    if not events:
        return []
    book = OrderBook(events[0].symbol)
    observations: list[dict[str, float]] = []
    for event in sorted(events):
        snapshot = book.snapshot()
        last_book_change_ts = int(book.last_timestamp_ns or 0)
        if event.event_type == "trade" and snapshot.mid_price > 0:
            contra_depth = 0.0
            if event.side == "buy":
                contra_depth = next((level.size for level in snapshot.depth_by_level["asks"] if level.price <= event.price), 0.0)
            elif event.side == "sell":
                contra_depth = next((level.size for level in snapshot.depth_by_level["bids"] if level.price >= event.price), 0.0)
            spread_bps = (snapshot.spread / snapshot.mid_price) * 10_000.0 if snapshot.mid_price else 0.0
            trade_to_depth = float(event.size) / max(contra_depth, 1e-9)
            pseudo_fill_ratio = max(0.0, min(trade_to_depth, 1.0))
            observations.append(
                {
                    "visible_depth": max(contra_depth, event.size, 1e-9),
                    "incoming_trade_size": float(event.size),
                    "spread_bps": spread_bps,
                    "imbalance": snapshot.order_book_imbalance,
                    "latency_bucket": float(event.latency_ns / 1_000_000.0),
                    "quote_age_ns": float(max(event.receive_ts_ns - last_book_change_ts, 0)),
                    "target_fill_ratio": pseudo_fill_ratio,
                }
            )
        book.apply_event(event)
    return observations


def calibrate_queue_model_from_observations(observations: list[dict[str, float]]) -> QueueCalibrationArtifact:
    if not observations:
        return QueueCalibrationArtifact()
    x_rows: list[list[float]] = []
    y: list[float] = []
    for row in observations:
        visible_depth = max(float(row["visible_depth"]), 1e-9)
        incoming_trade_size = float(row["incoming_trade_size"])
        order_size = min(incoming_trade_size, visible_depth)
        base_ratio = max((incoming_trade_size - max(visible_depth - order_size, 0.0)) / max(order_size, 1e-9), 0.0)
        x_rows.append(
            [
                1.0,
                base_ratio,
                incoming_trade_size / visible_depth,
                float(row["spread_bps"]) / 10.0,
                abs(float(row["imbalance"])),
                float(row["latency_bucket"]),
                float(row["quote_age_ns"]) / 1_000_000_000.0,
            ]
        )
        y.append(float(row["target_fill_ratio"]))
    x = np.array(x_rows, dtype=float)
    targets = np.array(y, dtype=float)
    ridge = np.eye(x.shape[1], dtype=float) * 1e-3
    ridge[0, 0] = 0.0
    coeffs = np.linalg.pinv(x.T @ x + ridge) @ x.T @ targets
    return QueueCalibrationArtifact(
        intercept=float(coeffs[0]),
        base_ratio_weight=float(coeffs[1]),
        trade_to_depth_weight=float(coeffs[2]),
        spread_bps_weight=float(coeffs[3]),
        imbalance_weight=float(coeffs[4]),
        latency_bucket_weight=float(coeffs[5]),
        quote_age_weight=float(coeffs[6]),
        noise_low=0.99,
        noise_high=1.01,
    )


def calibrate_queue_model_from_events(events: list[MarketEvent]) -> QueueCalibrationArtifact:
    return calibrate_queue_model_from_observations(build_queue_observations(events))


def evaluate_fill_model(
    observations: list[dict[str, float]],
    *,
    artifact: QueueCalibrationArtifact,
    baseline_artifact: QueueCalibrationArtifact | None = None,
) -> FillCalibrationReport:
    baseline_artifact = baseline_artifact or QueueCalibrationArtifact()
    baseline_model = QueuePositionModel(rng=random.Random(7), calibration=baseline_artifact)
    calibrated_model = QueuePositionModel(rng=random.Random(7), calibration=artifact)
    baseline_errors: list[float] = []
    calibrated_errors: list[float] = []
    missed_fill_events = 0
    adverse_selection_errors: list[float] = []
    for row in observations:
        target = float(row["target_fill_ratio"])
        kwargs = {
            "visible_depth": float(row["visible_depth"]),
            "incoming_trade_size": float(row["incoming_trade_size"]),
            "order_size": min(float(row["incoming_trade_size"]), float(row["visible_depth"])),
            "spread_bps": float(row["spread_bps"]),
            "imbalance": float(row["imbalance"]),
            "latency_bucket": float(row["latency_bucket"]),
            "quote_age_ns": int(row["quote_age_ns"]),
        }
        baseline = baseline_model.fill_ratio(**kwargs)
        calibrated = calibrated_model.fill_ratio(**kwargs)
        baseline_errors.append(abs(baseline - target))
        calibrated_errors.append(abs(calibrated - target))
        if target > 0.50 and calibrated < 0.10:
            missed_fill_events += 1
        if abs(float(row["imbalance"])) > 0.25:
            adverse_selection_errors.append(abs(calibrated - target))
    return FillCalibrationReport(
        observation_count=len(observations),
        baseline_error=mean(baseline_errors) if baseline_errors else 0.0,
        calibrated_error=mean(calibrated_errors) if calibrated_errors else 0.0,
        missed_fill_rate=(missed_fill_events / len(observations)) if observations else 0.0,
        adverse_selection_error=mean(adverse_selection_errors) if adverse_selection_errors else 0.0,
        latency_error_by_stage={},
    )


def _mean_abs_error(values: list[int], anchor: int) -> float:
    if not values:
        return 0.0
    return float(sum(abs(int(item) - int(anchor)) for item in values) / len(values))

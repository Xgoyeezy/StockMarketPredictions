from __future__ import annotations

from hft.optimization.types import SessionManifest, WalkForwardSplit


def generate_walk_forward_splits(
    manifests: list[SessionManifest],
    *,
    train_sessions: int,
    validation_sessions: int,
    holdout_sessions: int,
) -> list[WalkForwardSplit]:
    ordered = sorted(manifests, key=lambda item: (item.trading_day, item.session_id))
    if len(ordered) <= holdout_sessions:
        raise ValueError("Need more sessions than holdout_sessions to build walk-forward splits.")
    holdout = tuple(item.session_id for item in ordered[-holdout_sessions:])
    train_validation = ordered[:-holdout_sessions]
    splits: list[WalkForwardSplit] = []
    index = 0
    while index + train_sessions + validation_sessions <= len(train_validation):
        train_slice = train_validation[index : index + train_sessions]
        validation_slice = train_validation[index + train_sessions : index + train_sessions + validation_sessions]
        split = WalkForwardSplit(
            split_id=f"split-{len(splits)+1:03d}",
            train_sessions=tuple(item.session_id for item in train_slice),
            validation_sessions=tuple(item.session_id for item in validation_slice),
            holdout_sessions=holdout,
        )
        splits.append(split)
        index += 1
    if not splits:
        train_slice = train_validation[:-validation_sessions]
        validation_slice = train_validation[-validation_sessions:]
        if not train_slice or not validation_slice:
            raise ValueError("Insufficient sessions to build at least one walk-forward split.")
        splits.append(
            WalkForwardSplit(
                split_id="split-001",
                train_sessions=tuple(item.session_id for item in train_slice),
                validation_sessions=tuple(item.session_id for item in validation_slice),
                holdout_sessions=holdout,
            )
        )
    return splits

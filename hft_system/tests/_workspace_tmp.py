from __future__ import annotations

import shutil
from pathlib import Path


TMP_ROOT = Path(__file__).resolve().parents[1] / ".tmp_tests"
TMP_ROOT.mkdir(parents=True, exist_ok=True)


def reset_tmp_dir(name: str) -> Path:
    target = TMP_ROOT / name
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    return target

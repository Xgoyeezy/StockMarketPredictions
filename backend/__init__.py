from __future__ import annotations

import os
import sys


for _thread_env_name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_thread_env_name, "1")


def _disable_blocking_windows_wmi_platform_probe() -> None:
    if not sys.platform.startswith("win"):
        return

    try:
        import platform
    except Exception:
        return

    if hasattr(platform, "_wmi"):
        # Python 3.14 asks Windows WMI for platform details. On machines where
        # WMI is unhealthy, scientific-package imports can hang before the API
        # binds a port. Disabling the private WMI hook keeps platform.* on its
        # documented fallback path and avoids blocking backend startup.
        platform._wmi = None  # type: ignore[attr-defined]


_disable_blocking_windows_wmi_platform_probe()

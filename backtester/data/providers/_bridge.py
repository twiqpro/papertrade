"""Bridge to Twiq backend modules without conflicting with backtester/app.py."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]
_PKG = "twiq_app"


def _resolve_backend_app() -> Path:
    """Local dev: repo/backend/app · Docker: /app/app (backend copied without backend/ prefix)."""
    candidates = [
        REPO_ROOT / "backend" / "app",
        REPO_ROOT / "app",
    ]
    for path in candidates:
        if (path / "config.py").exists():
            return path
    tried = ", ".join(str(p) for p in candidates)
    raise ImportError(f"Twiq backend app not found (tried: {tried})")


BACKEND_APP = _resolve_backend_app()

load_dotenv(REPO_ROOT / ".env", override=True)
load_dotenv(REPO_ROOT / "backend" / ".env", override=True)

_dhan_module = None


def _load_backend_submodule(name: str, filename: str):
    """Load backend/app/<filename> as twiq_app.<name>."""
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(BACKEND_APP)]  # type: ignore[attr-defined]
        pkg.__package__ = _PKG
        sys.modules[_PKG] = pkg

    full_name = f"{_PKG}.{name}"
    if full_name in sys.modules:
        return sys.modules[full_name]

    path = BACKEND_APP / filename
    if not path.exists():
        raise ImportError(f"Twiq backend module not found: {path}")

    spec = importlib.util.spec_from_file_location(full_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {full_name}")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full_name] = mod
    spec.loader.exec_module(mod)
    return mod


def get_dhan_classes():
    """Return (DhanAdapter, DhanApiError) from Twiq backend."""
    global _dhan_module
    load_dotenv(REPO_ROOT / ".env", override=True)
    load_dotenv(REPO_ROOT / "backend" / ".env", override=True)
    config_mod = _load_backend_submodule("config", "config.py")
    if hasattr(config_mod, "get_settings"):
        config_mod.get_settings.cache_clear()
    if _dhan_module is None:
        _dhan_module = _load_backend_submodule("dhan_client", "dhan_client.py")
    return _dhan_module.DhanAdapter, _dhan_module.DhanApiError

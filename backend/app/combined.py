"""Combined ASGI app: paper trading API + options backtester."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from fastapi import FastAPI

APP_PKG = Path(__file__).resolve().parent

BACKTESTER_ROOT: Path | None = None
APP_ROOT: Path | None = None
for base in (APP_PKG.parent, APP_PKG.parent.parent):
    candidate = base / "backtester" / "app.py"
    if candidate.exists():
        BACKTESTER_ROOT = base / "backtester"
        APP_ROOT = base
        break

if BACKTESTER_ROOT is None:
    raise ImportError("Could not locate backtester/app.py next to the backend package")

os.environ.setdefault("ROOT_PATH", "/options-backtest")

if str(BACKTESTER_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKTESTER_ROOT))

_spec = importlib.util.spec_from_file_location("options_backtester_app", BACKTESTER_ROOT / "app.py")
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load options backtester from {BACKTESTER_ROOT / 'app.py'}")
_bt_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bt_mod)
import typing as _typing

_types_ns = {**_bt_mod.__dict__, **_typing.__dict__}
for _name in ("DownloadRequest", "RunRequest"):
    _model = getattr(_bt_mod, _name, None)
    if _model is not None and hasattr(_model, "model_rebuild"):
        _model.model_rebuild(_types_namespace=_types_ns)
options_bt_app = _bt_mod.app

from app.main import app as paper_app  # noqa: E402

root = FastAPI(title="Twiq")
root.mount("/options-backtest", options_bt_app)
root.mount("/", paper_app)

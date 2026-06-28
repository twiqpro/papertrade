"""Child process entrypoint — loads user Strategy and runs backtest."""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.feed import load  # noqa: E402
from engine.backtest import run_backtest  # noqa: E402


def _load_strategy(path: str):
    spec = importlib.util.spec_from_file_location("user_strategy", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load strategy from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "Strategy"):
        raise AttributeError("Strategy file must define a class named Strategy")
    return module.Strategy()


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"error": {"message": "Missing worker args"}}))
        sys.exit(1)

    try:
        args = json.loads(sys.argv[1])
        code_path = args["code_path"]
        symbol = args["symbol"]
        start = args["start"]
        end = args["end"]
        interval = args["interval"]
        strikes = args.get("strikes_around_atm", 10)
        dates = args.get("dates")

        strategy = _load_strategy(code_path)
        df = load(
            symbol=symbol,
            start=start,
            end=end,
            interval=interval,
            strikes_around_atm=strikes,
            dates=dates,
        )
        result = run_backtest(strategy, df)
        print(json.dumps(result, default=str))
    except Exception:
        print(
            json.dumps(
                {
                    "decisions": [],
                    "trades": [],
                    "summary": {
                        "total_trades": 0,
                        "win_rate": 0.0,
                        "total_pnl": 0.0,
                        "max_drawdown": 0.0,
                        "skip_count": 0,
                        "bar_count": 0,
                    },
                    "error": {
                        "message": "Worker failed",
                        "traceback": traceback.format_exc(),
                    },
                }
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

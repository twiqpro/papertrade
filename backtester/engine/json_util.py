"""JSON-safe serialization for API responses."""

from __future__ import annotations

import math
from typing import Any


def json_safe(value: Any) -> Any:
    """Replace NaN/Inf floats so Starlette can encode responses."""
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
    return value

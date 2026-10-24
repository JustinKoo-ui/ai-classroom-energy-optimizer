from __future__ import annotations

from collections.abc import Mapping, Sequence, Sized


def prediction_count(value: object) -> int | None:
    """Return a count from common Roboflow workflow output shapes."""
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value if value >= 0 else None

    if isinstance(value, Mapping):
        if "predictions" in value:
            predictions = value["predictions"]
            if isinstance(predictions, Sequence) and not isinstance(
                predictions, str | bytes
            ):
                return len(predictions)
            return None

        if "count" in value:
            return prediction_count(value["count"])

        if "output" in value:
            return prediction_count(value["output"])

        return None

    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return len(value)

    if isinstance(value, Sized) and not isinstance(value, str | bytes):
        count = len(value)
        return count if count >= 0 else None

    return None


def workflow_output_count(result: object, output_name: str) -> int | None:
    if not isinstance(result, Mapping):
        return None
    return prediction_count(result.get(output_name))


def workflow_result_shape(result: object) -> str:
    """Describe result field names and value types without logging prediction data."""
    if isinstance(result, Mapping):
        fields = []
        for name, value in result.items():
            if isinstance(value, Mapping):
                value_shape = f"mapping keys={list(value.keys())}"
            elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
                item_type = type(value[0]).__name__ if value else "empty"
                value_shape = f"sequence length={len(value)} item_type={item_type}"
            else:
                value_shape = type(value).__name__
            fields.append(f"{name}={value_shape}")
        return f"mapping {{{', '.join(fields)}}}"

    return type(result).__name__

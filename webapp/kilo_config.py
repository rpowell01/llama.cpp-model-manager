"""Kilo Code agent configuration generator.

Generates kilo.jsonc-compatible model configuration from the local model
registry.  When a model has a multimodal projector (mmproj) file path
configured, a ``modalities`` block is included so the Kilo Code agent knows
the model accepts image input.
"""

from __future__ import annotations

from typing import Any

from webapp.config import DEFAULTS
from webapp.models import get_models


def _has_mmproj(meta: dict[str, Any]) -> bool:
    """Return True when the model has a non-empty mmproj file path."""
    mmproj = meta.get("mmproj", DEFAULTS.get("mmproj", ""))
    return bool(mmproj and mmproj.strip())


def _build_limit(meta: dict[str, Any]) -> dict[str, int]:
    """Build the ``limit`` block from model metadata."""
    ctx_size = int(meta.get("ctx_size", DEFAULTS.get("ctx_size", 4096)))
    # Reserve ~15% of context for input, rest for output (heuristic).
    input_tokens = int(ctx_size * 0.85)
    output_tokens = ctx_size - input_tokens
    return {
        "context": ctx_size,
        "input": input_tokens,
        "output": output_tokens,
    }


def _build_supported_parameters() -> list[str]:
    """Return the standard supported_parameters list."""
    return [
        "include_reasoning",
        "max_tokens",
        "reasoning",
        "temperature",
        "tool_choice",
        "tools",
    ]


def _build_modalities() -> dict[str, list[str]]:
    """Return the modalities block for multimodal models."""
    return {
        "input": ["text", "image"],
        "output": ["text"],
    }


def generate_kilo_config() -> dict[str, Any]:
    """Generate kilo.jsonc-compatible configuration for all models.

    Returns a dict keyed by model name, where each value is a model entry
    compatible with the kilo.jsonc provider model schema.

    When a model has a non-empty ``mmproj`` path, a ``modalities`` block is
    inserted after ``limit`` and before ``supported_parameters``.
    """
    models = get_models()
    config: dict[str, Any] = {}

    for name, meta in models.items():
        entry: dict[str, Any] = {}

        # limit block (always first)
        entry["limit"] = _build_limit(meta)

        # modalities block (only when mmproj is configured)
        if _has_mmproj(meta):
            entry["modalities"] = _build_modalities()

        # supported_parameters (after limit and modalities)
        entry["supported_parameters"] = _build_supported_parameters()

        # default_parameters
        entry["default_parameters"] = {}

        # Capability flags
        entry["attachment"] = _has_mmproj(meta)
        entry["reasoning"] = bool(meta.get("reasoning_preserve", DEFAULTS.get("reasoning_preserve", True)))
        entry["tool_call"] = True
        entry["structured_output"] = True
        entry["temperature"] = True

        config[name] = entry

    return config


def generate_kilo_config_for_model(model_name: str) -> dict[str, Any] | None:
    """Generate kilo configuration for a single model.

    Returns the model entry dict, or None if the model is not found.
    """
    models = get_models()
    # Case-insensitive lookup
    model_key = None
    for k in models:
        if k.lower() == model_name.lower():
            model_key = k
            break
    if model_key is None:
        return None

    meta = models[model_key]
    entry: dict[str, Any] = {}

    entry["limit"] = _build_limit(meta)

    if _has_mmproj(meta):
        entry["modalities"] = _build_modalities()

    entry["supported_parameters"] = _build_supported_parameters()
    entry["default_parameters"] = {}
    entry["attachment"] = _has_mmproj(meta)
    entry["reasoning"] = bool(meta.get("reasoning_preserve", DEFAULTS.get("reasoning_preserve", True)))
    entry["tool_call"] = True
    entry["structured_output"] = True
    entry["temperature"] = True

    return entry

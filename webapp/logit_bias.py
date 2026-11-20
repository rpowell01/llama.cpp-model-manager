"""Model-specific token resolution for configurable logit penalties."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Callable


DEFAULT_OVERTHINKING_WORDS = [
    " perhaps", " maybe", " wait", " Wait", " actually",
    " hold", " Hmm", " hmm", " Alternatively", " alternatively",
    " However", " however", " instead", " Instead", " But",
    " but", " though", " although", " yet", " rather",
    " unless", " otherwise", " nonetheless", " nevertheless", " regardless",
    " still", " anyway", " Or", " or", " either",
    " whether", " uncertain", " unsure", " possibly", " might",
    " could", " another", " different", " reconsider", " rethink",
    " backtrack", " retry", " revisit", " doubt", " confused",
    " wrong", " mistake", " error", " incorrect",
]


class OverthinkingPenaltyError(RuntimeError):
    """Raised when an enabled penalty cannot be resolved safely."""


def normalize_overthinking_words(value: Any) -> list[str]:
    """Validate exact token strings while preserving significant whitespace."""
    if not isinstance(value, list):
        raise ValueError("overthinking_penalty_words must be a list of strings")

    words: list[str] = []
    seen: set[str] = set()
    for word in value:
        if not isinstance(word, str):
            raise ValueError("overthinking_penalty_words must contain only strings")
        word = word.rstrip("\r\n")
        if not word or not word.strip():
            continue
        if len(word) > 256:
            raise ValueError("Each overthinking penalty entry must be at most 256 characters")
        if word not in seen:
            words.append(word)
            seen.add(word)
    if len(words) > 256:
        raise ValueError("At most 256 overthinking penalty entries are allowed")
    return words


def _format_bias(value: Any) -> str:
    try:
        bias = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise OverthinkingPenaltyError("Overthinking penalty bias must be numeric") from exc
    if not math.isfinite(bias) or bias > 0:
        raise OverthinkingPenaltyError("Overthinking penalty bias must be a finite number <= 0")
    return str(int(bias)) if bias.is_integer() else str(bias)


def _file_signature(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except OSError:
        return {"size": None, "mtime_ns": None}


def _cache_signature(model_path: Path, tokenizer_path: Path, words: list[str]) -> str:
    payload = {
        "model": str(model_path),
        "model_file": _file_signature(model_path),
        "tokenizer": str(tokenizer_path),
        "tokenizer_file": _file_signature(tokenizer_path),
        "words": words,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _tokenize_word(
    tokenizer_path: Path,
    model_path_for_binary: str,
    word: str,
    timeout: int,
    runner: Callable[..., subprocess.CompletedProcess],
) -> list[int]:
    command = [
        str(tokenizer_path),
        "-m", model_path_for_binary,
        "-p", word,
        "--ids",
        "--no-bos",
        "--no-escape",
    ]
    try:
        result = runner(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OverthinkingPenaltyError(f"Failed to run llama-tokenize for {word!r}: {exc}") from exc

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown tokenizer error").strip()
        raise OverthinkingPenaltyError(f"llama-tokenize failed for {word!r}: {detail[-1000:]}")

    try:
        tokens = json.loads(result.stdout.strip())
        if not isinstance(tokens, list) or not all(
            isinstance(token, int) and not isinstance(token, bool) and token >= 0 for token in tokens
        ):
            raise ValueError("token IDs must be non-negative integers")
        return tokens
    except (json.JSONDecodeError, ValueError) as exc:
        raise OverthinkingPenaltyError(
            f"Unexpected llama-tokenize output for {word!r}: {result.stdout.strip()[-500:]}"
        ) from exc


def resolve_overthinking_logit_bias(
    meta: dict[str, Any],
    model_path: Path,
    model_path_for_binary: str,
    tokenizer_path: Path,
    *,
    timeout: int = 60,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> tuple[str, dict[str, list[int]]]:
    """Resolve one --logit-bias value and cache per-word tokenization results.

    Only entries that tokenize to exactly one token are penalized. Multi-token
    entries are returned separately so the caller can warn without partially
    penalizing a phrase. The numeric bias is deliberately excluded from the
    cache signature so changing it does not rerun llama-tokenize.
    """
    words = normalize_overthinking_words(meta.get("overthinking_penalty_words", DEFAULT_OVERTHINKING_WORDS))
    if not words:
        raise OverthinkingPenaltyError("Overthinking penalty is enabled but its word list is empty")
    bias = _format_bias(meta.get("overthinking_penalty_bias", -2.0))
    signature = _cache_signature(model_path, tokenizer_path, words)

    cache = meta.get("overthinking_penalty_cache")
    if not isinstance(cache, dict) or cache.get("signature") != signature:
        token_map = {
            word: _tokenize_word(tokenizer_path, model_path_for_binary, word, timeout, runner)
            for word in words
        }
        cache = {"signature": signature, "tokens": token_map}

    token_map = cache.get("tokens")
    if not isinstance(token_map, dict):
        raise OverthinkingPenaltyError("Cached overthinking penalty tokens are invalid")

    token_ids: list[int] = []
    seen_ids: set[int] = set()
    skipped: dict[str, list[int]] = {}
    for word in words:
        tokens = token_map.get(word)
        if not isinstance(tokens, list):
            raise OverthinkingPenaltyError(f"Cached tokenization is missing {word!r}")
        if len(tokens) == 1:
            token_id = tokens[0]
            if token_id not in seen_ids:
                token_ids.append(token_id)
                seen_ids.add(token_id)
        else:
            skipped[word] = tokens

    if not token_ids:
        raise OverthinkingPenaltyError(
            "No overthinking penalty entries resolve to one token; edit the list to remove multi-token entries"
        )

    # Serialize each token/bias pair as a separate --logit-bias flag.
    # llama.cpp parses --logit-bias as a single TOKEN_ID±BIAS per flag occurrence.
    # Comma-separated values in one flag are silently ignored after the first entry.
    # Zero bias values are omitted entirely since llama.cpp requires explicit sign (+0/-0).
    # The bias string from _format_bias already includes the sign (e.g., "-2", "-2.0").
    bias_value = float(bias)
    if bias_value == 0:
        logit_bias = ""
    else:
        # bias already has the sign (e.g., "-2"), so use it directly
        logit_bias = "\n".join(f"--logit-bias {token_id}{bias}" for token_id in token_ids)

    meta["overthinking_penalty_cache"] = cache
    meta["logit_bias"] = logit_bias
    return logit_bias, skipped

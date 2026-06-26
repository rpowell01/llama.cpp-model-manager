import logging
import requests
from config import DEFAULTS
from processes import get_server_host

log = logging.getLogger("llamaweb")

def _fetch_metrics_from_model(port: int) -> dict[str, float | None]:
    """Fetch performance metrics from a running llama-server's /metrics endpoint.

    Returns a dict with keys 'pp', 'tg', 'pg' (or None if not available).
    """
    host = get_server_host()
    url = f"http://{host}:{port}/metrics"
    result: dict[str, float | None] = {"pp": None, "tg": None, "pg": None}
    try:
        resp = requests.get(url, timeout=0.5)
        if resp.status_code != 200:
            return result
        text = resp.text
        # Parse Prometheus text format
        # We'll collect raw values by metric name (strip labels)
        values: dict[str, float] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Format: metric_name{labels...} value timestamp?
            # We only want the first two tokens
            parts = line.split()
            if len(parts) < 2:
                continue
            name_full = parts[0]
            # Strip label suffix if present (everything from first '{' to matching '}')
            name = name_full.split("{", 1)[0]
            try:
                val = float(parts[1])
                values[name] = val
            except ValueError:
                continue
        # Map to expected fields
        # llama-server uses colon-separated names; support both separators
        # pp: prompt processing time (ms). llama-server gives: llamacpp:prompt_seconds_total
        prompt_ms = None
        if "llamacpp_prompt_processing_time_ms" in values:
            prompt_ms = values["llamacpp_prompt_processing_time_ms"]
        elif "llamacpp:prompt_seconds_total" in values:
            prompt_ms = values["llamacpp:prompt_seconds_total"] * 1000.0
        if prompt_ms is not None:
            result["pp"] = round(prompt_ms, 2)

        # tg: tokens per second (generation throughput)
        # llama-server gives total time and total tokens; we compute the rate
        gen_tokens = None
        gen_seconds = None
        # Accept both underscore and colon metric names for tokens
        if "llamacpp_generated_tokens_total" in values:
            gen_tokens = values["llamacpp_generated_tokens_total"]
        elif "llamacpp:predicted_tokens_total" in values:
            gen_tokens = values["llamacpp:predicted_tokens_total"]
        # Accept both possible names for generation time
        if "llamacpp_tokens_per_second" in values:
            # Some builds may still expose a direct tokens_per_second metric
            result["tg"] = round(values["llamacpp_tokens_per_second"], 2)
        elif "llamacpp:tokens_predicted_seconds_total" in values:
            gen_seconds = values["llamacpp:tokens_predicted_seconds_total"]
        # Compute tg if we have tokens and seconds
        if "tg" not in result and gen_tokens is not None and gen_seconds and gen_seconds > 0:
            result["tg"] = round(gen_tokens / gen_seconds, 2)

        # pg: combined total tokens (prompt + generated)
        prompt_tokens = values.get("llamacpp_prompt_tokens_total")
        if prompt_tokens is None and "llamacpp:prompt_tokens_total" in values:
            prompt_tokens = values["llamacpp:prompt_tokens_total"]
        if prompt_tokens is not None and gen_tokens is not None:
            result["pg"] = int(prompt_tokens + gen_tokens)
        elif prompt_tokens is not None:
            result["pg"] = int(prompt_tokens)
        elif gen_tokens is not None:
            result["pg"] = int(gen_tokens)
    except Exception as e:
        log.debug(f"Failed to fetch metrics for port {port}: {e}")
    return result

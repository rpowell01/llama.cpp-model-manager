import logging
import requests
from config import DEFAULTS
from processes import get_server_host

log = logging.getLogger("llamaweb")

def _parse_metrics_text(text: str) -> dict[str, float]:
    """Parse Prometheus text-format metrics into a ``{name: value}`` dict.

    This is the shared metrics parsing/validation logic used by both
    :func:`_fetch_metrics_from_model` (the metrics tooltip/status path) and
    the metrics exporter, so that fetching and parsing happen in one place
    instead of being duplicated.

    Args:
        text: Raw Prometheus text payload from a llama-server ``/metrics``
            endpoint.

    Returns:
        A dict mapping metric name (label suffix stripped) to its float value.
        Comment lines, blank lines, and lines that are not parseable as
        ``name value`` are skipped.
    """
    values: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: metric_name{labels...} value [timestamp]
        parts = line.split()
        if len(parts) < 2:
            continue
        name_full = parts[0]
        # Strip label suffix if present (everything from first '{' to matching '}')
        name = name_full.split("{", 1)[0]
        try:
            values[name] = float(parts[1])
        except ValueError:
            continue
    return values


def _fetch_metrics_from_model(port: int) -> dict[str, float | None]:
    """Fetch performance metrics from a running llama-server's /metrics endpoint.

    Returns a dict with keys ``'pp'``, ``'tg'``, ``'pg'`` (or ``None`` if not
    available) plus every raw counter parsed from the endpoint. Exposing the
    raw counters lets consumers such as the metrics exporter build full CSV
    rows from a single shared fetch instead of re-parsing the endpoint
    themselves. ``pp``/``tg``/``pg`` remain available for the status/tooltip
    path.

    On any fetch or parse failure the derived keys are returned as ``None``
    and no raw counters are present.

    In WSL+Windows mode the server may be reachable via the Windows host IP
    (returned by :func:`get_server_host`) or via ``127.0.0.1`` (WSL localhost
    forwarding). Both are tried so the fetch succeeds regardless of which
    interface the client connects through.
    """
    result: dict[str, float | None] = {"pp": None, "tg": None, "pg": None}

    # Determine hosts to try. get_server_host() returns the Windows host IP in
    # WSL+Windows mode, but 127.0.0.1 may also work via WSL localhost
    # forwarding. Try both so the metrics fetch is not dependent on a single
    # interface being reachable.
    host = get_server_host()
    hosts_to_try = [host]
    if host != "127.0.0.1":
        hosts_to_try.append("127.0.0.1")

    for try_host in hosts_to_try:
        url = f"http://{try_host}:{port}/metrics"
        try:
            resp = requests.get(url, timeout=0.5)
            if resp.status_code != 200:
                continue
            values = _parse_metrics_text(resp.text)

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
            # Compute tg if we have tokens and seconds (only when not already set
            # from a direct tokens_per_second metric).
            if result.get("tg") is None and gen_tokens is not None and gen_seconds and gen_seconds > 0:
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

            # Expose the raw counters so consumers (e.g. the metrics exporter) can
            # build full metric rows from the same shared fetch instead of
            # re-parsing the endpoint.
            result.update(values)
            return result
        except Exception as e:
            log.debug(f"Failed to fetch metrics from {try_host}:{port}: {e}")
            continue

    return result

# Investigation Report: Existing Scheduling and Metrics Generation

**Bead:** web-mol-999  
**Date:** 2026-10-17  
**Status:** Complete

---

## Executive Summary

**No scheduling mechanism (cron, timer, APScheduler, periodic tasks) exists in the codebase.** Metrics are **pull-based (polled)** — both the backend and frontend fetch metrics on-demand from the llama-server's `/metrics` endpoint. The llama-server does **not push** metrics; it exposes a Prometheus-compatible `/metrics` endpoint that must be scraped.

---

## 1. Scheduling Mechanisms

### Finding: **No cron/scheduler/timer code exists**

Searched for: `cron`, `apscheduler`, `schedule`, `timer`, `periodic`, `background task`

| File | Mechanism Found | Notes |
|------|-----------------|-------|
| `main.py` | None | Standard Flask app.run() |
| `routes/*.py` | None | Request-response only |
| `processes.py` | Threading only | For subprocess output streaming |
| `downloads.py` | Threading only | Background download workers |
| `lifecycle.py` | Threading only | Subprocess output streaming |
| `optimizer.py` | Threading lock only | For concurrent optimizer access |
| `models.py` | Threading lock only | For models cache access |
| `system.py` | Threading only | Shutdown daemon thread |
| `state.py` | Threading locks | For shared state |
| **All tests** | None | No scheduler usage |

**Background tasks used:**
- **Download workers** (`downloads.py:192-196`): `threading.Thread` with `daemon=True` for file downloads
- **Subprocess output streaming** (`lifecycle.py:319-330`, `processes.py`): Daemon threads to read llama-server stdout/stderr
- **SSE log streaming** (`system.py:1122-1153`): Generator-based Server-Sent Events for real-time logs
- **Model startup polling** (`lifecycle.py:350-356`): Synchronous polling loop with `time.sleep(0.5)` for up to 40s

**No:** `apscheduler`, `schedule`, `croniter`, `APScheduler`, `BackgroundScheduler`, `cron` jobs, periodic callbacks, `threading.Timer`, or any recurring task infrastructure.

---

## 2. Metrics Generation & Fetching Architecture

### 2.1 llama-server `/metrics` Endpoint

The llama-server is started with `--metrics` flag (see `processes.py:702`):
```python
cmd += ["--metrics"]  # Line 702
```

This exposes a **Prometheus text-format endpoint** at `http://<host>:<port>/metrics`.

**The endpoint is PULL-only** — llama-server does not push metrics anywhere. It simply serves the current counter/gauge values when requested.

### 2.2 Backend Metrics Fetching (Python)

**File:** `metrics.py` — `_fetch_metrics_from_model(port)` (lines 8-83)

```python
def _fetch_metrics_from_model(port: int) -> dict[str, float | None]:
    host = get_server_host()
    url = f"http://{host}:{port}/metrics"
    resp = requests.get(url, timeout=0.5)
    # Parses Prometheus format, extracts:
    # - pp: prompt processing time (ms)
    # - tg: tokens per second (generation throughput)  
    # - pg: combined total tokens (prompt + generated)
```

**Called from:** `routes/status.py:207-208` in `_status_for_model()`:
```python
if status.startswith("running"):
    metrics = _fetch_metrics_from_model(port)
```

**Also used in:** `routes/models.py`:
- `api_export_metrics()` (line 342) — fetches for CSV export
- `api_get_metrics()` (line 479) — fetches for performance graph modal

**Key characteristics:**
- **Timeout:** 0.5s (status), 5s (export/get)
- **Synchronous:** Blocks the request thread
- **On-demand:** Only called when `/api/status` or metrics endpoints are hit
- **No caching** in backend (but frontend caches for 5s)

### 2.3 Frontend Metrics Fetching (JavaScript)

**File:** `static/js/model-manager.js` — `fetchModelMetrics()` (lines 16-48)

```javascript
async function fetchModelMetrics(modelName, port, bypassCache = false) {
    const cacheKey = `${modelName}:${port}`;
    // 5-second TTL cache
    if (!bypassCache && modelMetricsCache.has(cacheKey)) {
        const cached = modelMetricsCache.get(cacheKey);
        if (now - cached.timestamp < METRICS_CACHE_TTL) {  // 5000ms
            return cached.data;
        }
    }
    const res = await fetch(`http://127.0.0.1:${port}/metrics`, {
        cache: 'no-store',
        signal: AbortSignal.timeout(3000)
    });
    // Parses Prometheus format, calculates derived metrics
}
```

**Cache:** `Map` with **5-second TTL** (`METRICS_CACHE_TTL = 5000`)

**Parsing:** `parsePrometheusMetrics()` (lines 55-74) — regex-based parsing

**Derived metrics calculated:** `calculateDerivedMetrics()` (lines 81-130):
- `prompt_eval_time_ms` = prompt_seconds_total / prompt_tokens_total × 1000
- `prompt_eval_rate` = prompt_tokens_total / prompt_seconds_total
- `eval_time_ms` = tokens_predicted_seconds_total / tokens_predicted_total × 1000
- `tokens_per_sec` = tokens_predicted_total / tokens_predicted_seconds_total
- Speculative decoding: acceptance rate, avg draft length, estimated speedup

### 2.4 Frontend Tooltip Display

**File:** `static/js/ui-controls.js` — `showMetricsTooltip()` (lines 2114-2150)

```javascript
async function showMetricsTooltip(tile, modelName, port) {
    // Creates tooltip DOM element
    await updateTooltipContent(tooltip, modelName, port);
    
    // Auto-refresh every 3 seconds
    metricsTooltipInterval = setInterval(async () => {
        await updateTooltipContent(metricsTooltip, modelName, port, true);  // bypassCache=true
    }, 3000);
}
```

**Update flow:**
1. `showMetricsTooltip()` called on `onmouseenter` (tile hover)
2. `updateTooltipContent()` calls `window.fetchModelMetrics()` 
3. Results formatted by `window.formatMetricsTooltip()` (from model-manager.js)
4. **Auto-refresh:** Every 3 seconds via `setInterval` (bypasses cache)
5. Cleanup on `onmouseleave` via `hideMetricsTooltip()`

---

## 3. Metrics Data Flow Summary

```
┌─────────────────┐     HTTP GET /metrics      ┌──────────────────┐
│  llama-server   │ ◄────────────────────────── │  Client (Python  │
│  (--metrics)    │   Prometheus text format    │   or JS fetch)   │
└─────────────────┘                             └──────────────────┘
       │                                                  │
       │  Counter metrics exposed:                        ▼
       │  - llamacpp:prompt_seconds_total          ┌──────────────────┐
       │  - llamacpp:prompt_tokens_total           │ Backend:         │
       │  - llamacpp:tokens_predicted_seconds_total│ metrics.py       │
       │  - llamacpp:tokens_predicted_total        │ _fetch_metrics_  │
       │  - llamacpp:spec_decode_num_draft_tokens  │ _from_model()    │
       │  - llamacpp:spec_decode_num_accepted_...  │ (0.5s timeout)   │
       │  - llamacpp:spec_decode_num_drafts_total  └────────┬─────────┘
       │                                                   │
       ▼                                                   ▼
┌─────────────────┐                              ┌──────────────────┐
│  Frontend:      │                              │ Frontend:        │
│  model-manager  │                              │ ui-controls.js   │
│  .js            │                              │ showMetricsTooltip()
│  fetchModelMetrics() ───5s cache──► formatMetricsTooltip()
│  (3s timeout)   │                              │ (3s auto-refresh)│
└─────────────────┘                              └──────────────────┘
```

---

## 4. Key Implications for Automatic Metrics Export

### Current State
- **No automatic/periodic metrics collection** — metrics only fetched on user action
- **No persistent metrics storage** — only exported to CSV on manual request (`/api/export-metrics`)
- **No historical metrics** — each fetch returns current counters only

### For Automatic Export Feature
To implement automatic metrics export, you would need to add:

1. **Scheduler infrastructure** — e.g., `APScheduler` BackgroundScheduler
2. **Background job** — Periodic task that:
   - Iterates running models
   - Calls `_fetch_metrics_from_model(port)` for each
   - Appends to per-model CSV (like `api_export_metrics` does)
3. **Configuration** — Interval, retention, enabled/disabled
4. **Graceful shutdown** — Stop scheduler on app exit

### Example Implementation Sketch
```python
# New file: scheduler.py
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler(daemon=True)

def collect_metrics_job():
    models = get_models()
    for name, meta in models.items():
        if check_port_active(meta.get("port", 0)):
            metrics = _fetch_metrics_from_model(meta["port"])
            append_to_csv(name, metrics)  # Reuse logic from routes/models.py

def start_metrics_collection(interval_seconds=30):
    scheduler.add_job(collect_metrics_job, 'interval', seconds=interval_seconds)
    scheduler.start()

def stop_metrics_collection():
    scheduler.shutdown()
```

---

## 5. Files Referenced

| File | Purpose |
|------|---------|
| `metrics.py` | Backend metrics fetching (`_fetch_metrics_from_model`) |
| `static/js/model-manager.js` | Frontend metrics fetch, parsing, caching, formatting |
| `static/js/ui-controls.js` | Tooltip display with 3s auto-refresh |
| `routes/status.py` | `/api/status` — includes metrics in model status |
| `routes/models.py` | `/api/export-metrics`, `/api/get-metrics` endpoints |
| `routes/system.py` | Hardware detection, benchmark optimization |
| `routes/lifecycle.py` | Model start/stop, process management |
| `processes.py` | Process lifecycle, `--metrics` flag in build_cmd |
| `downloads.py` | Background download threads |
| `config.py` | Configuration, defaults |
| `main.py` | Flask app entry point |

---

## 6. Conclusions & Recommendations

1. **No scheduler exists** — Must add one (APScheduler recommended) for automatic metrics export
2. **Metrics are pull-based** — Both backend and frontend poll `/metrics` endpoint
3. **Frontend has caching** — 5s TTL in JS, but backend has no caching
4. **Tooltip auto-refreshes** — Every 3s while hovering (bypasses cache)
5. **Export is manual** — `/api/export-metrics` writes CSV on-demand
6. **llama-server pushes nothing** — It only serves `/metrics` when scraped

**Recommendation:** For the parent epic (web-mol-b0g), implement a lightweight `APScheduler`-based background collector that runs every 30-60s, collects metrics from running models, and appends to CSV files. This integrates cleanly with existing `metrics.py` and `routes/models.py` export logic.
# web-cl2.5: End-to-End Startup Blocking Test

## Test Objective
Verify that chat requests are blocked with a 503 error while a model is starting, and that the UI correctly hides the chat button and shows a "starting" status.

## Test Setup
- Running `app.py` on localhost:5000
- At least one model file exists in `models/` directory
- No model currently running

## Test Steps

### 1. Start a Slow Model
```bash
# Choose a large model that takes >10 seconds to load
# Example (adjust based on available models):
curl -X POST "http://localhost:5000/api/start" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama-3.1-70b","cache_type":"f16"}'
```

### 2. Immediately Observe Status (0-5 seconds)
- **Expected**: Model tile shows "● starting" (yellow chip)
- **Expected**: Chat button is NOT visible
- **Expected**: Status endpoint returns `{"status": "starting"}`

```bash
curl "http://localhost:5000/api/status" | jq '.models[] | select(.model=="llama-3.1-70b")'
```

### 3. Attempt Chat During Startup (5-10 seconds)
```bash
curl -X POST "http://localhost:5000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama-3.1-70b","messages":[{"role":"user","content":"test"}]}' \
  -v
```

- **Expected**: HTTP 503 response
- **Expected**: JSON body: `{"error":"Model is starting","status":"starting"}`
- **Expected**: Frontend (if testing via UI) shows no chat button, no error modal

### 4. Wait for Model to Finish Starting (~30-60 seconds)
- **Expected**: Status chip automatically changes to "● running" (green)
- **Expected**: Chat button appears on the tile
- **Expected**: `/api/status` returns `{"status": "running"}` for the model

### 5. Verify Chat Works After Startup
```bash
curl -X POST "http://localhost:5000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"model":"llama-3.1-70b","messages":[{"role":"user","content":"Hello"}]}' \
  -v
```

- **Expected**: HTTP 200 response
- **Expected**: Response contains `choices` array with generated text

## Automated Check Script (optional)

```python
#!/usr/bin/env python3
import time
import requests

BASE = "http://localhost:5000"
MODEL = "llama-3.1-70b"

def test_startup_blocking():
    # Start model
    resp = requests.post(f"{BASE}/api/start", json={"model": MODEL})
    assert resp.status_code == 200, f"Start failed: {resp.text}"
    print("✓ Model start initiated")

    # Poll status until no longer starting
    start_time = time.time()
    starting_seen = False
    running_seen = False
    
    while time.time() - start_time < 120:
        status_resp = requests.get(f"{BASE}/api/status")
        data = status_resp.json()
        model_status = next((m for m in data["models"] if m["model"] == MODEL), None)
        if not model_status:
            print("✗ Model not found in status")
            continue
        
        status = model_status["status"]
        print(f"  Status: {status}")
        
        if status == "starting":
            starting_seen = True
            # Try chat - should fail
            chat_resp = requests.post(f"{BASE}/api/chat", 
                json={"model": MODEL, "messages": [{"role":"user","content":"test"}]})
            assert chat_resp.status_code == 503, f"Expected 503, got {chat_resp.status_code}"
            assert chat_resp.json().get("status") == "starting"
            print("✓ Chat correctly blocked during startup")
        elif status == "running":
            running_seen = True
            # Try chat - should succeed
            chat_resp = requests.post(f"{BASE}/api/chat",
                json={"model": MODEL, "messages": [{"role":"user","content":"test"}]})
            assert chat_resp.status_code == 200, f"Expected 200, got {chat_resp.status_code}"
            print("✓ Chat succeeded after startup")
            break
        time.sleep(2)
    
    assert starting_seen, "Never observed 'starting' status"
    assert running_seen, "Never observed 'running' status"
    print("✓ All checks passed")

if __name__ == "__main__":
    test_startup_blocking()
```

## Acceptance Criteria
- [ ] Chat button hidden while model status is "starting"
- [ ] `/api/chat` returns 503 with `{"status":"starting"}` during startup
- [ ] Status endpoint shows "starting" state
- [ ] After startup, chat button appears
- [ ] After startup, `/api/chat` works normally

## Notes
- The "starting" state is determined by presence of `_start_timestamps[model]` and port not yet active
- The UI auto-refreshes every 3 seconds; status chip updates automatically
- Chat button is rendered conditionally: `running ? ' <button class="chat"...' : ''`
- Race condition protection: `_recent_stops` ensures stopped models don't appear running immediately after stop

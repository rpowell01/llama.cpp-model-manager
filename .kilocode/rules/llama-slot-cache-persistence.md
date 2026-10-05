# llama-server KV Cache Slot Persistence Rule

## Overview
This rule instructs the agent to automatically manage llama-server KV cache slot persistence using the `/slots/<id>/save` and `/slots/<id>/restore` REST API endpoints when `--slot-save-path` is configured.

## Prerequisites
- llama-server started with `--slot-save-path /path/to/cache`
- Server running with at least 1 slot (`-np 1` or higher)
- For SWA models: `--swa-full` flag required

## Save/Restore Workflow

### Before Each Request (Pre-Message Hook)
**MUST** call the restore endpoint before sending any chat completion request:

```bash
# Restore KV cache for this session
curl -sf -X POST \
  "http://<host>:<port>/slots/0/restore" \
  -H "Content-Type: application/json" \
  -d "{\"filename\": \"/path/to/cache/slot_<session_id>.bin\"}"
```

### After Each Response (Post-Message Hook)
**MUST** call the save endpoint after receiving the response:

```bash
# Save updated KV cache for this session
curl -sf -X POST \
  "http://<host>:<port>/slots/0/save" \
  -H "Content-Type: application/json" \
  -d "{\"filename\": \"/path/to/cache/slot_<session_id>.bin\"}"
```

## Agent Instructions

### When User Sends a Message to a Model with Slot Persistence Enabled:

1. **Detect if model has slot persistence:**
   - Check if model config has `slot_save_path` set (non-empty)
   - Check if model is running

2. **Generate session ID:**
   - Use model name + user session identifier (e.g., `modelname_session123`)
   - Sanitize for filesystem: replace spaces with underscores, remove special chars

3. **Before sending the chat completion request:**
   - Call restore endpoint for slot 0
   - Only attempt restore if slot file exists (graceful failure if not)

4. **Send the chat completion request:**
   - Include `"cache_prompt": true` in request body for best results
   - Use standard `/v1/chat/completions` endpoint

5. **After receiving response:**
   - Call save endpoint for slot 0
   - Save the updated KV cache to disk

## Session ID Generation
```javascript
// Generate session ID from model name and conversation context
function generateSessionId(modelName, userId = 'default') {
  const sanitized = modelName.replace(/[^a-zA-Z0-9_-]/g, '_');
  return `${sanitized}_${userId}`;
}
```

## Error Handling
- **Restore fails (file not found):** Log warning, continue without cache (first message in session)
- **Save fails:** Log error, but don't interrupt user experience
- **Network timeout:** Retry once with 5s timeout, then log warning

## Required Model Configuration
For this rule to work, the model must be started with:
```json
{
  "slot_save_path": "/path/to/cache",
  "cache_ram": 8192,
  "swa_full": true (for SWA models like Qwen)
}
```

## Disk Space Considerations
- Slot files can be 1-4+ GB each for long contexts
- Plan for several GB per active session
- Use SSD for cache directory
- Consider implementing cleanup for old/unused session slots

## Integration Points
This rule applies when:
- User sends message via chat interface
- Agent processes message through chat API
- Model has `slot_save_path` configured and is running

## Example API Calls

### Restore (before request):
```bash
curl -X POST "http://localhost:11004/slots/0/restore" \
  -H "Content-Type: application/json" \
  -d '{"filename": "/mnt/c/llama.cpp-sycl/cache/slot_yi-coder-9b-chat-q4-k-m_session1.bin"}'
```

### Save (after response):
```bash
curl -X POST "http://localhost:11004/slots/0/save" \
  -H "Content-Type: application/json" \
  -d '{"filename": "/mnt/c/llama.cpp-sycl/cache/slot_yi-coder-9b-chat-q4-k-m_session1.bin"}'
```

## Notes
- This is a **manual process** - llama-server does NOT auto-save/restore
- The rule must be executed by the orchestration layer (Kilo agent)
- Each conversation/session needs its own slot file
- Slot files can be large (1-4+ GB) - monitor disk space
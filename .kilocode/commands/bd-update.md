---
command: bd-update
description: Update a beads issue (claim, change status, etc.)
---

Update a beads issue.

```bash
# Claim work atomically
bd update <id> --claim

# Change status
bd update <id> --status=in-progress
bd update <id> --status=done

# Change priority
bd update <id> --priority=1

# Change type
bd update <id> --type=bug
```
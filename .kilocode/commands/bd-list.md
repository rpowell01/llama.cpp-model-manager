---
command: bd-list
description: List issues with optional filters
---

List beads issues with optional filters.

```bash
# List all open issues
bd list --status=open

# List all issues
bd list

# List by status
bd list --status=closed
bd list --status=in-progress

# List by type
bd list --type=bug
bd list --type=feature
bd list --type=task

# List by priority
bd list --priority=1
bd list --priority=2
```
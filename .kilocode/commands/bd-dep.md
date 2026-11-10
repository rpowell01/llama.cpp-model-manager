---
command: bd-dep
description: Manage dependencies between beads issues
---

Manage dependencies between beads issues.

```bash
# Add dependency: issue depends on another issue
bd dep add <issue> <depends-on>

# Remove dependency
bd dep remove <issue> <depends-on>

# List dependencies
bd dep list <issue>
```
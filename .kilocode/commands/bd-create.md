---
command: bd-create
description: Create a new beads issue/task/bug
---

Create a new beads issue.

```bash
bd create "title" -t task -p 2
bd create "Fix login bug" -t bug -p 1
bd create "Add new feature" -t feature -p 2
bd create "Update documentation" -t chore -p 3
```

**Options:**
- `-t, --type`: Issue type (bug, feature, task, epic, chore)
- `-p, --priority`: Priority (0=critical, 1=high, 2=medium, 3=low, 4=backlog)
- `-d, --description`: Optional description
---
description: Search LogicMonitor devices (read-only)
---

Run from the kit folder (the one that contains `lm_infosys/`). Replace SEARCH with the hostname or display name the user gave.

```bash
python3 -m lm_infosys devices --query "SEARCH" --size 25
```

If they asked for the full inventory:

```bash
python3 -m lm_infosys devices --size 50
```

Summarize id, status, and name. Read-only. Do not add or delete devices.

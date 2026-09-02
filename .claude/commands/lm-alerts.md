---
description: List current LogicMonitor alerts (read-only)
---

Run from the repository root. Never print API keys.

```bash
python3 -m lm_infosys alerts --size 20
```

Optional filters:

```bash
python3 -m lm_infosys alerts --severity critical --size 10
```

Summarize severity counts and the noisiest resources. Do not acknowledge, clear, or mutate alerts.

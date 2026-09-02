---
description: Verify LogicMonitor API connectivity for this Infosys session
---

Run from the repository root. Never print LM_ACCESS_ID or LM_ACCESS_KEY.

```bash
python3 -m lm_infosys health
```

Summarize portal, device, alert, and collector reachability. If the command exits 2, tell the user which env vars to set (LM_ACCOUNT, LM_ACCESS_ID, LM_ACCESS_KEY) without asking them to paste secrets into chat.

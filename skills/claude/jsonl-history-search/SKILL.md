---
name: jsonl-history-search
description: Search local Codex and Claude JSONL conversation logs first for prior user instructions, past decisions, reports, artifacts, or vague references to earlier chats.
---

# JSONL History Search

Use the portable CLI as a bounded transcript retrieval tool.

```bash
jsonl-history-search plan "the thing I told you before" --cwd "$PWD" --format text
jsonl-history-search search "the thing I told you before" --cwd "$PWD" --format markdown
```

The `plan` command is the trust boundary and does not open transcript files. It should show interpreted terms, inferred role, date window, selected roots, rejected roots, caps, warnings, and blind spots.

Do not recursively scan arbitrary home, drive, cache, model, or project roots. If a one-off forensic root is unavoidable, require `--allow-arbitrary-root` with explicit hard caps.

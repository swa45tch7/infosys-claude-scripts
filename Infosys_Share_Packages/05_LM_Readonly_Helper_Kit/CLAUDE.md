# Infosys Claude — LogicMonitor session notes

This folder is a **read-only** LogicMonitor helper kit. Prefer the slash commands in `.claude/commands/`. Do not invent write APIs (add/delete device, ack/clear alert, collector restart).

## Commands

- `python3 -m lm_infosys health`
- `python3 -m lm_infosys alerts [--severity warn|error|critical] [--size N]`
- `python3 -m lm_infosys devices [--query TEXT] [--size N]`
- `python3 -m lm_infosys device IDENTIFIER`
- `python3 -m lm_infosys collectors [--size N]`

## Credentials

Required: `LM_ACCOUNT`, `LM_ACCESS_ID`, `LM_ACCESS_KEY` (optional `LM_DOMAIN`, default `logicmonitor.com`). Load from the environment or a local `.env`. Never print secret values. If they are missing, tell the user which names to set.

## Output

Summarize for the engineer. Quote ids, names, and statuses from command output only.

# Connections

Registry of every system your AIOS can reach. Expanded over time as you wire new tools. `/audit` checks this file for domain coverage and freshness.

| # | Domain | Tool | Mechanism | Auth | Last checked |
|---|---|---|---|---|---|
| 1 | Revenue / Financials | Stripe | mcp | API key (live) | 2026-05-07 |
| 2 | Revenue tracking | Google Sheets (planned) | not yet connected | — | — |
| 3 | Customer interactions | Gmail | mcp | OAuth (Google) | 2026-05-07 |
| 4 | Customer interactions | Instagram DMs | not yet connected | — | — |
| 5 | Calendar | Google Calendar | mcp | OAuth (Google) | 2026-05-07 |
| 6 | Project / task tracking | Notion | mcp | OAuth (Notion) | 2026-05-07 |
| 9 | Project / task tracking | ClickUp | mcp | API key | 2026-05-08 |
| 7 | Meeting intelligence | Fireflies.ai | mcp | API key | 2026-05-07 |
| 8 | Knowledge / files | Notion | mcp | OAuth (Notion) | 2026-05-07 |
| 10 | AI Agent | Hermes Agent (Nous Research) | container on Hostinger VPS | NVIDIA NIM key (was OpenRouter) | 2026-06-17 |
| 11 | Messaging | Telegram (@BaoBei09bot) | via Hermes gateway | bot token | 2026-05-16 |
| 12 | LLM Routing | NVIDIA NIM (DeepSeek V4 Flash, free) | provider `nvidia` in Hermes config | `NVIDIA_API_KEY` | 2026-06-17 |
| 13 | Google Workspace (Hermes) | Gmail + Calendar + Drive + Sheets + Docs + Contacts | `google-workspace` skill on the VPS | OAuth (Google), token in `/opt/data/google_token.json` | 2026-06-17 |

**Mechanism options:** `mcp` (MCP server), `script` (Python/Bash hitting an API, in `scripts/`), `export` (CSV/JSON dump pipeline), `key+ref` (`.env` key + `references/{tool}-api.md` guide), `not yet connected`.

When you wire a new tool, also save `references/{tool}-api.md` capturing endpoints, auth flow, and common queries — researched-once-saved-forever.

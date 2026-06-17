# Hermes Agent Setup Reference

Last working setup: **2026-06-17** (NVIDIA DeepSeek V4 Flash, free, + Telegram, working end-to-end)

---

## Current working config

| Setting | Value |
|---|---|
| **VPS** | Hostinger, Docker container `hermes-agent-5c1k-hermes-agent-1` |
| **Install path** | `/opt/hermes/` (binary, in-container) |
| **Config path** | `/opt/data/config.yaml` (in-container) = **`/docker/hermes-agent-5c1k/data/config.yaml` on host** |
| **Secrets path** | `/opt/data/.env` (in-container) = **`/docker/hermes-agent-5c1k/data/.env` on host** |
| **Provider** | **`nvidia`** (Hermes native provider plugin) |
| **Model** | **`deepseek-ai/deepseek-v4-flash`** (free endpoint) |
| **Base URL** | `https://integrate.api.nvidia.com/v1` (set by the `nvidia` provider, not by `model.base_url`) |
| **API key** | `NVIDIA_API_KEY` in `.env` (the `nvidia` provider reads this) |
| **API mode** | `chat_completions` (OpenAI-compatible) |
| **Messaging** | Telegram — bot `@BaoBei09bot` (display name: Hermes101) |
| **Telegram user ID** | 8720641622 |
| **Cost** | **$0** for inference (free tier, rate-limited ~40 req/min). VPS hosting is the only fixed cost. |

> **History:** was OpenRouter + `anthropic/claude-sonnet-4.6` (2026-05-16), then drifted to OpenRouter free `meta-llama/llama-3.3-70b-instruct:free` (429-throttled), now NVIDIA DeepSeek V4 Flash (2026-06-17). For the model-selection rationale see `decisions/log.md` 2026-06-17.

---

## ⚠️ Gotchas (read before touching anything)

### 1. File ownership — the #1 cause of silent failures
The gateway runs as the **`hermes`** Linux user. The TUI and `./hermes` commands often run as **`root`**. When root edits config files, the gateway can't read them and **silently falls back to default config** — which has no model set → OpenRouter rejects requests with `"No models provided"`.

**After ANY edit to config files as root, fix ownership:**
```bash
chown hermes:hermes /opt/data/config.yaml /opt/data/auth.json /opt/data/.env
chmod 644 /opt/data/config.yaml /opt/data/.env
chmod 600 /opt/data/auth.json
```

How to spot it: `tail /opt/data/logs/gateway.log` → look for `Permission denied` warnings.

### 2. Provider must be configured via the wizard, not piecemeal
`./hermes config set model.provider openrouter` alone leaves `providers: {}` empty in config.yaml. The wizard (`./hermes setup`) populates the full provider block including `base_url` and `api_mode` — both required.

**Always reconfigure providers via `./hermes setup`, not via individual `config set` commands.**

### 3. Anthropic provider in Hermes is broken (as of May 2026)
Sends OpenAI-format requests (`Bearer` + `/chat/completions`) to Anthropic's URL → 400 errors. **Use OpenRouter as the provider instead** — same Claude models, just routed differently.

### 4. Telegram display name ≠ username
Bot can have a display name like "Hermes101" but the username (`@BaoBei09bot`) is what identifies it. Always message via the username, not the display name.

### 5. `pkill -f hermes` kills your terminal session
The Hostinger container terminal is itself a Hermes-spawned process. Killing all hermes processes drops you back to the host shell. Systemd auto-restarts the gateway, so this is fine — just re-enter the container.

### 6. The bot only works while Hermes is running
The agent process and the Telegram gateway are coupled. If `./hermes` exits, the bot stops responding. For 24/7 operation, the container's systemd handles restarts automatically.

### 7. ⚠️ `model.provider` decides the URL — `model.base_url` is IGNORED for named providers
This is the #1 cause of `HTTP 401 Missing Authentication header`. When `model.provider` is a named provider (`openrouter`, `nvidia`, `deepseek`, …), the provider **plugin hardcodes its own base URL** and **ignores the `model.base_url` field in config.yaml**. So `provider: openrouter` + `base_url: https://integrate.api.nvidia.com/v1` sends your NVIDIA key to **openrouter.ai** → 401.

**To point at a different endpoint, change `provider:` — not `base_url:`.** Use the matching native provider name; it supplies the correct URL and reads the correct key env var:

| Provider value | Base URL (automatic) | Key env var |
|---|---|---|
| `nvidia` | `https://integrate.api.nvidia.com/v1` | `NVIDIA_API_KEY` |
| `openrouter` | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `deepseek` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |
| `openai` / `custom` | honors `base_url` (OpenAI-compatible) | `OPENAI_API_KEY` (+ `OPENAI_BASE_URL`) |

Full list of native providers: `ls /opt/hermes/plugins/model-providers/`. Host→provider map: `_URL_TO_PROVIDER` in `/opt/hermes/agent/model_metadata.py`. For a truly custom endpoint with no native plugin, use `provider: openai` (or `custom`) — those honor `base_url`.

### 8. Debug dumps are written ONLY on errors
`/opt/data/sessions/request_dump_*.json` appears only when an API call fails. **No fresh dump after a message = the call succeeded.** Don't read a stale dump and think it's the latest request.

### 9. Live files are on the HOST, not just in-container
The `/opt/data/...` paths are the container's view. On the host they're bind-mounted at **`/docker/hermes-agent-5c1k/data/`**. Editing there + `docker restart hermes-agent-5c1k-hermes-agent-1` reloads config. `chown hermes:hermes` run on the *host* fails ("invalid user") — harmless; the gateway still reads the bind-mounted files.

---

## First-time setup (fresh container)

```bash
./hermes setup
```

Wizard prompts → answers:
1. **Provider** → OpenRouter
2. **API key** → paste `sk-or-v1-...` from openrouter.ai/keys
3. **Model** → `anthropic/claude-sonnet-4.6` (or `anthropic/claude-haiku-4.5` for cheaper)
4. **Terminal backend** → Docker
5. **Persistent filesystem** → yes
6. **CPU cores** → 1
7. **Max iterations** → 90
8. **Tool progress** → all
9. **Session reset** → Inactivity + daily reset
10. **TTS** → Keep current (Edge TTS)

Then wire Telegram:
```bash
./hermes gateway setup
```
- Pick **Telegram**
- Paste bot token from [@BotFather](https://t.me/botfather) (`/newbot`)
- Paste your Telegram user ID from [@userinfobot](https://t.me/userinfobot)

**Fix permissions** (always do this after setup):
```bash
chown hermes:hermes /opt/data/config.yaml /opt/data/auth.json /opt/data/.env
chmod 644 /opt/data/config.yaml /opt/data/.env
chmod 600 /opt/data/auth.json
```

Launch:
```bash
./hermes
```

Test by messaging the bot in Telegram.

---

## Debugging checklist when bot doesn't respond

1. **Is Hermes running?** `ps aux | grep hermes` — should see the gateway process
2. **Permission errors?** `tail -30 /opt/data/logs/gateway.log` → look for `Permission denied` → fix with chown above
3. **Empty model field?** `cat /opt/data/sessions/request_dump_*.json | head -15` → if `"model": ""`, the gateway can't read config.yaml (permissions issue)
4. **Right bot?** Check the bot's **username** (`@BaoBei09bot`), not the display name
5. **Right token?** Compare `cat /opt/data/.env | grep TELEGRAM` against what BotFather gave you

---

## Active API keys (in /opt/data/.env)

| Key | Status | Notes |
|---|---|---|
| `NVIDIA_API_KEY` | ✓ | **Primary LLM provider** (DeepSeek V4 Flash). Free `nvapi-` key. Rotate — exposed in chat 2026-06-17. |
| `OPENROUTER_API_KEY` | ✓ | Now unused for inference; kept as paid fallback (`provider: openrouter`) |
| `ANTHROPIC_API_KEY` | ✓ | Set but unused |
| `FIRECRAWL_API_KEY` | ✓ | Web scraping |
| `TELEGRAM_BOT_TOKEN` | ✓ | `@BaoBei09bot` |

---

## Tools enabled (CLI)

Web Search & Scraping, Browser Automation, Terminal, File Ops, Code Execution, Vision, TTS (Edge), Skills, Todo, Memory, Session Search, Clarify, Delegation, Cron, Cross-Platform Messaging.

**Disabled** (missing keys): Image Generation (`FAL_KEY` or `OPENAI_API_KEY`), Skills Hub (`GITHUB_TOKEN`), Spotify, Home Assistant.

---

## Key commands

```bash
./hermes                  # Start chatting (also starts gateway)
./hermes setup            # Re-run full wizard
./hermes gateway setup    # Configure Telegram/Discord/etc.
./hermes config show      # View current settings
./hermes config edit      # Open config.yaml in nano
./hermes config set <key> <value>   # Set single value (gotcha: needs chown after if run as root)
./hermes config check     # Validate config
tail -30 /opt/data/logs/gateway.log   # First place to look when something breaks
```

---

## Cost notes

**Current (NVIDIA DeepSeek V4 Flash):**
- **Inference: $0.** Free endpoint, no per-token charge, no card on file → NVIDIA can't bill you. Over-use **throttles** (HTTP 429) or hits a quota; it never charges.
- Catch: **rate-limited** (~40 req/min, possible monthly cap). Fine for demo/testing; move to a paid endpoint before a client pushes real lead volume.
- Get a free `nvapi-...` key at [build.nvidia.com](https://build.nvidia.com/models) → "Get API Key".
- Only fixed cost is the Hostinger VPS. Other tools with their own metering: Firecrawl (free allowance), Telegram (free).

**Paid upgrade path (if/when reliability matters):**
- OpenRouter → Anthropic rates: ~$3/$15 per M tokens (Sonnet 4.6), ~$1/$5 (Haiku 4.5). Switch back with `provider: openrouter` + `OPENROUTER_API_KEY` (see gotcha #7). Usage at [openrouter.ai/activity](https://openrouter.ai/activity).
- Or NVIDIA **partner** (paid) endpoints for higher limits on the same DeepSeek model.

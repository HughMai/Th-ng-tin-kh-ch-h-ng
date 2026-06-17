# VPS Access — Hostinger (Hermes box)

How to connect to Hughie's Hostinger VPS and where everything lives.
Last verified live: **2026-06-17**.

> ⚠️ Secrets policy: the root password is **NOT** stored in this file (it's git-tracked).
> It lives in `agents/hermes/.env` → `VPS_root_password` (that file is gitignored & untracked).
> Read it from there at connect time; never paste it into a tracked file.

---

## Connection

| Field | Value |
|---|---|
| **Host / IP** | `187.77.133.39` |
| **User** | `root` |
| **Hostname** | `Hermes` |
| **Auth** | password (in `agents/hermes/.env` → `VPS_root_password`) |
| **Command** | `ssh root@187.77.133.39` |

### How to connect non-interactively from this Windows box
No `sshpass`, `plink`, or `setsid` available. Use OpenSSH's `SSH_ASKPASS_REQUIRE=force`
(works without a TTY). Run via the Bash tool:

```bash
cat > /tmp/askpass.sh <<'EOF'
#!/bin/sh
printf '%s\n' "$SSH_PW"
EOF
chmod +x /tmp/askpass.sh
export SSH_PW='<paste VPS_root_password from agents/hermes/.env>'
export SSH_ASKPASS=/tmp/askpass.sh
export SSH_ASKPASS_REQUIRE=force
ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 \
    -o PreferredAuthentications=password -o PubkeyAuthentication=no \
    root@187.77.133.39 '<command>'
```

> TODO (better): add an SSH key to the box (`~/.ssh/authorized_keys`) so future
> logins are keyed, not password-based. Then rotate the root password.

---

## What's on the box

### Docker containers
| Name | Image | Purpose |
|---|---|---|
| `hermes-agent-5c1k-hermes-agent-1` | `ghcr.io/hostinger/hvps-hermes-agent:latest` | Hermes agent |
| `speed-to-lead-app-1` | `speed-to-lead-app` | Speed-to-lead demo (FastAPI) |
| `speed-to-lead-caddy-1` | `caddy:2` | Reverse proxy / TLS |

### Hermes data dir (on HOST, bind-mounted into the container)
**`/docker/hermes-agent-5c1k/data/`** — this is the real path; the
`/opt/data/...` paths in `hermes-setup.md` are the in-container view.

| Path | What |
|---|---|
| `…/data/config.yaml` | Provider / model / toolset config |
| `…/data/.env` | API keys (OPENROUTER, ANTHROPIC, FIRECRAWL, TELEGRAM, GITHUB) |
| `…/data/sessions/request_dump_*.json` | Ground-truth dump of each outgoing API request (real `model` field) |
| `…/data/logs/gateway.log` | First place to look when the bot breaks |

---

## Live config snapshot (2026-06-17, after NVIDIA swap)

| Setting | Live value |
|---|---|
| **Provider** | `nvidia` (Hermes native provider plugin) |
| **Model** | `deepseek-ai/deepseek-v4-flash` (free) |
| **Base URL** | `https://integrate.api.nvidia.com/v1` (set by the provider) |
| **Key** | `NVIDIA_API_KEY` in `.env` |
| API mode | `chat_completions` |
| **Health** | ✅ Working end-to-end via Telegram, $0 inference. Rate-limited ~40 req/min (free tier). |

> ⚠️ Gotcha that cost ~6 debug cycles: `model.base_url` is IGNORED when `model.provider` is a named provider — change `provider:` (to `nvidia`), not `base_url:`. Full writeup: `references/hermes-setup.md` gotcha #7 + `decisions/log.md` 2026-06-17.
>
> Prior states (for history): OpenRouter + Claude Sonnet 4.6 (2026-05-16) → OpenRouter free Llama-3.3 (429-throttled) → NVIDIA DeepSeek V4 Flash (now).

---

## Quick read-only verification commands

Run these (over SSH) to re-check state without changing anything:

```bash
D=/docker/hermes-agent-5c1k/data
# Provider + model config
grep -iE 'provider|model|base_url|api_mode' $D/config.yaml
# Ground truth: real model on the latest request
ls -t $D/sessions/request_dump_*.json | head -1 | xargs grep '"model"'
# Key names present (values hidden)
grep -oE '^[A-Za-z_]+=' $D/.env
# Recent gateway health
tail -15 $D/logs/gateway.log
```

---

## Safety notes
- Default to **read-only** commands. Confirm with Hughie before editing config or restarting.
- After ANY edit to config files as root, fix ownership (see `hermes-setup.md` gotcha #1):
  `chown hermes:hermes $D/config.yaml $D/.env` etc.
- `pkill -f hermes` can kill the container's own shell — systemd auto-restarts.

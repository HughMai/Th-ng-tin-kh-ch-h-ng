# VPS Access — Hostinger (Hermes box)

How to connect to Hughie's Hostinger VPS and where everything lives.
Last verified live: **2026-06-26**.

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

## Live config snapshot (2026-06-19, after OpenRouter GLM swap)

| Setting | Live value |
|---|---|
| **Provider** | `openrouter` (Hermes native provider plugin) |
| **Model** | `z-ai/glm-4.7-flash` ($0.06/$0.40 per Mtok) |
| **Base URL** | `https://openrouter.ai/api/v1` (set by the provider) |
| **Key** | `OPENROUTER_API_KEY` in `.env` |
| API mode | `chat_completions` |
| **Health** | ✅ Working end-to-end via Telegram. Pay-per-token (cents/day), no free-tier 429s. |

> ⚠️ Gotcha that cost ~6 debug cycles: `model.base_url` is IGNORED when `model.provider` is a named provider — change `provider:`, not `base_url:`. Full writeup: `references/hermes-setup.md` gotcha #7 + `decisions/log.md` 2026-06-17.
>
> ⚠️ The live bot is the Hermes *product* (this VPS config), NOT `agents/hermes/hermes.py` in the repo — that file is unrelated/abandoned.
>
> Prior states (for history): OpenRouter + Claude Sonnet 4.6 (2026-05-16) → OpenRouter free Llama-3.3 (429-throttled) → NVIDIA DeepSeek V4 Flash (2026-06-17) → OpenRouter GLM-4.7-flash (now).

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

## Speed-to-lead live voice snapshot (2026-06-27)

The live speed-to-lead app is deployed at `https://stl.187-77-133-39.sslip.io`
and runs inside Docker service `app` / container `speed-to-lead-app-1`.

Current live Deepgram Voice Agent config verified inside the restarted container:

| Layer | Live value |
|---|---|
| Listen | Deepgram `flux-general-en`, `version=v2`, `eot_threshold=0.65`, `eager_eot_threshold=0.45`, `eot_timeout_ms=1500` |
| Think | Anthropic hosted via Deepgram, `claude-haiku-4-5`, `temperature=0.3` |
| Speak | Deepgram Aura `aura-2-theia-en` (female AU; env `DEEPGRAM_VOICE`; code default `aura-2-hyperion-en`) |
| Pre-connect | Deepgram WebSocket handshake pre-opened during Twilio's ring (env `VOICE_PRECONNECT=1`); latency log carries `preconnect=hit\|miss`. Kill switch: set `0` + recreate |
| Tool policy | `alert_owner` and `book_job` queue during the call; one consolidated owner SMS/Telegram summary sends after call end |
| Call close | Agent asks "Anything else I can help you with?"; if caller says no, short goodbye, wait about 2s, hang up, then flush owner actions |

Verification notes:
- Public `/health` returned `{"status":"ok"}` through Caddy.
- Inside-container `/health` returned `{"status":"ok"}`.
- `/voice/probe?key=$DASHBOARD_TOKEN` showed `Welcome` + `SettingsApplied` + greeting audio. Its JSON `ok:false` is expected until the probe stops looking for the older `Ready` event.
- Last deploy backups: `/opt/speed-to-lead/.bak/20260626-113123/`, `/opt/speed-to-lead/.bak/20260626-113803/`, and `/opt/speed-to-lead/.bak/preconnect/` (2026-06-27).
- `VOICE_DEBUG_EVENTS=0` (left at `1` during 2026-06-26 debugging — turned off 2026-06-27).

---

## Live tenant routing — read the on-box STATE.md first

**Authoritative live state lives on the box at `/opt/speed-to-lead/STATE.md`** — a timestamped state + changelog updated on every deploy. **Read it first next session** (`ssh root@187.77.133.39 'cat /opt/speed-to-lead/STATE.md'`) instead of re-running the full verification suite. The local repo's `tenants/*.json` are NOT authoritative — they go stale vs the box (that's how 224's real owner got misread on 2026-06-27: handoff said dave, box had rapidflow-plumbing).

Snapshot as of 2026-06-27 (always verify against STATE.md):

| Number | Routed to | voice | owner_mobile | quotes | calendar |
|---|---|---|---|---|---|
| `+61468089224` ("224") | **crown-st-auto** (Crown Street Auto) | yes | +61402129328 | never | pending OAuth |
| — | rapidflow-plumbing (demo) | yes | +61426601862 | ranges | yes |
| — | dave (Dave's Electrical, demo) | yes | env `ELECTRICIAN_MOBILE` | never | yes |

Routing = `find_by_number(To) or DEFAULT_TENANT` (`app.py:_resolve_tenant`) — whoever holds `twilio_number` in their tenant config gets the call. Webhook URLs are shared/global (`/twilio/voice`, `/twilio/sms`), so repointing a number is a tenant-config data change + rebuild, **not** a Twilio-console change.

**Convention (do this every deploy):** append a timestamped entry to `/opt/speed-to-lead/STATE.md` and refresh its current-state tables. Include what changed + verification state.

---

## Deploying the speed-to-lead app

⚠️ **`/opt/speed-to-lead` is NOT a git checkout** — it's a plain copy of `product/speed-to-lead-demo/`. `git pull` does nothing there. Deploy by copying source over SSH, then rebuilding:

```bash
# from the local product dir — copy env-agnostic SOURCE only.
# NEVER overwrite the VPS's own .env, ./data (sqlite volume), or ./Caddyfile.
scp voice_server.py voice_engine.py app.py twilio_io.py store.py workflow.py \
    report.py canary.py evals.py goal_loop.py requirements.txt pytest.ini \
    root@187.77.133.39:/opt/speed-to-lead/
# on the box: rebuild (a plain `restart` won't install new deps in requirements.txt)
ssh root@187.77.133.39 'cd /opt/speed-to-lead && docker compose up -d --build app'
```

- **Rebuild, don't restart**, whenever `requirements.txt` changed (`build: .` bakes deps at build time).
- **Tenant configs live only on the box** and can be stale vs local — e.g. `voice_answer` was missing from the VPS `tenants/dave.json` even though local had it. Check tenant flags after a deploy.
- Container is `speed-to-lead-app-1`; secrets are read from `/opt/speed-to-lead/.env` via compose `env_file`.
- Quick health from inside the container: `docker compose exec -T app python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"`. Deepgram check (no call): `GET /voice/probe?key=$DASHBOARD_TOKEN` — a working connection shows `Welcome` + `SettingsApplied` + audio even though the JSON `ok` field reads false (the probe greps for a `Ready` event this API version doesn't send).
- *Future improvement:* make `/opt/speed-to-lead` a sparse/real git checkout of `AIS-OS` so deploys become `git pull && docker compose up -d --build`.

## Safety notes
- Default to **read-only** commands. Confirm with Hughie before editing config or restarting.
- After ANY edit to config files as root, fix ownership (see `hermes-setup.md` gotcha #1):
  `chown hermes:hermes $D/config.yaml $D/.env` etc.
- `pkill -f hermes` can kill the container's own shell — systemd auto-restarts.

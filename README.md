# Nemo

**An open-source personal agent for every device you own.** Nemo does things instead of only answering. It works through shells, files, the web, MCP servers and, when nothing else works, the screen itself. It stops to ask before anything you could not undo. It reaches your other devices and keeps going while you're away. It remembers you in files you can read, edit and roll back.

```
nemo/        the runtime: agent loop, Sentinel safety gate, vault, memory, tools, hands, hub client, local daemon
cloud/       the relay: accounts, one-time-code sign-in, metered model proxy, the device hub, conversation sync
web/         the console: React 18 + Vite + Tailwind v4, mobile-first (built into nemo/static/console)
tests/       offline test suite (MockLLM) incl. end-to-end multi-device tests against a real relay
```

## Quick start

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[all]"           # or just "." for the core
nemo                              # daemon + console on http://127.0.0.1:7860
```

The first run opens **Settings**. Pick a provider (Anthropic, OpenAI, DeepSeek, Bailian, OpenRouter, Gemini, Groq, Moonshot, Ollama, any OpenAI-compatible endpoint, or a relay's free allowance), paste a key and save. The key goes into the encrypted vault, never into `config.json`.

```bash
nemo chat                 # talk in the terminal
nemo run "summarise ~/notes.md"
nemo doctor               # what is set up, what is missing
nemo vault set GITHUB_TOKEN
nemo memory dream         # consolidate memories now
nemo login https://relay.example.com you@example.com
```

## How it works

### The agent loop (`nemo/agent`)
**Think → Gate → Execute → Observe**, repeated:
- Only one turn runs at a time per conversation. A message sent mid-run goes to the inbox and is folded in before the next model call.
- History is pruned to 50 messages, and the cut only lands on a user message, so a tool call is never separated from its result.
- Only the newest 3 screenshots stay as images; older ones become `[Screenshot from step N: WxH]`.
- `<think>…</think>`, `reasoning_content` and Anthropic thinking stream separately from the answer and show up in a collapsible block in the console.
- If a reply is cut off while still thinking, the request is retried once with a larger token ceiling.
- Three identical steps in a row stop the run, so a stuck loop can't keep spending.

### The Sentinel (`nemo/sentinel`)
Every tool call goes through `Sentinel.guard`:

1. Vault placeholders written by the model are refused.
2. The tool assesses its own call. Each tool has a base risk (SAFE / MODERATE / SENSITIVE), and the arguments can raise it:
   - `shell`: `rm -rf`, `sudo`, `mkfs`, `dd`, `curl | sh`, `chmod -R 777` and similar add a warning.
   - `python_execute`: network imports, subprocesses, environment access or paths outside the workspace raise it to SENSITIVE.
   - Phone and computer hands: labels like 确认支付, 转账, 提交订单, pay or checkout add a warning.
3. The policy runs in this order: deny list → `rules` → always-allow / always-ask lists → mode matrix (ask / strict / auto).
4. **Taint:** once a conversation has read private data (recall, files outside the workspace, authenticated fetches), anything that could send data to a host outside the allowlist must ask, even in auto mode.
5. **Warnings:** any call that carries a warning asks every time. No mode or standing grant gets around it.
6. Your answer can cover `once`, `conversation`, `session`, `24h` or `always`. The grant is bound to a `tool:target` key such as `shell:git` or `web_fetch:api.github.com`.
7. Subprocesses get a scrubbed environment (`*KEY*`, `*TOKEN*`, `*SECRET*`, `AWS_*` and the rest are removed).
8. On Linux, `python_execute` runs inside bubblewrap: read-only root, private `/tmp`, no network unless approved.
9. Secret values are redacted from results, and every decision is written to `audit.jsonl`.

### Memory (`nemo/memory`)
- **Profile files:** `SOUL.md`, `USER.md` and `GLOBAL.md` in Markdown.
- **Atomic facts in SQLite:** ranked by rare-word IDF, `ln(1 + (N − n + 0.5)/(n + 0.5))`, over words plus CJK character bigrams.
- **Semantic recall (optional):** embeddings fused with the IDF ranking by reciprocal rank fusion.
- **Nightly dream:** facts with more than 60% word overlap are proposed for merge, rewrite or drop. A proposal that would lose a name or number is rejected, and every change goes into a log you can undo.

### Hands (`nemo/computer`, `nemo/phone`)
- **The ladder of action:** API/CLI/MCP → direct HTTP → browser → screen, in that order.
- **Screen coordinates:** a 0..999 grid, mapped to pixels as `X = ⌊x/999·W⌋`.
- **Actions:** `tap`, `double_tap`, `long_press`, `swipe`, `type`, `key`, `takeover` (hands control to you) and `finish`.
- **Backends:** this computer through mss and pyautogui, an Android phone through adb.
- **The capsule:** the console shows what the hands are doing, with a STOP button.

### Devices (`nemo/hub`, `cloud/`)
Each device keeps a WebSocket open to the relay's `/v1/hub`. Frames (`call`, `result`, `event`, `approve`, `stop`) are routed only within one account, and the relay stamps the sender. Start a message with `@pc …` and the PC's own agent does the task. Its Sentinel's questions come back to the device in your hand, and your answer goes back. Chat text syncs through sequence-numbered pulls. Files and screenshots stay where they were made.

## Running a relay

```bash
pip install -e cloud
export NEMO_CLOUD_SECRET=$(python -c "import secrets;print(secrets.token_hex(32))")
export NEMO_CLOUD_UPSTREAM_BASE_URL=https://api.deepseek.com/v1 NEMO_CLOUD_UPSTREAM_API_KEY=sk-…
nemo-cloud                        # :8787
```

- **Accounts:** an e-mail address (SMTP, or codes printed to the log in development) or a +86 phone number (through `NEMO_CLOUD_SMS_WEBHOOK`). Passwords are optional and hashed with scrypt; repeated wrong attempts lock the account for a while.
- **Allowance:** a lifetime pool plus a daily cap, counted in micro-yuan (1 CNY = 1,000,000 uy) and charged from the usage the upstream reports. A request with its own key (`X-Upstream-Base-Url` and `X-Upstream-Key`) is recorded but not charged.
- **Data:** SQLite in WAL mode with 13 tables. Identifiers are stored as an HMAC hash plus an AES-GCM-encrypted copy.

## Development

```bash
pip install -e ".[all,dev]" -e cloud
pytest                            # offline: MockLLM, fake upstreams, a real local relay for the hub tests
cd web && npm install && npm run dev    # console with hot reload, proxied to a running `nemo`
npm run build                     # writes nemo/static/console
```

# Telephony Gateway — Integration Plan

**Status:** code fixed and tested, awaiting deploy · **Date:** 2026-08-20
**Gateway:** `neuro444/telephony` → `telephony_repo/`
**Brain:** `neuro444/chat_manager` → `chat_manager_repo/`
**Decisions:** main number `+14042071333`, VPS only (no local testing),
supersede the existing agent, keep it minimal.

> **Two repos, two deploys, one HTTP call between them.** The gateway reaches
> the brain at `CHAT_MANAGER_URL` and by no other means. No Plivo import in
> chat_manager; no menu price in the gateway.

---

## How a Phone Call Actually Works

The thing that surprises everyone: **Plivo does not stream audio to you.** It
calls your web server like a website, and your server replies with instructions
written in XML.

```
Caller dials +1 404 207 1333
   │
   ▼
Plivo answers, POSTs to  /voice/answer
   │   gateway asks the brain for a greeting, ElevenLabs speaks it
   ◄── XML: "<Play> this audio, then listen"
   │
Caller: "two samosas"
   │   Plivo transcribes it ITSELF — arrives as Speech="two samosas"
   ▼
POST /voice/turn
   │   1. verify the signature            (reject forgeries)
   │   2. POST the text to chat_manager /chat
   │   3. ElevenLabs turns the reply into speech.mp3
   │   4. act on the flags
   ◄── XML: "<Play> speech.mp3, then listen"   ← loops
       or   "<Play>, then <Hangup/>"           ← call_ended
       or   "<Play>, then <Dial> the manager"  ← Transfer_to_Manager
```

Two consequences worth internalizing:

1. **STT is already done.** Plivo's `<GetInput inputType="speech">` transcribes
   natively. No STT service, no Deepgram, no audio download in the turn loop.
2. **Plivo must reach your server from the public internet over HTTPS.** That is
   why this goes on the VPS behind nginx, and why `PLIVO_PUBLIC_BASE_URL` must
   be the exact public URL — the webhook signature is computed over it.

---

## Answers to the Config Questions

### `GREETING` — removed, and you were right

chat_manager already writes greetings, and greets returning callers **by name**
(`prompts.py:75`). A hardcoded `GREETING="Thanks for calling..."` threw that
away and gave every caller the same robotic line.

The gateway now sends chat_manager the message `"hello"` on answer and speaks
back whatever comes out. The brain's prompt already handles exactly this
(`prompts.py:85`: *"A standalone greeting such as 'hi' or 'hello' means the
caller wants a fresh welcome"*). Configured as `GREETING_PROMPT=hello` — the
*prompt*, not the greeting.

The other three stay. The rule: **keep only the lines needed when the brain
cannot answer.**

| Var | Kept? | Why |
|---|---|---|
| `GREETING` | **removed** | Brain does it better, and by name |
| `REPROMPT` | keep | Caller was silent — no words to send the brain |
| `BRAIN_DOWN_MSG` | keep | Brain is *down*; cannot ask it what to say |
| `TRANSFER_FAILED_MSG` | keep | Infrastructure, as you said — phone-layer concern |

### `SPEECH_HINTS` — optional, but take it

Calls work without it. But Plivo's ASR renders "Gobi Kondattam" as "go be
condiment." Hints are just a word list of what to expect. One command:

```bash
python scripts/generate_hints.py ../chat_manager_repo/menu/menu_flat.json
```

Now yields **153 real menu items**. It previously emitted 47 single letters —
see §1.3.

### `BRAIN_API_KEY` — you invent it, you don't find it

It is a password you make up, stored in two places that must match:

```bash
openssl rand -hex 32          # generate once
```

- `API_KEY=<value>` in **chat_manager**'s `.env` — the brain checks it
- `BRAIN_API_KEY=<value>` in **telephony**'s `.env` — the gateway sends it

Before this change chat_manager checked nothing (§1.1).

### Deepgram — delete it

Plivo transcribes speech itself. One less service, one less key, one less bill.

> **Rotate the Deepgram key** (`b044c…`) at console.deepgram.com — it was
> pasted into chat and should be treated as compromised. Also note it had been
> appended to `telephony_repo/.env.example`, which **is tracked by git**, along
> with your live `PLIVO_AUTH_TOKEN`. I removed that block. It was never
> committed or pushed — GitHub is clean — but credentials belong in `.env`
> (gitignored), never in `.env.example`.

---

## 1. What Was Fixed

All changes are committed to working trees in both repos; **nothing is pushed
or deployed yet.**

### 1.1 chat_manager had no authentication — CRITICAL

`brain/client.py` sent `X-API-Key`; nothing read it. `/callers`, `/sessions`,
`/search` returned **every caller's phone number, unauthenticated** — and the
gateway is about to put this on the public internet.

Added `require_api_key` in `api.py`, guarding `/chat`, `/callers`, `/sessions`,
`/search`, `/stt`, `/tts` and the delete routes. `/health` stays open so
Docker's healthcheck and the gateway probe work. **Auth is a no-op when
`API_KEY` is unset**, so local dev and the existing 165 tests are unaffected.

### 1.2 The greeting is now the brain's
Described above.

### 1.3 `generate_hints.py` silently produced garbage — HIGH

It returned `['C','a','k','e',' ', ...]` — it had iterated the *characters* of
`"Cake World, Alpharetta"`. Your `menu_flat.json` stores items as positional
arrays under `menu_items`, with column names in `menu_item_fields`. Silently
emitting 47 single-letter hints is worse than crashing: it ships. Now reads the
real shape and finds the `name` column by position. **153 items.**

### 1.4 `To_manager` was accepted and dropped — HIGH

`mark_handoff_emitted()` existed and nothing called it. Catering leads vanished.
Added `emit_handoff()` and wired it, recording the caller's own words
(`summary`, `verbatim_user_chat`) so staff read the request verbatim.

`To_manager` (async follow-up) and `Transfer_to_Manager` (live transfer) stay
strictly separate — confusing them either drops a lead or hangs up on a
customer.

### 1.5 Orders could be lost to a TTS outage

Emission ran *after* the `tts_cached()` try/except, so an ElevenLabs failure
returned early and dropped the order. Moved emission **before** TTS: a
completed order must survive a TTS outage. Test: `test_order_survives_a_tts_outage`.

### 1.6 Unescaped XML could drop calls

The TTS-fallback path hand-built `<Dial>` with no escaping. Worse, `escape()`
does not escape quotes — a `"` in any *attribute* silently truncates it.
Added `_attr()` using `quoteattr()`, and every builder is now verified as
parseable XML rather than by string matching.

### 1.7 Smaller fixes
- `purge_expired()` was defined and never called → leaked mp3s from calls that
  never reached `/voice/hangup`. Now runs on answer.
- TTS model was pinned to `eleven_v3` → now `ELEVENLABS_MODEL_ID`, defaulting to
  `eleven_turbo_v2_5` (markedly lower latency, audible on a phone call).
- Duplicate builders collapsed; `speak_*` / `play_*` names now say what they do.

### 1.8 The repo had zero automated tests — now 57

```
tests/test_plivo_xml.py    19   every builder is valid XML; injection; escaping
tests/test_security.py     11   Plivo's documented example reproduced exactly;
                                tampering, multi-token, missing-token → 500
tests/test_turn_flow.py    20   the full flag matrix, stubbed brain and TTS
tests/test_hints.py         7   the real menu shape, Plivo's 500/10k limits
```

Both suites green:

```
chat_manager   177 passed   (165 existing + 12 new auth tests)
telephony       57 passed
```

Notable coverage, each pinning a bug that would otherwise reach a caller:
`test_order_still_emitted_when_the_same_turn_ends_the_call`,
`test_to_manager_does_not_transfer_the_live_call`,
`test_duplicate_turn_does_not_emit_twice`,
`test_brain_down_transfers_to_a_human_never_a_dead_line`,
`test_does_not_iterate_the_restaurant_name`.

### 1.9 Plivo V3 signature incident — use the official SDK

**Production symptom:** the number rang with beeps and then disconnected. DNS,
TLS, nginx, Docker, the Plivo application, and both credentials were correct,
but every real callback ended with:

```text
POST /voice/answer HTTP/1.0 403 Forbidden
POST /voice/fallback HTTP/1.0 403 Forbidden
```

Plivo could reach the gateway; the gateway was rejecting authentic Plivo
requests before returning call-control XML.

The previous working repository
(`/Users/sreekanthgopi/Desktop/Apps/Restaurant/voice-ai-ordering-agent/`) gave
us the proven integration pattern. Its voice answer route did not validate a
signature, but its Plivo WhatsApp integration correctly delegated V3
verification to Plivo's maintained Python SDK:

```python
from plivo.utils import validate_v3_signature

valid = validate_v3_signature(
    request.method,
    public_url,
    nonce,
    auth_token,
    signature,
    form_parameters,
)
```

The new gateway originally reconstructed the HMAC payload manually. That was
the bug. Plivo's canonicalization has non-obvious URL, query-string, POST
parameter, separator, sorting, encoding, nonce, multi-token, and
account/subaccount rules. A hand-built implementation can look correct and
still disagree with the provider. In this incident, separator assumptions for
a POST URL without a query string caused every valid signature to fail.

The final fix (`telephony` commit `8699331`) was deliberately small:

1. Add `plivo>=4.51.0` to `requirements.txt`.
2. Delete the custom signature construction.
3. Call `plivo.utils.validate_v3_signature()` with the exact public webhook
   URL, HTTP method, nonce, auth token, received signature, and form fields.
4. Accept a signature only when the official validator returns true.
5. Keep support for both headers:
   - `X-Plivo-Signature-V3` — associated account/subaccount signature.
   - `X-Plivo-Signature-Ma-V3` — main-account signature.
6. Keep unsigned or invalid webhooks rejected with HTTP `403`.

Behind nginx, verification must use the externally visible URL:

```text
https://voice.neuroheart.ai/voice/answer
```

It must not use Docker's internal URL (`http://...:8080`) or the localhost
proxy target. Therefore the production environment contains:

```dotenv
PLIVO_PUBLIC_BASE_URL=https://voice.neuroheart.ai
```

After switching to the official validator, the same genuine Plivo request
changed from `403 Forbidden` to `200 OK`, while an unsigned `curl` continued to
return `403`.

**Developer rule:** when a webhook provider supplies an official signature
validator, use it. Do not duplicate provider canonicalization unless no
supported validator exists. A failed signature means no valid Plivo XML is
returned, so the caller hears beeping or a disconnect even though networking
and credentials appear healthy.

---

## 2. The Live-System Collision

`https://cakeworld.neuroheart.ai/plivo/answer` **is live right now** and returns
a bidirectional WebSocket streaming agent:

```xml
<Response>
  <Stream bidirectional="true" keepCallAlive="true"
          contentType="audio/x-mulaw;rate=8000">…</Stream>
  <Redirect method="POST">…/plivo/after-stream</Redirect>
</Response>
```

That is not this gateway. Per your decision we **supersede** it. Two facts make
this safe:

1. **The paths do not collide** — the old agent uses `/plivo/*`, this gateway
   uses `/voice/*`. Both can be deployed at once.
2. **Cutover is three fields in the Plivo console**, and rollback is putting
   them back. Nothing is deleted.

So: deploy the gateway alongside, verify it, then repoint. Do **not** stop the
old service until a real call succeeds on the new one.

---

## 3. Plivo Account — Verified Today

Your original question. **Credit is not the blocker.**

| Check | Value |
|---|---|
| Cash credits | **$29.14**, standard prepaid |
| Numbers | `+14042071333` (main), `+16782085441` (spare) — both voice-enabled |
| App | `cakeworld-voice-agent` (id `52430880779282033`) |
| Answer URL | `…/plivo/answer` → **the old agent** |
| Fallback URL | **empty** — see §5 |
| `POST /voice/answer` | **404** — the gateway isn't deployed |

The blocker was never credit. It was that the gateway is not deployed, the
paths don't match, and the brain had no auth.

---

## 4. Before You Deploy — Server Survey

Paste this whole block into the VPS shell and send the output back.

**Every command is read-only** — `ls`, `cat`, `grep`, `git log`, `docker ps`,
`systemctl list-units`, `ss`, `df`. Nothing is written, started, stopped, or
installed. Safe to run on production as-is; read it first if you like.

```bash
echo "===== 1. WHAT IS IN /opt ====="
ls -la /opt/

echo; echo "===== 2. CHAT_MANAGER STATE ====="
cd /opt/chat_manager 2>/dev/null && git log --oneline -3 && git status --short | head

echo; echo "===== 3. RUNNING CONTAINERS ====="
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'

echo; echo "===== 4. DOCKER NETWORKS ====="
docker network ls

echo; echo "===== 5. WHAT SERVES cakeworld.neuroheart.ai ====="
grep -rl "cakeworld" /etc/nginx/sites-enabled/ 2>/dev/null
grep -rhA4 "proxy_pass\|server_name" /etc/nginx/sites-enabled/ 2>/dev/null | head -40

echo; echo "===== 6. SYSTEMD VOICE SERVICES ====="
systemctl list-units --type=service --state=running --no-pager | grep -iE "voice|plivo|cake|agent|chat" || echo "(none)"

echo; echo "===== 7. PORTS IN USE ====="
ss -tlnp 2>/dev/null | grep -E "8000|8001|8080|8002" || echo "(none of 8000/8001/8080/8002)"

echo; echo "===== 8. CERTS ====="
ls /etc/letsencrypt/live/ 2>/dev/null || echo "(no certbot certs)"

echo; echo "===== 9. DOES chat_manager .env HAVE API_KEY ====="
grep -c "^API_KEY=" /opt/chat_manager/.env 2>/dev/null || echo "0 (not set yet)"

echo; echo "===== 10. DISK + DOCKER COMPOSE VERSION ====="
df -h / | tail -1
docker compose version
```

A copy also sits at `Chat_Manager/survey.sh` if you would rather `scp` it up
and run `bash survey.sh`.

### What each section decides

| # | Question it answers | Why it changes the deploy |
|---|---|---|
| 1–2 | Is chat_manager at `/opt/chat_manager`, and how stale? | The repo plan warns the running container is behind `main`; §5 Phase 2 rebuilds it |
| 3–4 | Container names and the compose network | The gateway reaches the brain **by container name on a shared network**, never over a published port |
| 5 | Which nginx file serves `cakeworld.neuroheart.ai`, and its `proxy_pass` | Decides whether `/voice/*` slots in beside the old `/plivo/*` or needs a new server block |
| 6–7 | Where the streaming agent runs, and which ports are taken | Picks the gateway's port and confirms nothing collides |
| 8 | Existing certbot certs | Whether TLS is already covered or certbot must run |
| 9 | Is `API_KEY` set on the server yet | Confirms §5 Phase 2 is still outstanding |
| 10 | Disk headroom and compose version | `docker compose` v2 syntax is assumed throughout |

**The open question this settles:** where the existing streaming agent lives
(container? systemd? which port?) and how nginx routes to it — which determines
whether the gateway slots in beside it on the same domain or takes a new one.

---

## 5. Deploy Steps

Filled in precisely once the survey lands. The shape:

### Phase 1 — Push both repos
Two repos, two commits, two pushes. Never one.

### Phase 2 — Generate and set the shared secret
```bash
openssl rand -hex 32
```
`API_KEY` in `/opt/chat_manager/.env`, the same value as `BRAIN_API_KEY` in the
gateway's `.env`. Then rebuild chat_manager — **the running container is stale**
and `.env` changes do not reach a running container:
```bash
cd /opt/chat_manager && git pull --ff-only
docker compose up -d --build --force-recreate api
curl -s localhost:8000/health                      # open, expect ok
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/callers   # expect 401
curl -s -o /dev/null -w '%{http_code}\n' -H "X-API-Key: $KEY" localhost:8000/callers  # 200
```
That 401 is the fix landing — it is the check that used to be missing.

### Phase 3 — Deploy the gateway
Clone to `/opt/telephony`, `.env` from `.env.example`, generate `SPEECH_HINTS`,
copy `ELEVEN_VOICE` from the brain's `.env` into `ELEVENLABS_VOICE_ID`, set
`PLIVO_PUBLIC_BASE_URL` to the exact public URL. Bound to `127.0.0.1` — never
publish the port. Brain reachable only on the compose network.

### Phase 4 — nginx + TLS
Route `/voice/*` and `/audio/*` to the gateway. Certbot cert. Verify:
```bash
curl https://<host>/health
curl -X POST https://<host>/voice/answer     # expect 403 — no signature
```
**That 403 is success**, not a failure: it proves signature verification is
live. An unsigned request must never be accepted.

### Phase 5 — Point Plivo at it
In the Plivo console, on `cakeworld-voice-agent`:

| Field | Value |
|---|---|
| Answer URL | `https://<host>/voice/answer` (POST) |
| Hangup URL | `https://<host>/voice/hangup` (POST) |
| **Fallback Answer URL** | `https://<host>/voice/fallback` (POST) |

**Fallback is currently empty and is not optional for a restaurant.** If the
gateway is down, the default is a failed call — a customer hearing nothing. The
endpoint already exists and dials the restaurant directly; it just needs wiring.

### Phase 6 — Real call
Call `+14042071333`, with `docker compose logs -f` open. Confirm: greeting plays
and uses your name if you've called before; ASR transcribes menu items (watch
`SpeechConfidenceScore` — the accuracy risk worth measuring early on
Indian-accented items); the order lands once in `/orders/recent`; the manager
transfer rings `+16468753366`; and deliberately *not* answering exercises the
`/voice/transfer_done` fallback.

**If the call fails, roll back by pasting the old URLs into those three fields.**

---

## 6. Open Items

1. **Server survey** (§4) — blocking; everything in §5 firms up once it lands.
2. **Rotate the Deepgram key**, and confirm the Plivo auth token was not exposed
   elsewhere. Delete `DEEPGRAM_API_KEY` — it is unused.
3. **`ELEVEN_VOICE` → `ELEVENLABS_VOICE_ID`** — reuse the brain's chosen
   Indian-accent voice rather than picking a new one.
4. **Confirm `+16468753366`** is the right live-transfer destination before a
   real call rings it.
5. **The old streaming agent** stays running until a real call succeeds on the
   new gateway. Decide what happens to it only after that.

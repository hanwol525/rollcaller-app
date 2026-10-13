# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

RollCaller: an organizer builds a roster, each participant gets a capability-token URL to record
their own name, the app derives IPA from that recording (or from the spelling), and synthesizes
one consistent ceremony voice for walk-across-the-stage playback.

Built fast for a hackathon, now being converted into a maintainable portfolio app.

---

## Working agreement — read this first

**Han controls all pushes, merges and PRs.** Work on a branch, commit locally, then **stop and
report**. Do not `git push`, do not merge, do not open or merge a pull request, do not
force-push, and do not create or delete remote branches — unless explicitly told to in the
current session. "The work is finished" is not permission to push.

**The code wins.** Where any brief, document, comment or this file contradicts what the code
demonstrably does, the code is right. Flag the contradiction explicitly, reproduce it, then
document the truth. Do not silently follow a wrong instruction, and do not silently diverge from
it either. Every significant error in this project has been caught by this rule.

**Work arrives as numbered briefs.** Each brief has a single concern and explicit scope fences.
Respect them: do not fix things outside the current brief, even when you are looking straight at
them — note them for the brief that owns them. One concern per commit.

**Verify a finding before acting on it.** See the reference documents below: not every recorded
defect has been verified, and acting on an unverified one has already cost a wrong brief.

## Reference documents

Both live at the repo root.

**`DEPLOY-NOTES.md`** — operational constraints for any future deploy target. There is
deliberately no deploy target yet. This file **supersedes `AUDIT.md`** wherever they conflict.

**`AUDIT.md`** — the defect map. ⚠️ **It has two tiers of rigor and presents them identically:**

- **§7 (verification ledger)** — 15 claims, each put through an adversarial refutation pass.
  7 of 15 were corrected by it. **Treat §7 as verified fact.**
- **Everything else (the sweep sections)** — never went through that pass, but is written in the
  same confident voice. **Treat as a hypothesis to verify before changing code.**
- **§4.2 and §8.1 are retracted** (retained in place with retraction notices). §8.1 claimed the
  former GKE deploy stack "did not work — four independently verified blockers"; all four were
  later disproven or narrowed, and the stack's last five deploy runs had in fact succeeded.

`AUDIT.md` is currently untracked and gitignored, so it may be **absent in a fresh clone**. If it
is missing, say so rather than guessing at what it said.

## Commands

The venv lives at **`backend/app/venv`** — not `backend/.venv`. Run backend commands from
`backend/`, because `DATABASE_URL` and `env_file` both resolve against the process CWD.

```bash
# Backend (from backend/)
./app/venv/bin/python -m pytest app/tests -q                    # full suite
./app/venv/bin/python -m pytest app/tests -q -k login          # subset, by keyword
./app/venv/bin/python -m pytest \
  app/tests/test_auth_endpoints.py::TestLogin::test_login_success -q   # by node id
./app/venv/bin/uvicorn app.main:app --reload --port 8000

# Frontend (from frontend/)
npm run dev          # Vite dev server on 5173 — the only working front end (see below)
npm run build        # adapter-node -> build/index.js
npm run check        # svelte-kit sync && svelte-check
npm test             # vitest     — 12 tests, green
npm run test:e2e     # playwright — needs a live backend on :8000 (see Gotchas)
```

### ⚠️ The only working local setup is `npm run dev` + `uvicorn`, run separately

`docker compose up` does **not** give you a working app, for three independent reasons:

1. The root compose declares `env_file: - .env`, which Compose treats as required-by-default, so
   it errors out unless you first `cp .env.example .env`.
2. The frontend container sets `ENV PORT=3000` / `EXPOSE 3000` (`frontend/Dockerfile:22,24`) but
   compose maps `"5173:5173"` (`docker-compose.yml:53-54`) — nothing is listening on the mapped
   port.
3. Compose has no path proxy, so browser-side `/invite` and `/media` calls 404 there (see
   "The Vite dev proxy is load-bearing" below).

Treat compose as broken-but-unfixed rather than as the quickstart. Fixing it is deferred work.

CI is `.github/workflows/ci.yml` — tests only, no deploy and no secrets. Push and PR run the
backend gate (`pytest -m "not integration"`) and the frontend gate (`npm ci && npm run check &&
npm test`). A third job, `backend-integration`, installs the full ML stack and runs everything
including the `@integration` tests; it fires only on `workflow_dispatch` or the weekly schedule.

There is still **no linter and no formatter** — no ruff/black/eslint/prettier config anywhere.
`svelte-check` is the only style-adjacent gate, and CI now runs it.

## Architecture

### The pronunciation pipeline

`app/pronunciation/orchestrator.py` is a thin façade over four stages; routers call only the
façade. Every stage defines a `warm()`, but **only three of the four self-heal on first use** —
see the known bug below.

| Stage | Module | Does | Lazy self-heal |
|---|---|---|---|
| transcode | `transcode.py` | ffmpeg → canonical 16 kHz mono WAV (browser sends WebM/Opus or MP4/AAC) | ✅ re-probes PATH |
| recognize | `recognize.py` | wav2vec2 CTC (`facebook/wav2vec2-lv-60-espeak-cv-ft`) audio → IPA | ✅ |
| g2p | `g2p.py` → `gemma.py` | LLM over an OpenAI-compatible endpoint; the eSpeak fallback below it is **dead code** | 🔴 **none** |
| tts | `tts.py` | Kokoro 82M, IPA → WAV, using `[name](/ipa/)` pronunciation-override markup | ✅ |

**All audio comes from Kokoro.** eSpeak in this codebase only ever produces IPA *text*, and
currently never even runs. Robotic output means Kokoro is voicing bad IPA — never that eSpeak
synthesized something.

🔴 **Known live bug — `g2p()` returns placeholder pseudo-IPA.** `orchestrator.warm_all()` is
called by nothing in production (the lifespan runs `init_db()` and `seed_organizer()` only), and
unlike the other stages `g2p.espeak_ipa()` has **no lazy self-heal** — it checks
`if _backend is not None` and never calls `warm()`. So `_backend` stays `None` and `g2p("Hannah
Wolfson")` returns `/hannah_wolfson/`, which Kokoro then reads as phonemes. `g2p.warm()` is dead
twice over: it calls `phonemizer.backend.Backend`, which does not exist (the class is
`EspeakBackend`). Fixing the symbol alone changes nothing — `warm_all()` would still never be
called. See `AUDIT.md` §4.1 (§7-verified).

### The IPA invariant

The load-bearing domain rule: a pronunciation's **provenance** matters. `Participant` tracks
`ipa_text` / `ipa_source` / `ipa_confirmed` together, enforced by
`Participant.validate_ipa_invariant()` (`models.py:94`).

⚠️ SQLModel `table=True` models do **not** run `@model_validator` on attribute assignment, so
this is an explicitly-called method, not a validator. Any route that mutates IPA fields must call
it before committing. Current call sites: `routers/ceremony.py:65`, `routers/invite.py:126`,
`routers/invite.py:199`.

### Request flow

`POST /invite/{token}/recording` → transcode → recognize → store blob → `status="recorded"`.
Preview (`/ipa/preview`) returns raw WAV bytes and **deliberately does not persist** — a real
product rule with real tests. `POST /spaces/{id}/render` ("Prep Clips") batch-renders the roster
on a `ThreadPoolExecutor`, with all DB writes and a single commit on the request thread.

Auth is two unrelated schemes: organizer sessions are opaque DB-backed tokens (no JWT, despite
`pyjwt` being pinned), and participants authenticate purely by possessing an invite token.

### Storage

`storage.py` exposes one `StorageBackend` ABC with `filesystem` and `minio` implementations,
selected by `settings.storage_backend` and bound to a **module-level singleton at import time**.
`MinIOStorage.url()` returns `/media/{key}`, so blobs are always proxied through the backend and
MinIO is never exposed. Adding a backend means touching only this file plus the `Literal` in
`config.py` — which is deliberately constrained so an unrecognized `STORAGE_BACKEND` fails loudly
at startup instead of silently falling through to the filesystem.

### Frontend

SvelteKit 2 / Svelte 5 runes / TS strict. Two API clients split by trust boundary:
`$lib/server` (`serverFetch`, cookie-forwarding, used by every `+page.server.ts`) and `$lib/api`
(browser-side, used by the record page and logout).

⚠️ **The Vite dev proxy is load-bearing.** Browser-side calls use *relative* paths (`/auth`,
`/invite/...`, `/media/...`), there are zero `+server.ts` routes, and the backend has no CORS
middleware. Those paths resolve in dev only because `vite.config.ts` proxies them. **Any deploy
therefore needs a reverse proxy routing `/auth`, `/invite` and `/media` to the backend** — the
former GKE setup did this at the nginx ingress. A bare adapter-node deploy with nothing in front
404s on every clip and invite call. Recorded in `DEPLOY-NOTES.md`.

## Gotchas that cost time

- **The local venv is deliberately slim** (Intel Mac — `torch`/`kokoro` have no macOS x86_64
  wheels past certain versions). Expected local state is **111 passed, 16 skipped, 0 failed**
  (127 collected): 12 `@integration` tests skip without `kokoro`, 4 transcode tests skip without
  `ffmpeg`. Treat any *failure* as a regression; the skips are the design.
- **Tests needing the real ML stack are marked `@pytest.mark.integration`** plus a `skipif` on
  `kokoro` being importable. The marker lets CI deselect by intent; the skipif keeps any
  kokoro-less machine from erroring. They run in the `backend-integration` CI job only.
- **Don't let a test depend on a dependency being absent.** The two
  `*_raises_when_pipeline_unavailable` tests used to pass only because `kokoro` wasn't installed,
  and failed the moment it was. They now force the condition themselves — nulling
  `tts._pipeline` is not enough, because `synthesize()` lazily re-warms at `tts.py:70`, so
  `tts.warm` is neutered too.
- **`requirements.txt` is a `pip freeze` at `backend/app/requirements.txt`** — note the path, and
  note it pins both `phonemizer` and `phonemizer-fork` (same import package, non-deterministic
  winner) plus dead deps (`allosaurus`, `pyjwt`) and `pytest` into the production image.
- **`.gitignore` is the GitHub Python template at the root of a polyglot repo.** An unanchored
  `lib/` once silently excluded the whole of `frontend/src/lib/`. Anchor new rules with a leading
  slash; 20+ unanchored directory patterns remain.
- **`shell grep` is a ugrep wrapper that honors `.gitignore`**, so `AUDIT.md` is invisible to
  `grep -r`. Use `git grep` or `command grep`.
- **`GEMMA_*` names a provider-agnostic layer.** The code is a bare OpenAI-compatible
  `/chat/completions` client; the repo variously points it at Gemma 4, `gemma-3-12b-it` and
  GLM-5.2, and the README's model claims contradict the config. Renaming is mapped in
  `AUDIT.md` §2.1 — the landmines are three `monkeypatch.setattr` *string* targets in
  `test_gemma_g2p.py` that fail only at runtime.
- **Recovery from the archived branches is path-scoped only.** `origin/ci-cd-gke-deploy` and the
  `archive/*` tags preserve the pre-consolidation state, but that branch never received the
  Allosaurus -> wav2vec2 migration and still pins `allosaurus==1.0.2` — restore specific paths
  from it, never wholesale. (Check the distance with
  `git rev-list --count archive/ci-cd-gke-deploy..main` rather than trusting a number here; it
  grows with every commit on main.)

## README caveats

The README's §"Running on AMD" and §"Powered by Gemma 4" are hackathon prize-track copy, and
several of its claims are false against the code — it says models warm at startup (they do not),
that an eSpeak floor runs (it does not), that ML models are stubbed in tests (nothing is), and it
documents `SESSION_SECRET` as security-relevant (no code reads it). De-branding is deferred work.
Prefer `DEPLOY-NOTES.md`, then `AUDIT.md` §7, over the README for anything factual.

---

*This file is living. The test/CI section changes as soon as the test-harness brief lands — update
it in the same commit rather than leaving it stale.*
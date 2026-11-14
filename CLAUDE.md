# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

RollCaller: an organizer builds a roster, each participant gets a capability-token URL to record
their own name, the app derives IPA from that recording (or from the spelling), and synthesizes
one consistent ceremony voice for walk-across-the-stage playback.

Built fast for a hackathon, now being converted into a maintainable portfolio app.

---

## Working agreement — read this first

> **Han owns the git history. Claude Code does not write to git — at all.**
>
> Make changes in the working tree and **leave them there, uncommitted and unstaged.** Han
> reviews, stages, commits and pushes.
>
> **Permitted:** `git status`, `git diff`, `git log`, `git show`, `git grep`, `git ls-files`,
> `git rev-list`, `git merge-base`, `git check-ignore`.
>
> **Forbidden** unless Han asks in the current session: `git add`/staging, `commit`, `push`,
> `stash`, `branch`, `switch`/`checkout`, `merge`, `rebase`, `reset`, `tag`, `revert`,
> `cherry-pick`, any PR or release operation, and editing anything under `.git/`.
>
> None of these is permission to commit: the work being finished; wanting it revertable; wanting
> a clean tree; wanting to stage it "so it's easier to review"; a brief using numbered units; a
> previous session having done it. If you believe a commit is necessary, **say so and stop.**
>
> Instead of committing, end each unit with a handoff report.

**The code wins.** Where any brief, document, comment or this file contradicts what the code
demonstrably does, the code is right. Flag the contradiction explicitly, reproduce it, then
document the truth. Do not silently follow a wrong instruction, and do not silently diverge from
it either. Every significant error in this project has been caught by this rule.

**Work arrives as numbered briefs.** Each brief has a single concern and explicit scope fences.
Respect them: do not fix things outside the current brief, even when you are looking straight at
them — note them for the brief that owns them. One concern per unit.

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
npm test             # vitest     — 30 tests (counted, not yet run after the last change)
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

There is **no automated test gate.** Nothing runs the backend or frontend suites on push, on PR
or on a schedule — both suites are local-only, via the commands above. `.github/workflows/ci.yml`
was deleted; if you are looking for the gate it described, it is gone, not broken.

The two workflows that remain are Claude Code integrations and neither runs a test:
`claude-code-review.yml` reviews pull requests (`opened`, `synchronize`, `ready_for_review`,
`reopened`), and `claude.yml` answers an `@claude` mention in an issue, an issue comment, a PR
review or a PR review comment. Both need an `ANTHROPIC_API_KEY` repository secret.

There is still **no linter and no formatter** — no ruff/black/eslint/prettier config anywhere.
`svelte-check` is the only style-adjacent check in the repo, and nothing runs it automatically —
`npm run check` is a manual step.

## Architecture

### The pronunciation pipeline

`app/pronunciation/orchestrator.py` is a thin façade over four stages; routers call only the
façade. Every stage defines a `warm()`, all four self-heal on first use, and
`orchestrator.warm_all()` runs from the FastAPI lifespan (`main.py`).

| Stage | Module | Does | Lazy self-heal |
|---|---|---|---|
| transcode | `transcode.py` | ffmpeg → canonical 16 kHz mono WAV (browser sends WebM/Opus or MP4/AAC) | ✅ re-probes PATH |
| recognize | `recognize.py` | wav2vec2 CTC (`facebook/wav2vec2-lv-60-espeak-cv-ft`) audio → IPA, else `None` + a log line | ✅ |
| g2p | `g2p.py` → `gemma.py` | LLM over an OpenAI-compatible endpoint, with a working eSpeak NG floor beneath it | ✅ per language |
| tts | `tts.py` | Kokoro 82M, IPA → WAV, using `[name](/ipa/)` pronunciation-override markup | ✅ |

**All audio comes from Kokoro.** eSpeak only ever produces IPA *text* — it does run now, but it
never synthesizes. Robotic output means Kokoro is voicing bad IPA, never that eSpeak synthesized
something.

**Bare IPA is the storage format — by convention, not by enforcement.** Every *producer*
(`gemma_ipa`, `espeak_ipa`, `g2p._fallback`) returns IPA with no `/slashes/`, and slashes are Kokoro
markup added only at the TTS boundary. But `IPAConfirmRequest.ipa` is an unvalidated `str` stored
verbatim, so a participant confirming `/ˈælɪs/` in ordinary dictionary notation puts wrappers
straight into the row.

⚠️ So the strip is **load-bearing, and shared** — not "defensive", which this file used to claim.
It lives in `tts.prepare_ipa()` (`_strip_wrappers` then `_normalise_for_kokoro`), which is pure and
idempotent, and `tts._reject_reason` runs it on its own input. That is what makes
`orchestrator.ipa_refusal` and the TTS boundary judge the **same string**: they used to differ, so a
slash-wrapped script narration passed the renderer's check, reached `synthesize`, was refused
*there*, and silently degraded to a clip voiced from the spelling — counted in `rendered` with no
`failures` entry. Enforcing bare IPA on write is still open, and would make the strip redundant
rather than load-bearing.

✅ **Fixed in Brief 03 — the eSpeak floor works.** It used to return placeholder pseudo-IPA:
`warm()` called `phonemizer.backend.Backend`, a symbol phonemizer does not export, the
`AttributeError` was swallowed with no log line, and `espeak_ipa()` had no lazy self-heal — so
`g2p("Hannah Wolfson")` returned `/hannah_wolfson/` and Kokoro read the spelling as phonemes. Now
it returns `hˈænə wˈʊlfsən`.

⚠️ `AUDIT.md` §4.1 still describes the old state as live, and §7 holds a **wrong refutation**
about it — see the corrected row in that ledger. Prefer this file for the g2p path.

When eSpeak genuinely cannot help, the floor returns `""` rather than inventing a pronunciation.
At the TTS boundary that degrades to Kokoro voicing the name with its own G2P — right for a
participant previewing their own recording. **The batch renderer refuses it instead**: that
participant gets no clip and is named in the render response, so the organizer can fix it before
the ceremony rather than hearing it during one. Constructing an `EspeakBackend` costs ~2.3 s, so
failures are cached per language rather than retried per name.

### The IPA invariant

The load-bearing domain rule: a pronunciation's **provenance** matters. `Participant` tracks
`ipa_text` / `ipa_source` / `ipa_confirmed` together, enforced by
`Participant.validate_ipa_invariant()` (`models.py:94`).

⚠️ SQLModel `table=True` models do **not** run `@model_validator` on attribute assignment, so
this is an explicitly-called method, not a validator. Any route that mutates IPA fields must call
it before committing. Call sites, named by function because the line numbers have now gone stale
twice: `routers/invite.py::upload_recording`, `routers/invite.py::confirm_ipa`,
`routers/ceremony.py::render_ceremony` (phase 2).

⚠️ **The invariant is narrower than it looks, and two things it does not catch are load-bearing.**
It couples only `ipa_text` / `ipa_source` / `ipa_confirmed` and says nothing about `status` or
about whether a recording still exists. In particular `(None, None, True)` **passes** — a stale
`ipa_confirmed` beside a null text is legal — so a route that clears IPA must reset all three
itself. Do not try to express either rule as an invariant rule; they are route-level obligations.

### Request flow

`POST /invite/{token}/recording` → transcode → recognize → store blob → `status="recorded"`.
**The blob is stored and the status advances even when recognition yields nothing** — it used to
raise 422 and throw the recording away, which punished the participant for a missing model. A
`recorded` row with `ipa_text=None` is legal, and Prep Clips then takes its g2p branch.

**A recording never replaces a hand-typed pronunciation.** The IPA fields move with provenance, in
both directions, and the route owns the whole table:

| prior `ipa_source` | recognition succeeds | recognition yields nothing |
|---|---|---|
| `recognized` | replaced | **cleared**, all three fields |
| `manual` | **kept** | **kept** |
| `g2p` | replaced | kept |
| `None` | set to `recognized` | stays `None` |

`recognized` is cleared rather than kept because its provenance *was* the blob the same request
deleted — keeping it described a recording that no longer existed, and that is the bug this
replaced. `g2p` survives because it is derived from the spelling, which an upload does not change.
`manual` survives both ways: a take we could not read is no more consent to discard a typed
pronunciation than one we could. The upshot worth relying on: **`ipa_source == "recognized"` means
"derived from the recording currently on file"**, which it did not before.

**An upload that changes or clears `ipa_text` withdraws the participant's clip**, because that clip
voices the old pronunciation. Without this, the roster said Ready and `GET /ceremony` served the
previous take until someone happened to run Prep Clips. The row drops `clip_key` before the commit
and the blob is deleted after it, the same ordering Prep Clips uses. An unchanged `ipa_text` (a kept
`manual` or `g2p` entry, or the same IPA read again) keeps its clip.

⚠️ `manual` is currently unreachable from the shipped UI — `confirmIpa` is called from one place
with `is_edit` hard-coded `false` and the record page has no IPA input — so both halves of that
carve-out are forward-looking. The participant-facing `ParticipantSelf` therefore carries
`ipa_source` (provenance, never the IPA text), because the record page has to distinguish "this
came from your recording" from "this is the pronunciation already on file".
Preview (`/ipa/preview`) returns raw WAV bytes and **deliberately does not persist** — a real
product rule, but its only test (`test_preview_does_not_store`) is `@integration`, so since the
CI gate was deleted it runs nowhere automatically (see Gotchas).

`POST /invite/{token}/ipa/confirm` **refuses unusable IPA with a 422 and writes nothing**, checked
before the first assignment so the row is never partially written. Confirming is a promise that the
pronunciation reaches the ceremony; without the check, `synthesize` accepted it, cleared the
override and voiced the *spelling*, so the participant was told they were confirmed, got a clip,
and only the organizer found out — at Prep Clips, where the renderer refused the very IPA this
route had just accepted. The participant-facing side of that 422 matters: `confirmIpa` unwraps
FastAPI's `detail` and the record page renders it **in place**, because the full-screen `error`
card has no buttons and recording again is the one thing that would help.

`POST /spaces/{id}/render` ("Prep Clips") batch-renders the roster on a `ThreadPoolExecutor` and
**returns a partial-success report** — `{rendered, failures: [{name, reason}]}` — because one
unrenderable name must not discard everyone else's clips. The ordering is load-bearing:
`_render_one` is pure (no ORM writes, since worker threads must not mutate Session-owned
instances), every write and the single commit happen on the request thread, and a superseded clip
is deleted **only after that commit**. So the worst a failure can do is leak a blob; it can never
leave a row pointing at a deleted file. This is the only endpoint in the codebase that reports
per-item failure — there is no other partial-success convention to match.

**Two classes of blob are orphaned, and they share that one post-commit loop.** A clip superseded by
a fresh render, and a clip **withdrawn** from a participant the guard refused. The second matters
because the clip a refused participant holds came from `confirm_ipa`, where `synthesize` degrades
rather than refuses — so it is a reading of their *spelling*, which `GET /ceremony` went on serving
while the render report said they had no pronunciation. Clearing it is strictly safer than a
supersede: the row stops referencing the key before the commit, so even a failed delete leaves no
dangling reference. **Deleting it inline would break the guarantee** — there is no new key to roll
back, so it looks harmless, but a commit failure would then roll `clip_key` back to a blob already
gone. `test_superseded_clips_are_deleted_only_after_the_commit` covers both classes for that reason.

⚠️ **A refusal and a raise are deliberately treated differently.** A refusal is a deterministic
judgement that will repeat, so the stale clip is actively wrong and goes. A raise is an unknown and
usually transient — a Kokoro OOM under four workers — and silence at a ceremony is worse than a clip
that worked on the last run, so a raise keeps its clip. Phase 2 sees both as an absent `outcomes`
entry, so phase 1 carries the distinction forward in a `refused` set. Provenance is untouched either
way, so one more Prep Clips is a full recovery. Deleting on both would be the obvious
simplification; `test_a_render_that_raises_keeps_the_clip_it_already_had` is there to stop it.

⚠️ **Worker purity is not the whole thread-safety story.** It covers the ORM, which is what it was
written for. What it never covered is a C library with global buffers underneath a pure Python
function: `_render_one` calls `g2p` → `espeak_ipa`, espeak-ng returns phonemes from library
globals, and `g2p._backends` deliberately shares one `EspeakBackend` per language. So concurrent
`phonemize()` calls handed each participant another participant's IPA — measured anywhere from
52% to 87% of calls under the 4-worker pool (it is a race; the rate moves with timing and roster
size) — which the renderer then stored with `ipa_source="g2p"`, `ipa_confirmed=True` while
reporting `failures: []`. **`g2p._espeak_lock` is the only lock in the codebase.** It is a
`threading.RLock` because `espeak_ipa` holds it across its lazy `warm()` call, and construction
needs it in its own right: `EspeakAPI.__init__` loads its own copy of the library and calls
`espeak_Initialize`. It deliberately does **not** cover `gemma_ipa`, which is an HTTP call that
must stay parallel. This was dormant until Brief 03 fixed the eSpeak floor — `warm()` used to call
`phonemizer.backend.Backend`, a symbol that package does not export, so `phonemize()` was
unreachable and the pre-existing pool never touched espeak at all.

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

**`clip_blocked_reason` is computed on every read, never stored** — on both `ParticipantRead` and
`CeremonyItem`, via `participants._to_read` and `ceremony.get_ceremony`, both calling the same
`orchestrator.ipa_refusal` the renderer calls. A column would go stale the moment a participant
re-records, could drift from the renderer's own judgement, and would need an `ALTER TABLE` that
`db.py`'s bare `create_all` will not do. It **under-reports on purpose**: it never runs g2p, so a
participant with no stored IPA reads as merely unprepared even if the render would refuse what g2p
gives them — deriving that per row would be an espeak call per name per page load, now serialised
behind `g2p._espeak_lock`. A render that *raised* has no row-local signature either. Under-reporting
is the right direction: the field never claims someone is blocked when they are not. Both read
models test `ipa_text is not None`, not truthiness, so a stored `""` reads as blocked.

It exists because the render report lives in `form`, which is `null` after any reload and after all
five redirecting actions — so the roster's **Clip column is the only durable signal**, and it used
to collapse "refused" and "never prepped" into one `None`. It now has four states: **Ready** /
**Needs attention** (with the reason in `title`) / **Not prepared** (a pronunciation waiting on a
render) / **None**. The ceremony page splits the same way: `notPrepped` for a roster nobody has
prepped, and `nothingUsable` when every clip is missing *and* something was refused — which must
not tell the organizer to press the button that just finished. That second state only became
reachable once the renderer started refusing names outright.

⚠️ **The Vite dev proxy is load-bearing.** Browser-side calls use *relative* paths (`/auth`,
`/invite/...`, `/media/...`), there are zero `+server.ts` routes, and the backend has no CORS
middleware. Those paths resolve in dev only because `vite.config.ts` proxies them. **Any deploy
therefore needs a reverse proxy routing `/auth`, `/invite` and `/media` to the backend** — the
former GKE setup did this at the nginx ingress. A bare adapter-node deploy with nothing in front
404s on every clip and invite call. Recorded in `DEPLOY-NOTES.md`.

## The pronunciation eval harness

`backend/eval/` measures the pronunciation path against a public dataset. It is **not part of the
app**: `backend/Dockerfile` copies only `app/requirements.txt` and `app/`, and the harness carries
its own `eval/requirements.txt` and `eval/pytest.ini`. It imports app code read-only —
`g2p.espeak_ipa`, `g2p.g2p`, `g2p.DEFAULT_LANGUAGE`, `orchestrator.ipa_refusal`,
`tts.prepare_ipa`, `config.settings`, and — in `eval/llm.py` only — four **private** names from
`gemma`: `_SYSTEM`, `_FEWSHOT`, `_sanitize_ipa`, `_THINK_OPEN`. It never touches the DB.
⚠️ Those four privates are what a `gemma.py` refactor will break, and the app's own suite will
not notice.

```bash
# from backend/, into the same venv as the app
./app/venv/bin/python -m pip install -r eval/requirements.txt    # once
./app/venv/bin/python -m eval generate --split test --engines floor,oracle
./app/venv/bin/python -m eval score  --run    eval/runs/<stamp>.jsonl
./app/venv/bin/python -m eval report --scores eval/runs/<stamp>.scores.json
./app/venv/bin/python -m eval paired --run eval/runs/<stamp>.jsonl --engines oracle,today
./app/venv/bin/python -m eval probe               # 1 raw LLM request
./app/venv/bin/python -m eval probe  --levers     # 5 of them; run before any scored LLM run
./app/venv/bin/python -m pytest eval/tests -q     # 332 tests, ~50 s
```

⚠️ **Never run bare `pytest` from `backend/`.** It collects both trees in one process and
`app/tests/conftest.py:19-24` writes `os.environ` at import time, which then leaks into the eval
tests. Always name the tree. `eval/pytest.ini` exists because `app/tests/pytest.ini` sets
`testpaths = .` and is loaded only when its own directory is the target — without it `pytest
eval/tests` gets rootdir `backend` and *no* configfile, so `--strict-markers` is off and a typo'd
marker only raises `PytestUnknownMarkWarning` — the test still passes — instead of failing
collection.

**Three engines, and the generate/score split is load-bearing.** `floor` is `espeak_ipa(name,
"en-us")` — what the app degrades to when no LLM is configured, and what `today` falls back to
per name. It is **not** the shipped path when `GEMMA_*` is set, so a gain measured against it is
not a gain over production. `oracle` is `espeak_ipa` handed the dataset's own language label —
Brief 04's **reference point**, not an upper bound and not a ceiling (the label is the Wiktionary
section a row was mined from, not the name's origin, and a hindsight best-of-two chosen per name
*using the reference* beats it in 6 of 8 languages — though in `fr`, where that margin looks
largest at 52.9%, **95% of it is a scoring artifact**: unstripped `(en)` language-switch markers
that panphon charges for, and excluding those 15 names leaves 9.4%). `today` is `g2p.g2p`, i.e. `gemma_ipa(name) or
espeak_ipa(name)`. ⚠️ Do not read any of this into the `ceiling` **column** of the per-language
error table, which means something unrelated: the cost of saying nothing. `generate` calls engines and writes JSONL; `score` is **pure**, so re-scoring
never re-calls a model and a scoring rule can change for free.

**The unit is the name, not the dataset row.** The corpus attests several pronunciations for some
names — up to **12** for one Spanish name in `test`, mean **1.23** — and an engine emits one, so
each engine is called once per name and scored against the **closest** attested variant: any
attested pronunciation counts as correct. ⚠️ The dataset card's **2.47 is Spanish's mean, not a
maximum and not corpus-wide**; measured here `es` is 2.49 and `vi` 2.62, so Spanish is not even
the top. **2,507** names in both `test` (3,091 rows) and `dev` (3,120 rows); `train` is 20,084
names over 24,841 rows.

**The number is error _per reference segment_, and it is not a rate.** Saying nothing costs a flat
~7.25 per segment (the `ceiling` column); saying too much exceeds that without bound, because
every extra segment is an insertion. **So a figure above `ceiling` is a diagnostic rather than a
worse pronunciation** — in this corpus it is always eSpeak narrating a script it cannot read.
Engines are compared within a language and never across, because dividing by reference length
prices the same error differently in a short name. Every figure is **authenticity against
Wiktionary, not correctness**.

**`score` bootstraps each cell on its own, which is the _marginal_ interval — use `paired` for
any engine-vs-engine claim.** Two overlapping marginal intervals do not mean two engines are
indistinguishable, because both answered the same names. On `test`, `ar`/`oracle` 0.590
[0.389, 0.827] and `ar`/`today` 0.596 [0.422, 0.798] overlap almost entirely, while the paired
difference is −0.006 [−0.250, 0.262] — the same conclusion, far tighter, and for the right
reason. `eval/paired.py` exists because three figures in the first baseline were computed in a
throwaway script and typed in by hand, and one had drifted by the time it was read.

**`today` needs credentials, and a run that calls a model needs Han's approval with the cost
stated first.** `GEMMA_BASE_URL` / `GEMMA_MODEL` / `GEMMA_API_KEY` go in **`backend/.env`** — not
the repo root, because `env_file` resolves against the process CWD. None is committed and
`config.py` defaults all three to `None`, so `today` refuses to start rather than running
half-configured. The eSpeak engines cost nothing and need no approval.

⚠️ **`temperature: 0` does not make the LLM path reproducible, and `gemma.py:268` claims it
does.** That line reads `"temperature": 0,  # deterministic -- prep re-runs are stable`. Measured
against OpenRouter on 2026-11-13, **eight identical requests** for `Malgorzata` returned **three**
distinct strings — `małɡɔʐˈata` ×6, `maɫɡɔʐˈata`, `małɡɔʐˈatə` — so the comment is false as
written. Reproduce with `python -m eval probe --name Malgorzata`, repeated. Across all 13 calls of
that session the output varied in exactly two positions, `ł`~`ɫ` and final `a`~`ə`, and all four
combinations appeared. The likely mechanism is OpenRouter routing the same request to providers
with differing quantisations, but that is **not verified** — `probe` does not record the served
provider.

⚠️ **That variation is not cosmetic: it changes what reaches audio.** `ł` is mapped to `w` by
`gemma.py`'s `_GRAPHEME_MAP`, so `małɡɔʐˈata` survives as `mawɡɔʐˈata`; `ɫ` is *not* in Kokoro's
114-symbol vocabulary and `prepare_ipa` leaves it alone, so `maɫɡɔʐˈata` reaches the model with a
symbol that `kokoro/model.py` then drops silently — the same clip, minus a consonant. (`ʐ` is
out-of-vocabulary in all four variants.) Two consequences: a `today` figure carries a run-to-run
variance component that re-scoring **cannot** remove, so every `today` summary must name the run it
came from; and at the app level a participant's clip can change pronunciation between two Prep Clips
runs with no input change. **The second is fenced** — 04a changes no app behaviour, so the stale
comment and the question of pinning a provider belong to the brief that owns `gemma.py`.

**Every `today` row carries exactly one branch, decided by the wrapper's return value rather than
the logs** — `gemma_ipa` logs nothing on success and its two "unconfigured" warnings are deduped
process-wide, so logs alone cannot classify a row. The four are `llm_accepted` / `llm_rejected` /
`llm_empty` / `llm_error`; an unexplained `None` **raises**, and `runfile.read` refuses a `today`
row with no branch, which is what makes "none unclassified" true by construction. ⚠️ Patch
`app.pronunciation.g2p.gemma_ipa`, never `gemma.gemma_ipa`: `g2p.py:16` binds the name into its
own namespace, so patching the other one records nothing and silently measures the real path.

Summaries are committed to `eval/results/`. `eval/runs/` (raw per-name output) and `eval/.cache/`
(the dataset, CC BY-SA 4.0, pinned at `saraelhark/say-my-name@c7d27b1e`) are gitignored and
reproducible from the provenance header every summary carries.

### What the baseline found — `eval/results/baseline-2026-11-13.md`

✅ **Complete: all three engines, both models, the full `test` split.** The question the harness
exists for is answered — **no, an LLM does not beat simply using the right eSpeak voice, but it
is the only engine that never refuses.** On the paired per-name test, `oracle` beats Gemma 4 31B
in 6 of 8 languages (`ar` and `vi` are ties), Gemma beats `floor` in all 8, and Gemma beats
Qwen3.5 9B in 7 of 8. But `today` voiced **2,507 of 2,507** names against `oracle`'s 2,491 and
`floor`'s 2,482: `oracle` refuses 16 Vietnamese names on tone digits. **So Brief 04's shape (LLM
picks the language, eSpeak phonemizes) is what the numbers support — and it inherits those
refusals unless the guard work ships with it.** Total cost $0.1889.

⚠️ **Three things that bound that answer.** `oracle` is handed the dataset's language label, so
it bounds a *perfect* identifier rather than a real one. The LLM path is **not reproducible** —
35.6% of names return different IPA across identical re-runs, so every `today` figure is one
draw, and the two tie verdicts (`ar` n=57, `vi` n=37) are the cells where that matters most.
And `today` has its own Kokoro-boundary loss: it writes ASCII `g` where IPA wants `ɡ`, which is
out-of-set, so that consonant is deleted from the clip silently.

The eSpeak findings below were generated twice, 16 cells each, and reproduced **bit-for-bit**.

- **`oracle` beats `floor` in all 8 languages**, by 69% (de) to 99% (ar) of the floor's own
  error; the paired 95% interval on the per-name difference excludes zero in all eight. The gain
  from choosing the right voice is real and large. ⚠️ The `de` end is an over-estimate — panphon
  cannot read the `ɚ` en-us eSpeak writes for German `-er`, so the floor is charged a full miss
  for a near-perfect match; re-scoring `ɚ` as **`ɐ`** — the symbol the German references
  themselves use — puts `de` at 62%.
- ⚠️ **The narration guard is script-specific, and Arabic walks straight through it.** All 57
  Arabic names under `floor` are narrated rather than pronounced (`أسماء` → `… ˈæɹəbɪksˈiːn
  ˈæɹəbɪkmˈiːm …`, "Arabic seen, Arabic meem"; all 302 tokens in the cell carry the `ˈæɹəbɪk`
  prefix) and **39 of the 57 are not refused**. The other 18 are not caught by anything about
  narration: they are the names containing **د**, whose narration `ˈæɹəbɪkdˈæl1` carries a stray
  digit — د and ذ are the only two letters in the alphabet that do, so 18/57 is a corpus
  accident, not a rate. **The reason the guard misses the rest is lexical, not positional:** its
  only narration rule is the marker set `{"lˈɛɾɚ", "lˌɛɾɚ"}` (`tts.py:147`), eSpeak's English
  word *letter* — and for the **28 basic Arabic letters** eSpeak uses the letter's own Arabic
  name instead (`ا` → `ˈæɹəbɪkælˈif`, `د` → `ˈæɹəbɪkdˈæl1`), so the marker, and even the bare
  substring `ɛɾɚ`, occur **0 times** in all 57 outputs. Matching it anywhere rather than
  token-finally would still catch 0 of 57; the signal the guard has no rule for is the leading
  script name, which prefixes every token.
  ⚠️ **Do not generalise that to "eSpeak never says *letter* for Arabic" — it does, for any
  character it cannot name**, and there token-finality is exactly what misses it. `ڤ` (VEH) →
  `ˈæɹəbɪklˌɛɾɚsˈɪksˈeɪfˈɔːɹ`, i.e. "Arabic letter six A four", narrating the codepoint U+06A4;
  likewise `ٱ`, tanwīn `ً` and tatweel `ـ`. The marker is present but **mid-token**, so the
  token-final test fails on it. Both halves matter to the fix: relaxing token-finality buys
  nothing on these 57 names and genuinely helps on extended letters.
  Owner: the guard fix, not 04a.
- ⚠️ **eSpeak writes one Vietnamese tone as a vowel, so the guard cannot see it.** *sắc* is
  written **`ɜ`** and never a digit (140 of 144 probed syllables; the 4 misses are illegal rimes
  eSpeak reduces to a glide). The digit test therefore passes `Hiếu` → `hˈiɛɜw`, and `ɜ` **is**
  in Kokoro's 114-symbol set, so unlike the dental diacritics it is not filtered out — the guard
  passes it and a clip is produced. (What that token *sounds* like is untested; nothing in the
  harness renders audio.) ⚠️ Do not read the other direction as settled: *ngang* is not silent in
  general — eSpeak writes the digit `1` for the `âu`/`êu` rimes, so `Châu` → `tʃˈə1w` **is**
  refused, and `â` is a vowel mark rather than a tone mark, so that digit tracks the rime. No
  name in either split has that rime, which is the only reason the corpus looked exceptionless.
  *sắc* is the only tone that never refuses; the 43% rate is this corpus's tone **and rime** mix
  and a real roster's would be higher.
- ⚠️ **Adopting the right-language voice loses phonemes before Kokoro — in two different places,
  only one of which is silent.** `tts.prepare_ipa`'s `_normalise_for_kokoro` folds away every
  combining mark but U+0303, and only what survives that reaches `kokoro/model.py`'s 114-symbol
  filter. Measured per stage over the whole `test` run:
  - **Folded away by `prepare_ipa`:** dental `◌̪` (ar ×6, vi ×10) and one syllabic `◌̩` (de).
  - **Dropped by the 114-set:** `ʕ`×12 `ħ`×7 (ar), `ʑ`×90 (pl), `ɫ`×2 (tr), `ʏ`×1 (de), `ɗ`×2
    and the tone digits (vi), a stray `-` (fr ×2, vi ×1), and `1`×21 in `ar`/`floor` narration.

  ⚠️ **This file used to call all of it "dropped silently by `kokoro/model.py`", which was wrong
  about both the stage and the silence.** The fold **logs** — `tts.py:233`, `"IPA folded to
  Kokoro's vocabulary"`, asserted by `test_synthesize_logs_when_ipa_is_folded`. The vocabulary
  filter is the silent one.

  ⚠️ **Nor is it an `oracle`-only cost.** `de`/`floor` pays **+0.002** (1.830 → 1.832) for that
  one syllabic mark in `Karsten`; every other `floor` cell pays exactly +0.000. Against `oracle`
  it costs back **+0.258** of the **49.375** gained in Arabic — both on the 39 names `ar`
  actually voices — plus +0.059 (pl), +0.096 (tr), +0.034 (vi) and +0.011 (de). `fr`/`oracle`
  loses two hyphens at a measured cost of **+0.000**, because panphon cannot read a hyphen
  either. (`en-us`'s Arabic digit `1` is equally out-of-set, but the guard refuses those names,
  so none of it is voiced.) Small against the gain, but concentrated in the languages the floor
  already fails worst.

## Gotchas that cost time

- **The local venv is deliberately slim, and on this machine it cannot be otherwise** (Intel
  x86_64, macOS 12.7.6). `torch`'s last macOS x86_64 wheel is **2.2.2**; later macOS wheels
  are arm64-only and macOS 14+, and `requirements.txt` pins `torch==2.13.0+cpu`, which has no
  macOS wheel at all. `kokoro` needs `torch`, so **the 11 `@integration` tests can never run on
  this Mac** — they need Linux or an arm64 Mac on macOS 14+. Expected local state is **182
  passed, 15 skipped, 0 failed** (197 collected): 11 skip without `kokoro`, 4 transcode tests
  skip without `ffmpeg`. ⚠️ **With `backend/.env` present it is 181 / 1 failed / 15** — see the
  credentials Gotcha below; that failure is environmental, not a regression. Otherwise treat
  any *failure* as a regression; the skips are the design.
  (Re-measured 2026-11-13, and the whole census in this file was re-derived with it:
  `--collect-only` gives 197, `-m integration` gives 11, and the 15 skips are exactly 11
  `kokoro not installed` plus 4 `ffmpeg not installed`. The previous figures — 158/15/173, "12
  `@integration`", "16 of the 127" — were all stale.)
- ⚠️ **Now that `backend/.env` exists, the app suite makes a real paid API call and one test
  fails.** `app/tests/test_gemma_g2p.py::test_gemma_ipa_returns_none_when_env_unset` does
  `monkeypatch.delenv("GEMMA_*")` and then `Settings()`, but **`delenv` unsets the environment,
  not the dotenv source** — pydantic still reads `backend/.env`, so the settings come back
  populated, `gemma_ipa("Bob")` hits the network and returns `'bɒb'` instead of `None`. Proof:
  with the three vars deleted from `os.environ`, `Settings().gemma_model` is
  `google/gemma-4-31b-it` while `Settings(_env_file=None).gemma_model` is `None`. **So the
  expected local state is now 181 passed / 1 failed / 15 skipped with credentials present, and
  182/15 without** — and the test never tested what it claims on any machine that had a `.env`.
  The fix is `Settings(_env_file=None)`, or monkeypatching the module's `settings` object
  directly. Fenced out of 04a (`backend/app/**`); owner is the brief that owns the test files.
- **In the slim venv a recording produces no IPA at all, and that is now the honest answer.**
  `recognize()` needs `transformers`, which is absent here, so it returns `None` and logs
  `wav2vec2 recognizer unavailable (ModuleNotFoundError: ...)` **once per process**. The recording
  is still stored and the participant still shows `recorded`; Prep Clips falls through to g2p on
  the spelling. **This is a slim-venv condition, not a production one** — `kokoro` depends on
  `transformers`, so any environment that can synthesize can also recognize. It is worth knowing
  because it makes the local app quietly *different from* production rather than broken: every
  clip you hear locally is g2p-derived, never recording-derived, so you cannot evaluate
  recognition quality on this machine at all.
- **`pip install -r app/requirements.txt` does not succeed as written.** The
  `torch==2.13.0+cpu` pin is a local version PyPI cannot serve, so the install needs
  `--extra-index-url https://download.pytorch.org/whl/cpu`. That flag lives in
  `backend/Dockerfile:18` and `README.md:122`, and was also in the deleted `ci.yml`, but it is
  not in the Commands section above.
- **A garbage guard sits at the TTS boundary, and it refuses rather than raises.**
  `tts.py:_reject_reason` rejects IPA that has nothing to voice (empty, wrapper-only like `/ /`,
  or stress marks only — judged with stress removed, because `synthesize` strips it before
  building the override), that contains a digit, that is mostly consecutive English
  letter *names*, or whose token ends in eSpeak's word for "letter" (how it narrates **Han**
  script: 李雷 → `tʃˈaɪniːzlˌɛɾɚ tʃˈaɪniːzlˌɛɾɚ`, "Chinese letter" twice). ⚠️ That last test is
  narrower than "a script it cannot read", in two ways. eSpeak names the 28 basic Arabic letters
  individually and never uses the word, so the marker is simply absent — it catches 0 of 57
  Arabic-script names. And for characters eSpeak *cannot* name (`ڤ`, tanwīn, tatweel) the marker
  **is** emitted but lands mid-token, so token-finality misses it. Both measured in the
  eval-harness section above. On a hit it logs
  and clears the override, so Kokoro voices the plain name via its own G2P. It deliberately does
  **not** raise; the *routes* decide what a refusal means, via
  `orchestrator.ipa_refusal`. **The two that make a durable promise both refuse outright:** the
  batch render names the participant in `failures` and withdraws any clip they held, and
  `/ipa/confirm` returns **422 and writes nothing**. **Preview still degrades** — hearing what
  would happen is the whole point of a preview. (Refuse-don't-raise inside `tts.py` used to be
  justified by `future.result()` being unguarded; that is fixed, but it is still correct, because
  it is what lets each route pick its own policy.) Both route-level checks and `synthesize` judge
  the identical string, because `_reject_reason` runs `tts.prepare_ipa` on its own input. Two
  non-obvious constraints:
  letter-name IPA is *correct* for initials, so the run
  must exceed the name's single-character words or "J R R Tolkien" is rejected; and the "letter"
  check is token-**final**, or `Letterman` → `lˈɛɾɚmən` is rejected. The guard was built against
  `recognize._fallback`'s fake `rec044`, which reached Kokoro as `[Alice Lee](/rec044/)` through
  the batch render with no client involvement; that `_fallback` is now deleted, so what the guard
  actually earns its place against is **real** output — a mis-scripted name from the recognizer or
  a stray gemma response.
- **Kokoro's phoneme vocabulary is a closed set of 114 symbols, and anything outside it is
  dropped silently.** `kokoro/model.py` does
  `filter(lambda i: i is not None, map(lambda p: self.vocab.get(p), phonemes))` — no unknown
  token, no error. So an unrenderable symbol does not degrade a clip, it **deletes a sound from
  it**. The set lives in the model's `config.json` on Hugging Face. What matters at the IPA
  boundary: the only combining mark in it is **U+0303** (nasalisation); the only precomposed
  Latin letters are **`ç æ ø œ`**; ASCII **`.`** (the IPA syllable separator) is in it; and every
  accented Latin letter — `á à â ä å ã é è ê ë í ì î ï ó ò ô ö õ ú ù û ü ñ ý ÿ š ž ł` — is
  **not**. `tts.py`'s `_normalise_for_kokoro` folds for this and `gemma.py`'s `_GRAPHEME_MAP`
  maps the leaked graphemes to phonemes the set contains. Both encode a config fetched at
  runtime, so `test_kokoro_constants_match_published_vocabulary` is what catches drift — it
  needs only `huggingface_hub`, so it is unmarked and does run here.
- **Tests needing the real ML stack are marked `@pytest.mark.integration`** plus a `skipif` on
  `kokoro` being importable. The marker lets you deselect by intent (`-m "not integration"`);
  the skipif keeps any kokoro-less machine from erroring.
- **15 of the 197 backend tests now run nowhere automatically** — both skipped groups, not just
  the marked ones. The 11 `@integration` tests need `kokoro`, which this venv does not have (see
  the slim-venv Gotcha above), so exercising them takes a machine with the full ML stack. The
  other 4 are `test_transcode.py`'s: they carry **no** marker, so the deleted push gate ran them
  after its explicit `Install ffmpeg` step. Their guard is only
  `skipif(shutil.which("ffmpeg") is None)`, so putting ffmpeg on `PATH` recovers all 4 locally —
  unlike `kokoro`, nothing about the platform is in the way.
- **What those 11 covered is wider than TTS.** They are 3 `TestRenderCeremony` tests, 1
  `TestGetCeremony` test, the 5 `TestPreviewIPA`/`TestConfirmIPA` tests,
  `test_render_keeps_g2p_source_when_gemma_unavailable`, and `test_warm_all_does_not_raise`. So what now runs nowhere also includes every route-level
  `ipa_source` transition except `recognized`-on-upload, the preview-does-not-persist guard
  (`test_preview_does_not_store` is its only test), and the only automated call to
  `warm_all()` — the surface of the known g2p bug above. The *model-level* invariant is
  unaffected: `TestParticipantIPAVariant` exercises `validate_ipa_invariant()` in 12 unmarked
  tests.
- **With no test gate, every brief's "measure before/after" step is entirely manual** — those
  numbers exist only if they are run and recorded by hand.
- **Three in-tree comments still name the deleted "push gate."** `app/tests/pytest.ini:9` (the
  `integration` marker description, which `pytest --markers` prints, so the suite advertises a
  gate the repo no longer has), `app/tests/test_models.py:529` (**"deselecting the other 11" is
  off by one** — that test is itself `@integration` and there are 11 in total, so the others
  number 10; the gate it says deselected them is also gone), and `app/tests/_deps.py:6` (justifies `find_spec` over a real import partly by
  that CI job installing the full pinned requirements — now moot; only the slim-local-venv half
  of the rationale still applies). Prose only, nothing breaks. All three were fenced out of the
  brief that deleted the gate, so they belong to the brief that owns the test files. Note when
  searching: `grep -i "push gate"` misses the hyphenated spelling in `_deps.py` — use
  `push.gate`.
- **Don't let a test depend on a dependency being absent.** The two
  `*_raises_when_pipeline_unavailable` tests used to pass only because `kokoro` wasn't installed,
  and failed the moment it was. They now force the condition themselves — nulling
  `tts._pipeline` is not enough, because `synthesize()` lazily re-warms at `tts.py:70`, so
  `tts.warm` is neutered too. The two recognizer-unavailable tests in `test_models.py` force
  theirs with `monkeypatch.setitem(sys.modules, "transformers", None)`, which makes the import
  raise `ModuleNotFoundError` even where `transformers` is installed.
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

*This file is living. When a brief changes how the repo actually behaves, update this file in
the same unit rather than leaving it stale.*

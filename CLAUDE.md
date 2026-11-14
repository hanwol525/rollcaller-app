# CLAUDE.md

RollCaller: an organizer builds a roster, each participant gets a capability-token URL to record their
own name, the app derives IPA from that recording (or from the spelling), and synthesizes one
consistent ceremony voice for stage playback. Hackathon build, now becoming a maintainable portfolio app.

## Working agreement

**The code wins.** Where any brief, document, comment or this file contradicts what the code
demonstrably does, the code is right. Flag the contradiction, reproduce it, then document the truth —
never silently follow a wrong instruction, never silently diverge from one. Every significant error in
this project has been caught by this rule.

**Work arrives as numbered briefs**, each with one concern and explicit scope fences. Do not fix
things outside the current brief even when looking straight at them — note them for the brief that
owns them.

**Verify a finding before acting on it.** Not every recorded defect has been verified, and acting on
an unverified one has already cost a wrong brief.

**Git:** normal use is fine — commit, stage and push when asked. Three things stay off the table
because they cannot be undone: force-push, rewriting published history (`rebase`, `reset --hard`,
`commit --amend` on pushed commits), and pushing to `main` or any protected branch. Branch first if
you are on `main`.

## Reference documents

- **`DEPLOY-NOTES.md`** (tracked) — deploy constraints; there is deliberately no deploy target yet.
  **Supersedes `AUDIT.md`** on conflict. Owns the compose gaps and the archive-tag recovery table.
- **`backend/eval/CLAUDE.md`** — the pronunciation eval harness; loads on demand when you touch that
  tree. **`backend/eval/results/baseline-2026-11-13.md`** (tracked) — every measured number.
- **`AUDIT.md`** — the defect map, ⚠️ **two tiers of rigor presented identically.** §7 (verification
  ledger) survived an adversarial refutation pass that corrected 7 of its 15 claims: **treat §7 as
  fact.** Everything else never had that pass but is written in the same confident voice: **treat as
  a hypothesis to verify.** §4.2 and item 1 of §8 are retracted in place. ⚠️ §4.1 still describes the
  old broken eSpeak floor as live, and §7 holds a **wrong refutation** about it — prefer this file for
  g2p. `AUDIT.md` is gitignored, so it may be **absent in a fresh clone**; if so, say so rather than
  guessing what it said.

## Commands

The venv is **`backend/app/venv`** — not `backend/.venv`. Run backend commands from `backend/`:
`DATABASE_URL` and `env_file` both resolve against the process CWD.

```bash
# Backend (from backend/)
./app/venv/bin/python -m pytest app/tests -q                 # full suite
./app/venv/bin/python -m pytest app/tests -q -k login        # subset by keyword
./app/venv/bin/uvicorn app.main:app --reload --port 8000

# Frontend (from frontend/)
npm run dev          # Vite on 5173 — the only working front end (see below)
npm run build        # adapter-node -> build/index.js
npm run check        # svelte-kit sync && svelte-check
npm test             # vitest — 30 tests
npm run test:e2e     # playwright — 2 tests, needs a live backend on :8000
```

⚠️ **Never run bare `pytest` from `backend/`** — it collects both trees in one process and
`app/tests/conftest.py:19-24` writes `os.environ` at import time, which leaks into the eval tests.
Always name the tree.

⚠️ **`pip install -r app/requirements.txt` fails as written.** `torch==2.13.0+cpu` is a local version
PyPI cannot serve; it needs `--extra-index-url https://download.pytorch.org/whl/cpu`
(`backend/Dockerfile:18`, `README.md:122`).

### The only working local setup is `npm run dev` + `uvicorn`, run separately

`docker compose up` does **not** give a working app, for three independent reasons: the backend
service declares `env_file: - .env` (`docker-compose.yml:20-21`), required-by-default, so it errors
out unless you `cp .env.example .env` first; `frontend/Dockerfile` serves on 3000 (`EXPOSE` `:22`,
`ENV PORT` `:24`) while compose maps `"5173:5173"` (`:53-54`); and compose has no path proxy, so
browser-side `/invite` and `/media` 404. Broken-but-unfixed, not the quickstart — `DEPLOY-NOTES.md`
§"Running it locally" has the rest.

**There is no automated test gate.** Nothing runs either suite on push, PR or schedule — both are
local-only. `ci.yml` was deleted; it is gone, not broken. So every brief's "measure before/after" is
manual, and those numbers exist only if run and recorded by hand.

The two remaining workflows are Claude Code integrations and neither runs a test:
`claude-code-review.yml` reviews PRs, `claude.yml` answers an `@claude` mention. Both need an
`ANTHROPIC_API_KEY` secret and both now declare `contents`/`pull-requests`/`issues` at **read**.
⚠️ **Claude's GitHub App has no workflow write access**, so a change touching `.github/workflows/**`
cannot be pushed from CI regardless of what a workflow grants — that is a local-session job.

**No linter, no formatter** anywhere — no ruff/black/eslint/prettier/biome config, and nothing in
`package.json` or a `pyproject.toml`. `svelte-check` is the only style-adjacent check and `npm run
check` is manual.

## Architecture

### The pronunciation pipeline

`app/pronunciation/orchestrator.py` is a thin façade over four stages; routers call only the façade.
Every stage defines `warm()`, all four self-heal on first use, and `orchestrator.warm_all()` runs from
the FastAPI lifespan (`main.py:39`).

| Stage | Module | Does | Self-heal |
|---|---|---|---|
| transcode | `transcode.py` | ffmpeg → canonical 16 kHz mono WAV (browser sends WebM/Opus or MP4/AAC) | ✅ re-probes PATH |
| recognize | `recognize.py` | wav2vec2 CTC (`facebook/wav2vec2-lv-60-espeak-cv-ft`) audio → IPA, else `None` + a log line | ✅ |
| g2p | `g2p.py` → `gemma.py` | LLM over an OpenAI-compatible endpoint, with a working eSpeak NG floor beneath | ✅ per language |
| tts | `tts.py` | Kokoro 82M, IPA → WAV, via `[name](/ipa/)` override markup | ✅ |

**All audio comes from Kokoro.** eSpeak only ever produces IPA *text*. Robotic output means Kokoro is
voicing bad IPA, never that eSpeak synthesized something.

The eSpeak floor works — `g2p("Hannah Wolfson")` → `hˈænə wˈʊlfsən`. Where it genuinely cannot help it
returns `""` rather than inventing a pronunciation; at the TTS boundary that degrades to Kokoro's own
G2P (right for a participant previewing their own recording), while **the batch renderer refuses it**
so the organizer finds out before the ceremony rather than during one. An `EspeakBackend` costs ~2.3 s
to build, so failures are cached per language.

**Bare IPA is the storage format — by convention, not enforcement.** Every producer (`gemma_ipa`,
`espeak_ipa`, `g2p._fallback`) returns IPA with no `/slashes/`; slashes are Kokoro markup added only at
the TTS boundary. But `IPAConfirmRequest.ipa` is an unvalidated `str` stored verbatim, so a participant
confirming `/ˈælɪs/` puts wrappers straight into the row. ⚠️ **So the strip is load-bearing and
shared:** `tts.prepare_ipa()` (`_strip_wrappers` then `_normalise_for_kokoro`) is pure and idempotent,
and `tts._reject_reason` runs it on its own input — which is what makes `orchestrator.ipa_refusal` and
the TTS boundary judge the **same string**. They used to differ, so a slash-wrapped narration passed the
renderer's check and was refused later at `synthesize`, degrading to a clip voiced from the spelling and
counted in `rendered` with no `failures` entry. Enforcing bare IPA on write is still open.

### The IPA invariant

A pronunciation's **provenance** matters. `Participant` tracks `ipa_text` / `ipa_source` /
`ipa_confirmed` together, enforced by `Participant.validate_ipa_invariant()` (`models.py:94`).

⚠️ SQLModel `table=True` models do **not** run `@model_validator` on attribute assignment, so this is an
explicitly-called method, not a validator. Any route mutating IPA fields must call it before committing.
Call sites, named by function because line numbers have gone stale twice:
`routers/invite.py::upload_recording`, `routers/invite.py::confirm_ipa`,
`routers/ceremony.py::render_ceremony` (phase 2).

⚠️ **It is narrower than it looks, and two gaps are load-bearing.** It couples only those three fields
and says nothing about `status` or whether a recording still exists. `(None, None, True)` **passes** — a
stale `ipa_confirmed` beside a null text is legal — so a route that clears IPA must reset all three
itself (`invite.py:158-160` says so in code). Do not try to express either as an invariant rule; they
are route-level obligations.

### Request flow

`POST /invite/{token}/recording` → transcode → recognize → store blob → `status="recorded"`. **The blob
is stored and the status advances even when recognition yields nothing**, rather than punishing the
participant for a missing model. A `recorded` row with `ipa_text=None` is legal; Prep Clips then takes
its g2p branch.

**A recording never replaces a hand-typed pronunciation.** The fields move with provenance in both
directions, and the route owns the whole table:

| prior `ipa_source` | recognition succeeds | recognition yields nothing |
|---|---|---|
| `recognized` | replaced | **cleared**, all three fields |
| `manual` | **kept** | **kept** |
| `g2p` | replaced | kept |
| `None` | set to `recognized` | stays `None` |

`recognized` is cleared because its provenance *was* the blob the same request deleted — keeping it
described a recording that no longer existed. `g2p` survives because it derives from the spelling, which
an upload does not change. `manual` survives both ways: a take we could not read is no more consent to
discard a typed pronunciation than one we could. The upshot to rely on: **`ipa_source == "recognized"`
means "derived from the recording currently on file."**

**An upload that changes or clears `ipa_text` withdraws the participant's clip**, because that clip
voices the old pronunciation; otherwise the roster says Ready and `GET /ceremony` serves the previous
take until someone runs Prep Clips. The row drops `clip_key` before the commit and the blob is deleted
after it. An unchanged `ipa_text` keeps its clip.

⚠️ `manual` is unreachable from the shipped UI — `confirmIpa` is called from one place with `is_edit`
hard-coded `false` and the record page has no IPA input — so both halves of that carve-out are
forward-looking. `ParticipantSelf` carries `ipa_source` *and* `ipa_text`, but the record page **never
displays the IPA**: `ipa_text` is only a truthiness gate and the baton sent back to preview/confirm,
and only `ipa_source` drives visible copy, because the page must distinguish "this came from your
recording" from "this is the pronunciation already on file".

`/ipa/preview` returns raw WAV bytes and **deliberately does not persist** — a real product rule whose
only test is `@integration`, so it runs nowhere automatically.

`POST /invite/{token}/ipa/confirm` **refuses unusable IPA with a 422 and writes nothing**, checked
before the first assignment so the row is never partially written. Confirming promises the pronunciation
reaches the ceremony; without the check `synthesize` accepted it, cleared the override and voiced the
*spelling*, so the participant was told they were confirmed and only the organizer found out — at Prep
Clips, where the renderer refused the very IPA this route had accepted. `confirmIpa` unwraps FastAPI's
`detail` and the record page renders it **in place**, because the full-screen `error` card has no
buttons and recording again is the one thing that would help.

`POST /spaces/{id}/render` ("Prep Clips") batch-renders on a `ThreadPoolExecutor` and **returns a
partial-success report** — `{rendered, failures: [{name, reason}]}` — because one unrenderable name must
not discard everyone else's clips. **The ordering is load-bearing:** `_render_one` is pure (worker
threads must not mutate Session-owned instances), every write and the single commit happen on the
request thread, and a superseded clip is deleted **only after that commit**. So the worst a failure can
do is leak a blob; it can never leave a row pointing at a deleted file. This is the only endpoint that
reports per-item failure — there is no other partial-success convention to match.

**Two classes of blob are orphaned and share that one post-commit loop:** a clip superseded by a fresh
render, and a clip **withdrawn** from a participant the guard refused. The second matters because a
refused participant's clip came from `confirm_ipa`, where `synthesize` degrades rather than refuses — so
it is a reading of their *spelling* that `GET /ceremony` went on serving while the render report said
they had no pronunciation. Clearing it is safer than a supersede: the row stops referencing the key
before the commit, so even a failed delete leaves no dangling reference. **Deleting it inline would
break the guarantee** — there is no new key to roll back, so a commit failure would roll `clip_key` back
to a blob already gone. `test_superseded_clips_are_deleted_only_after_the_commit` covers both.

⚠️ **A refusal and a raise are deliberately different.** A refusal is a deterministic judgement that
will repeat, so the stale clip is actively wrong and goes. A raise is unknown and usually transient — a
Kokoro OOM under four workers — and silence at a ceremony is worse than a clip that worked last run, so
a raise **keeps** its clip. Phase 2 sees both as an absent `outcomes` entry, so phase 1 carries the
distinction in a `refused` set. Provenance is untouched either way, so one more Prep Clips fully
recovers. Deleting on both is the obvious simplification;
`test_a_render_that_raises_keeps_the_clip_it_already_had` stops it.

⚠️ **Worker purity is not the whole thread-safety story.** It covers the ORM. What it never covered is a
C library with global buffers under a pure Python function: `_render_one` calls `g2p` → `espeak_ipa`,
espeak-ng returns phonemes from library globals, and `g2p._backends` deliberately shares one
`EspeakBackend` per language. Concurrent `phonemize()` calls handed each participant another's IPA —
most calls under the 4-worker pool, a race whose rate moves with timing and roster size — which the
renderer stored as `ipa_source="g2p"`, `ipa_confirmed=True` while reporting `failures: []`.
**`g2p._espeak_lock` (`g2p.py:61`) is the only lock in `backend/app` production code.** It is an
`RLock` because `espeak_ipa` holds it across its lazy `warm()`, and construction needs it in its own
right (`EspeakAPI.__init__` loads its own copy of the library and calls `espeak_Initialize`). It
deliberately does **not** cover `gemma_ipa`, an HTTP call that must stay parallel.

Auth is two unrelated schemes: organizer sessions are opaque DB-backed tokens (no JWT, despite `pyjwt`
being pinned), and participants authenticate purely by possessing an invite token.

### Storage

`storage.py` exposes one `StorageBackend` interface — a plain class whose methods raise
`NotImplementedError`, **not** an `abc.ABC` — with `filesystem` and `minio` implementations, selected by
`settings.storage_backend` and bound to a **module-level singleton at import time** (`:125`).
`MinIOStorage.url()` returns `/media/{key}`, so blobs are always proxied and MinIO is never exposed.
Adding a backend touches only this file plus the `Literal` in `config.py:24`, deliberately constrained
so an unrecognized `STORAGE_BACKEND` fails loudly at startup instead of falling through to the filesystem.

### Frontend

SvelteKit 2 / Svelte 5 runes / TS strict. Two API clients split by trust boundary: `$lib/server`
(`serverFetch`, cookie-forwarding, every `+page.server.ts`) and `$lib/api` (browser-side, the record
page and logout).

**`clip_blocked_reason` is computed on every read, never stored** — on both `ParticipantRead` and
`CeremonyItem`, via `participants._to_read` and `ceremony.get_ceremony`, both calling the same
`orchestrator.ipa_refusal` the renderer calls. A column would go stale the moment a participant
re-records, could drift from the renderer's judgement, and would need an `ALTER TABLE` that `db.py`'s
bare `create_all` will not do. It **under-reports on purpose**: it never runs g2p, so a participant with
no stored IPA reads as merely unprepared even if the render would refuse what g2p gives them — deriving
that per row would be an espeak call per name per page load, now serialised behind `g2p._espeak_lock`. A
render that *raised* has no row-local signature either. Under-reporting is the right direction: the
field never claims someone is blocked when they are not. Both read models test `ipa_text is not None`,
not truthiness, so a stored `""` reads as blocked.

It exists because the render report lives in `form`, which is `null` after any reload and after all five
redirecting actions — so the roster's **Clip column is the only durable signal**, and it used to
collapse "refused" and "never prepped" into one `None`. Four states now: **Ready** / **Needs attention**
(reason in `title`) / **Not prepared** (a pronunciation waiting on a render) / **None**. The ceremony
page splits the same way: `notPrepped` for a roster nobody has prepped, `nothingUsable` when every clip
is missing *and* something was refused — which must not tell the organizer to press the button that just
finished.

⚠️ **The Vite dev proxy is load-bearing.** Browser-side calls use *relative* paths (`/auth`, `/invite`,
`/media`), there are zero `+server.ts` routes, and the backend has no CORS middleware. Those paths
resolve in dev only because `vite.config.ts:11-29` proxies them. **Any deploy therefore needs a reverse
proxy routing `/auth`, `/invite` and `/media` to the backend** — see `DEPLOY-NOTES.md` §6.

## Gotchas that cost time

- **The venv is deliberately slim, and on this machine it cannot be otherwise** (Intel x86_64, macOS
  12.7.6). `torch`'s last macOS x86_64 wheel is **2.2.2**; later ones are arm64-only and macOS 14+, and
  the pin is `torch==2.13.0+cpu`, which has no macOS wheel at all. `kokoro` needs `torch`, so the 11
  `@integration` tests **can never run on this Mac** — they need Linux, an arm64 Mac on macOS 14+, or
  Windows x86_64. Expected local state is **182 passed / 15 skipped / 0 failed** (197 collected): 11
  skip without `kokoro`, 4 transcode tests without `ffmpeg`. Holds whether or not `backend/.env` exists.
  **Treat any *failure* as a regression; the skips are the design.**
- **Those 15 run nowhere automatically, and cover more than TTS.** The 11 are 3 `TestRenderCeremony`, 1
  `TestGetCeremony`, the 5 `TestPreviewIPA`/`TestConfirmIPA`,
  `test_render_keeps_g2p_source_when_gemma_unavailable` and `test_warm_all_does_not_raise` — so every
  route-level `ipa_source` transition except `recognized`-on-upload, the preview-does-not-persist guard,
  and the only automated `warm_all()` call. The *model-level* invariant is unaffected:
  `TestParticipantIPAVariant` covers `validate_ipa_invariant()` in 12 unmarked tests. The other 4 are
  `test_transcode.py`'s, carrying **no** marker — only `skipif(shutil.which("ffmpeg") is None)` — so
  ffmpeg on `PATH` recovers all 4 locally.
- ⚠️ **`monkeypatch.delenv` unsets the environment, not the dotenv _source_.** A bare `Settings()` still
  reads `backend/.env`, so any test needing a setting unset must pass **`_env_file=None`** (or explicit
  `None` kwargs, the idiom at `test_gemma_g2p.py:475,496`). `delenv` alone is not enough and the failure
  is a silent live **paid** API call, not an error — one test used to get real credentials and `'bɒb'`
  back instead of `None`. Sites that `setenv` first are safe for the opposite reason: the environment
  outranks the file.
- **In the slim venv a recording produces no IPA at all.** `recognize()` needs `transformers`, absent
  here, so it returns `None` and logs once per process; the recording is still stored and the
  participant still shows `recorded`. **A slim-venv condition, not a production one** — `kokoro` depends
  on `transformers`, so anything that can synthesize can also recognize. It matters because every clip
  you hear locally is g2p-derived, never recording-derived: you cannot evaluate recognition here at all.
- **A garbage guard sits at the TTS boundary, and it refuses rather than raises.** `tts._reject_reason`
  rejects IPA with nothing to voice (empty, wrapper-only, or stress-only — judged with stress removed),
  containing a digit, mostly consecutive English letter *names*, or whose token ends in eSpeak's word
  for "letter". On a hit it logs and clears the override, so Kokoro voices the plain name via its own
  G2P. It deliberately does **not** raise; the *routes* decide what a refusal means via
  `orchestrator.ipa_refusal`. **The two that make a durable promise both refuse outright:** the batch
  render names the participant in `failures` and withdraws any clip they held, and `/ipa/confirm`
  returns 422 and writes nothing. **Preview still degrades** — hearing what would happen is the point of
  a preview. Two non-obvious constraints: letter-name IPA is *correct* for initials, so the run must
  exceed the name's single-character words or "J R R Tolkien" is rejected; and the "letter" check is
  token-**final**, or `Letterman` → `lˈɛɾɚmən` is rejected. ⚠️ That test misses Arabic-script names
  entirely — measured in `backend/eval/CLAUDE.md`. Owner: the guard-fix brief.
- **Kokoro's phoneme vocabulary is a closed set of 114 symbols; anything outside it is dropped
  silently.** `kokoro/model.py` filters unknowns with no error, so an unrenderable symbol does not
  degrade a clip, it **deletes a sound from it**. At the IPA boundary: the only combining mark in the set
  is **U+0303**; the only precomposed Latin letters are **`ç æ ø œ`**; ASCII **`.`** is in it; every
  accented Latin letter is **not**. `_normalise_for_kokoro` folds for this and **logs** when it does
  (`tts.py:234`, asserted by `test_synthesize_logs_when_ipa_is_folded`) — the vocabulary filter is the
  silent one, not the fold — while `gemma.py`'s `_GRAPHEME_MAP` maps leaked graphemes into the set. Both
  encode a config fetched at runtime, so `test_kokoro_constants_match_published_vocabulary` catches
  drift; it needs only `huggingface_hub`, so it does run here.
- **Tests needing the real ML stack are `@pytest.mark.integration`** plus a `skipif` on `kokoro` being
  importable: the marker deselects by intent, the skipif keeps a kokoro-less machine from erroring.
- **Don't let a test depend on a dependency being absent.** The two `*_raises_when_pipeline_unavailable`
  tests used to pass only because `kokoro` wasn't installed. They now force the condition — nulling
  `tts._pipeline` is not enough, since `synthesize()` lazily re-warms, so `tts.warm` is neutered too.
  The recognizer pair forces theirs with `monkeypatch.setitem(sys.modules, "transformers", None)`.
- **`requirements.txt` is a `pip freeze` at `backend/app/requirements.txt`** — note the path. It pins
  `phonemizer-fork==3.3.2` (there is **no** bare `phonemizer` pin) plus dead deps (`allosaurus`,
  `pyjwt`) and `pytest` into the production image.
- **`.gitignore` is the GitHub Python template at the root of a polyglot repo.** An unanchored `lib/`
  once excluded the whole of `frontend/src/lib/`; it is anchored now (`/lib/` at `:17`), but **41**
  unanchored directory patterns remain. Anchor new rules with a leading slash.
- **`shell grep` is a ugrep wrapper honoring `.gitignore`**, so `AUDIT.md` is invisible to `grep -r`.
  Use `git grep` or `command grep`.
- **`GEMMA_*` names a provider-agnostic layer** — a bare OpenAI-compatible `/chat/completions` client.
  The repo's own examples disagree on the model: `.env.example:8` says `google/gemma-4-31b-it` while
  `backend/.env.example:35` says `accounts/fireworks/models/gemma-3-12b-it`, and the README's Fireworks
  example is a third. Since credentials must live in `backend/.env`, whichever example you copy decides
  which model you silently get. Renaming is mapped in `AUDIT.md` §2.1 — the landmines are **five**
  `monkeypatch.setattr` *string* targets in `test_gemma_g2p.py` (`:38`, `:204`, `:217`, `:233`, `:276`)
  that fail only at runtime.
- **Two in-tree comments still name the deleted push gate:** `app/tests/pytest.ini:9` (the `integration`
  marker description, which `pytest --markers` prints, so the suite advertises a gate the repo no longer
  has) and `app/tests/test_models.py:529`. Prose only. Both were fenced out of the brief that deleted
  the gate, so they belong to the brief that owns the test files.
- **Recovery from the archived branches is path-scoped only.** `origin/ci-cd-gke-deploy` and the
  `archive/*` tags preserve the pre-consolidation state, but that branch never got the Allosaurus →
  wav2vec2 migration and still pins `allosaurus==1.0.2` — restore specific paths, never wholesale.
  `DEPLOY-NOTES.md` §"Recovering any of this" has the tag table and the safe commands.

## README caveats

§"Running on AMD" and §"Powered by Gemma 4" are hackathon prize-track copy; de-branding is deferred. It
documents `SESSION_SECRET` as security-relevant, but **no code reads it**. Prefer `DEPLOY-NOTES.md`,
then `AUDIT.md` §7, over the README for anything factual. (Three claims this file used to list as false
— startup warming, the eSpeak floor, stubbed models in tests — have since become true in the code. Do
not reinstate them.)

---

*This file is living. When a brief changes how the repo behaves, update this file in the same unit.*

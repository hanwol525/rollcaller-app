# RollCaller

**Every name at a ceremony, announced the way its owner actually says it.**

Graduations and award ceremonies mispronounce names constantly. RollCaller fixes that at the source: participants record their own name, the app transcribes the *actual audio* into phonetics (IPA), and at the ceremony a single consistent AI voice reads every name back — recorded names in their owner's own pronunciation, and everyone else's generated from the spelling by **Gemma 4**. It distinguishes accents and homograph pronunciations too (the same spelling recorded as "Tom Smith" vs "Tim Smythe" produces different IPA — it transcribes sound, not spelling).

---

## How it works

Two paths converge on one consistent ceremony voice:

1. **Recorded path (personal):** participant audio → a `wav2vec2` phoneme recognizer → IPA → Kokoro TTS. Captures how *you* say your name, accent and all.
2. **Gemma g2p path (for anyone who didn't record):** name spelling → **Gemma 4** (multilingual — produces origin-appropriate phonemes) → IPA → Kokoro TTS. If Gemma isn't configured, an **eSpeak NG** floor is used so the app still runs.

Clips are rendered live at "Prep Clips" time and stored, then played in walk order during the ceremony. Nothing is pre-baked or hardcoded — every pronunciation is generated on demand.

---

## Powered by Gemma 4

Gemma 4 is the pronunciation brain behind RollCaller. Getting a name right from its *spelling alone* is genuinely hard — rule-based grapheme-to-phoneme engines mangle anything they weren't hand-tuned for, because they don't know that *Siobhán* is Irish, *Nguyễn* is Vietnamese, or *Þórunn* is Icelandic. Gemma does.

**What Gemma does here:** for every participant who doesn't record their own name, RollCaller sends the name to Gemma 4 and asks for its pronunciation in IPA. Gemma draws on its multilingual knowledge to return an origin-appropriate transcription — the phonemes a native speaker would use — which the app feeds to the ceremony voice. This is what lets a roster of *Siobhán*, *Xiùyīng*, *João*, and *Małgorzata* come out **right** in one consistent voice instead of anglicized guesses.

**How the integration works** (`backend/app/pronunciation/gemma.py`):

- **Any OpenAI-compatible endpoint.** Gemma is called over the standard `/chat/completions` API, configured by three env vars (`GEMMA_BASE_URL` / `GEMMA_MODEL` / `GEMMA_API_KEY`). The *same code* runs Gemma on OpenRouter, on Fireworks, or on a **self-hosted AMD Instinct GPU via vLLM's ROCm build** — swapping providers is a one-line `.env` change, no code edits.
- **Free-form LLM output → reliable structured IPA.** An LLM won't always return clean phonemes — it may wrap them in prose, markdown, quotes, a reasoning block, or leak a raw spelling character. So Gemma's output runs through a purpose-built sanitizer that strips all of that, extracts the IPA, maps stray graphemes to their nearest phoneme (e.g. Polish `ł` → `/w/`), removes combining diacritics the voice can't render, and rejects anything that still reads as prose. The result is IPA the TTS can actually speak.
- **Deterministic and bounded.** Temperature 0 (so re-renders are stable), a short token budget (a name's IPA is brief), reasoning disabled.
- **Graceful degradation.** If Gemma is unreachable or unconfigured, the call returns cleanly and the app falls back to the eSpeak floor — RollCaller runs with or without a key, and never breaks on a bad pronunciation.

In short: Gemma turns *"reads names phonetically"* into *"correctly pronounces names across a dozen-plus languages from spelling alone"* — the core intelligence of the product.

---

## Tech stack

| Layer | Tech |
|---|---|
| Frontend | SvelteKit + TypeScript (Vite) |
| Backend | FastAPI + SQLModel + Uvicorn |
| Database | SQLite (dev) · PostgreSQL (prod/Docker) |
| Blob storage | Local filesystem (dev/Docker) · MinIO / S3 (optional prod) |
| Spelling → IPA | **Gemma 4** via any OpenAI-compatible endpoint · eSpeak NG floor |
| Audio → IPA | `facebook/wav2vec2-lv-60-espeak-cv-ft` (transformers + torch, CPU) |
| IPA → speech | Kokoro-82M (`af_heart`, 24 kHz) — requires eSpeak NG |
| Audio transcode | FFmpeg (browser webm/opus/mp4 → 16 kHz mono WAV) |
| Auth | Session cookie, single seeded organizer |

---

## Run with Docker (one command)

The whole app — frontend, API, and database — runs with a single command. You need **Docker** and, for the Gemma path, an OpenAI-compatible LLM key (e.g. OpenRouter or Fireworks).

```bash
git clone https://github.com/hanwol525/rollcaller-app && cd rollcaller-app
cp .env.example .env          # then paste your key into GEMMA_API_KEY
docker compose up
```

Open **http://localhost:5173** and sign in (`organizer` / `changeme` by default).

> **Two compose files — use the root one.** Run the **root** `docker-compose.yml` (the command above) to launch the whole app. The separate `backend/docker-compose.yml` only brings up Postgres + MinIO for backend-only local development; you don't need it for a normal run.

> **First launch downloads the speech models** (Kokoro + wav2vec2, ~2 GB) in the background — the app comes up in seconds, but the *first* "Prep Clips" or recording waits a minute or two for the models to finish loading. They're cached on a volume after that, so later runs are instant.

Without a `GEMMA_API_KEY`, the app still runs — hard names just use the eSpeak fallback instead of Gemma.

### Configuring Gemma for the Docker run

`docker compose` reads a **`.env` at the repository root** (next to `docker-compose.yml`) — not `backend/.env`. Copy `.env.example` → `.env` there and set:

```env
GEMMA_BASE_URL=https://openrouter.ai/api/v1
GEMMA_MODEL=google/gemma-4-31b-it
GEMMA_API_KEY=sk-or-...
```

Two things that will bite you:
- `GEMMA_BASE_URL` is the **base only** — the code appends `/chat/completions`. Do **not** include the full path.
- Use the provider's **exact, namespaced** model id. Free tiers (`…:free`) rate-limit hard and silently drop names to eSpeak mid-batch.

---

## Run without Docker (SQLite + local file storage)

The app's dev defaults use on-disk SQLite and filesystem storage — no Postgres, no MinIO.

### 1. System deps

```bash
# Debian/Ubuntu/WSL
sudo apt update && sudo apt install -y ffmpeg espeak-ng
# macOS
brew install ffmpeg espeak-ng
```

### 2. Backend

```bash
cd backend
uv venv --python 3.12            # uv brings its own 3.12; put the venv on NATIVE storage
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
uv pip install -r app/requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu
cp .env.example .env
sed -i 's/\r$//' .env            # Windows: normalize line endings (see Troubleshooting)
uvicorn app.main:app --reload --port 8000     # RUN FROM backend/ — first launch downloads models
```

For the non-Docker run, the Gemma vars live in **`backend/.env`** (the directory you launch uvicorn from). Same three vars as above.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

---

## Configuration reference (`.env`)

Only `ORGANIZER_*` and (for prod) `DATABASE_URL` are required to boot; everything else has a working default. The Gemma vars are optional (eSpeak floor without them).

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./rollcaller.db` | `postgresql+psycopg2://…` for Postgres |
| `STORAGE_BACKEND` | `filesystem` | `filesystem` or `minio` |
| `STORAGE_FS_ROOT` | `./blobstore` | Clip location in filesystem mode |
| `S3_*` | — | Only when `STORAGE_BACKEND=minio` |
| `ORGANIZER_USERNAME` / `ORGANIZER_PASSWORD` | `organizer` / `changeme` | The single seeded login |
| `SESSION_SECRET` | dev value | Change for anything non-local |
| `GEMMA_BASE_URL` / `GEMMA_MODEL` / `GEMMA_API_KEY` | — | Gemma g2p endpoint (see above) |

---

## Using it

1. **Sign in** as the organizer.
2. **Create an event** and build the roster — add names by hand and/or **Import CSV** (a `name` column; extra columns ignored).
3. **Distribute record links** — each participant gets a private link to record their own name; they can re-record until it's right.
4. **Prep Clips** — renders every name's audio: recorded names from their own audio, the rest via Gemma (or eSpeak).
5. **Start Ceremony** — plays every clip in walk order, in one consistent voice.

---

## Troubleshooting (a.k.a. every wall we hit, so you don't)

**`transformers`/`torch` install fails or produces corrupt files on WSL (e.g. `METADATA: No such file or directory`).**
Your venv is on the Windows-mounted drive (`/mnt/c/...`). The Windows↔Linux filesystem bridge corrupts large installs. **Put the venv on native Linux storage** (`~/…`). Your code can live anywhere; the *venv* must be on the Linux side.

**`spacy` / `blis` fails to build; "Failed to build … wheel".**
You're on Python 3.13, which some deps don't ship wheels for. **Use Python 3.12.** `uv venv --python 3.12` fetches its own — no apt, no version roulette. (The Docker image is already pinned to 3.12.)

**`torch==2.13.0+cpu` won't resolve from PyPI.**
The `+cpu` build lives on PyTorch's own index. Install with `--extra-index-url https://download.pytorch.org/whl/cpu`.

**App won't start: `bool_parsing … 'false\r'`, or `source .env` prints `: command not found`.**
Windows CRLF line endings; the trailing `\r` breaks parsing and shell sourcing. Fix: `sed -i 's/\r$//' .env`, and set your editor to **LF** for that file.

**Gemma "works in a script" but the app uses eSpeak / names come out wrong.**
`Settings()` reads `.env` **relative to your current directory**, once at startup. Run the backend **from `backend/`** (non-Docker) and restart after any `.env` change. For Docker, the vars come from the **root** `.env` — a common mix-up is putting them in `backend/.env` instead.

**Gemma returns nothing / falls back to eSpeak in Docker.**
Check the container actually got the vars: `docker compose exec backend printenv | grep GEMMA`. If blank, your **root** `.env` is missing the key (or has a `\r`, or the model id has `:free`). Fix and `docker compose up` again.

**`en_core_web_sm` missing (spaCy model).**
It's pinned in `requirements.txt` and installs automatically. If a fresh install ever complains, run `python -m spacy download en_core_web_sm`.

**First backend launch seems frozen.**
It's downloading model weights (wav2vec2 ≈ 1–2 GB). Not a hang. Set `HF_TOKEN` for faster Hugging Face downloads if you like (optional).

**Recorded names sound flatter than the roster names.**
Expected: the recognizer returns phonemes without stress marks, so Kokoro renders them evenly; roster names carry stress from the g2p step. Cosmetic.

---

## Project layout

```
backend/
  app/
    main.py                 # FastAPI entry (lifespan: init db, seed organizer, warm models in background)
    config.py               # Settings (env-driven)
    storage.py              # filesystem / MinIO blob backends
    pronunciation/
      recognize.py          # audio -> IPA (wav2vec2)
      gemma.py              # spelling -> IPA (Gemma 4, OpenAI-compatible + sanitizer)
      g2p.py                # gemma_ipa() or espeak floor
      tts.py                # IPA -> audio (Kokoro)
      transcode.py          # browser audio -> 16 kHz mono WAV (ffmpeg)
      orchestrator.py       # warms + wires the pipeline
    routers/                # auth, spaces, participants, ceremony, invite
    requirements.txt        # Python deps  ← note: under app/
  Dockerfile
  docker-compose.yml        # Postgres + MinIO for backend-only dev (NOT the app runner)
frontend/                   # SvelteKit + TypeScript
  Dockerfile
docker-compose.yml          # ← the one to run: full app (frontend + backend + Postgres)
.env.example                # root env template for the Docker run
```

## Notes

- The Gemma endpoint is provider-agnostic — point `GEMMA_*` at OpenRouter, Fireworks, or a self-hosted AMD/vLLM server; swapping is an `.env` change, no code.
- Tests run with zero external services (in-memory SQLite + temp filesystem, ML models stubbed): from `backend/`, `pytest app/tests`.

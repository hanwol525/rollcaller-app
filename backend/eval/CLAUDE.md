# backend/eval — the pronunciation eval harness

Measures the pronunciation path against a public dataset. **Not part of the app:** `backend/Dockerfile`
copies only `app/requirements.txt` and `app/` (`:17`, `:21`), and the harness carries its own
`eval/requirements.txt` and `eval/pytest.ini`. It never touches the DB.

```bash
# from backend/, into the same venv as the app
./app/venv/bin/python -m pip install -r eval/requirements.txt    # once
./app/venv/bin/python -m eval generate --split test --engines floor,oracle
./app/venv/bin/python -m eval score  --run    eval/runs/<stamp>.jsonl
./app/venv/bin/python -m eval report --scores eval/runs/<stamp>.scores.json
./app/venv/bin/python -m eval paired --run eval/runs/<stamp>.jsonl --engines oracle,today
./app/venv/bin/python -m eval probe               # 1 raw LLM request
./app/venv/bin/python -m eval probe  --levers     # 5 of them; run before any scored LLM run
./app/venv/bin/python -m pytest eval/tests -q     # 345 tests, ~52 s
```

⚠️ **Never run bare `pytest` from `backend/`.** It collects both trees in one process and
`app/tests/conftest.py:19-24` writes `os.environ` at import time, which leaks in here. Always name the
tree. `eval/pytest.ini` exists because `app/tests/pytest.ini` sets `testpaths = .` and is loaded only
when its own directory is the target — without it, `pytest eval/tests` gets rootdir `backend` and *no*
configfile, so `--strict-markers` is off and a typo'd marker only raises `PytestUnknownMarkWarning`
while the test still passes, instead of failing collection.

## What it borrows from the app

Mostly read-only, but **not entirely** — and this is what a refactor will break, invisibly to the app's
own suite:

- `g2p.espeak_ipa`, `g2p.g2p`, `g2p.DEFAULT_LANGUAGE`, `g2p.warm` (`engines.py:159`), and the **private**
  `g2p._backends` (`engines.py:161`).
- `orchestrator.ipa_refusal` (`score.py:213`), `tts.prepare_ipa` (`score.py:142`), `config.settings`.
- ⚠️ **Four private names from `gemma`**, in `eval/llm.py` only: `_SYSTEM`, `_FEWSHOT`, `_sanitize_ipa`,
  `_THINK_OPEN`.
- ⚠️ `eval/llm.py:279` **assigns** `g2p.gemma_ipa = recording_gemma_ipa` and restores it at `:283` — so
  "imports read-only" is not strictly true.

⚠️ **Patch `app.pronunciation.g2p.gemma_ipa`, never `gemma.gemma_ipa`:** `g2p.py:16` binds the name into
its own namespace, so patching the other records nothing and silently measures the real path.

## The espeak pin is load-bearing, not tidy

`eval/espeak_setup.py` runs from `eval/__init__.py` — **before** the submodules, because `eval/dataset.py`
imports `g2p` at module level and `EspeakWrapper.set_library` is only consulted when a backend is
*constructed*. It points phonemizer at the `espeakng-loader` wheel already in the venv, calling
`set_library` **and** `set_data_path`.

Without it the harness relies on phonemizer discovering a system espeak-ng, which **fails silently**:
`g2p.warm()` catches every exception and negative-caches it, so an unfindable library is
indistinguishable from a working one — `espeak_ipa()` returns `""`, one WARNING line is the only trace,
and a whole run reads as 100% refusal in every language rather than as void.

⚠️ Never hardcode the filename (`libespeak-ng.so` / `.dylib` / `espeak-ng.dll`, no `lib` prefix on
Windows), and note that `make_library_available()` is **not** sufficient — it only extends the DLL search
path and never tells phonemizer anything. Adopting the pin was verified not to move the numbers: the
bundled library is also **1.52.0** and re-running `floor`+`oracle` on `test` reproduced the committed
baseline **bit-for-bit**, 16 cells and 352 fields. A mismatch is recorded rather than gated —
`provenance._espeak()` compares against `EXPECTED_ESPEAK` (the tuple `(1, 52, 0)`) and writes a warning
into the run file.

## Encoding is named everywhere, and the audit is mechanical

Every file read and write under `eval/` names its encoding explicitly. Audit with
`python -X warn_default_encoding -W always::EncodingWarning -m eval …`, which fires on `open(p, "w")` and
stays silent on `open(p, encoding="utf-8")`. Our own code is at zero; remaining warnings come from
`regex`, `huggingface_hub` and `panphon`.

The one site this caught was `provenance._git`, which passed `text=True` with no `encoding` — and that
mattered because `git_dirty` is `bool()` of `git status --porcelain`, whose output carries paths: on a
single-byte-code-page host an undecodable path fell into the bare `except` and returned `""`, so the
header would have reported a **clean tree while the tree was dirty**. Now
`encoding="utf-8", errors="replace"`, so an unreadable byte costs one character rather than the field.

## Three engines, and the generate/score split

`ENGINES` (`engines.py:208`) is `floor` / `oracle` / `today`.

- **`floor`** — `espeak_ipa(name, "en-us")`: what the app degrades to with no LLM configured, and what
  `today` falls back to per name. It is **not** the shipped path when `GEMMA_*` is set, so a gain
  measured against it is not a gain over production.
- **`oracle`** — `espeak_ipa` handed the dataset's own language label. A **reference point**, not an
  upper bound: the label is the Wiktionary section a row was mined from, not the name's origin.
- **`today`** — `g2p.g2p`, i.e. `gemma_ipa(name) or espeak_ipa(name)`.

`generate` calls engines and writes JSONL; **`score` is pure**, so re-scoring never re-calls a model and
a scoring rule can change for free.

**Every `today` row carries exactly one branch**, decided by the wrapper's return value rather than the
logs (`gemma_ipa` logs nothing on success and its "unconfigured" warnings are deduped process-wide). The
four are `llm_accepted` / `llm_rejected` / `llm_empty` / `llm_error`; an unexplained `None` **raises**,
and `runfile.read` refuses a `today` row with no branch — which is what makes "none unclassified" true by
construction.

## Reading the numbers

**The unit is the name, not the dataset row.** The corpus attests several pronunciations for some names —
up to 12 for one Spanish name in `test`, mean 1.23 — and an engine emits one, so each engine is called
once per name and scored against the **closest** attested variant: any attested pronunciation counts as
correct. **2,507** names in both `test` (3,091 rows) and `dev` (3,120 rows); `train` is 20,084 names over
24,841 rows.

**The number is error _per reference segment_, and it is not a rate.** Saying nothing costs a flat ~7.25
per segment (the `ceiling` column); saying too much exceeds that without bound, because every extra
segment is an insertion. **So a figure above `ceiling` is a diagnostic rather than a worse
pronunciation** — in this corpus it is always eSpeak narrating a script it cannot read. Engines are
compared within a language and never across, because dividing by reference length prices the same error
differently in a short name. Every figure is **authenticity against Wiktionary, not correctness**.

**`score` bootstraps each cell on its own, which is the _marginal_ interval — use `paired` for any
engine-vs-engine claim.** Two overlapping marginal intervals do not mean two engines are
indistinguishable, because both answered the same names. `eval/paired.py` exists because three figures in
the first baseline were computed in a throwaway script and typed in by hand, and one had drifted by the
time it was read.

## Credentials and cost

`today` needs `GEMMA_BASE_URL` / `GEMMA_MODEL` / `GEMMA_API_KEY` in **`backend/.env`** — not the repo
root, because `env_file` resolves against the process CWD. None is committed and `config.py` defaults all
three to `None`, so `today` refuses to start rather than running half-configured. **A run that calls a
model needs Han's approval with the cost stated first.** The eSpeak engines cost nothing and need no
approval.

⚠️ **`temperature: 0` does not make the LLM path reproducible, and `gemma.py:268` still claims it does.**
Measured 2026-11-13, eight identical requests for `Malgorzata` returned three distinct strings. Reproduce
with `python -m eval probe --name Malgorzata`, repeated. Two consequences: a `today` figure carries a
run-to-run variance component that re-scoring **cannot** remove, so every `today` summary must name the
run it came from; and at app level **a participant's clip can change pronunciation between two Prep Clips
runs with no input change**, because the variants differ in what survives Kokoro's vocabulary.

## What the baseline found

Full numbers: **`eval/results/baseline-2026-11-13.md`** (tracked, reproducible from the provenance header
every summary carries). The conclusions that change how you write code:

- **No, an LLM does not beat simply using the right eSpeak voice — but it is the only engine that never
  refuses.** `oracle` beats Gemma 4 31B in 6 of 8 languages on the paired per-name test; Gemma beats
  `floor` in all 8. But `today` voiced 2,507 of 2,507 names against `oracle`'s 2,491: `oracle` refuses 16
  Vietnamese names on tone digits. **So Brief 04's shape (LLM picks the language, eSpeak phonemizes) is
  what the numbers support — and it inherits those refusals unless the guard work ships with it.**
- **`oracle` is handed the dataset's label, so it bounds a *perfect* identifier, not a real one.**
- ⚠️ **The narration guard is script-specific, and Arabic walks straight through it.** All 57 Arabic names
  under `floor` are narrated rather than pronounced, and **39 of the 57 are not refused.** The guard's
  only narration rule is the marker set `{"lˈɛɾɚ", "lˌɛɾɚ"}` (`tts.py:147`) — eSpeak's English word
  *letter* — but for the 28 basic Arabic letters eSpeak uses the letter's own Arabic name instead, so the
  marker occurs **0 times** in all 57 outputs. Do **not** generalise to "eSpeak never says *letter* for
  Arabic": for characters it cannot name (`ڤ`, tanwīn, tatweel) the marker **is** emitted but lands
  mid-token, so the token-final test misses it. Relaxing token-finality buys nothing on these 57 names and
  genuinely helps on extended letters. Owner: the guard-fix brief.
- ⚠️ **eSpeak writes one Vietnamese tone as a vowel, so the guard cannot see it.** *sắc* is written `ɜ` and
  never a digit, and `ɜ` **is** in Kokoro's 114-symbol set, so the guard passes `Hiếu` → `hˈiɛɜw` and a
  clip is produced. Do not read the other direction as settled: *ngang* is not silent in general.
- ⚠️ **Adopting the right-language voice loses phonemes before Kokoro, in two places, only one of which is
  silent.** `prepare_ipa`'s fold **logs**; the 114-symbol vocabulary filter is the silent one. Per-stage
  counts are in the report. Small against the gain, but concentrated in the languages the floor already
  fails worst.

`eval/runs/` (raw per-name output) and `eval/.cache/` (the dataset, CC BY-SA 4.0, pinned at
`saraelhark/say-my-name@c7d27b1e`) are gitignored. Summaries are committed to `eval/results/`, pinned to
LF by `/.gitattributes:11`.

## Running the harness on Windows

⚠️ Every command above is POSIX. **The table below was written on the macOS host and has not been executed
on Windows** — the ✅ facts were verified from the published wheels and indices; the commands are
translations.

| Purpose | POSIX | PowerShell |
|---|---|---|
| Interpreter | `./app/venv/bin/python` | `app\venv\Scripts\python.exe` |
| Create the venv | `python3 -m venv app/venv` | `py -3.12 -m venv app\venv` |
| App deps | `… -m pip install -r app/requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu` | same, Windows interpreter |
| Suites / `-m eval` subcommands | as above | same, Windows interpreter |
| **One env var, one command** | `GEMMA_MODEL=x … -m eval …` | `$env:GEMMA_MODEL = "x"` on **its own line**, then the command |
| Reset that env var | nothing to do — it never leaked | `Remove-Item Env:\GEMMA_MODEL` |
| Force UTF-8 I/O | not needed | `$env:PYTHONUTF8 = "1"` |
| Search for a literal | `command grep -rn "pat" .` | `Select-String -Path . -Pattern "pat" -Recurse` |

**Four traps, in the order they will bite.**

1. ⛔ **`VAR=value command` is a parse error in PowerShell**, not an ignored prefix. And `$env:VAR`
   **persists for the rest of the session**, so the second model's run silently reuses the first while
   provenance claims otherwise — two runs of one model filed under different names, worse than no data.
   Reset explicitly between runs, and make provenance the guard: **assert each summary's recorded
   `model_id` against the row it is filed under.**
2. **Set `PYTHONUTF8=1` for any run whose stdout is redirected to a file.** Every `open()` under `eval/`
   names its encoding, but that does not change how `print` encodes to a redirected pipe, and cp1252
   cannot represent `ʃ ɪ ˈ ɔ ː` at all — it raises rather than corrupting, so a long run dies at the first
   IPA character.
3. **The macOS venv does not transfer.** `backend/app/venv` holds macOS wheels. Build a fresh one.
4. **`core.autocrlf` defaults to `true` in Git for Windows' installer**, which is what `/.gitattributes`
   is there for.

✅ **Verified from the published artifacts, 2026-11-14.** `espeakng-loader` 0.2.4 ships `win_amd64` **and**
`win_arm64` wheels; the `win_amd64` DLL carries **1.52.0**, matching `EXPECTED_ESPEAK`, and its
`espeak-ng-data/` is **byte-identical** to the macOS wheel's (364 files each). So `espeak_setup.py`'s pin
resolves on Windows with no system espeak-ng installed. Worth confirming once on the box:
`EspeakWrapper().version` after `import eval`.

✅ **Unlike the Mac, a Windows x86_64 host can run the whole ML stack.** `torch==2.13.0+cpu` publishes
`win_amd64` wheels for cp310–cp314, so the install succeeds with `--extra-index-url` and `kokoro` comes
with it. **Consequences:** the 11 `@integration` tests actually run rather than skipping, and
`transformers` imports, so `recognize()` works and recordings produce real IPA. With `ffmpeg` on `PATH` a
Windows box reaches **197 passed, 0 skipped** — the only machine in this project that can.

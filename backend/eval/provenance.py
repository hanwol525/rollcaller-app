"""What a run was measured against, recorded with the run.

Every number this harness produces depends on software versions it does not
control -- espeak-ng's phoneme tables, Kokoro's vocabulary, the dataset
revision, the LLM behind an env var. A baseline that cannot name those is not
reproducible, and Brief 04 is meant to be judged against this one.

So the header below goes on every run file and every summary. It is cheap to
write and the only thing that makes a stale number detectable later.

**Two stages produce it, and the fields belong to different ones.** The whole
point of splitting `generate` from `score` is that scoring happens later and
possibly elsewhere, so a single flat record is a trap: espeak-ng, the dataset
and wordfreq determine the *outputs* and are fixed at generate time, while
panphon, Kokoro's vocabulary and the normalisation ruleset determine the
*numbers* and are whatever the scoring process had. panphon is imported by
`score.py` alone, so recording its version from the generate stage -- which
never touches it -- would attribute every distance to the wrong library.
`merge_for_scoring` keeps each field with the stage that decides it.
"""
from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from eval import dataset

# The espeak-ng the guard constants and every expected-IPA string in the app's
# suite were recorded under (app/tests/test_models.py:434-461 asserts it).
# Phoneme tables change between releases, so a different version makes a
# baseline incomparable rather than merely different.
EXPECTED_ESPEAK = (1, 52, 0)


def _git(*args: str) -> str:
    """Run a read-only git command. Never a write -- Han owns the history.

    ``encoding`` is explicit because ``text=True`` alone decodes with the
    locale's preferred encoding, and that is not a cosmetic difference here.
    ``git_dirty`` is ``bool()`` of ``git status --porcelain``, whose output
    carries **paths**; on a host whose locale is a single-byte code page, a
    non-ASCII path can fail to decode, the bare ``except`` below turns that
    into ``""``, and the provenance header then reports a **clean tree while
    the tree is dirty**. ``errors="replace"`` rather than strict for the same
    reason: a byte we cannot read should cost one character, not the whole
    field. Neither value is ever parsed -- one is hex, the other is only
    tested for emptiness -- so a replacement character is harmless.
    """
    try:
        return subprocess.run(
            ("git",) + args,
            capture_output=True, text=True, timeout=10, check=True,
            encoding="utf-8", errors="replace",
        ).stdout.strip()
    except Exception:
        return ""


def _version(pkg: str) -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(pkg)
    except PackageNotFoundError:
        return "absent"


def _espeak() -> tuple[str, str, str | None]:
    """Return ``(version, library, warning)``."""
    try:
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        found = EspeakWrapper().version
        library = str(EspeakWrapper().library())
    except Exception as e:
        return "unavailable", f"{type(e).__name__}: {e}", "espeak-ng could not be probed"
    warning = None
    if tuple(found) != EXPECTED_ESPEAK:
        warning = (
            f"espeak-ng is {found}, not {EXPECTED_ESPEAK}: phoneme tables differ "
            "between releases, so these numbers are not comparable with a "
            "baseline recorded under "
            f"{'.'.join(str(n) for n in EXPECTED_ESPEAK)}"
        )
    return ".".join(str(n) for n in found), library, warning


@dataclass(frozen=True)
class Provenance:
    generated_at: str
    git_commit: str
    git_dirty: bool
    espeak_version: str
    espeak_library: str
    espeak_warning: str | None
    dataset_repo: str
    dataset_revision: str
    split: str
    kokoro_repo: str
    kokoro_revision: str
    panphon_version: str
    wordfreq_version: str
    huggingface_hub_version: str
    python_version: str
    platform: str
    # Engine-specific; absent for the eSpeak-only engines, which have no model.
    model_id: str | None = None
    model_host: str | None = None
    temperature: float | None = None
    # Non-None when the harness added fields to the outgoing request body at
    # the httpx layer -- `today`'s thinking-off configuration. The run then
    # measures a path the app cannot take by itself, so every summary built
    # from it has to say so rather than presenting it as today's behaviour.
    request_modification: str | None = None
    # Filled in at *score* time, not generate time: normalisation is a scoring
    # concern and the whole point of the two-stage split is that it can change
    # without re-calling an engine.
    ruleset_version: str | None = None
    # When scoring happened, if it has. None means this record describes a run
    # file that has not been scored.
    scored_at: str | None = None
    # Set when the harness code moved between generating and scoring.
    score_warning: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        """Markdown, for the head of a summary or the top of a dry run."""
        generated = [
            ("generated", self.generated_at),
            ("split", f"{self.dataset_repo}@{self.dataset_revision[:8]} / {self.split}"),
            ("espeak-ng", f"{self.espeak_version}  ({self.espeak_library})"),
            ("wordfreq", self.wordfreq_version),
            ("model", self.model_id or "— (no model; eSpeak-only engines)"),
            ("host", self.model_host or "—"),
            ("temperature", "—" if self.temperature is None else str(self.temperature)),
            ("request", self.request_modification or "as shipped (unmodified)"),
        ]
        scored = [
            ("scored", self.scored_at or "— (not scored yet)"),
            ("panphon", self.panphon_version),
            ("kokoro vocab", f"{self.kokoro_repo}@{self.kokoro_revision[:8]}"),
            ("normalisation", self.ruleset_version or "— (set at score time)"),
            ("huggingface_hub", self.huggingface_hub_version),
            ("python", f"{self.python_version} on {self.platform}"),
            ("git commit", f"{self.git_commit or 'unknown'}"
                           f"{'  (DIRTY WORKING TREE)' if self.git_dirty else ''}"),
        ]
        width = max(len(k) for k, _ in generated + scored)
        out = ["## Provenance", "",
               "Split by stage, because scoring happens later and possibly "
               "elsewhere. The first group fixed the engine **outputs**; the "
               "second computed the **numbers** from them.", "",
               "*Generating:*", ""]
        out += [f"- `{k.ljust(width)}`  {v}" for k, v in generated]
        out += ["", "*Scoring:*", ""]
        out += [f"- `{k.ljust(width)}`  {v}" for k, v in scored]
        warnings = [self.espeak_warning, self.score_warning]
        if self.request_modification:
            warnings.append(
                "**modified request.** The harness added "
                f"`{self.request_modification}` to every outgoing LLM request "
                "body. This is not exactly the path the application takes: "
                "`gemma_ipa` builds a fixed payload and cannot send that "
                "field (gemma.py:265-275), so these numbers describe the "
                "model's capability, not today's behaviour."
            )
        for warning in warnings:
            if warning:
                out += ["", f"> ⚠️ {warning}"]
        return "\n".join(out)


# Fields the *generate* stage decides. Everything else is decided by whoever
# scored, so merge_for_scoring takes it from the scoring process.
GENERATE_OWNED = (
    "generated_at", "espeak_version", "espeak_library", "espeak_warning",
    "dataset_repo", "dataset_revision", "split", "wordfreq_version",
    "model_id", "model_host", "temperature", "request_modification",
)


def merge_for_scoring(
    generate_header: dict, ruleset_version: str, cache_dir: str | None = None
) -> Provenance:
    """Combine a run file's generate-time record with this scoring process.

    Called by ``score``. The alternative -- copying the run file's record and
    overwriting ``ruleset_version`` -- silently credits the generate machine's
    panphon, Python and platform with numbers this process computed, and
    panphon is imported by ``score.py`` alone.
    """
    local = collect(split=generate_header.get("split", "?"), cache_dir=cache_dir,
                    ruleset_version=ruleset_version)
    merged = {**local.as_dict(),
              **{k: generate_header[k] for k in GENERATE_OWNED
                 if k in generate_header},
              "scored_at": local.generated_at,
              "ruleset_version": ruleset_version}
    before, after = generate_header.get("git_commit"), local.git_commit
    if before and after and before != after:
        merged["score_warning"] = (
            f"the harness moved between stages: generated at {before[:8]}, "
            f"scored at {after[:8]}. The engine outputs came from the first "
            "and every number here from the second."
        )
    return Provenance(**merged)


def collect(
    split: str,
    cache_dir: str | None = None,
    model_id: str | None = None,
    model_host: str | None = None,
    temperature: float | None = None,
    ruleset_version: str | None = None,
    request_modification: str | None = None,
) -> Provenance:
    espeak_version, espeak_library, espeak_warning = _espeak()
    from eval import kokoro_vocab
    try:
        _, kokoro_revision = kokoro_vocab.fetch(cache_dir)
    except Exception as e:
        # Recorded, not raised: a dry run that only counts rows should not need
        # the network, and a run that *does* need the vocabulary fails later at
        # the point that actually uses it.
        kokoro_revision = f"unresolved ({type(e).__name__})"
    return Provenance(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        git_commit=_git("log", "-1", "--format=%H"),
        git_dirty=bool(_git("status", "--porcelain")),
        espeak_version=espeak_version,
        espeak_library=espeak_library,
        espeak_warning=espeak_warning,
        dataset_repo=dataset.DATASET_REPO,
        dataset_revision=dataset.DATASET_REVISION,
        split=split,
        kokoro_repo=kokoro_vocab.KOKORO_REPO,
        kokoro_revision=kokoro_revision,
        panphon_version=_version("panphon"),
        wordfreq_version=_version("wordfreq"),
        huggingface_hub_version=_version("huggingface_hub"),
        python_version=platform.python_version(),
        platform=f"{platform.system()} {platform.machine()}",
        model_id=model_id,
        model_host=model_host,
        temperature=temperature,
        ruleset_version=ruleset_version,
        request_modification=request_modification,
    )

"""What a run was measured against, recorded with the run.

Every number this harness produces depends on software versions it does not
control -- espeak-ng's phoneme tables, Kokoro's vocabulary, the dataset
revision, the LLM behind an env var. A baseline that cannot name those is not
reproducible, and Brief 04 is meant to be judged against this one.

So the header below goes on every run file and every summary. It is cheap to
write and the only thing that makes a stale number detectable later.
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
    """Run a read-only git command. Never a write -- Han owns the history."""
    try:
        return subprocess.run(
            ("git",) + args,
            capture_output=True, text=True, timeout=10, check=True,
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
    # Filled in at *score* time, not generate time: normalisation is a scoring
    # concern and the whole point of the two-stage split is that it can change
    # without re-calling an engine.
    ruleset_version: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    def render(self) -> str:
        """Markdown, for the head of a summary or the top of a dry run."""
        rows = [
            ("generated", self.generated_at),
            ("git commit", f"{self.git_commit or 'unknown'}"
                           f"{'  (DIRTY WORKING TREE)' if self.git_dirty else ''}"),
            ("split", f"{self.dataset_repo}@{self.dataset_revision[:8]} / {self.split}"),
            ("espeak-ng", f"{self.espeak_version}  ({self.espeak_library})"),
            ("kokoro vocab", f"{self.kokoro_repo}@{self.kokoro_revision[:8]}"),
            ("panphon", self.panphon_version),
            ("wordfreq", self.wordfreq_version),
            ("huggingface_hub", self.huggingface_hub_version),
            ("python", f"{self.python_version} on {self.platform}"),
            ("normalisation", self.ruleset_version or "— (set at score time)"),
            ("model", self.model_id or "— (no model; eSpeak-only engines)"),
            ("host", self.model_host or "—"),
            ("temperature", "—" if self.temperature is None else str(self.temperature)),
        ]
        width = max(len(k) for k, _ in rows)
        out = ["## Provenance", ""]
        out += [f"- `{k.ljust(width)}`  {v}" for k, v in rows]
        if self.espeak_warning:
            out += ["", f"> ⚠️ {self.espeak_warning}"]
        return "\n".join(out)


def collect(
    split: str,
    cache_dir: str | None = None,
    model_id: str | None = None,
    model_host: str | None = None,
    temperature: float | None = None,
    ruleset_version: str | None = None,
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
    )

"""The engines, and the one place that calls into the app's pronunciation path.

Three engines, all of them thin wrappers over functions in
``app.pronunciation.g2p``. Nothing here reimplements the app; the point of the
baseline is to measure what the app does.

===========  ====================================================  ===========
engine       what it calls                                         Brief 04 is
===========  ====================================================  ===========
``floor``    ``espeak_ipa(name)`` -- en-us, the shipped default     the *before*
``today``    ``g2p(name)`` -- gemma, falling back to the floor      the *before*
``oracle``   ``espeak_ipa(name, language=CODE[lang])``              the *ceiling*
===========  ====================================================  ===========

``oracle`` is the measurement Brief 04 exists to justify: it hands eSpeak the
language the dataset labels the name with, which is what an LLM
language-identifier would buy if it always agreed with that label. So
``oracle - floor`` is the headroom.

⚠️ **It is a reference point, not an upper bound.** The dataset card defines
``lang`` as the Wiktionary pronunciation section a row was mined from, not the
name's origin -- so an identifier answering ``en`` for a name sitting in the
French section can beat ``oracle`` with the same phonemizer. Measured on dev:
a per-name choice between these two engines beats ``oracle`` in 5 of 8
languages (54 of 2,507 names), ``fr`` by 19.3% of its headroom. The real bound
available from a run is ``Cell.lang_per_name_best``.

**Single-threaded, deliberately.** ``g2p._espeak_lock`` serialises
``phonemize()`` because espeak-ng returns phonemes from library globals and
``g2p._backends`` shares one backend per language -- measured at 52-87% of
concurrent calls returning another name's IPA (``g2p.py:40-60``). A pool here
would buy nothing and risk the very corruption the lock exists to stop.
``today``'s gemma branch is the only parallelisable part, and that is Unit 4's.
"""
from __future__ import annotations

import contextlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from eval import llm
from eval.dataset import LANGUAGES, NameRecord, espeak_code
from eval.llm import ThreadLogCapture

log = logging.getLogger(__name__)

# espeak's own name for "I was given no language", and the app's default.
from app.pronunciation.g2p import DEFAULT_LANGUAGE  # noqa: E402

ENGINE_NAMES = ("floor", "oracle", "today")

# phonemizer's narration signal. Emitted by words_mismatch.Ignore -- the
# DEFAULT mode -- when a line's input and output word counts differ.
WORD_COUNT_MISMATCH = "words count mismatch"

# Loggers worth watching during a run (P14). `phonemizer` is the one that
# earns its place: it is where the narration signal comes from.
WATCHED_LOGGERS = (
    "app.pronunciation.g2p",
    "app.pronunciation.gemma",
    "app.pronunciation.tts",
    "phonemizer",
)


class EngineUnavailable(RuntimeError):
    """Raised before any work, never mid-run."""


@dataclass(frozen=True)
class EngineOutput:
    """One engine's answer for one name, plus what the loggers said."""

    name: str
    lang: str
    engine: str
    output: str
    word_count_mismatch: bool
    log_messages: tuple[str, ...]
    # `today` only. One of llm.BRANCHES, with the sanitizer's or the
    # exception's own words as the reason. None for the eSpeak engines,
    # which have no LLM call to classify.
    branch: str | None = None
    branch_reason: str | None = None


class LogWatch(ThreadLogCapture):
    """Capture records from the watched loggers for the duration of a block.

    A thin default over ``llm.ThreadLogCapture``: same attachment point, same
    ``drain()`` contract, watching ``WATCHED_LOGGERS``. The parent keys
    records by emitting thread, which is invisible single-threaded and
    load-bearing once ``today`` runs its LLM calls on a pool -- one shared
    list would hand one name's reason to another name's row.

    **Attached to the root logger, which is not a detail.** phonemizer's
    ``get_logger()`` runs ``logger.handlers = []`` and installs a
    ``NullHandler`` (``phonemizer/logger.py``), and ``BaseBackend.__init__``
    calls it on *every* backend construction (``backend/base.py:72-73``). So a
    handler attached to the ``phonemizer`` logger is destroyed by the next
    ``warm()`` -- silently, and a run would then measure nothing. Propagation
    is left intact by that wipe, so the root is the one stable attachment
    point.

    Attaching to the root also stops Python's ``lastResort`` handler printing
    every record to stderr, which matters because ``g2p._fallback`` logs once
    per name it cannot pronounce. Those are counted and reported instead of
    scrolling past.
    """

    def __init__(self) -> None:
        super().__init__(WATCHED_LOGGERS)


def espeak_codes(langs: tuple[str, ...] = LANGUAGES) -> dict[str, str]:
    """Map each dataset language to its eSpeak voice, validating first.

    **Validation happens before any ``warm()``, and that ordering is the whole
    point.** ``g2p.warm`` catches every exception, including an unsupported
    language, and caches a *sticky* ``None`` (``g2p.py:30-34``, ``:86-89``)
    that is never retried. So a wrong code does not raise: it costs ~1.6 s
    once and then returns ``""`` for every name in that language, which the
    scorer reads as a 100% refusal rate -- a plausible, entirely wrong
    measurement. ``supported_languages()`` needs no backend and costs ~0.6 s.
    """
    from phonemizer.backend import EspeakBackend

    supported = EspeakBackend.supported_languages()
    wanted = {lang: espeak_code(lang) for lang in langs}
    wanted[DEFAULT_LANGUAGE] = DEFAULT_LANGUAGE   # floor's voice

    missing = {lang: code for lang, code in wanted.items() if code not in supported}
    if missing:
        raise EngineUnavailable(
            "eSpeak does not support "
            + ", ".join(f"{code!r} (for {lang!r})" for lang, code in sorted(missing.items()))
            + f"; this espeak-ng offers {len(supported)} voices. Fix "
            "dataset.ESPEAK_CODE rather than warming: g2p.warm caches a "
            "sticky None for a language whose backend failed, so the run "
            "would report a 100% refusal rate for it instead of an error."
        )
    return {lang: code for lang, code in wanted.items() if lang in langs}


def warm(langs: tuple[str, ...] = LANGUAGES) -> dict[str, float]:
    """Build every backend once, up front. Returns per-code seconds.

    Warming here rather than lazily so the cost is visible and paid once, and
    so a failure surfaces before any name is scored. ~2.2 s per language.
    """
    from app.pronunciation import g2p

    codes = espeak_codes(langs)                      # validates first
    timings: dict[str, float] = {}
    for code in dict.fromkeys([DEFAULT_LANGUAGE, *codes.values()]):
        start = time.perf_counter()
        g2p.warm(code)
        timings[code] = time.perf_counter() - start
        if g2p._backends.get(code) is None:
            raise EngineUnavailable(
                f"eSpeak backend for {code!r} failed to build even though "
                "supported_languages() lists it. g2p caches that failure "
                "stickily, so continuing would measure empty output as "
                "refusal. See the warning logged above for the cause."
            )
    return timings


def floor(name: str, lang: str) -> str:
    """eSpeak with the shipped default voice. `lang` is deliberately unused.

    This is what the app does today for every participant: ``g2p()`` falls
    through to ``espeak_ipa(text)`` with no language argument, so every name
    on the roster is read as American English however it is spelled.
    """
    from app.pronunciation import g2p

    return g2p.espeak_ipa(name)


def oracle(name: str, lang: str) -> str:
    """eSpeak with the correct language: Brief 04's ceiling, not its design."""
    from app.pronunciation import g2p

    return g2p.espeak_ipa(name, language=espeak_code(lang))


def today(name: str, lang: str) -> str:
    """What the app does now: gemma, falling back to the eSpeak floor.

    ``lang`` is deliberately unused -- ``g2p()`` takes no ``language``
    parameter (P13), which is the whole point of the engine: today's path has
    no idea what language a name is.

    The LLM branch for this call is left in ``llm``'s thread-local stash by the
    recording wrapper and collected by ``run()``. It is not returned here
    because ``ENGINES`` is a uniform ``(name, lang) -> str`` mapping, and
    widening that contract for one engine would push the special case into
    every caller.
    """
    from app.pronunciation import g2p

    return g2p.g2p(name)


ENGINES = {"floor": floor, "oracle": oracle, "today": today}


def _one(
    engine: str, fn, record: NameRecord, watch: ThreadLogCapture
) -> EngineOutput:
    """One name through one engine, on whichever thread is running it.

    Everything thread-local happens here rather than in the caller: the drain
    and the stash are both keyed by thread, so collecting them from the main
    loop would attribute a worker's records to whatever name the main thread
    happened to be on.
    """
    output = fn(record.name, record.lang)
    # The LLM's own records were already drained and classified inside the
    # recording wrapper. What is left on this thread is the eSpeak side --
    # g2p._fallback's "no pronunciation" line, and phonemizer's word-count
    # mismatch -- which is exactly the ordering the run file wants: the
    # model's reason first, then what the floor did about it.
    messages = [r.getMessage() for r in watch.drain()]
    call = llm.take_call()
    if call is not None:
        messages = list(call.log_messages) + messages
    return EngineOutput(
        name=record.name,
        lang=record.lang,
        engine=engine,
        output=output,
        word_count_mismatch=any(WORD_COUNT_MISMATCH in m for m in messages),
        log_messages=tuple(messages),
        branch=call.branch if call is not None else None,
        branch_reason=call.reason if call is not None else None,
    )


def run(
    engine: str,
    records: list[NameRecord],
    progress_every: int = 250,
    workers: int = 1,
    extra_payload: dict | None = None,
) -> list[EngineOutput]:
    """Call one engine over every record, in file order.

    ``workers`` is accepted only for ``today``, and only because its LLM call
    is an HTTP round trip deliberately left outside ``g2p._espeak_lock``
    (``gemma.py:290``). The eSpeak engines must stay single-threaded: the lock
    serialises ``phonemize()`` anyway, so a pool buys nothing, and without it
    espeak-ng returns another name's phonemes from its library globals on
    52-87% of concurrent calls (``g2p.py:40-60``).

    Note the asymmetry a ``today`` pool really buys: ``g2p()`` is
    ``gemma_ipa(text) or espeak_ipa(text)`` (``g2p.py:112``), so every LLM
    failure falls straight back onto the serialised eSpeak branch. The pool
    helps on the accepted path and not at all on the others (P13).
    """
    if engine not in ENGINES:
        raise EngineUnavailable(f"unknown engine {engine!r}; have {sorted(ENGINES)}")
    fn = ENGINES[engine]
    is_llm = engine == "today"

    if workers > 1 and not is_llm:
        raise EngineUnavailable(
            f"refusing to run {engine!r} on {workers} workers: "
            "g2p._espeak_lock serialises phonemize() because espeak-ng "
            "returns phonemes from library globals, so a pool cannot speed "
            "this up -- and if the lock were ever removed it would hand one "
            "name another name's IPA on most calls (g2p.py:40-60)."
        )
    if extra_payload and not is_llm:
        raise EngineUnavailable(
            f"extra_payload modifies the LLM request body; {engine!r} makes "
            "no request. Passing it here would stamp a run as a modified "
            "request without modifying anything."
        )

    with contextlib.ExitStack() as stack:
        watch = stack.enter_context(LogWatch())
        if is_llm:
            # Patches app.pronunciation.g2p.gemma_ipa -- the binding g2p()
            # actually reads (g2p.py:16), not gemma.gemma_ipa.
            stack.enter_context(llm.recorder(watch))
            if extra_payload:
                stack.enter_context(llm.transport_hook(extra_payload))

        out: list[EngineOutput] = []
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                # map yields in input order, so the run file stays in file
                # order however the calls interleave.
                for i, result in enumerate(
                    pool.map(lambda r: _one(engine, fn, r, watch), records), 1
                ):
                    out.append(result)
                    if progress_every and i % progress_every == 0:
                        print(f"  {engine}: {i}/{len(records)}", flush=True)
        else:
            for i, record in enumerate(records, 1):
                out.append(_one(engine, fn, record, watch))
                if progress_every and i % progress_every == 0:
                    print(f"  {engine}: {i}/{len(records)}", flush=True)

        stray = watch.leftovers()
        if stray:
            # Not fatal, but it means some records were logged by a thread
            # that never completed a call -- worth seeing rather than losing.
            print(
                f"  note: {sum(len(v) for v in stray.values())} log record(s) "
                f"from {len(stray)} thread(s) were never attributed to a name",
                flush=True,
            )
    return out

"""The `today` engine's LLM side: recording, classification, and the probe.

``today`` is the only engine that calls a paid endpoint, and the only one whose
answer has more than one way of being absent. ``g2p()`` is
``gemma_ipa(text) or espeak_ipa(text)`` (``g2p.py:112``), so a ``None`` from the
LLM is indistinguishable in the *output* from an LLM that was never configured.
The run file has to say which, or the baseline cannot separate "the model is bad
at this" from "the request never happened".

**The branch comes from the wrapper's return value, not from the logs**
(Amendment B). P6 established that logs alone cannot support the
classification: ``gemma.py`` collapses network error, timeout, JSON parse
failure and a missing field into one template, and -- the part that actually
breaks a log-only scheme -- the "not configured" and "httpx not installed"
lines are deduped process-wide via ``_warned_unconfigured`` /
``_warned_no_httpx``, so only the *first* name ever logs them. Success logs
nothing at all. So the return value decides the branch and the records supply
only the reason.

**Why the wrapper drains inside itself.** The records belonging to the LLM call
are available the instant ``gemma_ipa`` returns, *before* ``g2p()`` runs the
eSpeak fallback and ``g2p._fallback`` logs its own line. Draining after
``g2p()`` returns would mix the two and attribute a floor-side message to the
model. So the drain happens in the patched function, on the calling thread.

**``llm_unconfigured`` is not a row branch.** It is a static, process-wide
condition, so it is asserted once at start-up by ``require_configured()`` and
aborts the run. Treating it as a row branch would be actively misleading: the
dedup above means name #1 would carry it and names #2..n would carry nothing
at all, so a run against an unconfigured endpoint reads as one explained row
followed by thousands of unexplainable ones.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The four row-level branches. `llm_unconfigured` is deliberately absent --
# see the module docstring.
BRANCHES = ("llm_accepted", "llm_rejected", "llm_error", "llm_empty")

# Message prefixes, each copied from the `log.warning` call that emits it.
# Checked against the source by `test_llm.py::TestPrefixesMatchTheApp`, which
# reads gemma.py and fails if any of these stops appearing in it -- because a
# reworded log line would otherwise turn every row into an unclassified raise.
REJECTED_PREFIX = "Gemma IPA rejected"            # gemma.py:151,187-218
EMPTY_CONTENT_PREFIX = "Gemma returned empty content"   # gemma.py:312
ERROR_PREFIXES = (
    "Gemma request failed",                        # gemma.py:305
    "Gemma HTTP ",                                 # gemma.py:294
)
UNCONFIGURED_PREFIXES = (
    "Gemma not configured",                        # gemma.py:253
    "httpx not installed",                         # gemma.py:286
)


class Unclassified(RuntimeError):
    """A ``None`` the records cannot explain. Raised, never silently bucketed.

    This is what makes Unit 4's done-criterion -- "every row carries exactly
    one branch, none unclassified" -- true by construction rather than by
    hope.
    """


class NotConfigured(RuntimeError):
    """Raised by ``require_configured`` before any name is called."""


@dataclass(frozen=True)
class Call:
    """One LLM call: what came back, and which branch that puts it in."""

    name: str
    returned: str | None
    branch: str
    reason: str | None
    log_messages: tuple[str, ...]


def classify(
    returned: str | None, records: list[logging.LogRecord]
) -> tuple[str, str | None]:
    """Return ``(branch, reason)`` for one call. Raises on an unexplained None.

    ``records`` are the ``LogRecord`` objects rather than formatted strings
    because the reason for ``llm_error`` lives in ``record.args[0]``, not in
    the rendered message: ``gemma.py:305`` logs
    ``"Gemma request failed (%s: %s)"`` with ``type(e).__name__`` first, and
    that type is the only thing separating a ``ReadTimeout`` from a
    ``KeyError`` (P6a).
    """
    if returned is not None:
        # Success logs nothing, so the return value is the only evidence --
        # and it is authoritative.
        return "llm_accepted", None

    messages = [r.getMessage() for r in records]

    # Order matters. Empty content and a rejection are different findings:
    # the first means the model produced nothing to judge, the second means it
    # produced something the sanitizer refused. gemma.py checks `if not
    # content` *before* calling _sanitize_ipa, so the two cannot both fire for
    # one call -- but asserting the order keeps that true if gemma.py changes.
    for record, message in zip(records, messages):
        if message.startswith(EMPTY_CONTENT_PREFIX):
            return "llm_empty", "empty content"

    for record, message in zip(records, messages):
        if message.startswith(REJECTED_PREFIX):
            return "llm_rejected", _rejection_reason(message)

    for record, message in zip(records, messages):
        for prefix in ERROR_PREFIXES:
            if message.startswith(prefix):
                return "llm_error", _error_reason(record, message, prefix)

    for message in messages:
        for prefix in UNCONFIGURED_PREFIXES:
            if message.startswith(prefix):
                raise NotConfigured(
                    f"the LLM reported itself unconfigured mid-run: {message!r}. "
                    "require_configured() is meant to catch this before any "
                    "name is called."
                )

    raise Unclassified(
        f"gemma_ipa returned None for {len(messages)} log record(s) that "
        f"explain nothing: {messages!r}.\n"
        "The most likely cause is an unconfigured endpoint: gemma.py dedups "
        "its 'not configured' and 'httpx not installed' warnings "
        "process-wide (_warned_unconfigured / _warned_no_httpx), so only the "
        "FIRST name logs them and every later one looks exactly like this. "
        "Check settings.gemma_base_url / gemma_model / gemma_api_key."
    )


def _rejection_reason(message: str) -> str:
    """The sanitizer's own words, after the name it was rejected for.

    ``gemma.py`` formats every rejection as
    ``"Gemma IPA rejected for name=%r: <reason>"``, so the reason is whatever
    follows the first ``": "`` after the name. Six distinct reasons share that
    prefix and they are separate findings -- "exceeds the 80-char ceiling" is
    a truncated reasoning trace, "reads as prose" is a chatty model.
    """
    marker = ": "
    head, sep, tail = message.partition(marker)
    return tail.strip() if sep else message


def _error_reason(
    record: logging.LogRecord, message: str, prefix: str
) -> str:
    """The exception class, or the HTTP status -- the separable part (P6a)."""
    if prefix == "Gemma HTTP " and record.args:
        return f"HTTP {record.args[0]}"
    if record.args:
        # type(e).__name__ is the first arg of "Gemma request failed (%s: %s)".
        return str(record.args[0])
    return message


class ThreadLogCapture(logging.Handler):
    """Collect watched records per *emitting thread*, for the LLM pool.

    **Attached to the root logger, which is not a detail** -- the same reason
    ``engines.LogWatch`` documents: phonemizer's ``get_logger()`` runs
    ``logger.handlers = []`` and ``BaseBackend.__init__`` calls it on every
    backend construction (``backend/base.py:72-73``), so a handler on a named
    logger is destroyed by the next ``warm()``. Propagation survives that
    wipe; the root does not.

    **Keyed by thread because the LLM stage runs on up to 4 workers.** A
    single shared list would hand one name's rejection reason to another
    name's row -- the same class of bug as the espeak phoneme race in
    ``g2p.py:40-60``, and just as invisible in the output.
    """

    def __init__(self, logger_prefixes: tuple[str, ...]) -> None:
        super().__init__(level=logging.DEBUG)
        self._prefixes = {p.split(".")[0] for p in logger_prefixes}
        self._by_thread: dict[int, list[logging.LogRecord]] = {}
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.split(".")[0] not in self._prefixes:
            return
        # record.thread is set by logging to the ident of the thread that
        # logged, which is the thread whose call this record belongs to.
        with self._lock:
            self._by_thread.setdefault(record.thread, []).append(record)

    @property
    def records(self) -> list[logging.LogRecord]:
        """This thread's undrained records, as a copy. For assertions."""
        with self._lock:
            return list(self._by_thread.get(threading.get_ident(), []))

    def drain(self) -> list[logging.LogRecord]:
        """Take and clear this thread's records."""
        with self._lock:
            return self._by_thread.pop(threading.get_ident(), [])

    def leftovers(self) -> dict[int, list[logging.LogRecord]]:
        """Records nobody drained. Non-empty means a call went unattributed."""
        with self._lock:
            return {k: list(v) for k, v in self._by_thread.items() if v}

    def __enter__(self) -> ThreadLogCapture:
        root = logging.getLogger()
        self._prior_level = root.level
        root.addHandler(self)
        if root.level > logging.WARNING or root.level == logging.NOTSET:
            root.setLevel(logging.WARNING)
        return self

    def __exit__(self, *exc) -> None:
        root = logging.getLogger()
        root.removeHandler(self)
        root.setLevel(self._prior_level)


# Where a wrapper leaves the Call it just classified, for `run()` to pick up.
# Thread-local for the same reason the capture is: with a pool, a module-level
# attribute would hand one worker's branch to another worker's row.
_stash = threading.local()


def take_call() -> Call | None:
    """Take the Call this thread's last ``gemma_ipa`` recorded, if any."""
    call = getattr(_stash, "call", None)
    _stash.call = None
    return call


class recorder:
    """Patch ``g2p.gemma_ipa`` so every call is classified as it happens.

    **The patch target is ``app.pronunciation.g2p.gemma_ipa``, not
    ``gemma.gemma_ipa``.** ``g2p.py:16`` does
    ``from .gemma import gemma_ipa``, which binds the function into ``g2p``'s
    own namespace at import time, and ``g2p()`` looks it up there. Patching
    the ``gemma`` module rebinds a name ``g2p`` never reads again, so the run
    would record nothing and every row would raise ``Unclassified``.
    """

    def __init__(self, capture: ThreadLogCapture) -> None:
        self._capture = capture

    def __enter__(self) -> recorder:
        from app.pronunciation import g2p

        self._module = g2p
        self._real = g2p.gemma_ipa

        real, capture = self._real, self._capture

        def recording_gemma_ipa(name: str) -> str | None:
            returned = real(name)
            # Drained here, inside the wrapper: g2p() runs espeak_ipa next on
            # a None, and g2p._fallback logs. Draining after g2p() returns
            # would file a floor-side message as the model's reason.
            records = capture.drain()
            branch, reason = classify(returned, records)
            _stash.call = Call(
                name=name,
                returned=returned,
                branch=branch,
                reason=reason,
                log_messages=tuple(r.getMessage() for r in records),
            )
            return returned

        g2p.gemma_ipa = recording_gemma_ipa
        return self

    def __exit__(self, *exc) -> None:
        self._module.gemma_ipa = self._real


def require_configured() -> dict[str, str]:
    """Abort before any name is called if ``today`` cannot actually run.

    Amendment B's ``llm_unconfigured``: a static, process-wide condition
    checked once rather than a row branch. Returns the model id and host for
    provenance.
    """
    try:
        import httpx  # noqa: F401
    except ImportError as e:
        raise NotConfigured(
            "httpx is not installed, so gemma_ipa() returns None for every "
            "name and `today` would measure the eSpeak floor while labelled "
            "as an LLM. Install it into the venv that runs the harness."
        ) from e

    from app.config import settings

    unset = [
        env for env, value in (
            ("GEMMA_BASE_URL", settings.gemma_base_url),
            ("GEMMA_MODEL", settings.gemma_model),
            ("GEMMA_API_KEY", settings.gemma_api_key),
        ) if not value
    ]
    if unset:
        raise NotConfigured(
            "`today` needs " + ", ".join(unset) + " set; config.py:50-52 "
            "defaults all three to None and there is no committed .env. "
            "Unset, gemma_ipa() returns None immediately and every row would "
            "be the eSpeak floor wearing an LLM's label. Put them in "
            "backend/.env -- env_file resolves against the process CWD, so a "
            "repo-root .env is not read when the harness runs from backend/."
        )
    return {
        "model_id": settings.gemma_model,
        "model_host": settings.gemma_base_url,
    }


# ---------------------------------------------------------------------------
# The probe, and the transport hook the probe exists to choose a field for.
# ---------------------------------------------------------------------------

# What a probe reports back. Everything here is read from the raw response
# body, because `gemma_ipa` discards all of it: it reads
# `data["choices"][0]["message"]["content"]` and nothing else, so a reasoning
# trace OpenRouter returned in a sibling field, a `finish_reason` of "length",
# and the reasoning token count are all invisible from inside the app's path.
PROBE_NAME = "Malgorzata"


@dataclass(frozen=True)
class Probe:
    """One raw request's complete answer, plus the branch it would land in."""

    model: str
    extra_payload: dict
    status: int
    content: str | None
    reasoning_text: str | None
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    sanitized: str | None
    branch: str
    reason: str | None
    error: str | None = None

    @property
    def reasons_by_default(self) -> bool | None:
        """Did this request produce a reasoning trace? None if unknowable."""
        if self.error or self.status != 200:
            return None
        if self.reasoning_tokens:
            return True
        if self.reasoning_text:
            return True
        if self.finish_reason == "length" and not self.content:
            # The budget went somewhere and it was not content.
            return True
        if self.content and _THINK_OPEN_IN(self.content):
            return True
        return False

    def render(self) -> str:
        lines = [
            f"  model            {self.model}",
            f"  extra payload    {self.extra_payload or '— (as-shipped)'}",
            f"  status           {self.status}",
            f"  finish_reason    {self.finish_reason}",
            f"  tokens           prompt={self.prompt_tokens} "
            f"completion={self.completion_tokens} "
            f"reasoning={self.reasoning_tokens}",
            f"  content          {self.content!r}",
            f"  reasoning field  "
            + (f"{len(self.reasoning_text)} chars: {self.reasoning_text[:80]!r}"
               if self.reasoning_text else "— (absent)"),
            f"  sanitized to     {self.sanitized!r}",
            f"  -> branch        {self.branch}"
            + (f"  ({self.reason})" if self.reason else ""),
            f"  reasons?         {self.reasons_by_default}",
        ]
        if self.error:
            lines.append(f"  error            {self.error}")
        return "\n".join(lines)


def _THINK_OPEN_IN(text: str) -> bool:
    """Is there an *unterminated* think block in this content?

    gemma.py's `_THINK_RE` requires both `<think>` and `</think>`
    (``gemma.py:59-62``), so a trace truncated at the 256-token cap is not
    stripped -- it falls through to the plausibility checks and is rejected
    for exceeding the 80-char ceiling. That is a different branch from an
    empty answer, and it is the case this helper names.
    """
    from app.pronunciation.gemma import _THINK_OPEN

    return _THINK_OPEN.lower() in text.lower()


def probe(extra_payload: dict | None = None, name: str = PROBE_NAME) -> Probe:
    """Send one request and report everything the app's path throws away.

    Unit 4's first step. The payload is built from ``gemma.py``'s own
    ``_SYSTEM`` and ``_FEWSHOT`` rather than a copy, so a prompt change in the
    app cannot leave the probe measuring a different request than the run
    does.

    **Covered by the paid-run gate as an explicit exemption:** one request of
    roughly 100 input tokens, a small fraction of a cent.
    """
    import json as _json

    import httpx

    from app.config import settings
    from app.pronunciation.gemma import _FEWSHOT, _SYSTEM, _sanitize_ipa

    extra = dict(extra_payload or {})
    payload = {
        "model": settings.gemma_model,
        "messages": (
            [{"role": "system", "content": _SYSTEM}]
            + list(_FEWSHOT)
            + [{"role": "user", "content": name}]
        ),
        # Identical to gemma.py:265-275. Kept in step by test_llm.py.
        "temperature": 0,
        "max_tokens": 256,
        "stream": False,
        **extra,
    }
    url = settings.gemma_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.gemma_api_key}",
        "Content-Type": "application/json",
    }

    blank = dict(
        model=settings.gemma_model, extra_payload=extra, status=0,
        content=None, reasoning_text=None, finish_reason=None,
        prompt_tokens=None, completion_tokens=None, reasoning_tokens=None,
        sanitized=None, branch="llm_error", reason=None,
    )
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, json=payload, headers=headers)
    except Exception as e:
        return Probe(**{**blank,
                        "reason": type(e).__name__,
                        "error": f"{type(e).__name__}: {e}"})

    if resp.status_code != 200:
        return Probe(**{**blank,
                        "status": resp.status_code,
                        "reason": f"HTTP {resp.status_code}",
                        "error": resp.text[:400]})
    try:
        data = resp.json()
        message = data["choices"][0]["message"]
    except Exception as e:
        return Probe(**{**blank,
                        "status": resp.status_code,
                        "reason": type(e).__name__,
                        "error": f"{type(e).__name__}: {e} "
                                 f"body={resp.text[:300]!r}"})

    content = message.get("content")
    # OpenRouter returns a reasoning trace in a sibling field rather than
    # inline, unless the provider inlines it. Both shapes are checked, since
    # which one happens is the thing the probe is here to find out.
    reasoning_text = message.get("reasoning") or (
        _json.dumps(message["reasoning_details"])
        if message.get("reasoning_details") else None
    )
    usage = data.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}

    # The branch this would have landed in, computed by the app's own
    # sanitizer rather than predicted.
    captured: list[logging.LogRecord] = []

    class _Grab(logging.Handler):
        def emit(self, record):
            captured.append(record)

    grab = _Grab(level=logging.DEBUG)
    app_log = logging.getLogger("app.pronunciation.gemma")
    app_log.addHandler(grab)
    prior = app_log.level
    app_log.setLevel(logging.DEBUG)
    try:
        if not content:
            sanitized = None
            branch, reason = "llm_empty", "empty content"
        else:
            sanitized = _sanitize_ipa(content, name=name)
            branch, reason = classify(sanitized, captured)
    finally:
        app_log.removeHandler(grab)
        app_log.setLevel(prior)

    return Probe(
        model=settings.gemma_model,
        extra_payload=extra,
        status=resp.status_code,
        content=content,
        reasoning_text=reasoning_text,
        finish_reason=(data["choices"][0] or {}).get("finish_reason"),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        reasoning_tokens=details.get("reasoning_tokens"),
        sanitized=sanitized,
        branch=branch,
        reason=reason,
    )


# The candidate levers, in the order the probe should try them. The first one
# that makes `reasons_by_default` False is the one the run uses.
#
# **`chat_template_kwargs` is last, and is expected to do nothing.** It is the
# field the brief prescribes, and it is listed in `supported_parameters` for
# **0 of the 469 models** OpenRouter serves (measured 2026-11-10), while
# `reasoning` is listed for every endpoint of both M1 and M2. OpenRouter's
# routing docs say providers "can still receive the request, but will ignore
# unknown parameters" -- so sending it would most likely produce a
# `thinking-off` configuration that is byte-identical in behaviour to
# `as-shipped` while being labelled as a modified request. That is a
# measurement bug, not a conservative choice, which is why the probe tries the
# documented lever first and `require_parameters` exists below.
THINKING_OFF_CANDIDATES = (
    {"reasoning": {"enabled": False}},
    {"reasoning": {"effort": "none"}},
    {"chat_template_kwargs": {"enable_thinking": False}},
)

# Turns a silently-ignored parameter into a visible routing failure:
# "When you set `require_parameters` to `true`, the request won't even be
# routed to that provider." Sent alongside a candidate, it distinguishes
# "the lever worked" from "the lever was dropped and the model wasn't
# thinking anyway".
REQUIRE_PARAMETERS = {"provider": {"require_parameters": True}}


class transport_hook:
    """Add fields to ``gemma_ipa``'s outgoing JSON, at the ``httpx`` layer.

    The payload is a fixed dict built inside ``gemma_ipa``
    (``gemma.py:265-275``) and both thinking-control fields sit commented out,
    so there is no way to add one from outside without either editing
    ``backend/app/`` -- which this brief may not touch -- or intercepting the
    transport. This patches ``httpx.Client.post`` for the duration of a block
    and merges ``extra`` into the JSON body.

    **Scoped to the gemma endpoint by URL**, so nothing else in the process
    (the dataset download, the Kokoro config fetch) has its body rewritten.

    A run using this **must** stamp its provenance as a modified request: it is
    no longer exactly today's path, and a summary that does not say so is
    claiming a measurement it did not make.
    """

    def __init__(self, extra: dict, url_contains: str = "/chat/completions"):
        self.extra = dict(extra)
        self.url_contains = url_contains
        self.calls = 0

    def __enter__(self) -> transport_hook:
        import httpx

        self._httpx = httpx
        self._real_post = httpx.Client.post
        real_post, extra, needle = self._real_post, self.extra, self.url_contains
        hook = self

        def post(client_self, url, *args, **kwargs):
            if needle in str(url) and isinstance(kwargs.get("json"), dict):
                kwargs["json"] = {**kwargs["json"], **extra}
                hook.calls += 1
            return real_post(client_self, url, *args, **kwargs)

        httpx.Client.post = post
        return self

    def __exit__(self, *exc) -> None:
        self._httpx.Client.post = self._real_post

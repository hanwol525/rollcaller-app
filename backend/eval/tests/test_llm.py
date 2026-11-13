"""The branch classifier, tested offline — before any paid call.

Unit 4's done-criterion is "every row carries exactly one branch, none
unclassified". That is a claim about a classifier, so it is testable without
spending anything, and it is tested here two ways:

**Synthetic records** pin the classifier's contract -- precedence between
branches, what the reason is, and that an unexplained ``None`` raises.

**Real records, through gemma.py's real code paths** pin the thing synthetic
records cannot: that the prefixes still match. ``classify`` recognises a
branch by the opening words of a log message, so a reworded ``log.warning`` in
the app would turn every row into an ``Unclassified`` raise mid-run -- after
the money was spent. These tests make ``gemma.py`` emit its genuine messages
by driving its genuine branches with a patched transport, so a rewording fails
here instead, for free.
"""
from __future__ import annotations

import logging
import threading

import pytest

from eval.tests._deps import HTTPX_AVAILABLE

needs_httpx = pytest.mark.skipif(not HTTPX_AVAILABLE, reason="httpx not installed")


def record(message: str, *args, name: str = "app.pronunciation.gemma"):
    """A LogRecord shaped exactly as logging builds one."""
    return logging.LogRecord(
        name=name, level=logging.WARNING, pathname=__file__, lineno=1,
        msg=message, args=args, exc_info=None,
    )


class TestTheBranchSet:
    def test_unconfigured_is_not_a_row_branch(self):
        """Amendment B: it is a process-wide start-up condition.

        As a row branch it would be actively misleading. gemma.py dedups that
        warning process-wide (`_warned_unconfigured`), so name #1 would carry
        the branch and names #2..n would carry nothing -- one explained row
        followed by thousands of unexplainable ones.
        """
        from eval import llm
        assert "llm_unconfigured" not in llm.BRANCHES
        assert set(llm.BRANCHES) == {
            "llm_accepted", "llm_rejected", "llm_error", "llm_empty"}


class TestClassifySyntheticRecords:
    def test_a_returned_string_is_accepted_with_no_records_at_all(self):
        """Success logs nothing (P6), so the return value is the only
        evidence -- and it is sufficient."""
        from eval import llm
        assert llm.classify("ˈalitʃe", []) == ("llm_accepted", None)

    def test_the_return_value_beats_a_stale_record(self):
        """If a record somehow survives from an earlier call, a string back
        from the wrapper still means accepted. The alternative -- letting a
        log line override the actual answer -- is how a working model gets
        recorded as broken."""
        from eval import llm
        branch, _ = llm.classify(
            "ˈalitʃe", [record("Gemma IPA rejected for name='x': prose")])
        assert branch == "llm_accepted"

    def test_empty_content_is_its_own_branch(self):
        """An empty answer is not the same finding as a rejected one: the
        model produced nothing to judge, rather than something refused."""
        from eval import llm
        branch, reason = llm.classify(None, [
            record("Gemma returned empty content for name=%r — using the "
                   "eSpeak floor", "Alice")])
        assert branch == "llm_empty"
        assert reason == "empty content"

    def test_a_rejection_carries_the_sanitizers_own_words(self):
        from eval import llm
        branch, reason = llm.classify(None, [
            record("Gemma IPA rejected for name=%r: %d chars exceeds the "
                   "%d-char ceiling (text=%.120r)", "Alice", 310, 80, "x" * 310)])
        assert branch == "llm_rejected"
        assert "exceeds the 80-char ceiling" in reason

    def test_an_error_carries_the_exception_class_not_the_template(self):
        """P6a: four distinct operational faults share one template, and
        `record.args[0]` -- type(e).__name__ -- is the only thing separating
        them. A classifier that kept the rendered message would report
        'Gemma request failed' for a timeout and a KeyError alike."""
        from eval import llm
        for exc in ("ReadTimeout", "ConnectError", "JSONDecodeError", "KeyError"):
            branch, reason = llm.classify(None, [
                record("Gemma request failed (%s: %s) — using the eSpeak "
                       "floor", exc, "detail")])
            assert branch == "llm_error"
            assert reason == exc

    def test_an_http_error_carries_the_status(self):
        from eval import llm
        branch, reason = llm.classify(None, [
            record("Gemma HTTP %s from %s — using the eSpeak floor "
                   "(body=%.200r)", 429, "http://x/chat/completions", "slow down")])
        assert branch == "llm_error"
        assert reason == "HTTP 429"

    def test_empty_content_wins_over_a_rejection(self):
        """gemma.py checks `if not content` before calling _sanitize_ipa, so
        both cannot fire for one call. Asserting the precedence keeps that
        true if gemma.py ever changes."""
        from eval import llm
        branch, _ = llm.classify(None, [
            record("Gemma IPA rejected for name='x': nothing left"),
            record("Gemma returned empty content for name='x'"),
        ])
        assert branch == "llm_empty"

    def test_an_unconfigured_record_raises_rather_than_scoring(self):
        from eval import llm
        for message in ("Gemma not configured (unset: GEMMA_API_KEY) — using "
                        "the eSpeak floor",
                        "httpx not installed — using the eSpeak floor"):
            with pytest.raises(llm.NotConfigured):
                llm.classify(None, [record(message)])

    def test_an_unexplained_none_raises_and_names_the_likely_cause(self):
        """What makes "none unclassified" true by construction rather than by
        hope. The message has to name the dedup, because an unconfigured
        endpoint is overwhelmingly the likeliest cause and it produces
        exactly this shape for every name after the first."""
        from eval import llm
        with pytest.raises(llm.Unclassified) as excinfo:
            llm.classify(None, [])
        message = str(excinfo.value)
        assert "_warned_unconfigured" in message
        assert "FIRST name" in message

    def test_records_from_other_loggers_do_not_explain_a_none(self):
        """A phonemizer or g2p record is not evidence about the LLM."""
        from eval import llm
        with pytest.raises(llm.Unclassified):
            llm.classify(None, [
                record("words count mismatch on 100.0% of the lines",
                       name="phonemizer"),
                record("no IPA for name='x'", name="app.pronunciation.g2p"),
            ])

    def test_every_branch_it_can_return_is_declared(self):
        from eval import llm
        cases = [
            ("ok", []),
            (None, [record("Gemma returned empty content for name='x'")]),
            (None, [record("Gemma IPA rejected for name='x': prose")]),
            (None, [record("Gemma request failed (%s: %s)", "ReadTimeout", "x")]),
        ]
        got = {llm.classify(r, recs)[0] for r, recs in cases}
        assert got == set(llm.BRANCHES)


class TestEveryRejectionReasonIsDistinguishable:
    """Six reasons share the "Gemma IPA rejected" prefix and they are
    different findings. "exceeds the 80-char ceiling" is a truncated reasoning
    trace; "reads as prose" is a chatty model; "nothing left after cleaning"
    is a model that answered in a script the sanitizer stripped entirely.
    Collapsing them would hide the one Unit 4 exists to detect.
    """

    @pytest.mark.parametrize("raw,fragment", [
        ("", "empty model output"),
        ("```\n```", "nothing left after cleaning"),
        ("x" * 310, "exceeds the 80-char ceiling"),
        # No punctuation here on purpose: _PUNCT_RE is checked before
        # _PROSE_RE, so a comma would mask the prose reason entirely.
        ("the pronunciation", "prose word"),
        ("ˈalitʃe, roughly", "ASCII sentence punctuation"),
    ])
    def test_each_reason_survives_classification(self, raw, fragment):
        """Driven through gemma.py's REAL sanitizer, so these are the app's
        own messages rather than my transcription of them."""
        from app.pronunciation.gemma import _sanitize_ipa

        from eval import llm
        captured = []

        class Grab(logging.Handler):
            def emit(self, rec):
                captured.append(rec)

        log = logging.getLogger("app.pronunciation.gemma")
        handler = Grab(level=logging.DEBUG)
        log.addHandler(handler)
        try:
            result = _sanitize_ipa(raw, name="Alice")
        finally:
            log.removeHandler(handler)

        assert result is None, f"{raw!r} was not rejected; test premise is stale"
        branch, reason = llm.classify(result, captured)
        assert branch == "llm_rejected"
        assert fragment in reason, f"got reason {reason!r}"


class TestPrefixesMatchTheApp:
    """The drift guard. ``classify`` matches on the opening words of a log
    message, so a reworded ``log.warning`` in gemma.py would turn every row
    into an Unclassified raise -- mid-run, after the money was spent.
    """

    def test_every_prefix_still_appears_in_gemma_py(self):
        import inspect

        from app.pronunciation import gemma

        from eval import llm
        source = inspect.getsource(gemma)
        expected = (
            (llm.REJECTED_PREFIX,)
            + (llm.EMPTY_CONTENT_PREFIX,)
            + llm.ERROR_PREFIXES
            + llm.UNCONFIGURED_PREFIXES
        )
        missing = [p for p in expected if p not in source]
        assert not missing, (
            f"gemma.py no longer contains {missing!r}. classify() keys off "
            "these, so every `today` row would raise Unclassified mid-run."
        )

    def test_the_probe_payload_matches_the_app_payload(self):
        """The probe exists to predict what a run will do. If its request
        differs from gemma.py's, it predicts a different request."""
        import inspect

        from app.pronunciation import gemma
        source = inspect.getsource(gemma.gemma_ipa)
        for field in ('"temperature": 0', '"max_tokens": 256', '"stream": False'):
            assert field in source, (
                f"gemma.py no longer sends {field}; eval/llm.py's probe still "
                "does, so the probe would measure a different request."
            )

    def test_gemma_still_cannot_disable_reasoning_from_inside(self):
        """P17's second half, as a test. If someone adds the field to the app,
        the whole two-configuration design in the brief becomes unnecessary
        and this test says so."""
        import inspect

        from app.pronunciation import gemma
        source = inspect.getsource(gemma.gemma_ipa)
        assert "chat_template_kwargs" not in source
        # Both thinking-control fields are commented out, so they are present
        # as text but not as payload keys.
        assert '# "thinking"' in source or '"thinking": ' not in source


@needs_httpx
class TestRealGemmaBranchesOffline:
    """gemma.py's genuine error paths, driven offline with a patched
    transport. No network, no key, no money -- but the log records are real.
    """

    @pytest.fixture
    def configured(self, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "gemma_base_url", "http://127.0.0.1:9/v1")
        monkeypatch.setattr(settings, "gemma_model", "test/model")
        monkeypatch.setattr(settings, "gemma_api_key", "test-key")
        # gemma.py dedups its unconfigured warnings process-wide; reset so one
        # test cannot silence another.
        from app.pronunciation import gemma
        monkeypatch.setattr(gemma, "_warned_unconfigured", False)
        monkeypatch.setattr(gemma, "_warned_no_httpx", False)

    def _call(self, monkeypatch, post):
        """Run the real gemma_ipa with a patched httpx.Client.post."""
        import httpx

        from app.pronunciation.gemma import gemma_ipa

        from eval import llm
        monkeypatch.setattr(httpx.Client, "post", post)
        with llm.ThreadLogCapture(("app.pronunciation.gemma",)) as capture:
            returned = gemma_ipa("Alice")
            return returned, capture.drain()

    def test_a_transport_exception_classifies_as_llm_error(
        self, monkeypatch, configured
    ):
        import httpx

        from eval import llm

        def post(self, url, *a, **k):
            raise httpx.ConnectError("refused")

        returned, records = self._call(monkeypatch, post)
        assert returned is None
        assert llm.classify(returned, records) == ("llm_error", "ConnectError")

    def test_a_non_200_classifies_as_llm_error_with_the_status(
        self, monkeypatch, configured
    ):
        from eval import llm

        class Resp:
            status_code = 429
            text = "rate limited"

        returned, records = self._call(monkeypatch, lambda s, u, *a, **k: Resp())
        assert returned is None
        assert llm.classify(returned, records) == ("llm_error", "HTTP 429")

    def test_empty_content_classifies_as_llm_empty(self, monkeypatch, configured):
        """The branch the brief asks for conditionally. It is reachable: a
        model whose whole 256-token budget went to a reasoning trace returns
        content="" with finish_reason="length"."""
        from eval import llm

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"content": ""},
                                     "finish_reason": "length"}]}

        returned, records = self._call(monkeypatch, lambda s, u, *a, **k: Resp())
        assert returned is None
        assert llm.classify(returned, records) == ("llm_empty", "empty content")

    def test_a_missing_field_classifies_as_llm_error_with_keyerror(
        self, monkeypatch, configured
    ):
        """P6a's fourth fault sharing the one template."""
        from eval import llm

        class Resp:
            status_code = 200
            text = "{}"

            def json(self):
                return {}

        returned, records = self._call(monkeypatch, lambda s, u, *a, **k: Resp())
        assert returned is None
        branch, reason = llm.classify(returned, records)
        assert branch == "llm_error"
        assert reason == "KeyError"

    def test_a_good_answer_classifies_as_accepted_and_logs_nothing(
        self, monkeypatch, configured
    ):
        from eval import llm

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {"content": "ˈalitʃe"}}]}

        returned, records = self._call(monkeypatch, lambda s, u, *a, **k: Resp())
        assert returned == "ˈalitʃe"
        assert records == [], "success must log nothing (P6)"
        assert llm.classify(returned, records) == ("llm_accepted", None)

    def test_a_terminated_thinking_block_is_stripped_and_accepted(
        self, monkeypatch, configured
    ):
        """Contradicts the brief's R8 mechanism for this case.
        `_sanitize_ipa` step 1 strips `<think>...</think>` already
        (gemma.py:157), so a model that reasons inline and still finishes
        lands on llm_accepted, not the floor."""
        from eval import llm

        open_tag, close_tag = chr(60) + "think" + chr(62), chr(60) + "/think" + chr(62)

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {
                    "content": f"{open_tag}Polish, so sz is ʃ{close_tag}mawɡɔʒata"}}]}

        returned, records = self._call(monkeypatch, lambda s, u, *a, **k: Resp())
        assert returned == "mawɡɔʒata"
        assert llm.classify(returned, records)[0] == "llm_accepted"

    def test_an_unterminated_thinking_block_is_rejected_for_length(
        self, monkeypatch, configured
    ):
        """And the case the brief's R8 is right about, via a branch its text
        does not name. `_THINK_RE` requires BOTH tags (gemma.py:59-62), so a
        trace truncated at the 256-token cap is not stripped -- it reaches the
        plausibility checks and is rejected for exceeding 80 chars.
        """
        from eval import llm

        open_tag = chr(60) + "think" + chr(62)

        class Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"choices": [{"message": {
                    "content": open_tag + "The name is Polish. " * 20},
                    "finish_reason": "length"}]}

        returned, records = self._call(monkeypatch, lambda s, u, *a, **k: Resp())
        assert returned is None
        branch, reason = llm.classify(returned, records)
        assert branch == "llm_rejected"
        assert "ceiling" in reason or "prose" in reason


class TestThreadLogCapture:
    def test_records_are_kept_per_thread(self):
        """With a pool, a shared list hands one name's rejection reason to
        another name's row -- the same class of bug as the espeak phoneme
        race, and just as invisible in the output."""
        from eval import llm
        seen = {}
        with llm.ThreadLogCapture(("app.pronunciation.gemma",)) as capture:
            def work(tag):
                logging.getLogger("app.pronunciation.gemma").warning(
                    "Gemma IPA rejected for name=%r: prose", tag)
                seen[tag] = [r.getMessage() for r in capture.drain()]

            threads = [threading.Thread(target=work, args=(f"n{i}",))
                       for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert len(seen) == 6
        for tag, messages in seen.items():
            assert len(messages) == 1, f"{tag} saw {messages}"
            assert tag in messages[0], f"{tag} got another thread's record"

    def test_the_main_thread_does_not_see_a_workers_records(self):
        from eval import llm
        with llm.ThreadLogCapture(("app.pronunciation.gemma",)) as capture:
            t = threading.Thread(target=lambda: logging.getLogger(
                "app.pronunciation.gemma").warning("worker only"))
            t.start()
            t.join()
            assert capture.drain() == []
            assert capture.leftovers(), "the worker's record was lost entirely"

    def test_it_filters_by_logger(self):
        from eval import llm
        with llm.ThreadLogCapture(("app.pronunciation.gemma",)) as capture:
            logging.getLogger("httpx").warning("unrelated")
            logging.getLogger("app.pronunciation.gemma").warning("mine")
            assert [r.getMessage() for r in capture.drain()] == ["mine"]

    def test_it_attaches_to_the_root_not_the_named_logger(self):
        """Same gotcha engines.LogWatch documents: phonemizer's get_logger()
        wipes handlers on every backend construction."""
        from eval import llm
        with llm.ThreadLogCapture(("phonemizer",)) as capture:
            assert capture in logging.getLogger().handlers
            assert capture not in logging.getLogger("phonemizer").handlers


class TestTheRecordingWrapper:
    def test_it_patches_the_binding_g2p_actually_reads(self, monkeypatch):
        """Amendment B's landmine. g2p.py:16 does `from .gemma import
        gemma_ipa`, binding it into g2p's namespace at import time. Patching
        `gemma.gemma_ipa` rebinds a name g2p never reads again, so the run
        would record nothing and every row would raise Unclassified."""
        from app.pronunciation import g2p, gemma

        from eval import llm
        before_g2p, before_gemma = g2p.gemma_ipa, gemma.gemma_ipa
        with llm.ThreadLogCapture(("app.pronunciation.gemma",)) as capture:
            with llm.recorder(capture):
                assert g2p.gemma_ipa is not before_g2p
                assert gemma.gemma_ipa is before_gemma, (
                    "the wrapper patched the gemma module; g2p() reads its "
                    "own binding and would be unaffected")
        assert g2p.gemma_ipa is before_g2p

    def test_it_stashes_a_call_per_name(self, monkeypatch):
        from app.pronunciation import g2p

        from eval import llm
        monkeypatch.setattr(g2p, "gemma_ipa", lambda name: "ˈalitʃe")
        with llm.ThreadLogCapture(("app.pronunciation.gemma",)) as capture:
            with llm.recorder(capture):
                assert g2p.gemma_ipa("Alice") == "ˈalitʃe"
                call = llm.take_call()
        assert call.name == "Alice"
        assert call.branch == "llm_accepted"
        assert llm.take_call() is None, "a Call must be taken only once"

    def test_it_drains_before_the_espeak_fallback_can_log(self, monkeypatch):
        """Why the drain lives inside the wrapper. g2p() is
        `gemma_ipa(text) or espeak_ipa(text)`, so on a None the floor runs
        next and g2p._fallback logs its own line. Draining after g2p()
        returned would file that floor-side message as the model's reason.
        """
        from app.pronunciation import g2p

        from eval import llm

        def failing_gemma(name):
            logging.getLogger("app.pronunciation.gemma").warning(
                "Gemma IPA rejected for name=%r: reads as prose", name)
            return None

        monkeypatch.setattr(g2p, "gemma_ipa", failing_gemma)
        monkeypatch.setattr(g2p, "espeak_ipa", lambda *a, **k: (
            logging.getLogger("app.pronunciation.g2p").warning(
                "no IPA for name='Alice'") or "fallback"))

        with llm.ThreadLogCapture(
            ("app.pronunciation.gemma", "app.pronunciation.g2p")
        ) as capture:
            with llm.recorder(capture):
                assert g2p.g2p("Alice") == "fallback"
                call = llm.take_call()
                floor_side = [r.getMessage() for r in capture.drain()]

        assert call.branch == "llm_rejected"
        assert "reads as prose" in call.reason
        assert all("no IPA" not in m for m in call.log_messages), (
            "the floor's message was filed as the model's reason")
        assert any("no IPA" in m for m in floor_side)

    def test_an_unclassifiable_call_raises_through_the_wrapper(self, monkeypatch):
        from app.pronunciation import g2p

        from eval import llm
        monkeypatch.setattr(g2p, "gemma_ipa", lambda name: None)
        with llm.ThreadLogCapture(("app.pronunciation.gemma",)) as capture:
            with llm.recorder(capture):
                with pytest.raises(llm.Unclassified):
                    g2p.gemma_ipa("Alice")


class TestRequireConfigured:
    def test_it_names_every_unset_variable_and_where_to_put_it(self, monkeypatch):
        from app.config import settings

        from eval import llm
        monkeypatch.setattr(settings, "gemma_base_url", None)
        monkeypatch.setattr(settings, "gemma_model", None)
        monkeypatch.setattr(settings, "gemma_api_key", None)
        with pytest.raises(llm.NotConfigured) as excinfo:
            llm.require_configured()
        message = str(excinfo.value)
        for env in ("GEMMA_BASE_URL", "GEMMA_MODEL", "GEMMA_API_KEY"):
            assert env in message
        # The CWD trap: env_file resolves against the process CWD, and the
        # harness runs from backend/, so a repo-root .env is not read.
        assert "backend/.env" in message

    def test_a_partial_configuration_is_refused(self, monkeypatch):
        """gemma_ipa() needs all three; with any one unset it returns None
        immediately, so the run would be the eSpeak floor wearing an LLM's
        label."""
        from app.config import settings

        from eval import llm
        monkeypatch.setattr(settings, "gemma_base_url", "http://x/v1")
        monkeypatch.setattr(settings, "gemma_model", "m")
        monkeypatch.setattr(settings, "gemma_api_key", None)
        with pytest.raises(llm.NotConfigured, match="GEMMA_API_KEY"):
            llm.require_configured()

    @needs_httpx
    def test_a_full_configuration_returns_what_provenance_needs(self, monkeypatch):
        from app.config import settings

        from eval import llm
        monkeypatch.setattr(settings, "gemma_base_url", "https://host/api/v1")
        monkeypatch.setattr(settings, "gemma_model", "vendor/model")
        monkeypatch.setattr(settings, "gemma_api_key", "secret")
        got = llm.require_configured()
        assert got == {"model_id": "vendor/model",
                       "model_host": "https://host/api/v1"}
        assert "secret" not in str(got), "the key must never reach provenance"


@needs_httpx
class TestTheTransportHook:
    def test_it_merges_extra_fields_into_the_outgoing_body(self):
        from eval import llm
        seen = {}

        import httpx
        real = httpx.Client.post
        try:
            httpx.Client.post = lambda s, url, *a, **k: seen.update(k) or "resp"
            with llm.transport_hook({"reasoning": {"enabled": False}}) as hook:
                httpx.Client().post("http://x/chat/completions",
                                    json={"model": "m", "max_tokens": 256})
            assert seen["json"]["reasoning"] == {"enabled": False}
            assert seen["json"]["max_tokens"] == 256, "it must add, not replace"
            assert hook.calls == 1
        finally:
            httpx.Client.post = real

    def test_it_leaves_other_urls_alone(self):
        """Scoped by URL so the dataset download and the Kokoro config fetch
        do not get an LLM field spliced into their bodies."""
        from eval import llm
        seen = {}

        import httpx
        real = httpx.Client.post
        try:
            httpx.Client.post = lambda s, url, *a, **k: seen.update(k) or "resp"
            with llm.transport_hook({"reasoning": {"enabled": False}}) as hook:
                httpx.Client().post("https://huggingface.co/api/whatever",
                                    json={"unrelated": True})
            assert "reasoning" not in seen["json"]
            assert hook.calls == 0
        finally:
            httpx.Client.post = real

    def test_it_restores_post_on_exit_even_after_a_raise(self):
        from eval import llm

        import httpx
        before = httpx.Client.post
        with pytest.raises(ValueError):
            with llm.transport_hook({"a": 1}):
                raise ValueError("boom")
        assert httpx.Client.post is before


class TestTheThinkingOffCandidates:
    def test_the_documented_lever_is_tried_before_the_briefs_field(self):
        """Measured 2026-11-10: `chat_template_kwargs` appears in
        `supported_parameters` for 0 of the 469 models OpenRouter serves,
        while `reasoning` appears for every endpoint of both M1 and M2.
        OpenRouter's routing docs say providers "ignore unknown parameters",
        so sending the brief's field would most likely produce a
        `thinking-off` run that is behaviourally identical to `as-shipped`
        while being labelled a modified request. That is a measurement bug,
        so the documented lever is tried first.
        """
        from eval import llm
        keys = [list(c)[0] for c in llm.THINKING_OFF_CANDIDATES]
        assert keys.index("reasoning") < keys.index("chat_template_kwargs")

    def test_require_parameters_is_available_to_prove_a_lever_landed(self):
        """Turns a silently-ignored parameter into a visible routing failure,
        which is the only way to tell "the lever worked" from "the lever was
        dropped and the model wasn't thinking anyway"."""
        from eval import llm
        assert llm.REQUIRE_PARAMETERS == {"provider": {"require_parameters": True}}

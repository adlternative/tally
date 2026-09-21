#!/usr/bin/env python3
"""Checks for tally's offline logic: extraction, chunking, retrieval, request/answer mapping.

Run: python3 test_tally.py    (no network, never loads the real token)
"""
import contextlib
import json
from pathlib import Path

import tally as jev_ask

HTML = """<html><head><title>Ada Lovelace</title><style>b{color:red}</style>
<script>var leaked = 1;</script></head><body><nav>Home About</nav>
<p>Ada Lovelace was born in 1815.</p><div>She wrote notes on the Analytical Engine.</div>
</body></html>"""


def test_html_to_text():
    text, title = jev_ask.html_to_text(HTML)
    assert title == "Ada Lovelace", title
    assert "Ada Lovelace was born in 1815." in text
    assert "She wrote notes on the Analytical Engine." in text
    assert "leaked" not in text and "color:red" not in text, "script/style must be dropped"
    assert "\n\n" in text and "  " not in text, "blocks separated, runs collapsed"


def test_terms_both_scripts():
    t = jev_ask.terms("When was Ada Lovelace born? 阿达出生于哪一年")
    assert {"ada", "lovelace", "born"} <= t
    assert "when" not in t and "was" not in t, "stopwords dropped"
    assert "阿达" in t and "出生" in t, "CJK must yield bigrams, not one huge token"
    assert len(jev_ask.terms("Ada Lovelace")) == 2


def test_chunks():
    text = "First sentence here. Second sentence here! 第三句在这里。Fourth sentence here. " * 12
    cs = jev_ask.chunks(text, target=200, cap=200)
    assert len(cs) > 3
    assert all(len(c) <= 200 for c in cs), max(len(c) for c in cs)
    assert all(c.endswith((".", "!", "。")) for c in cs), "chunks must not cut mid-sentence"
    assert all(len(c) >= 20 for c in cs)
    assert jev_ask.chunks("short") == []


def test_rank_prefers_relevant_then_keeps_document_order():
    cands = ["Filler one about nothing.", "Ada Lovelace was born in 1815.", "Filler two about widgets.",
             "Filler three about gadgets.", "Lovelace wrote the first algorithm.", "Filler four about sprockets."]
    assert jev_ask.rank("When was Ada Lovelace born?", cands, k=2) == [1, 4]
    assert jev_ask.rank("Who wrote the first algorithm?", cands, k=1) == [4]


def test_rank_falls_back_when_nothing_matches():
    cands = ["Filler one about nothing.", "Ada Lovelace was born in 1815.", "Filler two about widgets."]
    # No lexical overlap (question in another language than the page): hand Jev the whole page
    # in document order instead of a bad top-k.
    assert jev_ask.rank("完全无关的中文问题", cands, k=1) == [0, 1, 2]


def test_parse_options():
    assert jev_ask.parse_options("看好\n不看好, 中立") == ["看好", "不看好", "中立"]
    assert jev_ask.parse_options("a，b、c") == ["a", "b", "c"]
    assert jev_ask.parse_options("dup,dup,,x") == ["dup", "x"], "blank and repeated entries dropped"
    assert jev_ask.parse_options("") == []


def test_build_request_shapes():
    """Each kind must produce the primitive the user asked for, with the question sent verbatim."""
    passage = jev_ask.build_request("passage", "https://x.test/a", "Q?", "page text", ["p0", "p1"], [])
    question = passage["questions"]["answer"]
    assert passage["model"] == jev_ask.MODEL
    assert list(passage["state"]) == ["url", "question", "page"]
    assert passage["state"] == {"url": "https://x.test/a", "question": "Q?", "page": "page text"}
    assert question["type"] == "choice"
    assert list(question["criteria"]) == ["c00", "c01", "none"], "stable keys plus a no-match option"
    assert "Q?" not in json.dumps(question["instructions"]), "question referenced by path, not duplicated"

    noul = jev_ask.build_request("noul", "u", "这篇讨论里大家看好吗？", "page")
    assert noul["questions"]["answer"] == {"type": "noul", "instructions": "这篇讨论里大家看好吗？"}

    choice = jev_ask.build_request("choice", "u", "态度？", "page", [], ["看好", "不看好"])
    assert choice["questions"]["answer"]["criteria"] == {"看好": None, "不看好": None}, "null = no extra rubric"

    score = jev_ask.build_request("score", "u", "多严重？", "page", [], ["轻", "中", "重"])
    assert score["questions"]["answer"]["criteria"] == ["轻", "中", "重"], "score levels stay ordered"


def test_api_errors_do_not_leak_the_token():
    from urllib.error import HTTPError
    original = jev_ask.urlopen

    def unauthorized(*args, **kwargs):
        raise HTTPError(jev_ask.API, 401, "Unauthorized", {}, None)

    jev_ask.urlopen = unauthorized
    try:
        jev_ask.post_json({"state": "s"}, "stub-token")
    except RuntimeError as e:
        assert "stub-token" not in str(e) and "401" in str(e), str(e)
    else:
        raise AssertionError("401 must raise")
    finally:
        jev_ask.urlopen = original


def test_transient_5xx_retried_and_4xx_not():
    """A 503 upstream blip must be retried; a 422 must surface at once (no wasted calls, no masking)."""
    import io
    from urllib.error import HTTPError
    original_urlopen, original_sleep = jev_ask.urlopen, jev_ask.time.sleep
    jev_ask.time.sleep = lambda seconds: None

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"answers": {}}'

    try:
        attempts = []

        def flaky(req, timeout=0):
            attempts.append(1)
            if len(attempts) < 3:
                raise HTTPError(jev_ask.API, 503, "no healthy upstream", {}, None)
            return _Response()

        jev_ask.urlopen = flaky
        assert jev_ask.post_json({"state": "s"}, "stub-token") == {"answers": {}}
        assert len(attempts) == 3, "503 must be retried"

        attempts.clear()

        def client_error(req, timeout=0):
            attempts.append(1)
            raise HTTPError(jev_ask.API, 422, "bad field", {}, io.BytesIO(b'{"detail": "state"}'))

        jev_ask.urlopen = client_error
        try:
            jev_ask.post_json({"state": "s"}, "stub-token")
        except RuntimeError as e:
            assert "422" in str(e) and "state" in str(e) and "stub-token" not in str(e), str(e)
        else:
            raise AssertionError("422 must raise")
        assert len(attempts) == 1, "4xx must not be retried"
    finally:
        jev_ask.urlopen, jev_ask.time.sleep = original_urlopen, original_sleep


def test_estimate_tokens_handles_cjk():
    assert jev_ask.estimate_tokens("你好世界") == 4, "CJK is about one token per character"
    assert jev_ask.estimate_tokens("a" * 360) == 100, "latin is about 3.6 chars per token"
    assert jev_ask.estimate_tokens("") == 0


def test_preview_reports_cost_before_asking():
    with _patched(PAGE, None):
        d = jev_ask.preview("https://x.test/a")
    assert d["domain"] == "x.test" and d["passages"] > 1
    assert d["tokens"] > 0 and d["usd"] > 0 and len(d["preview"]) <= 420


COMMENTS = ("<html><body><nav>Home About Login</nav>"
             + "".join(f'<tr class="athing"><td class="default"><div class="comtext">Comment {i} about zcode: '
                       + ("I like zcode a lot." if i % 3 == 0 else "zcode uploaded my git history, avoid it.")
                       + " padding padding padding</div></td></tr>" for i in range(1, 31))
             + "<footer>Also nav junk</footer></body></html>")


def test_extract_items_finds_the_repeating_container():
    items = jev_ask.extract_items(COMMENTS)
    assert len(items) == 30, len(items)
    assert all("padding padding" in t for t in items), "the comment body, not the row header"
    assert not any(t.startswith("Home") for t in items), "nav must not win: volume, not count"
    assert jev_ask.extract_items("<html><body><p>One paragraph only.</p></body></html>") == []


def test_items_for_falls_back_to_passages():
    page = {"html": "<html><body><p>single block</p></body></html>", "text": PAGE}
    items, mode = jev_ask.items_for(page)
    assert mode == "段落" and len(items) > 1
    items, mode = jev_ask.items_for({"html": COMMENTS, "text": PAGE})
    assert mode == "条目" and len(items) == 30


def test_guess_filter_picks_the_question_term_that_occurs():
    items = ["nothing here", "zcode is fine", "zcode again", "other"]
    assert jev_ask.guess_filter("\u7528\u6237\u5bf9 zcode \u770b\u597d\u7684\u6bd4\u4f8b", items) == "zcode"
    assert jev_ask.guess_filter("\u5b8c\u5168\u65e0\u5173\u7684\u95ee\u9898", items) == ""


def test_per_item_request_is_one_call_with_one_question_each():
    req = jev_ask.build_item_request("https://x.test/t", "T", ["a", "b", "c"], "\u770b\u597d\u5417\uff1f", "noul", [])
    assert len(req["questions"]) == 3, "one question per item, not one per request"
    assert list(req["questions"]) == ["i1", "i2", "i3"]
    assert req["state"]["items"][1] == {"id": 2, "text": "b"}
    assert req["questions"]["i2"]["instructions"]["item"] == "`items[1].text`", "backticked path"
    assert req["questions"]["i1"]["type"] == "noul"
    assert "T" in req["state"]["title"] and "chrome" not in json.dumps(req["state"]), "state is entries only"


def _item_answers(values):
    return {f"i{i + 1}": {"type": "noul", "noul": v} for i, v in enumerate(values)}


def test_aggregate_counts_in_code_not_in_the_model():
    items = [f"item {i}" for i in range(6)]
    rows, dist, lead, share, mean, uncertain, _people = jev_ask.aggregate("noul", _item_answers([0.9, 0.8, 0.5, 0.2, 0.1, 0.85]), items, [])
    by_label = {label: (round(s, 4), count) for label, s, count in dist}
    assert lead == "\u770b\u597d" and share == 0.5, (lead, share)
    assert by_label["\u770b\u597d"] == (0.5, 3) and by_label["\u4e0d\u770b\u597d"] == (0.3333, 2)
    assert by_label["\u4e0d\u786e\u5b9a"] == (0.1667, 1), "0.5 lands in the uncertain band, exactly once"
    assert uncertain == 1 and round(mean, 4) == 0.5583
    assert rows[0]["certainty"] <= rows[-1]["certainty"], "most uncertain first, for auditing"
    assert sum(count for _, _, count in dist) == 6, "every entry is counted once"


def test_aggregate_bands_are_exhaustive_and_disjoint():
    values = [0.6, 0.4, 0.599, 0.401, 1.0, 0.0]
    _, dist, _, _, _, _, _ = jev_ask.aggregate("noul", _item_answers(values), ["x"] * len(values), [])
    # 0.6/1.0 -> 看好, 0.4/0.0 -> 不看好 (edges inclusive), 0.599/0.401 -> 不确定. Every entry
    # lands in exactly one band, so the counts must sum to the number of entries.
    assert [count for _, _, count in dist] == [2, 2, 2], dist
    assert sum(count for _, _, count in dist) == len(values)


def test_aggregate_counts_people_not_just_posts():
    """'How many users' is not 'how many comments': one author posting three times is one vote."""
    items = ["a", "b", "c", "d"]
    authors = ["alice", "alice", "bob", "carol"]
    answers = _item_answers([0.9, 0.9, 0.1, 0.9])
    rows, dist, lead, share, mean, uncertain, people = jev_ask.aggregate("noul", answers, items, [], authors)
    assert [round(s, 3) for _, s, _ in dist] == [0.75, 0.0, 0.25], dist
    assert people["authors"] == 3, "three distinct authors"
    by_label = {label: (round(share, 3), count) for label, share, count in people["distribution"]}
    assert by_label["\u770b\u597d"] == (0.667, 2) and by_label["\u4e0d\u770b\u597d"] == (0.333, 1)
    assert rows[0]["author"] in ("alice", "bob", "carol")


def test_people_is_none_when_authors_are_missing():
    rows, _dist, _lead, _share, _mean, _uncertain, people = jev_ask.aggregate(
        "noul", _item_answers([0.9, 0.1]), ["a", "b"], [], ["", ""])
    assert people is None, "no authors -> no people breakdown, not a wrong one"


def test_aggregate_choice_counts_labels():
    labels = ["\u770b\u597d", "\u4e0d\u770b\u597d"]
    answers = {f"i{i + 1}": {"type": "choice", "choice": labels[i % 3 == 2 and 1 or 0], "confidence": 0.8}
               for i in range(3)}
    rows, dist, lead, share, mean, uncertain, _people = jev_ask.aggregate("choice", answers, ["a", "b", "c"], labels)
    assert lead == "\u770b\u597d" and dist[0][2] == 2 and dist[1][2] == 1
    assert mean is None, "a choice aggregate has no mean"


def test_ask_items_requires_a_filter_match():
    page = {"html": COMMENTS, "text": PAGE, "url": "u", "title": "T"}
    original = jev_ask.fetch_text
    jev_ask.fetch_text = lambda url, timeout=20: page
    try:
        try:
            jev_ask.ask("https://x.test/t", "q", token="stub-token", scope="items",
                        filter_term="\u5b8c\u5168\u4e0d\u5b58\u5728\u7684\u8bcd")
        except RuntimeError as e:
            assert "contains" in str(e), str(e)
        else:
            raise AssertionError("an empty filtered set must raise, not divide by zero")
    finally:
        jev_ask.fetch_text = original


def test_ask_items_end_to_end():
    page = {"html": COMMENTS, "text": PAGE, "url": "https://x.test/t", "title": "T"}
    seen = {}

    def post(payload, token, **kwargs):
        seen["payload"] = payload
        answers = {key: {"type": "noul", "noul": 0.9 if i % 3 == 0 else 0.1}
                   for i, key in enumerate(payload["questions"])}
        return {"model": "jev-1.13.0", "usage": {"input_tokens": 5000, "output_tokens": 300}, "answers": answers}

    original = jev_ask.fetch_text, jev_ask.post_json
    jev_ask.fetch_text, jev_ask.post_json = (lambda url, timeout=20: page), post
    try:
        r = jev_ask.ask("https://x.test/t", "\u8fd9\u6761\u8bc4\u8bba\u5bf9 zcode \u662f\u770b\u597d\u7684\u5417\uff1f",
                        token="stub-token", scope="items", kind="noul", filter_term="zcode")
    finally:
        jev_ask.fetch_text, jev_ask.post_json = original
    assert seen["payload"]["state"]["items"][0]["text"].startswith("Comment 1")
    assert len(seen["payload"]["questions"]) == 30
    assert r["scope"] == "items" and r["counted"] == 30 and r["total_items"] == 30
    assert r["lead"] == "\u770b\u597d" and r["distribution"][0][2] == 10, "10 of 30 in the fast threshold"
    out = jev_ask.render(r)
    assert "A: \u770b\u597d 33.3%" in out and "10/30" in out, out
    assert "stub-token" not in json.dumps(r)


def test_extract_items_prefers_the_body_over_the_card():
    card = ("<div class='card'><span class='meta'>2 days ago | prev | next</span>"
            "<div class='body'>" + "x" * 60 + "</div></div>")
    items = jev_ask.extract_items("<html><body>" + card * 5 + "</body></html>")
    assert len(items) == 5
    assert all(t.startswith("xxx") for t in items), items[:1]
    assert not any("prev | next" in t for t in items), "per-item metadata must not reach the judgment"


def test_retry_backoff_sleeps_before_retrying():
    """A 529 overload must actually wait; a retry storm only makes an overload worse."""
    from urllib.error import HTTPError
    sleeps, original_urlopen, original_sleep = [], jev_ask.urlopen, jev_ask.time.sleep
    jev_ask.time.sleep = sleeps.append

    def always_overloaded(req, timeout=0):
        raise HTTPError(jev_ask.API, 529, "overloaded", {}, None)

    jev_ask.urlopen = always_overloaded
    try:
        message = ""
        try:
            jev_ask.post_json({"state": "s"}, "stub-token")
        except RuntimeError as e:
            message = str(e)
        else:
            raise AssertionError("529 with no successes must raise")
    finally:
        jev_ask.urlopen, jev_ask.time.sleep = original_urlopen, original_sleep
    assert len(sleeps) == 3, f"one sleep between the four attempts, got {sleeps}"
    assert "\u8fc7\u8f7d" in message and "529" in message and len(message) < 80, message
    assert "error_type" not in message, "the provider's JSON blob is for logs, not for people"
    assert sleeps[0] >= 5 and sleeps[1] > sleeps[0], f"backoff must grow, got {sleeps}"


def test_json_body_serializes_dicts():
    assert jev_ask.json_body({"error": "x"}) == b'{"error": "x"}'
    assert jev_ask.json_body("hi") == b"hi" and jev_ask.json_body(b"hi") == b"hi"


def test_http_url_scheme_only():
    for bad in ("file:///etc/passwd", "ftp://x.test/a", "javascript:alert(1)"):
        try:
            jev_ask.fetch_text(bad)
        except RuntimeError as e:
            assert "http" in str(e)
        else:
            raise AssertionError(f"{bad} must be refused")


# ------------------------------------------------------------------ stub plumbing

PAGE = ("Widgets are round. " * 20) + "\n\n" + ("A widget costs four dollars. " * 20) + "\n\n" + ("Gears are metal. " * 20)


@contextlib.contextmanager
def _patched(page_text, post):
    original = jev_ask.fetch_text, jev_ask.post_json
    jev_ask.fetch_text = lambda url, timeout=20: {"url": url, "title": "T", "text": page_text}
    jev_ask.post_json = post
    try:
        yield
    finally:
        jev_ask.fetch_text, jev_ask.post_json = original


def _reply(pick, tokens=123, model="jev-1.13.0"):
    """Choice-over-passages stub. `pick(criteria, keys)` returns the chosen key."""
    seen = {}

    def post(payload, token, **kwargs):
        criteria = payload["questions"]["answer"]["criteria"]
        keys = [k for k in criteria if k != "none"]
        picked = pick(criteria, keys)
        seen.update(criteria=criteria, picked=picked, token=token, payload=payload)
        probabilities = {k: 0.01 for k in keys}
        probabilities["none"] = 0.95 if picked == "none" else 0.04
        if picked != "none":
            probabilities[picked] = 0.94
        return {"model": model, "usage": {"input_tokens": tokens, "output_tokens": 7},
                "answers": {"answer": {"type": "choice", "choice": picked, "confidence": 0.91,
                                       "probabilities": probabilities}}}

    return post, seen


def _verbatim_api(answer, tokens=50):
    def post(payload, token, **kwargs):
        return {"model": "jev-1.13.0", "usage": {"input_tokens": tokens, "output_tokens": 3},
                "answers": {"answer": answer}}

    return post


def test_answer_is_the_source_passage_verbatim():
    post, seen = _reply(lambda criteria, keys: next(k for k in keys if "four dollars" in criteria[k]))
    with _patched(PAGE, post):
        r = jev_ask.ask("https://x.test/w", "How much does a widget cost?", token="stub-token")
    assert seen["payload"]["state"]["question"] == "How much does a widget cost?"
    assert r["kind"] == "passage" and r["found"] is True
    assert r["answer"] == seen["criteria"][seen["picked"]], "the selected option's passage is copied verbatim, not generated"
    assert "four dollars" in r["answer"], r["answer"]
    assert r["top"][0]["text"] == r["answer"] and r["top"][0]["probability"] == 0.94
    assert r["model"] == "jev-1.13.0" and r["usage"]["input_tokens"] == 123
    assert r["estimated_usd"] == round(123 * jev_ask.USD_PER_MTOK / 1e6, 6)
    assert "stub-token" not in json.dumps(r), "the API token must never leak into results"


def test_none_choice_is_reported_as_no_answer():
    post, seen = _reply(lambda criteria, keys: "none")
    with _patched("Widgets are round. " * 20, post):
        r = jev_ask.ask("https://x.test/w", "What is the capital of France?", token="stub-token")
    assert r["found"] is False and r["answer"] is None
    assert r["none_probability"] == 0.95
    assert "no passage" in jev_ask.render(r)
    assert 'id="root"' in jev_ask.page(), "the React application must be served"


def test_noul_kind_returns_yes_probability():
    with _patched(PAGE, _verbatim_api({"type": "noul", "noul": 0.72})):
        r = jev_ask.ask("https://x.test/t", "这篇讨论里大家看好吗？", token="stub-token", kind="noul")
    assert r["kind"] == "noul" and r["found"] is True and r["answer"] == 0.72
    assert r["distribution"] == [["是 yes", 0.72], ["否 no", 0.28]], "both sides, summing to 1"
    assert r["confidence"] is None, "noul carries no separate confidence"
    assert "yes 72.0%" in jev_ask.render(r)


def test_choice_kind_returns_user_options_distribution():
    answer = {"type": "choice", "choice": "看好", "confidence": 0.44,
              "probabilities": {"看好": 0.55, "不看好": 0.35, "中立": 0.10}}
    with _patched(PAGE, _verbatim_api(answer)):
        r = jev_ask.ask("https://x.test/t", "态度？", token="stub-token", kind="choice",
                        options=["看好", "不看好", "中立"])
    assert r["answer"] == "看好"
    assert r["distribution"] == [["看好", 0.55], ["不看好", 0.35], ["中立", 0.1]], "sorted by probability"
    out = jev_ask.render(r)
    assert "A: 看好" in out and "55.0%" in out and "35.0%" in out


def test_score_kind_maps_legend_labels():
    answer = {"type": "score", "score": 1.05, "confidence": 0.92,
              "legend": {"0": "calm", "1": "frustrated", "2": "angry"},
              "probabilities": {"0": 0.0, "1": 0.95, "2": 0.05}}
    with _patched(PAGE, _verbatim_api(answer)):
        r = jev_ask.ask("https://x.test/t", "How frustrated?", token="stub-token", kind="score",
                        options=["calm", "frustrated", "angry"])
    assert r["answer"] == 1.05
    assert r["distribution"][0] == ["frustrated", 0.95], "levels reported by label, not raw index"
    assert "weighted score 1.05" in jev_ask.render(r)


def test_option_validation_before_any_network_call():
    cases = [("choice", ["only-one"]), ("score", ["only-one"]),
             ("score", [str(i) for i in range(11)]), ("bogus", ["a", "b"])]
    for kind, options in cases:
        try:
            jev_ask.ask("https://x.test/t", "q", token="stub-token", kind=kind, options=options)
        except RuntimeError as e:
            assert any(w in str(e) for w in ("kind", "options", "levels")), str(e)
        else:
            raise AssertionError(f"{kind} with {len(options)} options must be refused")
    assert jev_ask.ask.__defaults__[1] == "passage", "extractive Q&A stays the default kind"






def test_render_of_found_answer():
    out = jev_ask.render({"kind": "passage", "title": "T", "url": "u", "question": "q", "found": True, "answer": "A",
                          "confidence": 0.9, "none_probability": 0.05, "model": "m", "usage": {"input_tokens": 10},
                          "top": [], "estimated_usd": 0.0})
    assert "A: A" in out and "confidence 0.9" in out


# ------------------------------------------------------------------- the token
# The token must never come back out, and a rejected token must never be stored.

def _isolated_token(tmp):
    """Point the token file somewhere disposable; remember what to restore."""
    keep = (jev_ask.TOKEN_FILE, jev_ask.LEGACY_TOKEN_FILE)
    jev_ask.TOKEN_FILE = Path(tmp) / "token"
    jev_ask.LEGACY_TOKEN_FILE = Path(tmp) / "legacy-token"
    return keep


def test_token_priority_and_files(tmp_path=None):
    import os, tempfile
    from pathlib import Path as P
    root = P(tempfile.mkdtemp())
    keep = _isolated_token(root)
    saved = {k: os.environ.get(k) for k in ("TYPESAFE_API_KEY", "JEV_TOKEN")}
    try:
        for k in saved: os.environ.pop(k, None)
        try:
            jev_ask.load_token()
        except RuntimeError as e:
            assert "token" in str(e), str(e)
        else:
            raise AssertionError("no token anywhere must raise, not return something")
        (root / "legacy-token").write_text("from-legacy\n")
        assert jev_ask.load_token() == "from-legacy", "the old path still works"
        (root / "token").write_text("KEY=from-file\n")
        assert jev_ask.load_token() == "from-file", "the UI-written file wins over the legacy one"
        assert jev_ask.token_status()["source"] == "文件"
        os.environ["JEV_TOKEN"] = "from-env"
        assert jev_ask.load_token() == "from-env", "the environment wins over any file"
        assert jev_ask.token_status()["source"] == "环境变量"
    finally:
        jev_ask.TOKEN_FILE, jev_ask.LEGACY_TOKEN_FILE = keep
        for k, v in saved.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
        import shutil; shutil.rmtree(root, ignore_errors=True)


def test_token_status_never_contains_the_token():
    import os, tempfile
    from pathlib import Path as P
    root = P(tempfile.mkdtemp())
    keep = _isolated_token(root)
    saved = os.environ.pop("TYPESAFE_API_KEY", None), os.environ.pop("JEV_TOKEN", None)
    try:
        secret = "jev_supersecret_value_123"
        (root / "token").write_text(secret + "\n")
        status = jev_ask.token_status()
        assert status["configured"] is True
        assert secret not in json.dumps(status), "the status must not carry the secret"
        assert set(status) == {"configured", "source", "where", "can_write"}
    finally:
        jev_ask.TOKEN_FILE, jev_ask.LEGACY_TOKEN_FILE = keep
        for var, value in zip(("TYPESAFE_API_KEY", "JEV_TOKEN"), saved):
            if value is not None: os.environ[var] = value
        import shutil; shutil.rmtree(root, ignore_errors=True)


def test_save_token_verifies_before_storing():
    """A rejected token must leave no file behind; a good one is stored 0600."""
    import tempfile, os
    from pathlib import Path as P
    from urllib.error import HTTPError
    root = P(tempfile.mkdtemp())
    keep = _isolated_token(root)
    original = jev_ask.urlopen
    try:
        try:
            jev_ask.save_token("short")
        except RuntimeError as e:
            assert "不像" in str(e)
        else:
            raise AssertionError("a 5-char token must be refused without asking the API")

        def rejects(req, timeout=0):
            raise HTTPError(jev_ask.API, 401, "Unauthorized", {}, None)
        jev_ask.urlopen = rejects
        try:
            jev_ask.save_token("jev_not_a_real_token_at_all")
        except RuntimeError as e:
            assert "401" in str(e) and "拒绝" in str(e), str(e)
        else:
            raise AssertionError("a 401 must raise")
        assert not (root / "token").exists(), "a rejected token must not be written"

        class _OK:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
        jev_ask.urlopen = lambda req, timeout=0: _OK()
        status = jev_ask.save_token("jev_a_realistic_looking_token_value")
        assert status["configured"] is True and status["where"].endswith("token")
        written = (root / "token").read_text().strip()
        assert written == "jev_a_realistic_looking_token_value"
        assert oct((root / "token").stat().st_mode)[-3:] == "600", "0600 — nobody else reads it"
        assert jev_ask.load_token() == written
    finally:
        jev_ask.urlopen = original
        jev_ask.TOKEN_FILE, jev_ask.LEGACY_TOKEN_FILE = keep
        import shutil; shutil.rmtree(root, ignore_errors=True)


def test_the_cli_reports_a_bad_ident_without_a_traceback():
    """It was a Python traceback in the terminal and a raw opencli error dump in the page."""
    import os
    import subprocess
    import sys
    here = os.path.dirname(os.path.abspath(jev_ask.__file__))
    env = {**os.environ, "TYPESAFE_API_KEY": "not-a-real-token"}
    result = subprocess.run([sys.executable, "tally.py", "--source", "zhihu",
                             "--ident", "https://www.zhihu.com/question/357296785",
                             "--kind", "choice", "--options", "喜欢,讨厌", "测试"],
                            cwd=here, capture_output=True, text=True, env=env, timeout=60)
    assert "Traceback" not in result.stderr, result.stderr
    assert "错误：" in result.stderr and "回答" in result.stderr, result.stderr
    assert result.returncode == 2, result.returncode


def test_every_detail_row_carries_the_label_the_distribution_counts():
    """The third column lets you click 「讨厌 22 条」 to see those 22 comments. That only works if
    a row knows which label it is — and it must be the *same* label the count used."""
    answers = {f"i{i+1}": {"noul": v} for i, v in enumerate([0.9, 0.5, 0.1, 0.7])}
    rows, dist = jev_ask.aggregate("noul", answers, ["a", "b", "c", "d"], [])[:2]
    # rows come back sorted by certainty, so compare by their original number
    assert {r["n"]: r["label"] for r in rows} == {1: "看好", 2: "不确定", 3: "不看好", 4: "看好"}, rows
    for label, _share, count in dist:                  # distribution rows are [label, share, count]
        assert count == sum(1 for r in rows if r["label"] == label), (label, count)
    answers = {f"i{i+1}": {"choice": c, "confidence": 1.0}
               for i, c in enumerate(["讨厌", "讨厌", "喜欢"])}
    rows, dist = jev_ask.aggregate("choice", answers, ["a", "b", "c"], ["喜欢", "讨厌"])[:2]
    assert sum(1 for r in rows if r["label"] == "讨厌") == 2
    assert {row: count for row, _share, count in dist}["讨厌"] == 2








def test_production_assets_are_contained_and_missing_build_is_actionable():
    """A static server must never turn /assets into a local-file reader."""
    import tempfile
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen
    from urllib.error import HTTPError
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "dist"
        (root / "assets").mkdir(parents=True)
        (root / "index.html").write_text('<div id="root"></div>')
        (root / "assets/app.js").write_text('console.log("public")')
        outside = Path(directory) / "private.txt"
        outside.write_text("test-only-not-public")
        (root / "assets/link.txt").symlink_to(outside)
        handler = jev_ask.make_handler("test-only")
        handler.log_message = lambda *args: None
        with patch.object(jev_ask, "WEB_ROOT", root):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urlopen(base + "/assets/app.js") as response:
                    assert response.status == 200
                    assert response.headers['X-Content-Type-Options'] == 'nosniff'
                    assert 'javascript' in response.headers['Content-Type']
                    assert response.read() == b'console.log("public")'
                for path in ('/assets/link.txt', '/assets/%2e%2e/%2e%2e/private.txt',
                             '/private.txt', '/sources.py', '/token-file'):
                    try:
                        urlopen(base + path)
                    except HTTPError as error:
                        assert error.code == 404, path
                        assert b'test-only-not-public' not in error.read()
                    else:
                        raise AssertionError(path)
                (root / "index.html").unlink()
                try:
                    urlopen(base + '/')
                except HTTPError as error:
                    assert error.code == 503
                    assert b'npm ci' in error.read()
                else:
                    raise AssertionError('missing build must report 503')
            finally:
                server.shutdown()
                server.server_close()
                worker.join()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} checks passed")

#!/usr/bin/env python3
"""End to end: the real server, a fake Jev, a fake data source, a real web page.

Every scenario the tool claims to support runs here against a live `tally.py --serve`:
the HTTP contract, the four question kinds, both scopes, the source pull, the cache, the
token, the error paths, the cross-origin guard.

Nothing is mocked away that matters: the server is the real one, the answers travel over
HTTP, the counting/aggregation is the real code. What *is* fake is the two things we do not
own — the upstream API and the site being scraped — so the suite is deterministic, needs no
token, costs nothing, and can assert exact percentages.

Run: python3 test_e2e.py        (no network, no real token, ~2 s)
"""
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The comments the fake source returns. The fake Jev reads *these words* to decide — the same rule
# a human would use — so the expected percentages below are readable from the fixture itself.
# Long enough to look like real comments: the item extractor ignores containers whose median text is
# under 40 characters, precisely so it does not mistake a list of nav links for content.
COMMENTS = [
    {"text": "很好玩，我喜欢这款小游戏，像素风的画风也挺舒服，中午休息的时候玩两把刚好，期待后续更新更多玩法。",
     "author": "alice", "likes": 30},
    {"text": "一般般吧，没什么惊喜，玩法比较单调，开了几次就开始重复了，希望以后能加一点新的内容。",
     "author": "bob", "likes": 10},
    {"text": "太差了，一点意思都没有，操作手感很生硬，界面也不太清楚，玩了两分钟就关掉了。",
     "author": "carol", "likes": 2},
    {"text": "还不错，画风我喜欢，节奏比较轻快，就是内容少了一点，希望后面能多出一些玩法。",
     "author": "alice", "likes": 7},
]

PAGE = ("<!doctype html><html><head><title>样例贴</title></head><body><h1>网吧物语</h1>"
        + "".join(f'<div class="comment"><span class="who">{c["author"]}</span>'
                  f'<p class="body">{c["text"]}</p><span class="meta">3 天前</span></div>'
                  for c in COMMENTS)
        + "</body></html>")


# ----------------------------------------------------------------- the fake upstream (Jev)
def judge(text, wanted):
    """Deterministic, text-derived answers: the fixture text decides the outcome."""
    if "喜欢" in text:
        pick, noul, score = wanted[0], 0.9, 3
    elif "一般" in text:
        pick, noul, score = wanted[1 % len(wanted)], 0.5, 2
    else:
        pick, noul, score = wanted[-1], 0.1, 1
    return pick, noul, score


def answer_for(question, texts, question_text="", whole_page=False):
    """One answer object shaped exactly like TypeSafe's, for one question in the request."""
    kind = question["type"]
    text = texts[0]
    # A passage question *is* a choice question — its criteria are the candidate passages (plus
    # `none`), and its instructions are a dict. A user's choice question carries a plain string.
    if kind == "choice" and whole_page and isinstance(question.get("instructions"), dict):
        candidates = list(question["criteria"])
        choice = "none" if "没有答案" in question_text else candidates[0]
        rest = round(0.3 / max(1, len(candidates) - 1), 4)
        return {"choice": choice, "confidence": 0.7,
                "probabilities": {k: (0.7 if k == choice else rest) for k in candidates}}
    if kind == "choice":
        options = list(question["criteria"])
        pick, _, _ = judge(text, options)
        share = 0.8
        probs = {option: round((1 - share) / max(1, len(options) - 1), 4) for option in options}
        probs[pick] = share
        return {"choice": pick, "probabilities": probs, "confidence": share}
    if kind == "noul":
        _, value, _ = judge(text, ["x"])
        return {"noul": value, "confidence": abs(value - 0.5) * 2}
    if kind == "score":
        levels = list(question["criteria"])
        _, _, score = judge(text, ["x"])
        level = levels[min(score, len(levels) - 1)]
        probs = {name: (0.8 if name == level else round(0.2 / max(1, len(levels) - 1), 4))
                 for name in levels}
        return {"score": score, "probabilities": probs, "legend": {l: l for l in levels},
                "confidence": 0.8}
    return {"choice": "c00", "confidence": 0.5, "probabilities": {"c00": 0.5, "none": 0.5}}


class FakeJev(BaseHTTPRequestHandler):
    """A tiny stand-in for api.typesafe.ai: two endpoints, one token, no network."""

    token = "good-token"
    log = []
    page = PAGE
    files = {}                      # extra paths the fixture sources can fetch

    def log_message(self, *args):
        pass                        # keep the test output readable

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        got = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        return got == self.token

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/page":
            body = self.page.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in self.files:
            self._json(200, self.files[path])
            return
        if path == "/v1/models":
            if not self._auth_ok():
                return self._json(401, {"error": "invalid api key"})
            return self._json(200, {"data": [{"id": "jev-latest"}]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/v1/systemone":
            return self._json(404, {"error": "not found"})
        if not self._auth_ok():
            return self._json(401, {"error": "invalid api key"})
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = json.loads(body or b"{}")
        FakeJev.log.append(payload)
        items = payload.get("state", {}).get("items") or []
        whole = json.dumps(payload["state"], ensure_ascii=False)
        texts_of = {f"i{i + 1}": [it["text"]] for i, it in enumerate(items)}
        answers = {}
        for key, question in payload["questions"].items():
            # whole-page questions see the whole state; per-item ones see only their own entry
            answers[key] = answer_for(question, texts_of.get(key) or [whole],
                                      (payload["state"] or {}).get("question") or "",
                                      whole_page=(key == "answer"))
        self._json(200, {"model": "fake-jev-1", "answers": answers,
                         "usage": {"input_tokens": 1234}})


# ----------------------------------------------------------------- the fake data sources
FIXTURE_SOURCE = '''
import json, os, sys
ident = sys.argv[1]
log = os.environ["FIXTURE_LOG"]
with open(log, "a") as fh:
    fh.write(ident + "\\n")                      # proves how many times the command really ran
comments = json.loads(os.environ["FIXTURE_COMMENTS"])
if ident == 'short':
    comments = comments[:2]
elif ident == 'single':
    comments = comments[:1]
json.dump(comments, sys.stdout)
'''

SUBJECT_SOURCE = '''
import json, sys
json.dump({"title": "样例贴：一个小游戏", "id": sys.argv[1]}, sys.stdout)
'''

EMPTY_SOURCE = '''
import json, sys
json.dump([], sys.stdout)
'''


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """The fake upstream plus a real `tally.py --serve`, wired together in a temp home."""

    def __init__(self, port=None):
        self.root = Path(tempfile.mkdtemp(prefix="tally-e2e-"))
        self.plugins = self.root / "sources.d"
        self.plugins.mkdir()
        self.log = self.root / "runs.txt"
        self.log.write_text("")
        self.token_file = self.root / "token"
        self.cache = self.root / "cache"
        self.jev = ThreadingHTTPServer(("127.0.0.1", 0), FakeJev)
        self.jev_port = self.jev.server_address[1]
        threading.Thread(target=self.jev.serve_forever, daemon=True).start()
        self.page_url = f"http://127.0.0.1:{self.jev_port}/page"
        self.token_file.write_text(FakeJev.token)      # every scenario starts with a valid token
        self.token_file.chmod(0o600)

        script = self.root / "fixture.py"
        script.write_text(FIXTURE_SOURCE)
        def source(label, label_en, ident, ident_en, **rest):
            return {"label": label, "label_en": label_en, "ident": ident, "ident_en": ident_en,
                    "note": "只给测试用。", "note_en": "For the tests only.",
                    "run": f"python3 {script} {{ident}}", "text": "text", "author": "author", **rest}

        (self.plugins / "fixture.json").write_text(json.dumps({
            "fixture": source("样例源", "Fixture source", "任意字符串", "anything", weight="likes"),
            "fixture-url": source("样例源（认链接）", "Fixture source (link)", "帖子 id", "post id",
                                  ident_from="t/(\\d+)"),
            "fixture-skip": source("样例源（带 skip）", "Fixture source (skip)", "任意", "anything",
                                   skip={"author": "bob"}),
            "fixture-subject": {**source("样例源（有标题）", "Fixture source (subject)", "任意", "anything"),
                                "subject": {"run": f"python3 {self.root / 'subject.py'} {{ident}}",
                                            "at": "title"}},
            "fixture-empty": {"label": "样例源（空）", "label_en": "Fixture source (empty)",
                              "ident": "要一个话题 id", "ident_en": "a topic id",
                              "run": f"python3 {self.root / 'empty.py'} {{ident}}", "text": "text"},
        }, ensure_ascii=False), encoding="utf-8")
        (self.root / "subject.py").write_text(SUBJECT_SOURCE)
        (self.root / "empty.py").write_text(EMPTY_SOURCE)

        self.port = port or free_port()
        env = {**os.environ,
               "JEV_TALLY_API_BASE": f"http://127.0.0.1:{self.jev_port}",
               "JEV_TALLY_SOURCES_D": str(self.plugins),
               "JEV_TALLY_CACHE_DIR": str(self.cache),
               "JEV_TALLY_TOKEN_FILE": str(self.token_file),
               "FIXTURE_LOG": str(self.log),
               "FIXTURE_COMMENTS": json.dumps(COMMENTS, ensure_ascii=False)}
        env.pop("TYPESAFE_API_KEY", None)
        env.pop("JEV_TOKEN", None)
        env["HOME"] = str(self.root)        # so ~/jev_token cannot be found: a test must never send
        #                                     the developer's real token anywhere
        self.proc = subprocess.Popen([sys.executable, "tally.py", "--serve", "--port", str(self.port)],
                                     cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.wait_ready()

    def wait_ready(self, seconds=15):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError("server died at startup:\n" + self.proc.stderr.read().decode())
            try:
                self.get("/token")
                return
            except Exception:
                time.sleep(0.05)
        raise AssertionError("server did not come up")

    # ---- HTTP helpers: the same surface the browser uses ----
    def _request(self, path, payload=None, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, method="POST" if payload is not None else "GET",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def get(self, path, headers=None):
        return self._request(path, None, headers)

    def post(self, path, payload, headers=None):
        return self._request(path, payload, headers)

    def json_post(self, path, payload, headers=None):
        code, body = self.post(path, payload, headers)
        return code, json.loads(body)

    # ---- what the fixture recorded ----
    def runs(self):
        return [line for line in self.log.read_text().splitlines() if line]

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.jev.shutdown()
        self.jev.server_close()
        shutil.rmtree(self.root, ignore_errors=True)


# ----------------------------------------------------------------- the scenarios
def test_the_page_and_its_assets_come_back():
    code, body = S.get("/")
    assert code == 200, code
    assert "<title" in body and "tally" in body
    assert 'id="root"' in body and 'type="module"' in body
    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', body)
    assert assets, "production bundle must be referenced"
    for asset in assets:
        code, content = S.get(asset)
        assert code == 200 and content, asset
    for path in ('/nope', '/tally.py', '/sources.json', '/assets/%2e%2e/%2e%2e/tally.py'):
        assert S.get(path)[0] == 404, path


def test_sources_are_listed_in_both_languages():
    code, body = S.get("/sources")
    assert code == 200, body
    rows = json.loads(body)
    names = {r["name"] for r in rows}
    assert {"bilibili", "v2ex", "hackernews", "fixture"} <= names, names
    assert "xueqiu" not in names, "removed on request"
    for row in rows:
        assert row["label"] and row["label_en"], row
        assert row["ident"] and row["ident_en"], row
    fixture = next(r for r in rows if r["name"] == "fixture")
    assert fixture["label_en"] == "Fixture source" and fixture["url_ok"] is False
    assert next(r for r in rows if r["name"] == "fixture-url")["url_ok"] is True


def test_the_token_status_never_leaks_the_value():
    """This endpoint is what the page polls; it may say *where* the token is, never what it is."""
    code, body = S.get("/token")
    assert code == 200, code
    status = json.loads(body)
    assert set(status) == {"configured", "source", "where", "can_write"}, status
    assert status["configured"] is True, status
    assert status["can_write"] is True and status["where"].endswith("token"), status
    assert FakeJev.token not in body, "the token value came back over HTTP"
    for path in ("/token", "/sources"):
        assert FakeJev.token not in S.get(path)[1]


def test_a_bad_token_is_refused_and_writes_nothing():
    code, body = S.json_post("/token", {"token": "bad-token"})
    assert code == 400, (code, body)
    assert S.token_file.read_text().strip() == FakeJev.token, "a rejected token must not be written"
    assert "bad-token" not in body, "the token must not be echoed back"


def test_a_good_token_is_verified_and_saved_0600():
    code, status = S.json_post("/token", {"token": FakeJev.token})
    assert code == 200 and status["configured"] is True, (code, status)
    assert S.token_file.read_text().strip() == FakeJev.token
    mode = stat.S_IMODE(S.token_file.stat().st_mode)
    assert mode == 0o600, oct(mode)


def test_a_page_is_fetched_and_shaped():
    code, preview = S.json_post("/fetch", {"url": S.page_url})
    assert code == 200, preview
    assert preview["title"] == "样例贴", preview["title"]
    assert preview["item_count"] == 4 and preview["mode"] == "条目", preview
    assert preview["tokens"] > 0 and preview["usd"] > 0
    assert "很好玩" in preview["preview"] and "网吧物语" in preview["preview"]
    assert len(preview["items"]) == 4


def test_whole_page_choice_reports_a_distribution():
    code, out = S.json_post("/ask", {"url": S.page_url, "question": "喜欢吗？",
                                     "kind": "choice", "options": ["喜欢", "一般", "讨厌"]})
    assert code == 200, out
    assert out["kind"] == "choice" and out["answer"] == "喜欢", out
    labels = [row[0] for row in out["distribution"]]
    assert labels[0] == "喜欢" and abs(sum(r[1] for r in out["distribution"]) - 1.0) < 1e-6
    assert out["model"] == "fake-jev-1" and out["estimated_usd"] > 0


def test_whole_page_passage_and_its_none_case():
    code, out = S.json_post("/ask", {"url": S.page_url, "question": "哪一段说好玩？", "kind": "passage"})
    assert code == 200 and out["found"] is True, out
    assert out["answer"] and out["none_probability"] is not None
    assert out["top"] and out["top"][0]["probability"] >= out["top"][-1]["probability"]
    code, out = S.json_post("/ask", {"url": S.page_url, "question": "没有答案的问题", "kind": "passage"})
    assert code == 200 and out["found"] is False, out
    assert out["answer"] is None


def test_whole_page_noul_and_score():
    code, out = S.json_post("/ask", {"url": S.page_url, "question": "是好评吗？", "kind": "noul"})
    assert code == 200 and out["answer"] == 0.9, out
    assert [r[0] for r in out["distribution"]] == ["是 yes", "否 no"]
    code, out = S.json_post("/ask", {"url": S.page_url, "question": "几档？", "kind": "score",
                                     "options": ["差", "中", "好", "很好"]})
    assert code == 200 and out["answer"] == 3, out
    assert [r[0] for r in out["distribution"]][0] == "很好"


def test_items_choice_counts_every_entry_and_labels_it():
    code, out = S.json_post("/ask", {"url": S.page_url, "question": "这一条喜欢吗？", "kind": "choice",
                                     "options": ["喜欢", "一般", "讨厌"], "scope": "items"})
    assert code == 200, out
    assert out["scope"] == "items" and out["counted"] == 4 and out["total_items"] == 4
    counts = {label: count for label, _share, count in out["distribution"]}
    assert counts == {"喜欢": 2, "一般": 1, "讨厌": 1}, counts
    assert abs(out["answer"] - 0.5) < 1e-9 and out["lead"] == "喜欢"
    assert len(out["items"]) == 4
    for row in out["items"]:
        assert row["label"] in ["喜欢", "一般", "讨厌"], row
        assert row["text"], row
        assert row["author"] == "", "a fetched page has no authors — only a data source does"
    # rows come back least-sure first, and every label matches the count above
    certainties = [row["certainty"] for row in out["items"]]
    assert certainties == sorted(certainties), certainties
    for label, _share, count in out["distribution"]:
        assert count == sum(1 for r in out["items"] if r["label"] == label)
    assert out["people"] is None, "a fetched page has no authors, so there is no per-person count"


def test_items_noul_uses_the_same_bands_as_the_counts():
    code, out = S.json_post("/ask", {"url": S.page_url, "question": "这一条是好评吗？",
                                     "kind": "noul", "scope": "items"})
    assert code == 200, out
    counts = {label: count for label, _share, count in out["distribution"]}
    assert counts == {"看好": 2, "不确定": 1, "不看好": 1}, counts
    assert abs(out["mean"] - 0.6) < 1e-9 and out["uncertain"] == 1
    assert {row["label"] for row in out["items"]} == {"看好", "不确定", "不看好"}


def test_a_source_pull_is_cached_and_a_second_ask_does_not_refetch():
    # its own ident: other scenarios share the cache directory, and a hit is what "not cached"
    # would be indistinguishable from.
    IDENT = "pull-topic"
    before = len(S.runs())
    code, data = S.json_post("/sourcedata", {"source": "fixture", "ident": IDENT, "limit": 10})
    assert code == 200, data
    assert data["count"] == 4 and data["authors"] == 3, data
    assert data["label_en"] == "Fixture source", data.get("label_en")
    assert data["label"] == "样例源", data.get("label")
    assert data["items"][0].startswith("很好玩"), data["items"][0]
    assert data["cache"]["cached"] is False, "a first pull is not a cache hit: " + str(data["cache"])
    assert len(S.runs()) == before + 1, S.runs()

    code, again = S.json_post("/sourcedata", {"source": "fixture", "ident": IDENT, "limit": 10})
    assert again["cache"]["cached"] is True, again["cache"]
    assert again["count"] == 4, again
    assert len(S.runs()) == before + 1, "the cache must mean the command does not run again"

    code, fresh = S.json_post("/sourcedata", {"source": "fixture", "ident": IDENT,
                                              "limit": 10, "refresh": True})
    assert fresh["cache"]["cached"] is False and len(S.runs()) == before + 2, S.runs()

    # and a question about that source uses the cache: the comment list cannot move underneath
    code, out = S.json_post("/ask", {"question": "对这个小游戏是什么态度？", "kind": "choice",
                                     "options": ["喜欢", "一般", "讨厌"], "scope": "items",
                                     "source": "fixture", "ident": IDENT, "limit": 10})
    assert code == 200, out
    assert out["source"] == "fixture", out.get("source")
    assert out["counted"] == 4, out.get("counted")
    assert {label: count for label, _s, count in out["distribution"]} == {"喜欢": 2, "一般": 1, "讨厌": 1}
    assert len(S.runs()) == before + 2, "asking must not refetch"


def test_items_on_a_source_counts_people_not_just_comments():
    code, out = S.json_post("/ask", {"question": "这一条喜欢吗？", "kind": "choice",
                                     "options": ["喜欢", "一般", "讨厌"], "scope": "items",
                                     "source": "fixture", "ident": "topic-1", "limit": 10})
    assert code == 200, out
    assert {row["author"] for row in out["items"]} == {"alice", "bob", "carol"}
    assert out["people"]["authors"] == 3, out["people"]
    # alice posted two comments and both are 喜欢 — she is still one opinion, so the split is 1/1/1
    by_person = {label: count for label, _share, count in out["people"]["distribution"]}
    assert by_person == {"喜欢": 1, "一般": 1, "讨厌": 1}, by_person
    assert abs(sum(share for _l, share, _c in out["people"]["distribution"]) - 1.0) < 1e-9


def test_a_filter_narrows_the_denominator_in_code():
    """--filter / the 关键词 box: only entries containing the term are counted, and the total stays."""
    code, out = S.json_post("/ask", {"question": "这一条喜欢吗？", "kind": "choice",
                                     "options": ["喜欢", "一般", "讨厌"], "scope": "items",
                                     "filter": "画风", "source": "fixture", "ident": "topic-1",
                                     "limit": 10})
    assert code == 200, out
    assert out["counted"] == 2 and out["total_items"] == 4, out
    assert out["filter"] == "画风"
    assert all("画风" in row["text"] for row in out["items"])
    code, body = S.json_post("/ask", {"question": "q", "kind": "choice", "options": ["喜欢", "讨厌"],
                                      "scope": "items", "filter": "这个词不存在",
                                      "source": "fixture", "ident": "topic-1", "limit": 10})
    assert code == 400 and "no entry contains" in body["error"], (code, body)


def test_the_same_question_twice_gives_the_same_numbers():
    payload = {"question": "这一条喜欢吗？", "kind": "choice", "options": ["喜欢", "一般", "讨厌"],
               "scope": "items", "source": "fixture", "ident": "topic-1", "limit": 10}
    before = len(S.runs())
    first = S.json_post("/ask", payload)[1]
    second = S.json_post("/ask", payload)[1]
    assert first["distribution"] == second["distribution"]
    assert first["items"] == second["items"]
    assert len(S.runs()) == before, "still no refetch"


def test_a_source_can_supply_the_subject():
    code, data = S.json_post("/sourcedata", {"source": "fixture-subject", "ident": "x", "limit": 5})
    assert code == 200, data
    assert data["subject"] == "样例贴：一个小游戏", data


def test_a_url_is_turned_into_the_id_the_source_wants():
    before = len(S.runs())
    code, data = S.json_post("/sourcedata", {"source": "fixture-url",
                                             "ident": "https://example.com/t/1243550", "limit": 5})
    assert code == 200, data
    assert S.runs()[before:] == ["1243550"], "the command must receive the extracted id"


def test_the_wrong_kind_of_link_is_explained_not_run():
    before = len(S.runs())
    code, body = S.json_post("/sourcedata", {"source": "fixture-url",
                                             "ident": "https://example.com/topic/999", "limit": 5})
    assert code == 400, (code, body)
    assert "样例源（认链接）" in body["error"] and "帖子 id" in body["error"], body
    assert len(S.runs()) == before, "the command must not run at all: " + str(S.runs()[before:])


def test_skip_keeps_non_opinions_out_of_the_denominator():
    code, data = S.json_post("/sourcedata", {"source": "fixture-skip", "ident": "x", "limit": 10})
    assert code == 200, data
    assert data["count"] == 3 and data["authors"] == 2, data
    assert "bob" not in json.dumps(data, ensure_ascii=False), "the skipped row must be gone"


def test_an_empty_pull_says_what_the_source_wanted():
    code, body = S.json_post("/sourcedata", {"source": "fixture-empty", "ident": "abc", "limit": 5})
    assert code == 400, (code, body)
    assert "没拿到任何文本" in body["error"] and "要一个话题 id" in body["error"], body


def test_unknown_source_and_bad_request_are_refused():
    code, body = S.json_post("/sourcedata", {"source": "nope", "ident": "x", "limit": 5})
    assert code == 400 and "没有这个数据源" in body["error"], (code, body)
    code, body = S.json_post("/ask", {"url": S.page_url, "question": "", "kind": "choice",
                                      "options": ["a", "b"]})
    assert code == 400 and "question" in body["error"], (code, body)
    code, body = S.json_post("/ask", {"url": S.page_url, "question": "q", "kind": "choice",
                                      "options": "喜欢,讨厌"})
    assert code == 400, (code, body)
    code, body = S.json_post("/ask", {"question": "q", "kind": "choice", "options": []})
    assert code == 400, "a source or a url is required"


def test_cross_origin_writes_are_refused():
    code, body = S.post("/ask", {"url": S.page_url, "question": "q", "kind": "passage"},
                        headers={"Origin": "http://evil.example"})
    assert code == 403, (code, body)
    code, body = S.get("/token", headers={"Origin": "http://evil.example"})
    assert code == 403, (code, body)
    code, _ = S.get("/", headers={"Origin": "http://evil.example"})
    assert code == 200, "reading the page itself is harmless"


def test_a_real_site_path_still_works_end_to_end():
    """The URL mode has to work against a real web server too — `file://` would not prove it."""
    code, out = S.json_post("/ask", {"url": S.page_url, "question": "这一条对画风是什么态度？",
                                     "kind": "choice", "options": ["喜欢", "一般", "讨厌"],
                                     "scope": "items"})
    assert code == 200 and out["counted"] == 4
    assert all(row["text"] for row in out["items"])
    assert out["url"] == S.page_url and out["title"] == "样例贴"


if __name__ == "__main__":
    S = Server()
    try:
        tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
        failures = []
        for test in tests:
            try:
                test()
                print(f"ok  {test.__name__}")
            except Exception as error:                     # keep going: report every failure
                failures.append((test.__name__, error))
                print(f"FAIL  {test.__name__}: {error}")
        print(f"\n{len(tests) - len(failures)}/{len(tests)} e2e scenarios passed")
        if failures:
            sys.exit(1)
    finally:
        S.close()

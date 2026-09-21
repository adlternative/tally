#!/usr/bin/env python3
"""Checks for sources: the argv builder (no shell), path mapping, plugin merge, retry.

Every test uses a throwaway command or a temp plugin dir — no network, no opencli.

Run: python3 test_sources.py
"""
import json
import shutil
import tempfile
from pathlib import Path

import sources

sources.CACHE_DIR = Path(tempfile.mkdtemp()) / "cache"   # tests never touch the real cache


def _entry(**over):
    base = {"label": "t", "ident": "x", "run": "cat {ident}", "text": "text"}
    base.update(over)
    return base


def test_command_for_never_goes_through_a_shell():
    """An ident is one argument, so `;`, `$()` and backticks are just characters."""
    evil = "a; rm -rf / #$(whoami)`id`"
    argv = sources.command_for(_entry(run="opencli bilibili comments {ident} --limit {limit} -f json"), evil, 7)
    assert argv == ["opencli", "bilibili", "comments", evil, "--limit", "7", "-f", "json"], argv
    assert argv[3] == evil, "the ident must stay a single argument"
    assert len(argv) == 8, "no word splitting happened"


def test_command_for_requires_the_ident_placeholder():
    try:
        sources.command_for(_entry(run="cat fixed.json"), "x", 5)
    except RuntimeError as error:
        assert "ident" in str(error)
    else:
        raise AssertionError("a template without {ident} cannot take an ident")


def test_dig_walks_dots_and_list_indexes():
    row = {"a": {"b": [{"c": 1}, {"c": 2}]}, "n": 5}
    assert sources._dig(row, "a.b.1.c") == 2
    assert sources._dig(row, "n") == 5
    assert sources._dig(row, "") is row
    assert sources._dig(row, "a.zz") is None
    assert sources._dig(row, "a.b.9.c", "fallback") == "fallback"


def test_rows_finds_the_list_at_top_level_or_nested():
    entry = _entry()
    assert sources._rows([{"text": "a"}], entry) == [{"text": "a"}]
    assert sources._rows({"data": [{"text": "b"}]}, entry) == [{"text": "b"}], "common wrapper key"
    assert sources._rows({"items": [{"text": "c"}]}, entry) == [{"text": "c"}]
    assert sources._rows({"x": 1}, entry) == [], "not a list anywhere -> nothing"
    assert sources._rows({"data": {"deep": [{"text": "d"}]}}, _entry(items="data.deep")) == [{"text": "d"}]


def test_run_maps_filters_and_dedupes(tmp_path=None):
    root = Path(tempfile.mkdtemp())
    payload = root / "in.json"
    payload.write_text(json.dumps([
        {"author": "a", "text": "第一条", "likes": 3},
        {"author": "a", "text": "第一条", "likes": 3},        # duplicate text
        {"author": "b", "text": "", "likes": 9},              # empty -> dropped
        {"author": "b", "text": "x", "likes": 1},             # below min_chars
        {"author": "c", "text": "第二条", "likes": 5},
    ], ensure_ascii=False), encoding="utf-8")
    try:
        entry = _entry(run="cat {ident}", text="text", author="author", weight="likes", min_chars=2)
        items = sources.run("t", str(payload), entry=entry)
        assert [i["text"] for i in items] == ["第一条", "第二条"]
        assert items[0]["author"] == "a" and items[0]["weight"] == 3
        assert sources.run("t", str(payload), entry=entry) == items, "deterministic"
        limited = sources.run("t", str(payload), 1, entry=entry)
        assert len(limited) == 1, "limit is respected"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_run_maps_a_content_field_too():
    root = Path(tempfile.mkdtemp())
    payload = root / "in.json"
    payload.write_text(json.dumps([{"content": "知乎/v2ex 用 content"}], ensure_ascii=False), encoding="utf-8")
    try:
        items = sources.run("t", str(payload), entry=_entry(run="cat {ident}", text="content"))
        assert items[0]["text"] == "知乎/v2ex 用 content"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_failing_command_surfaces_its_own_words():
    with tempfile.TemporaryDirectory() as root:
        script = Path(root) / "boom.py"
        script.write_text("import sys; sys.stderr.write('Navigation rejected\\n'); sys.exit(1)\n")
        entry = _entry(run=f"python3 {script} {{ident}}")
        try:
            sources.run("t", "x", entry=entry, attempts=1)
        except RuntimeError as error:
            assert "Navigation rejected" in str(error) and "退出码 1" in str(error), str(error)
        else:
            raise AssertionError("a non-zero exit must raise")


def test_a_transient_failure_is_retried_once():
    """Browser-backed sources fail this way; reading is idempotent, so one retry is free."""
    with tempfile.TemporaryDirectory() as root:
        script = Path(root) / "flaky.py"
        script.write_text(
            "import json, pathlib, sys\n"
            "mark = pathlib.Path(sys.argv[1]) / 'tried'\n"
            "if not mark.exists():\n"
            "    mark.write_text('1'); sys.stderr.write('Navigation rejected\\n'); sys.exit(1)\n"
            "print(json.dumps([{'text': '这次成功了'}]))\n")
        entry = {"label": "t", "run": f"python3 {script} {root} {{ident}}", "text": "text"}
        items = sources.run("t", "x", entry=entry, attempts=2)
        assert items[0]["text"] == "这次成功了", "the second attempt should succeed"


def test_a_missing_command_says_what_to_install():
    try:
        sources.run("t", "x", entry=_entry(run="definitely-not-a-real-binary-xyz {ident}"), attempts=1)
    except RuntimeError as error:
        assert "找不到命令" in str(error)
    else:
        raise AssertionError("a missing binary must raise")


def test_non_json_output_is_reported_not_guessed():
    with tempfile.TemporaryDirectory() as root:
        script = Path(root) / "html.py"
        script.write_text("print('<html>not json</html>')\n")
        try:
            sources.run("t", "x", entry={"label": "t", "run": f"python3 {script} {{ident}}", "text": "text"},
                        attempts=1)
        except RuntimeError as error:
            assert "没有输出 JSON" in str(error)
        else:
            raise AssertionError("non-JSON must raise")


def test_plugins_merge_over_the_builtins():
    original = sources.PLUGIN_DIR
    root = Path(tempfile.mkdtemp())
    try:
        sources.PLUGIN_DIR = root
        (root / "mine.json").write_text(json.dumps({
            "my-source": {"label": "我的源", "ident": "我的标识", "run": "cat {ident}", "text": "t"},
            "v2ex": {"label": "覆盖内置", "ident": "x", "run": "cat {ident}", "text": "content"},
        }, ensure_ascii=False), encoding="utf-8")
        loaded = sources.load()
        assert loaded["my-source"]["label"] == "我的源"
        assert loaded["my-source"]["from"] == "mine.json"
        assert loaded["v2ex"]["label"] == "覆盖内置", "a plugin may override a built-in"
        assert loaded["bilibili"]["from"] == "sources.json", "built-ins survive"
        listed = {s["name"] for s in sources.describe()}
        assert "my-source" in listed and "bilibili" in listed
    finally:
        sources.PLUGIN_DIR = original
        shutil.rmtree(root, ignore_errors=True)


def test_describe_rejects_an_unknown_source():
    try:
        sources.describe("no-such-source")
    except RuntimeError as error:
        assert "没有这个数据源" in str(error)
    else:
        raise AssertionError("unknown source must raise")


def test_the_shipped_builtins_are_well_formed():
    """A typo in sources.json should fail here, not in the middle of a run."""
    loaded = sources.load()
    assert {"bilibili", "weibo", "xiaohongshu", "zhihu", "youtube", "v2ex", "hackernews"} <= set(loaded)
    assert "xueqiu" not in loaded, "removed on request: it needs a login and was never verified"
    for name, entry in loaded.items():
        assert entry.get("label"), f"{name} has no label"
        assert entry.get("ident"), f"{name} has no ident help"
        assert "{ident}" in entry.get("run", ""), f"{name}.run has no {{ident}}"
        assert entry.get("text"), f"{name} has no text path"


def test_a_second_pull_comes_from_the_cache():
    """The bug this exists to prevent: asking two questions about one video scraped it twice, so
    the denominator could move between questions."""
    import tempfile
    from pathlib import Path as P
    root = P(tempfile.mkdtemp())
    counter = root / "runs.txt"
    script = root / "source.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "c = pathlib.Path(sys.argv[1])\n"
        "c.write_text((c.read_text() if c.exists() else '') + 'x')\n"
        "print(json.dumps([{'text': '第 ' + str(len(c.read_text())) + ' 次抓到的内容'}]))\n")
    entry = {"label": "t", "run": f"python3 {script} {counter} {{ident}}", "text": "text"}
    keep = sources.CACHE_DIR
    sources.CACHE_DIR = root / "cache"
    try:
        first = sources.run("t", "abc", 10, entry=entry)
        assert counter.read_text() == "x", "the command ran once"
        second = sources.run("t", "abc", 10, entry=entry)
        assert second == first, "the second pull must be the same data"
        assert counter.read_text() == "x", "and must NOT run the command again"
        status = sources.cache_status("t", "abc", 10, entry)
        assert status["count"] == 1 and status["cached"] is True and status["fetched_at"]
        assert sources.cache_status("t", "other", 10, entry)["cached"] is False, "keyed by ident"
        assert sources.cache_status("t", "abc", 99, entry)["cached"] is False, "keyed by limit too"
        fresh = sources.run("t", "abc", 10, entry=entry, refresh=True)
        assert counter.read_text() == "xx", "refresh=True re-pulls on purpose"
        assert fresh[0]["text"] != first[0]["text"], "and really is a new pull"
    finally:
        sources.CACHE_DIR = keep
        import shutil; shutil.rmtree(root, ignore_errors=True)


def test_editing_the_source_definition_busts_the_cache():
    """Same name, different command -> the old pull must not be served."""
    import tempfile
    from pathlib import Path as P
    root = P(tempfile.mkdtemp())
    keep = sources.CACHE_DIR
    sources.CACHE_DIR = root / "cache"
    try:
        one = {"label": "t", "run": "echo x " + "{ident}", "text": "text"}
        two = {"label": "t", "run": "echo y " + "{ident}", "text": "text"}
        assert sources.cached("t", "abc", 10, one) is None
        sources._store("t", "abc", 10, one, [{"text": "旧的"}])
        assert sources.cached("t", "abc", 10, one) is not None
        assert sources.cached("t", "abc", 10, two) is None, "a changed command must re-pull"
    finally:
        sources.CACHE_DIR = keep
        import shutil; shutil.rmtree(root, ignore_errors=True)


def test_a_corrupt_cache_is_ignored_not_fatal():
    import tempfile
    from pathlib import Path as P
    root = P(tempfile.mkdtemp())
    keep = sources.CACHE_DIR
    sources.CACHE_DIR = root
    try:
        (root / "junk.json").write_text("{not json")
        script = root / "src.py"
        script.write_text("import json; print(json.dumps([{'text': 'ok'}]))")
        assert sources.run("t", "x", entry=_entry(run=f"python3 {script} {{ident}}"))[0]["text"] == "ok"
    finally:
        sources.CACHE_DIR = keep
        import shutil; shutil.rmtree(root, ignore_errors=True)


def test_subject_reads_both_payload_shapes():
    """The subject (video/note title) comes back either as a plain object or as opencli's
    [{field, value}, …] rows — the layer must handle both, and never raise when it cannot."""
    import tempfile
    from pathlib import Path as P
    root = P(tempfile.mkdtemp())
    keep = (sources.CACHE_DIR,)
    sources.CACHE_DIR = root / "cache"

    def script(name, payload):
        f = root / name
        f.write_text("import json; print(json.dumps(" + payload + "))")
        return f

    flat = script("flat.py", "[{'id': 1, 'title': '一条普通对象的标题'}]")
    kv = script("kv.py", "[{'field': 'author', 'value': 'x'}, {'field': 'Title', 'value': '键值对形式的标题'}]")
    nested = script("nested.py", "{'data': {'title': '嵌在 data 里的标题'}}")
    broken = script("broken.py", "{'nope': 1}")
    try:
        assert sources.subject("t", "a", entry={"subject": {"run": f"python3 {flat} " + "{ident}", "at": "title"}}) \
            == "一条普通对象的标题"
        assert sources.subject("t", "a", entry={"subject": {"run": f"python3 {kv} " + "{ident}", "at": "title"}}) \
            == "键值对形式的标题", "field/value rows, matched case-insensitively"
        assert sources.subject("t", "a", entry={"subject": {"run": f"python3 {nested} " + "{ident}", "at": "data.title"}}) \
            == "嵌在 data 里的标题"
        assert sources.subject("t", "a", entry={"subject": {"run": f"python3 {broken} " + "{ident}", "at": "title"}}) == ""
        assert sources.subject("t", "a", entry={"subject": {"run": "no-such-cmd-xyz " + "{ident}", "at": "title"}}) == "", \
            "a source with no subject command, or a failing one, is not an error"
        assert sources.subject("t", "a", entry={}) == "", "no subject block at all"
        # cached, so the second look does not run the command
        (root / "flat.py").write_text("import sys; sys.exit(1)")
        assert sources.subject("t", "a", entry={"subject": {"run": f"python3 {flat} " + "{ident}", "at": "title"}}) \
            == "一条普通对象的标题", "the title is cached like the items"
    finally:
        sources.CACHE_DIR = keep[0]
        import shutil; shutil.rmtree(root, ignore_errors=True)


def test_every_builtin_subject_block_is_well_formed():
    for name, entry in sources.load().items():
        spec = entry.get("subject")
        if not spec:
            continue
        assert "{ident}" in spec.get("run", ""), f"{name}.subject.run has no {{ident}}"
        assert spec.get("at"), f"{name}.subject has no `at` path"


def test_skip_drops_rows_that_are_not_opinions():
    """Hacker News returns the story itself as a row (type POST). It is not an opinion about
    itself, so it must not enter the denominator."""
    import tempfile
    from pathlib import Path as P
    root = P(tempfile.mkdtemp())
    keep = sources.CACHE_DIR
    sources.CACHE_DIR = root / "cache"
    payload = root / "in.json"
    payload.write_text(json.dumps([
        {"type": "POST", "author": "op", "text": "帖子本体，不是评论"},
        {"type": "L0", "author": "a", "text": "第一条评论"},
        {"type": "post", "author": "b", "text": "大小写不同也要剔掉"},
        {"type": "L1", "author": "c", "text": "被 skip 掉的类型"},
        {"type": "", "author": "d", "text": "第二条评论"},
    ], ensure_ascii=False), encoding="utf-8")
    try:
        reader = root / "read.py"
        reader.write_text("import pathlib, sys; print(pathlib.Path(sys.argv[1]).read_text())")
        run = f"python3 {reader} {payload} " + "{ident}"
        entry = _entry(run=run, text="text", author="author", skip={"type": ["POST", "L1"]})
        items = sources.run("t", "x", entry=entry)
        assert [i["text"] for i in items] == ["第一条评论", "第二条评论"], items
        without = sources.run("t", "x", entry=_entry(run=run, text="text"))
        assert len(without) == 5, "without skip, every row counts"
        assert sources.cached("t", "x", 200, entry) is not None
        changed = {**entry, "skip": {"type": "L0"}}
        assert sources.cached("t", "x", 200, changed) is None, "editing skip must bust the cache"
    finally:
        sources.CACHE_DIR = keep
        import shutil; shutil.rmtree(root, ignore_errors=True)


def test_a_pasted_url_becomes_the_id_the_source_wants():
    """V2EX wants 1243550; the clipboard has https://www.v2ex.com/t/1243550."""
    entry = {"run": "opencli v2ex replies {ident} -f json", "ident_from": r"t/(\d+)"}
    assert sources.normalize_ident(entry, "https://www.v2ex.com/t/1243550") == "1243550"
    assert sources.normalize_ident(entry, "1243550") == "1243550", "a bare id is already fine"
    assert sources.normalize_ident(entry, " 1243550 ") == "1243550"
    assert sources.normalize_ident(entry, "not-a-topic") == "not-a-topic", "no match: pass it through"
    assert sources.normalize_ident({}, "anything") == "anything", "no ident_from: untouched"
    argv = sources.command_for(entry, "https://www.v2ex.com/t/1243550", 10)
    assert argv == ["opencli", "v2ex", "replies", "1243550", "-f", "json"], argv


def test_every_builtin_ident_pattern_extracts_from_its_own_url():
    """Whatever URL the site shows, pasting it must work — this is the whole point."""
    loaded = sources.load()
    examples = {
        "v2ex": "https://www.v2ex.com/t/1243550",
        "bilibili": "https://www.bilibili.com/video/BV1WtAGzYEBm/?spm_id_from=333",
        "hackernews": "https://news.ycombinator.com/item?id=49750694",
        "youtube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "zhihu": "https://www.zhihu.com/question/1/answer/123456789",
    }
    for name, url in examples.items():
        got = sources.normalize_ident(loaded[name], url)
        assert got != url, f"{name}: {url!r} was passed through unchanged"
        assert " " not in got and "/" not in got, f"{name} extracted {got!r}"


def test_the_empty_result_error_says_what_the_source_wanted():
    import tempfile
    from pathlib import Path as P
    root = P(tempfile.mkdtemp())
    keep = sources.CACHE_DIR
    sources.CACHE_DIR = root / "cache"
    script = root / "empty.py"
    script.write_text("import json; print(json.dumps([]))")
    try:
        entry = {"label": "t", "ident": "话题 id（t/ 后面的数字）", "run": f"python3 {script} {{ident}}", "text": "text"}
        try:
            sources.run("v2ex", "123", entry=entry, attempts=1)
        except RuntimeError as e:
            assert "话题 id" in str(e) and "123" in str(e), str(e)
        else:
            raise AssertionError("an empty result must raise")
    finally:
        sources.CACHE_DIR = keep
        import shutil; shutil.rmtree(root, ignore_errors=True)


def test_the_subject_call_normalizes_the_ident_too():
    """The subject is fetched by its own command; it needs the same id/title fix as the comments.

    (Found in the browser: pasting a V2EX url fetched the comments fine but silently lost the
    title, because only the comments command knew how to pull the id out of the url.)
    """
    import tempfile
    from pathlib import Path as P
    root = P(tempfile.mkdtemp())
    keep = sources.CACHE_DIR
    sources.CACHE_DIR = root / "cache"
    seen = root / "arg.txt"
    script = root / "s.py"
    # writes down the id it was handed, then returns that id as a title
    script.write_text("import json, pathlib, sys\n"
                      f"pathlib.Path({str(seen)!r}).write_text(sys.argv[1])\n"
                      "print(json.dumps([{'id': sys.argv[1], 'title': 'T-' + sys.argv[1]}]))")
    try:
        entry = {"ident": "id", "ident_from": r"t/(\d+)", "run": f"python3 {script} {{ident}}",
                 "text": "x", "subject": {"run": f"python3 {script} {{ident}}", "at": "title"}}
        title = sources.subject("s", "https://www.v2ex.com/t/1243550", entry=entry)
        assert title == "T-1243550", title
        assert seen.read_text() == "1243550", "the subject command got " + seen.read_text()
    finally:
        sources.CACHE_DIR = keep
        import shutil; shutil.rmtree(root, ignore_errors=True)


def test_describe_tells_the_ui_which_sources_accept_a_url():
    """The ident box says "a link works too" — but only for sources where that is true."""
    for row in sources.describe():
        assert set(row) == {"name", "label", "ident", "note", "from", "url_ok",
                            "label_en", "ident_en", "note_en"}, row
        assert isinstance(row["url_ok"], bool), row
    by_name = {row["name"]: row for row in sources.describe()}
    assert by_name["v2ex"]["url_ok"] is True
    assert by_name["bilibili"]["url_ok"] is True
    assert by_name["xiaohongshu"]["url_ok"] is False, "a note url needs its xsec_token: no id to pull out"
    assert sources.describe("v2ex")["url_ok"] is True


def test_a_wrong_kind_of_link_is_refused_before_anything_runs():
    """Zhihu comments hang off *answers*; a question link has none. opencli would answer that with
    `exitCode: 2 / Answer ID must be …`, which says nothing a user can act on."""
    entry = {"label": "知乎回答评论", "ident": "回答 URL 或 answer id",
             "run": "opencli zhihu answer-comments {ident} --limit {limit} -f json",
             "ident_from": r"answer/(\d+)", "note": "评论挂在「回答」下面。"}
    try:
        sources.command_for(entry, "https://www.zhihu.com/question/357296785", 5)
    except RuntimeError as e:
        message = str(e)
        assert "知乎回答评论" in message and "回答 URL" in message, message
        assert "评论挂在「回答」下面" in message, "the source's own note must be passed through"
        assert "question/357296785" in message, "say what was rejected"
    else:
        raise AssertionError("a link with no id in it must be refused")
    # the forms that do work must still work
    assert sources.command_for(entry, "https://www.zhihu.com/question/1/answer/1937205528846655537", 5)[3] \
        == "1937205528846655537"
    assert sources.command_for(entry, "1937205528846655537", 5)[3] == "1937205528846655537"
    bare = {"label": "x", "run": "echo {ident} --limit {limit}"}
    assert sources.command_for(bare, "https://anything/goes", 5)[1] == "https://anything/goes"


def test_zhihu_ships_the_answer_only_rule():
    entry = sources.load()["zhihu"]
    assert entry.get("ident_from"), "without a pattern the guard above cannot protect anything"
    assert "回答" in entry.get("note", ""), "the note must tell the user where comments live"


def test_every_source_has_english_too():
    """The page is bilingual, so each source needs a name and a help line in both languages."""
    for row in sources.describe():
        assert row["label_en"].strip(), row["name"] + " has no label_en"
        assert row["ident_en"].strip(), row["name"] + " has no ident_en"
        if row["note"].strip():          # an empty note needs no translation
            assert row["note_en"].strip(), row["name"] + " has a note but no note_en"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\n{len(tests)} checks passed")

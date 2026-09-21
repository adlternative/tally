#!/usr/bin/env python3
"""Pull the text out of a data source, so the statistics engine never has to care where it came from.

A source is **a command that prints JSON, plus the paths saying which field is the text**. Most
built-ins are `opencli <site> <command>` (https://github.com/jackwener/OpenCLI — 900+ commands over
100+ sites), which is why B站 / 微博 / 小红书 / 知乎 / YouTube each reduce to one line. A source does
NOT have to be opencli: anything that prints JSON works — a python script, a curl call, your own
crawler.

Every pull is **cached** (memory, then `~/.jev-tally/cache/*.json`), so asking a second question
about the same video does not scrape it again: the denominator stays the same, the run is fast, and
a re-run afterwards replays the same comments instead of whatever the site now returns. Pass
`refresh=True` to re-pull on purpose.

    sources.json        the built-ins (edit freely)
    sources.d/*.json    your own sources; merged after, so they can add or override anything

An entry looks like this:

    "bilibili": {
      "label":  "B站视频评论",
      "ident":  "视频 BV 号，如 BV1WtAGzYEBm",
      "run":    "opencli bilibili comments-all {ident} --max {limit} -f json",
      "text":   "text",          # dotted path inside one row, or relative to `items`
      "author": "author",        # optional — used for the "how many people" count
      "weight": "likes",         # optional, kept for later weighting
      "id":     "rpid",          # optional
      "items":  "",              # optional: dotted path to the array (empty = top level is it)
      "min_chars": 2             # optional: drop emptier rows than this
    }

The command is run **without a shell** (argv, not a string), so an ident containing `;` or `$()` is
just a filename, not a command.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILTIN = HERE / "sources.json"
PLUGIN_DIR = Path(os.environ.get("JEV_TALLY_SOURCES_D") or (HERE / "sources.d"))
TIMEOUT = 300
MAX_OUTPUT = 12_000_000
CACHE_DIR = Path(os.environ.get("JEV_TALLY_CACHE_DIR") or (Path.home() / ".jev-tally" / "cache"))


def _dig(row, path, default=None):
    """Walk a dotted path ('a.b.0.c') into parsed JSON. Missing -> default."""
    if not path:
        return row
    current = row
    for key in str(path).split("."):
        if isinstance(current, dict):
            if key not in current:
                return default
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            return default
    return current


def load():
    """Every known source: the built-in file, then sources.d/*.json merged over it."""
    sources = {}
    for path in [BUILTIN] + sorted(PLUGIN_DIR.glob("*.json")) if PLUGIN_DIR.exists() else [BUILTIN]:
        if not path.exists():
            continue
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise RuntimeError(f"{path.name} is not valid JSON: {error}") from None
        for name, entry in loaded.items():
            sources[name] = {**entry, "from": path.name}
    return sources


def describe(name=None):
    """What the UI needs: the label, the help text for the ident box, and whether a url will do."""
    def one(key, value):
        # label_en / ident_en / note_en are optional: the page falls back to Chinese
        return {"name": key, "label": value.get("label", key), "ident": value.get("ident", "标识"),
                "note": value.get("note", ""), "from": value.get("from", ""),
                "label_en": value.get("label_en", ""), "ident_en": value.get("ident_en", ""),
                "note_en": value.get("note_en", ""),
                "url_ok": bool(value.get("ident_from"))}   # the UI says "a link works too"
    sources = load()
    if name is None:
        return [one(key, value) for key, value in sources.items()]
    if name not in sources:
        raise RuntimeError(f"没有这个数据源：{name}")
    return one(name, sources[name])


def normalize_ident(entry, ident):
    """The id a source actually wants, when the user pasted the URL instead.

    Sources declare `ident_from` (a regex with one capture group) — V2EX wants `1243550` but you
    have `https://www.v2ex.com/t/1243550` in your clipboard. No pattern, or no match: unchanged.
    """
    raw = str(ident or "").strip()
    pattern = entry.get("ident_from")
    if not pattern:
        return raw
    found = re.search(pattern, raw)
    return found.group(1) if found else raw


def command_for(entry, ident, limit):
    """argv for one run. No shell: the ident and limit are single arguments, never parsed."""
    if "{ident}" not in entry["run"]:
        raise RuntimeError("这个数据源的 run 里缺少 {ident}")
    raw = str(ident or "").strip()
    # A link we cannot read an id out of is almost always the *wrong kind* of link: a Zhihu
    # question page has no comments (they hang off answers), a V2EX link to /go/ is not a topic.
    # Catch it here — the alternative is a terse adapter error or, worse, an empty result.
    if entry.get("ident_from") and "//" in raw and not re.search(entry["ident_from"], raw):
        raise RuntimeError(f"{entry.get('label') or '这个源'}：你给的 {raw!r} 里没有它需要的 id，"
                           f"它要的是「{entry.get('ident') or '标识'}」。{entry.get('note') or ''}".strip())
    ident = normalize_ident(entry, raw)
    argv = []
    for part in shlex.split(entry["run"]):
        argv.append(part.replace("{ident}", str(ident)).replace("{limit}", str(int(limit))))
    return argv


def _rows(payload, entry):
    """The list of raw rows inside whatever the command printed."""
    at = _dig(payload, entry.get("items") or "")
    if isinstance(at, dict):                      # one object holding the list under a named key
        for key in ("data", "items", "list", "results", "comments", "replies"):
            if isinstance(at.get(key), list):
                return at[key]
        return []
    return at if isinstance(at, list) else []


def _fingerprint(entry):
    """Digest of what the source *is*. Editing sources.json must bust the cache, or a changed
    command would keep serving the old pull under the same name (the cookbook's `rubric_hash`)."""
    keep = {key: entry.get(key) for key in ("run", "items", "text", "author", "weight", "id",
                                            "min_chars", "at", "skip")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]


def _cache_file(name, ident, limit, entry):
    key = hashlib.sha256(f"{name}|{ident}|{limit}|{_fingerprint(entry)}".encode()).hexdigest()[:20]
    return CACHE_DIR / f"{key}.json"


def cached(name, ident, limit=200, entry=None):
    """The stored pull for this exact source+ident+limit, or None. Never runs the command."""
    entry = entry or load().get(name)
    if entry is None:
        return None
    path = _cache_file(name, ident, limit, entry)
    if not path.exists():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return blob if blob.get("items") else None


def cache_status(name, ident, limit=200, entry=None):
    """What the UI shows: was this already pulled, when, and how much."""
    blob = cached(name, ident, limit, entry)
    if not blob:
        return {"cached": False, "count": 0, "fetched_at": None, "age_seconds": None}
    stamp = blob.get("fetched_at")
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        age = None
    return {"cached": True, "count": len(blob["items"]), "fetched_at": stamp,
            "age_seconds": None if age is None else round(age)}


def _store(name, ident, limit, entry, items):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    blob = {"source": name, "ident": ident, "limit": limit, "items": items,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path = _cache_file(name, ident, limit, entry)
    path.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
    return blob


def _exec(argv, attempts=2):
    """Run one command and parse its JSON. Browser-backed sources fail transiently, so a read is
    tried twice; a second failure surfaces the command's own words, which name the real cause
    (not logged in, extension down) far better than we could."""
    last = None
    for attempt in range(attempts):
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
        except FileNotFoundError:
            raise RuntimeError(f"找不到命令：{argv[0]}（这个数据源要装 OpenCLI 或你自己的脚本）") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{argv[0]} 超时（{TIMEOUT}s）") from None
        if done.returncode == 0 and done.stdout.strip():
            break
        tail = [line.strip() for line in ((done.stderr or done.stdout) or "").strip().splitlines()
                if line.strip() and not line.strip().startswith("exitCode:")][-3:]
        last = (f"{argv[0]} {argv[1] if len(argv) > 1 else ''} 退出码 {done.returncode}："
                + (" / ".join(tail)[:300] if tail else "（没有输出）"))
        if attempt < attempts - 1:
            time.sleep(1.5)
    else:
        raise RuntimeError(last or "这个数据源没有输出")
    if len(done.stdout) > MAX_OUTPUT:
        raise RuntimeError("这个数据源一次返回太多了，先调小 limit")
    try:
        return json.loads(done.stdout)
    except ValueError:
        head = done.stdout.strip()[:200]
        raise RuntimeError(f"这个数据源没有输出 JSON：{head!r}") from None


def subject(name, ident, entry=None, refresh=False):
    """What the comments are *about* — the video/note/post title — or "" if we cannot tell.

    The question a case asks needs an object ("这条评论对「夸克 S1 眼镜」是什么态度？"), and the
    source already knows what the object is, so the user should not have to type it. A title
    that cannot be fetched is not an error: the question falls back to a generic phrasing.
    """
    entry = entry or load().get(name)
    spec = (entry or {}).get("subject") or {}
    if not spec.get("run"):
        return ""
    pseudo = {"run": spec["run"], "at": spec.get("at"), "ident_from": entry.get("ident_from")}
    path = _cache_file(name, ident, 0, pseudo)
    if not refresh and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("subject", "")
        except ValueError:
            pass
    try:
        payload = _exec(command_for(pseudo, ident, 1))
    except RuntimeError:
        return ""                                  # no title is fine; the user may type one
    at = str(spec.get("at") or "title")
    rows = payload if isinstance(payload, list) else [payload]
    if rows and isinstance(rows[0], dict) and "field" in rows[0] and "value" in rows[0]:
        # opencli's metadata commands return [{field, value}, …] instead of a plain object
        text = next((row.get("value") for row in rows if str(row.get("field")).lower() == at.lower()), "")
    else:
        # most sources return [{…}] (one row), some return the object itself; dig into the row
        text = _dig(rows[0] if rows else {}, at)
    text = " ".join(str(text or "").split())[:120]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"subject": text,
                                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                               ensure_ascii=False), encoding="utf-8")
    return text


def run(name, ident, limit=200, entry=None, attempts=2, refresh=False):
    """Return [{text, author, weight, id}] for this source, **from the cache when we have it**.

    A second question about the same video must not scrape it again — the comments would move
    under the denominator, and the run would be slow and non-reproducible. `refresh=True` is the
    explicit "go and look again".

    Browser-backed sources (opencli COOKIE/INTERCEPT) fail transiently — 'Pre-navigation …
    Navigation rejected' when the automation window will not open — so a read is tried twice.
    Reading is idempotent, so a retry is free; a second failure surfaces opencli's own words,
    which name the real cause (not logged in, extension down) far better than we could.
    """
    entry = entry or load().get(name)
    if entry is None:
        raise RuntimeError(f"没有这个数据源：{name}")
    if not refresh:
        hit = cached(name, ident, limit, entry)
        if hit:
            return hit["items"]
    argv = command_for(entry, ident, limit)
    payload = _exec(argv, attempts)

    minimum = int(entry.get("min_chars", 2))
    skip = entry.get("skip") or {}
    items, seen = [], set()
    for row in _rows(payload, entry):
        if not isinstance(row, dict):
            continue
        # Some sources return the thing being commented on as a row too (Hacker News returns the
        # story with type POST), and some pad the list with placeholders ("[+5 more replies]",
        # an empty type). Neither is an opinion, so `skip` drops them: field -> value or list.
        if any(str(row.get(field, "")).lower() in [str(v).lower() for v in
                                                   (value if isinstance(value, list) else [value])]
               for field, value in skip.items()):
            continue
        text = _dig(row, entry.get("text", "text"))
        if isinstance(text, list):
            text = " ".join(str(part) for part in text)
        text = (text or "").strip() if isinstance(text, (str, int, float)) else ""
        if len(text) < minimum or text in seen:
            continue
        seen.add(text)
        items.append({
            "text": text,
            "author": str(_dig(row, entry["author"]) or "").strip() if entry.get("author") else "",
            "weight": _dig(row, entry["weight"]) if entry.get("weight") else None,
            "id": str(_dig(row, entry["id"]) or "") if entry.get("id") else "",
        })
        if len(items) >= int(limit):
            break
    if not items:
        # Say what the source wanted and what we actually used — "check the value" is useless when
        # the real problem is "this source wants an id and you gave it a url".
        used = normalize_ident(entry, ident)
        given = f"（你给的是 {str(ident)!r}，已从里面取出 {used!r}）" if used != str(ident or "").strip() else ""
        raise RuntimeError(f"{name} 这次没拿到任何文本。这个源要的是「{entry.get('ident') or '标识'}」，"
                           f"实际用 {used!r} 去取{given}，检查这个取值是否正确")
    _store(name, ident, limit, entry, items)
    return items


def main():
    import sys
    sources = describe()
    if len(sys.argv) < 3:
        print("用法: python3 sources.py <source> <ident> [limit]\n")
        for entry in sources:
            print(f"  {entry['name']:<16}{entry['label']:<18}{entry['ident']}")
        return 2
    name, ident = sys.argv[1], sys.argv[2]
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    items = run(name, ident, limit)
    print(f"{name}({ident}) -> {len(items)} 条，作者 {len({i['author'] for i in items if i['author']})} 个")
    for item in items[:5]:
        print(f"  [{item['author'] or '-'}] {item['text'][:90]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""tally — turn any pile of text into percentages, with Jev judging every item.

Point it at a data source (B站 / 微博 / 小红书 / 知乎 / YouTube / V2EX / your own plugin), pick a
few labels (赞 / 中立 / 反对 / 无关), and it asks Jev one question per item and counts the answers
in code. You get a percentage, the denominator, a per-author count, and the per-item detail sorted
by how unsure Jev was.

Two modes, because two things get asked. **逐条统计** (the point of the tool) asks one narrow
question about every item and counts in code — that is how you get 赞 41% / 中立 33% / 反对 26% with
a denominator and an audit trail. **整页一问** puts a single question to one page, either as a
typed judgment (noul / choice / score) or as “which passage answers this”, where the passage is
copied out verbatim rather than generated.

  Server  python3 tally.py --serve [--port 8020]     -> http://127.0.0.1:8020
  CLI     python3 tally.py --source bilibili --ident BV1... --kind choice \
                          --options "赞,反对,中立,无关" "这条评论是什么态度？"
  CLI     python3 tally.py https://example.com "这一页讲了什么"
  Tests   python3 test_tally.py && python3 test_sources.py

Files: tally.py (engine + server) · frontend/src (React workspace) · frontend/dist (production build)
· sources.py + sources.json (data sources) · sources.d/*.json (your plugins).

Token: $TYPESAFE_API_KEY, $JEV_TOKEN, ~/.jev-tally/token (written by the UI), or ~/jev_token.
It is never printed, echoed, or sent to the browser.
"""
import argparse
import json
import mimetypes
import os
import random
import re
import sys
import time
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

API_BASE = os.environ.get("JEV_TALLY_API_BASE") or "https://api.typesafe.ai"
API = API_BASE + "/v1/systemone"
MODEL = "jev-latest"          # pin "jev-1.13.0" if you tune thresholds against a version
USD_PER_MTOK = 0.042          # list price for input tokens; output tokens are free
MAX_PAGE_CHARS = 60_000       # API budget is 32k tokens for state + longest question
N_CANDIDATES = 12             # options handed to Jev when retrieval finds matches
N_CANDIDATES_FALLBACK = 40    # ...and when it finds none (paraphrase, cross-lingual questions)
MAX_OPTIONS = 250             # API allows 255 per Choice
UA = "Mozilla/5.0 (compatible; jev-ask/1.0)"


# ---------------------------------------------------------------- credentials

TOKEN_FILE = Path(os.environ.get("JEV_TALLY_TOKEN_FILE") or (Path.home() / ".jev-tally" / "token"))
LEGACY_TOKEN_FILE = Path.home() / "jev_token"


def load_token():
    """The API key, from the environment or a token file. The value is never logged or echoed.

    Order: $TYPESAFE_API_KEY / $JEV_TOKEN → ~/.jev-tally/token (what the UI writes, 0600) →
    ~/jev_token (the old scratch-dir location, still honoured).
    """
    for var in ("TYPESAFE_API_KEY", "JEV_TOKEN"):
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    for path in (TOKEN_FILE, LEGACY_TOKEN_FILE):
        if path.exists():
            raw = path.read_text().strip()
            if "=" in raw:                      # tolerate a `KEY=value` file
                raw = raw.split("=", 1)[1]
            if raw.strip().strip('"').strip("'"):
                return raw.strip().strip('"').strip("'")
    raise RuntimeError("没有 Jev token：在页面上填一个，或者设 $TYPESAFE_API_KEY")


def token_status():
    """Where the token comes from — never the token itself, so this is safe to return."""
    if any(os.environ.get(var, "").strip() for var in ("TYPESAFE_API_KEY", "JEV_TOKEN")):
        return {"configured": True, "source": "环境变量", "where": "$TYPESAFE_API_KEY / $JEV_TOKEN",
                "can_write": True}
    for path in (TOKEN_FILE, LEGACY_TOKEN_FILE):
        if path.exists() and path.read_text().strip():
            return {"configured": True, "source": "文件", "where": str(path), "can_write": True}
    return {"configured": False, "source": "", "where": str(TOKEN_FILE), "can_write": True}


def save_token(token):
    """Verify the token against the API, then store it 0600. Verifying is free (no inference)."""
    token = (token or "").strip().strip('"').strip("'")
    if len(token) < 8:
        raise RuntimeError("这看起来不像一个 token")
    req = Request(API_BASE + "/v1/models", headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                raise RuntimeError(f"TypeSafe 返回 {resp.status}")
    except HTTPError as error:
        if error.code == 401:
            raise RuntimeError("这个 token 被拒绝了（401），检查是否复制完整") from None
        raise RuntimeError(f"验证 token 时 TypeSafe 返回 {error.code}") from None
    except URLError as error:
        raise RuntimeError(f"连不上 TypeSafe：{error.reason}") from None
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token + "\n")
    TOKEN_FILE.chmod(0o600)
    return token_status()


# ------------------------------------------------------------------- fetching

class _Extract(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "section", "article", "blockquote", "pre", "table", "ul", "ol", "dt", "dd"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip, self.title, self.in_title = [], 0, "", False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag == "title":
            self.in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag == "title":
            self.in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.parts.append(data)


def html_to_text(html):
    """Strip markup (stdlib parser, no bs4) and collapse whitespace. Returns (text, title)."""
    ex = _Extract()
    ex.feed(html)
    ex.close()
    text = "".join(ex.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{2,}", "\n\n", text).strip(), " ".join(ex.title.split())


def fetch_text(url, timeout=20):
    """Fetch a URL and return readable text. Only http(s), so file:// cannot be reached."""
    scheme = urlparse(url).scheme
    if scheme not in ("http", "https"):
        raise RuntimeError("url must start with http:// or https://")
    req = Request(url, headers={"User-Agent": UA,
                                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"})
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read(4_000_000)
        ctype = resp.headers.get_content_type()
        charset = resp.headers.get_content_charset() or "utf-8"
        final = resp.geturl()
    body = raw.decode(charset, "replace")
    if ctype in ("text/html", "application/xhtml+xml") or body.lstrip()[:1] == "<":
        text, title = html_to_text(body)
    else:
        text, title = body, ""
    return {"url": final, "title": title, "text": text[:MAX_PAGE_CHARS], "html": body[:1_500_000]}


# ------------------------------------------------- candidate retrieval (code)

SENT = re.compile(r"(?<=[.!?;；])\s+|\n+|(?<=[。！？])")
ASCII = re.compile(r"[a-z0-9]{2,}")
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f]+")
STOP = set("""the a an and or of to in for on with is are was were be been being this that these those it its
as at by from not no do does did how what which who whom when where why can could should would will shall may
might must if then than there here you your we our they their he she his her i me my about into over after""".split())


def terms(text):
    """Words for latin script, character bigrams for CJK (works for both languages)."""
    text = text.lower()
    out = {w for w in ASCII.findall(text) if w not in STOP}
    for run in CJK.findall(text):
        if len(run) > 1:
            out.update(run[i:i + 2] for i in range(len(run) - 1))
        else:
            out.add(run)
    return out


def chunks(text, target=450, cap=900):
    """Pack sentences into ~450-char passages so each candidate is one answer-sized unit.

    ponytail: packing glues neighbouring lines together, so a selected passage can carry
    site chrome (a Wikipedia sidebar rides along with the birth date). Jev still picks the
    passage that holds the answer, and the answer stays verbatim. Upgrade path: strip
    boilerplate per site, or pack to ~250 chars, which risks short nav links outranking
    real sentences in `rank`.
    """
    out, buf = [], ""
    for piece in SENT.split(text):
        piece = piece.strip()
        if not piece:
            continue
        for part in [piece[i:i + cap] for i in range(0, len(piece), cap)]:
            if buf and len(buf) + len(part) + 1 > target:
                out.append(buf)
                buf = part
            else:
                buf = f"{buf} {part}".strip()
    if buf:
        out.append(buf)
    return [c for c in out if len(c) >= 20]


def rank(question, candidates, k=N_CANDIDATES):
    """Lexical prefilter: returns kept indices in document order. Jev does the judging."""
    qterms = terms(question)
    scores = []
    for c in candidates:
        cterms = terms(c)
        hits = len(cterms & qterms) if qterms else 0
        scores.append(hits / (len(cterms) ** 0.5) if hits else 0.0)
    best = sorted(range(len(candidates)), key=lambda i: (-scores[i], i))
    if not any(scores[i] > 0 for i in best[:k]):
        # Nothing matched lexically (paraphrase, or a question in another language than
        # the page): give Jev more options instead of a bad 12. ponytail: O(n) scan, index
        # by inverted term list if pages ever get much larger.
        return sorted(range(min(N_CANDIDATES_FALLBACK, len(candidates), MAX_OPTIONS)))
    return sorted(best[:k])


MAX_ITEMS = 200      # per-item mode asks one question per entry, all in a single request
BAND = 0.6          # per-item noul: p >= BAND reads as one side, p <= 1 - BAND as the other


class _Repeats(HTMLParser):
    """Text of every element keyed by (tag, first class), so a list of repeated containers
    (comments, reviews, table rows) appears as the group with the most text volume."""

    VOID = {"br", "img", "meta", "link", "input", "hr", "source", "area"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.groups = [], {}

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        classes = (dict(attrs).get("class") or "").split()
        self.stack.append([tag, classes[0] if classes else "", []])

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                node = self.stack.pop(i)
                text = " ".join("".join(node[2]).split())
                if text and node[1]:
                    group = self.groups.setdefault(f"{node[0]}.{node[1]}", {"texts": [], "depth": i})
                    group["texts"].append(text)
                if i:
                    self.stack[i - 1][2].append("".join(node[2]))
                break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1][2].append(data)


def extract_items(html, min_len=40, min_items=3):
    """Item texts from the most text-heavy repeating container, or [] when nothing repeats.

    Among candidates with a similar member count the deeper one wins: that is the post body
    rather than the card around it, which keeps per-item metadata ("2 days ago | prev | next")
    out of the text Jev judges.
    """
    parser = _Repeats()
    try:
        parser.feed(html)
    except Exception:
        return []
    candidates = []
    for group in parser.groups.values():
        texts = group["texts"]
        if len(texts) < min_items:
            continue
        lengths = sorted(len(t) for t in texts)
        median = lengths[len(lengths) // 2]
        if median < min_len:                      # volume, not count: rules out nav link farms
            continue
        candidates.append({"texts": texts, "depth": group["depth"], "n": len(texts), "median": median,
                           "volume": len(texts) * median})
    if not candidates:
        return []
    best = max(candidates, key=lambda c: c["volume"])
    # An inner container that still holds most of the best one's text is the content rather
    # than the card around it; a short one is metadata ("2 days ago | prev | next").
    inner = [c for c in candidates if c["depth"] > best["depth"] and c["n"] >= 0.8 * best["n"]
             and c["median"] >= 0.6 * best["median"]]
    return (max(inner, key=lambda c: c["depth"]) if inner else best)["texts"]


def items_for(page):
    """(items, mode): structural entries when the markup repeats, else text passages."""
    found = [t for t in extract_items(page.get("html") or "") if len(t) >= 8]
    if len(found) >= 3:
        return found[:MAX_ITEMS], "条目"
    return chunks(page["text"])[:MAX_ITEMS], "段落"


def guess_filter(question, items):
    """The question's most specific term that actually occurs in the items: the denominator hook."""
    lowered = [t.lower() for t in items]
    counts = {}
    for term in terms(question):
        seen = sum(1 for text in lowered if term in text)
        if seen:
            counts[term] = seen
    return min(counts, key=lambda t: (counts[t], -len(t))) if counts else ""


# --------------------------------------------------------------- the Jev call

KINDS = ("passage", "noul", "choice", "score")
SCOPES = ("page", "items")


def parse_options(raw):
    """Split user-supplied options/levels on newlines or commas."""
    out = []
    for part in re.split(r"[\n,，、]+", raw or ""):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def build_request(kind, url, question, page_text, candidates=(), options=()):
    """Build one Jev request. `kind` picks the primitive the user asked for.

    passage -> choice over retrieved passages, plus `none` (extractive Q&A, the default)
    noul    -> P(yes); use for one yes/no question about the page
    choice  -> the user's own competing options
    score   -> the user's own ordered levels
    """
    state = {"url": url, "question": question, "page": page_text}
    if kind == "passage":
        criteria = {f"c{i:02d}": text for i, text in enumerate(candidates)}
        criteria["none"] = "No option answers `question`."
        question_obj = {
            "type": "choice",
            "instructions": {
                "task": "Pick the option whose passage answers `question`, using only `page`.",
                "rules": [
                    "Choose the option that contains the answer to `question`.",
                    "Choose `none` if no option answers it, or `page` does not contain the answer.",
                ],
            },
            "criteria": criteria,
        }
    elif kind == "noul":
        question_obj = {"type": "noul", "instructions": question}
    elif kind == "choice":
        question_obj = {"type": "choice", "instructions": question,
                        "criteria": {option: None for option in options}}
    else:
        question_obj = {"type": "score", "instructions": question, "criteria": list(options)}
    return {"model": MODEL, "state": state, "questions": {"answer": question_obj}}


def build_item_request(url, title, items, question, per, options):
    """One request, one narrow question per item. Jev judges each entry; code does the counting.

    The state is only the entries (no page chrome), and each question points at its own entry
    by backticked path, which is the documented way to reference nested state.
    """
    state = {"url": url, "title": title,
             "items": [{"id": i + 1, "text": text} for i, text in enumerate(items)]}
    questions = {}
    for i in range(len(items)):
        instructions = {"item": f"`items[{i}].text`", "question": question}
        if per == "noul":
            questions[f"i{i + 1}"] = {"type": "noul", "instructions": instructions}
        elif per == "choice":
            questions[f"i{i + 1}"] = {"type": "choice", "instructions": instructions,
                                      "criteria": {option: None for option in options}}
        else:
            questions[f"i{i + 1}"] = {"type": "score", "instructions": instructions,
                                      "criteria": list(options)}
    return {"model": MODEL, "state": state, "questions": questions}


def band_label(value):
    """Which of the three bands a 0..1 judgment falls in — one definition, used for both the
    counts and each row's label, so a filter in the UI can never disagree with the distribution."""
    return "看好" if value >= BAND else "不看好" if value <= 1 - BAND else "不确定"


def aggregate(per, answers, items, options, authors=None):
    """Count the per-item judgments in code. Jev's probabilities are judgments, not tallies:
    a proportion has to be computed here, from one judgment per entry.

    `authors` (optional) turns "how many entries" into "how many people": one author who posts
    five times is one opinion, not five.
    """
    rows, legend = [], {}
    for i, text in enumerate(items):
        answer = answers[f"i{i + 1}"]
        if per == "noul":
            value, certainty = answer["noul"], abs(answer["noul"] - 0.5)
            label = band_label(value)
        elif per == "choice":
            value, label, certainty = None, answer["choice"], answer.get("confidence", 1.0)
        else:
            legend = legend or (answer.get("legend") or {})
            modal = max(answer["probabilities"], key=answer["probabilities"].get)
            value, label, certainty = answer["score"], legend.get(modal, modal), answer.get("confidence", 1.0)
        rows.append({"n": i + 1, "text": text, "value": value, "label": label, "certainty": certainty,
                     "author": (authors or [""] * len(items))[i]})
    total = len(rows)
    if per == "noul":
        names = ("看好", "不确定", "不看好")
        bands = [(name, lambda r, name=name: band_label(r["value"]) == name) for name in names]
        counts = [(name, sum(1 for r in rows if test(r))) for name, test in bands]
        mean, uncertain = sum(r["value"] for r in rows) / total, counts[1][1]
    else:
        labels = list(options)
        counts = [(label, sum(1 for r in rows if r["label"] == label)) for label in labels]
        counts.sort(key=lambda pair: -pair[1])
        mean = sum(r["value"] for r in rows) / total if per == "score" else None
        uncertain = sum(1 for r in rows if r["certainty"] < 0.15)
    lead, lead_count = counts[0]
    rows.sort(key=lambda r: r["certainty"])
    # Per author: average the probabilities (a person who posts five times counts once), then count.
    people = None
    named = [r for r in rows if r["author"]]
    if named and len(named) == len(rows):
        by_author = {}
        for row in rows:
            value = row["value"] if row["value"] is not None else None
            by_author.setdefault(row["author"], []).append((value, row["label"]))
        if per == "noul":
            person_counts = [(label, sum(1 for values in by_author.values()
                                        if test({"value": sum(v for v, _ in values) / len(values)})))
                             for label, test in bands]
        else:
            picked = {author: max({l for _, l in values}, key=lambda l: sum(1 for _, x in values if x == l))
                      for author, values in by_author.items()}
            person_counts = [(label, sum(1 for l in picked.values() if l == label)) for label in labels]
        people = {"authors": len(by_author),
                  "distribution": [[label, count / len(by_author), count] for label, count in person_counts]}
    return (rows, [[label, count / total, count] for label, count in counts], lead, lead_count / total,
            mean, uncertain, people)


def _ask_items(page, question, token, kind, options, filter_term):
    """Per-item mode on a fetched page: extract the entries, filter in code, ask one question each."""
    all_items, mode = items_for(page)
    if not all_items:
        raise RuntimeError("no per-item entries found on that page")
    return _ask_items_list(all_items, question, token, kind, options, filter_term, mode,
                           {"url": page["url"], "title": page["title"]})


def ask_source(source, ident, question, token, kind, options, filter_term, limit=200, refresh=False):
    """Per-item mode on a data source (B站/微博/… or your own plugin). No page, no HTML.

    The pull is cached, so asking a second question about the same thing does not scrape it again.
    """
    import sources
    entry = sources.describe(source)
    got = sources.run(source, ident, limit, refresh=refresh)
    if not got:
        raise RuntimeError(f"{source} 没有返回任何条目")
    return _ask_items_list([item["text"] for item in got], question, token, kind, options, filter_term,
                           "条评论", {"source": source, "ident": ident, "title": entry["label"],
                                     "url": "", "_authors": [item["author"] for item in got],
                                     "_weights": [item["weight"] for item in got]})


def _ask_items_list(all_items, question, token, kind, options, filter_term, mode, meta):
    """The shared half: filter the候选, put one narrow question to every entry, count in code."""
    term = (filter_term or "").strip()
    keep = [i for i, t in enumerate(all_items) if (not term) or term.lower() in t.lower()]
    if not keep:
        raise RuntimeError(f"no entry contains \"{term}\"")
    items = [all_items[i] for i in keep]
    authors = [meta.get("_authors", [""] * len(all_items))[i] for i in keep]
    per = "noul" if kind == "passage" else kind      # "which passage" has no per-item meaning
    result = post_json(build_item_request(meta.get("url") or meta.get("source", ""),
                                          meta["title"], items, question, per, options), token)
    rows, distribution, lead, share, mean, uncertain, people = aggregate(
        per, result["answers"], items, options, authors)
    out = {"scope": "items", "kind": per, "found": True, "url": meta.get("url", ""),
           "source": meta.get("source"), "ident": meta.get("ident"), "title": meta["title"],
           "question": question, "model": result.get("model"), "confidence": None,
           "total_items": len(all_items), "counted": len(items), "mode": mode, "filter": term,
           "answer": share, "lead": lead, "mean": mean, "uncertain": uncertain,
           "distribution": distribution, "items": rows, "band": BAND, "people": people,
           "usage": result.get("usage"),
           "estimated_usd": round((result.get("usage", {}).get("input_tokens") or 0) * USD_PER_MTOK / 1e6, 6)}
    return out


def post_json(payload, token, timeout=90, attempts=4):
    """POST to TypeSafe, retrying transient failures (429, 5xx) with backoff. Never echoes the key."""
    data = json.dumps(payload).encode()
    for attempt in range(attempts):
        req = Request(API, data=data, method="POST",
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except HTTPError as e:
            # 429 and any 5xx (529 Overloaded, 503 no healthy upstream) are worth another try;
            # 4xx is a real client error and must surface immediately.
            transient = e.code == 429 or e.code >= 500
            if transient and attempt < attempts - 1:
                retry_after = e.headers.get("retry-after")
                # A 529 overload can outlast a couple of seconds, so the base delay is longer
                # than a plain doubling; jitter keeps parallel callers from retrying in lockstep.
                delay = float(retry_after) if (retry_after or "").isdigit() else 5 * 2 ** attempt
                time.sleep(min(delay, 30) + random.random())
                continue
            if e.code == 401:
                raise RuntimeError("Jev rejected the token (HTTP 401). Check ~/jev_token.") from None
            if transient:
                # The provider's body is a JSON blob meant for a log, not for a person.
                raise RuntimeError(f"TypeSafe 上游过载或不可用（HTTP {e.code}），已重试 {attempts} 次，稍后再试") from None
            body = e.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"Jev request failed: HTTP {e.code}: {body}") from None
        except URLError as e:
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"Jev request failed: {e.reason}") from None
    raise RuntimeError("Jev request failed: no response")


CJK_CHARS = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f]")


def estimate_tokens(text):
    """Rough input-token estimate: ~1 token per CJK char, ~3.6 chars per token for the rest."""
    cjk = len(CJK_CHARS.findall(text))
    return int(cjk + (len(text) - cjk) / 3.6)


def preview(url):
    """What a URL would cost before asking anything: stage 1 of the UI."""
    page = fetch_text(url)
    text = page["text"]
    tokens = estimate_tokens(text)
    items, mode = items_for(page)
    item_state = {"url": page["url"], "title": page["title"],
                  "items": [{"id": i + 1, "text": t} for i, t in enumerate(items)]}
    item_tokens = estimate_tokens(json.dumps(item_state, ensure_ascii=False))
    return {"url": page["url"], "title": page["title"], "domain": urlparse(page["url"]).netloc,
            "chars": len(text), "tokens": tokens, "passages": len(chunks(text)),
            "usd": round(tokens * USD_PER_MTOK / 1e6, 6), "preview": text[:420],
            "items": items, "mode": mode, "item_count": len(items), "item_tokens": item_tokens,
            "item_usd": round(item_tokens * USD_PER_MTOK / 1e6, 6),
            "item_preview": items[:2]}


def ask(url, question, token=None, kind="passage", options=(), top=3, scope="page", filter_term=None,
        source=None, ident=None, limit=200, refresh=False):
    """Put one typed judgment (or one per entry) to Jev about something.

    Two ways in: a `url` (fetch the page, extract its passages or its entries) or a `source`
    + `ident` (pull the entries from a data source — B站/微博/… or a plugin; see sources.py).
    scope="page" asks one question about the whole thing; scope="items" asks the same narrow
    question about every entry and counts the answers in code, which is what a proportion needs.
    """
    if scope not in SCOPES:
        raise RuntimeError(f"scope must be one of {', '.join(SCOPES)}")
    if kind not in KINDS:
        raise RuntimeError(f"kind must be one of {', '.join(KINDS)}")
    options = [o.strip() for o in options if o.strip()]
    if kind in ("choice", "score") and len(options) < 2:
        raise RuntimeError(f"{kind} needs at least two options")
    if kind == "score" and len(options) > 10:
        raise RuntimeError("score accepts at most 10 levels")
    if source and not url:
        return ask_source(source, ident or "", question, token or load_token(), kind, options,
                          filter_term, limit, refresh)
    page = fetch_text(url)
    if scope == "items":
        return _ask_items(page, question, token or load_token(), kind, options, filter_term)
    candidates = ()
    if kind == "passage":
        all_candidates = chunks(page["text"])
        if not all_candidates:
            raise RuntimeError("no readable text found at that URL")
        candidates = [all_candidates[i] for i in rank(question, all_candidates)]
    result = post_json(build_request(kind, page["url"], question, page["text"], candidates, options),
                       token or load_token())
    answer = result["answers"]["answer"]
    out = {"kind": kind, "url": page["url"], "title": page["title"], "question": question,
           "model": result.get("model"), "confidence": answer.get("confidence"),
           "usage": result.get("usage"),
           "estimated_usd": round((result.get("usage", {}).get("input_tokens") or 0) * USD_PER_MTOK / 1e6, 6)}
    if kind == "passage":
        keys = [f"c{i:02d}" for i in range(len(candidates))]
        by_key = dict(zip(keys, candidates))
        probabilities = answer["probabilities"]
        passage = by_key.get(answer["choice"])
        out.update(found=passage is not None, answer=passage, none_probability=probabilities.get("none"),
                   top=[{"text": by_key[k], "probability": p}
                        for k, p in sorted(probabilities.items(), key=lambda kv: -kv[1]) if k in by_key][:top])
    elif kind == "noul":
        yes = answer["noul"]
        out.update(found=True, answer=yes, distribution=[["是 yes", yes], ["否 no", round(1 - yes, 4)]])
    elif kind == "choice":
        out.update(found=True, answer=answer["choice"],
                   distribution=[[k, p] for k, p in sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])])
    else:
        legend = answer.get("legend") or {}
        out.update(found=True, answer=answer["score"],
                   distribution=[[legend.get(k, k), p]
                                 for k, p in sorted(answer["probabilities"].items(), key=lambda kv: -kv[1])])
    return out


def render(result):
    lines = [result["title"] or result["url"], f"  {result['url']}", f"Q: {result['question']}", ""]
    if result.get("scope") == "items":
        mean = f" · 平均概率 {result['mean']:.2f}" if result["mean"] is not None else ""
        where = (result.get("source") or result.get("url") or "")
        lines[1] = f"  {where}{(' · ' + str(result['ident'])) if result.get('ident') else ''}".rstrip()
        lines.append(f"A: {result['lead']} {result['answer']:.1%} · {result['distribution'][0][2]}/{result['counted']}"
                     f" 条{mean} · 接近 0.5 的 {result['uncertain']} 条")
        for label, share, count in result["distribution"]:
            lines.append(f"   {share:>6.1%} {count:>4} 条  {label}")
        people = result.get("people")
        if people:
            lines.append(f"   按人不重复（{people['authors']} 个作者）：")
            for label, share, count in people["distribution"]:
                lines.append(f"   {share:>6.1%} {count:>4} 人  {label}")
        scope = f"统计 {result['counted']}/{result['total_items']} 个{result['mode']}"
        if result["filter"]:
            scope += f" · 只看含「{result['filter']}」的"
        lines.append(f"   {scope}")
        if result["items"]:
            lines.append("   最不确定的几条：")
            for row in result["items"][:3]:
                shown = f"{row['value']:.2f}" if row["value"] is not None else str(row["label"])
                lines.append(f"     {shown:>6}  {row['text'][:88]}")
    elif result["kind"] == "passage":
        lines.append(f"A: {result['answer']}" if result["found"]
                     else f"A: no passage in the page answers that question (p(none)={result['none_probability']})")
    elif result["kind"] == "noul":
        lines.append(f"A: yes {result['answer']:.1%} · no {1 - result['answer']:.1%}")
    elif result["kind"] == "choice":
        lines.append(f"A: {result['answer']}")
    else:
        lines.append(f"A: weighted score {result['answer']}")
    if result.get("scope") != "items":
        for label, probability in result.get("distribution") or []:
            lines.append(f"   {probability:>6.1%}  {label}")
    usage = result["usage"] or {}
    confidence = "" if result["confidence"] is None else f"confidence {result['confidence']} · "
    lines += ["", f"   {confidence}{result['model']} · {usage.get('input_tokens')} input tokens · "
                  f"${result['estimated_usd']}"]
    if result["kind"] == "passage":
        others = [t for t in result.get("top") or [] if t["text"] != result["answer"]][:2]
        if others:
            lines += ["", "Other candidates:"]
            lines += [f"  {t['probability']:.2f}  {t['text'][:110]}" for t in others]
    return "\n".join(lines)


def json_body(body):
    """HTTP body bytes for a str/bytes/dict/list payload."""
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    return body.encode() if isinstance(body, str) else body


# --------------------------------------------------------------------- server

WEB_ROOT = Path(__file__).parent / "frontend" / "dist"


def page():
    """Serve the Vite production build; running the app does not require Node."""
    try:
        return (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RuntimeError("Frontend not built. Run: cd frontend && npm ci && npm run build") from None



def make_handler(token=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "tally"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            raw = json_body(body)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def _same_origin(self):
            """Block a random web page from spending the token via this server.

            Same-origin means the Origin's netloc matches Host, which also holds through a
            tunnel or port forward; a fixed localhost allowlist would 403 those.
            """
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urlparse(origin).netloc.lower() == (self.headers.get("Host") or "").lower()

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/sources":               # the data sources this install knows about
                if not self._same_origin():
                    return self._send(403, {"error": "cross-origin request refused"})
                import sources
                return self._send(200, json.dumps(sources.describe(), ensure_ascii=False))
            if path == "/token":                 # status only — never the token itself
                if not self._same_origin():
                    return self._send(403, {"error": "cross-origin request refused"})
                return self._send(200, json.dumps(token_status(), ensure_ascii=False))
            if path.startswith("/assets/"):
                # Only public build assets. Resolve before checking containment to block traversal
                # and symlinks escaping dist/assets; never serve Python sources or credentials.
                root = (WEB_ROOT / "assets").resolve()
                asset = (WEB_ROOT / unquote(path).lstrip("/")).resolve()
                if not asset.is_relative_to(root) or not asset.is_file():
                    return self._send(404, {"error": "not found"})
                ctype = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
                return self._send(200, asset.read_bytes(), ctype)
            if path != "/":
                return self._send(404, {"error": "not found"})
            try:
                self._send(200, page(), "text/html; charset=utf-8")
            except RuntimeError as error:
                self._send(503, {"error": str(error)})

        def do_POST(self):
            path = self.path.split("?")[0]
            if path not in ("/ask", "/fetch", "/sourcedata", "/token"):
                return self._send(404, {"error": "not found"})
            if not self._same_origin():
                return self._send(403, {"error": "cross-origin request refused"})
            try:
                payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if path == "/token":
                    status = save_token(str(payload.get("token") or ""))
                    sys.stderr.write(f"  token saved to {TOKEN_FILE}\n")
                    return self._send(200, json.dumps(status, ensure_ascii=False))
                if path == "/sourcedata":             # pull the entries (cached), judge nothing yet
                    import sources
                    name = str(payload.get("source") or "")
                    ident = str(payload.get("ident") or "")
                    limit = max(1, min(int(payload.get("limit") or 200), 2000))
                    # Ask *before* running: after the pull the cache always exists, so reading it
                    # afterwards made every first fetch claim "this came from the cache".
                    was_cached = sources.cache_status(name, ident, limit)["cached"]
                    got = sources.run(name, ident, limit, refresh=bool(payload.get("refresh")))
                    texts = [item["text"] for item in got]
                    size = estimate_tokens(json.dumps(texts, ensure_ascii=False))
                    return self._send(200, json.dumps({
                        "source": name, "ident": ident, "label": sources.describe(name)["label"],
                        "label_en": sources.describe(name).get("label_en", ""),
                        "count": len(got), "authors": len({i["author"] for i in got if i["author"]}),
                        "preview": [text[:180] for text in texts[:3]], "items": texts,
                        "tokens": size, "usd": round(size * USD_PER_MTOK / 1e6, 6),
                        "subject": sources.subject(name, ident, refresh=bool(payload.get("refresh"))),
                        "cache": {**sources.cache_status(name, ident, limit),
                                  "cached": bool(was_cached and not payload.get("refresh"))},
                    }, ensure_ascii=False))
                url = str(payload.get("url") or "").strip()
                if not url and not payload.get("source"):
                    raise ValueError("url or source is required")
                if path == "/fetch":
                    return self._send(200, json.dumps(preview(url), ensure_ascii=False))
                question = str(payload.get("question") or "").strip()
                if not question:
                    raise ValueError("question is required")
                options = payload.get("options") or []
                if not isinstance(options, list):
                    raise ValueError("options must be a list of strings")
                result = ask(url, question, token=token, kind=str(payload.get("kind") or "passage"),
                             options=[str(o) for o in options], scope=str(payload.get("scope") or "page"),
                             filter_term=str(payload.get("filter") or ""),
                             source=(str(payload.get("source") or "") or None),
                             ident=str(payload.get("ident") or ""),
                             limit=int(payload.get("limit") or 200),
                             refresh=bool(payload.get("refresh")))
                self._send(200, json.dumps(result, ensure_ascii=False))
            except Exception as e:
                self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False))

    return Handler


def serve(port, token=None):
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(token))
    state = token_status()
    print(f"tally on http://127.0.0.1:{port}  (Ctrl+C to stop)", flush=True)
    if state["configured"]:
        print(f"token: {state['source']} · {state['where']}", flush=True)
    else:
        print("token: 未配置 —— 打开页面填一个（验证后存到 " + str(TOKEN_FILE) + "，权限 0600）", flush=True)
    server.serve_forever()


def main():
    ap = argparse.ArgumentParser(description="Ask a question about a web page with Jev.")
    ap.add_argument("url", nargs="?")
    ap.add_argument("question", nargs="?")
    ap.add_argument("--serve", action="store_true", help="run the local web UI instead of one CLI query")
    ap.add_argument("--port", type=int, default=8020)
    ap.add_argument("--kind", choices=KINDS, default="passage",
                    help="passage: find the answering passage (default); noul: yes/no; "
                         "choice: your own options; score: your own ordered levels")
    ap.add_argument("--scope", choices=SCOPES, default="page",
                    help="page: one question about the whole page (default); "
                         "items: the same question about every entry, then counted")
    ap.add_argument("--source", default=None,
                    help="data source name (bilibili / weibo / xiaohongshu / v2ex / … or your own "
                         "plugin in sources.d/). With --source, give --ident instead of a url")
    ap.add_argument("--ident", default="", help="what the source needs: BV id, post id, URL, …")
    ap.add_argument("--limit", type=int, default=200, help="max entries to pull from the source")
    ap.add_argument("--filter", default="",
                    help="with --scope items, only count entries containing this text")
    ap.add_argument("--options", default="",
                    help="comma or newline separated options for --kind choice, or low-to-high levels for score")
    ap.add_argument("--json", action="store_true", help="print the raw result as JSON")
    args = ap.parse_args()
    if args.serve:
        return serve(args.port)
    token = load_token()
    url, question = args.url or "", args.question
    if args.source and question is None:      # with a source, one positional is the question
        url, question = "", args.url or ""
    if not question or (not url and not args.source):
        ap.error("give a url (or --source + --ident) and a question, or use --serve")
    try:
        result = ask(url, question, token=token, kind=args.kind,
                     options=parse_options(args.options), scope=args.scope, filter_term=args.filter,
                     source=args.source, ident=args.ident, limit=args.limit)
    except RuntimeError as error:
        # These messages are written to be read by a human ("这个源要的是…"). A traceback buries
        # them, and the UI shows the same text in a red box.
        print(f"错误：{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
    return 0 if result["found"] else 1


if __name__ == "__main__":
    sys.exit(main())

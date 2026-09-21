# tally

**Analyze social media comments with Jev. Percentages you can audit, in one request.**

[简体中文](README.zh-CN.md)

Point it at a thread of comments, pick a few labels (`喜欢 / 中立 / 讨厌 / 无关`), and it asks Jev the
same narrow question about every comment, then counts the answers **in code**. You get a percentage,
the denominator behind it, a per-author count, and the comment-by-comment trail that produced it.

```
30 comments · one request · 1.1s · $0.00022

喜欢  46.7%  14
中立  30.0%   9
讨厌  16.7%   5
无关   6.7%   2
```

---

## Why Jev, and not a chat model

A chat LLM can absolutely do this job. The difference is how many round trips the job takes.

To judge N comments you generally have two options with a chat model: call it once per comment (N
requests, time growing linearly with the thread), or stuff all N into one prompt and ask for a
structured answer back (one request, but you now own the parsing, the retries, and the fact that
generated JSON drifts as N grows). Neither gives you a calibrated probability per comment for free.

Jev is built for the other shape: **many independent, typed questions answered in a single request.**
The comments go in as one shared state, each comment gets its own question pointing at it, and one
response comes back with a probability or a choice per comment.

| | a chat model, one call per comment | Jev |
|---|---|---|
| round trips for 30 comments | 30 | **1** |
| measured wall time, 30 comments | — | **1.1s** |
| output you have to parse | free text / JSON you prompted for | typed: `choice`, `noul`, `score` |
| per-comment probability | not returned | returned |
| output tokens billed | yes | free (input only) |

That is the whole reason this tool exists. Judging a thousand comments is not a thousand times the
work; it is one request with a bigger state. Cost is input tokens only, roughly **$0.02–0.03 per
10,000 comments**.

Measured on a 33-comment V2EX thread: 5,198 input tokens, $0.000218, 1.13s. Run the same 30 comments
through your own chat model if you want the comparison to be yours rather than mine.

---

## Quick start

```bash
git clone https://github.com/adlternative/tally.git
cd tally/frontend && npm ci && npm run build && cd ..
python3 tally.py --serve          # → http://127.0.0.1:8020
```

Requires Python 3.11+. Building the UI additionally needs Node — Vite 6 accepts 18, 20 or 22+; this
repo was built and tested on 22.3. A built checkout runs on Python alone. First run opens Settings: paste a [TypeSafe API key](https://console.typesafe.ai/), which is
verified against the API and stored at `~/.jev-tally/token` with mode `0600`.

Command line, no build needed:

```bash
python3 tally.py --source v2ex --ident https://www.v2ex.com/t/1243550 --limit 30 \
                 --kind choice --options '喜欢,中立,讨厌,无关' '这条评论是什么态度？'

python3 tally.py https://example.com 'What is this page about?'
```

---

## Data sources

A source is **a command that prints JSON, plus the paths saying which field is the text**. Nothing
else, which is why nine sources fit in one file and your own crawler fits just as well.

```json
"v2ex": {
  "label": "V2EX 帖子回复",
  "ident": "话题 id（t/ 后面的数字）",
  "run":   "opencli v2ex replies {ident} --limit {limit} -f json",
  "text": "content", "author": "author"
}
```

Bundled: Bilibili, Weibo, Xiaohongshu, Zhihu answer comments, YouTube, V2EX, Hacker News, plus an
example plugin that uses `curl` and no scraper framework at all.

Most bundled sources are one line of [OpenCLI](https://github.com/jackwener/opencli) (public npm
package, installed separately). V2EX and Hacker News need no login; several others need a browser
logged into the site, and some adapters may need installing beyond the defaults. Commands run as
**argv, never through a shell**, so an identifier containing `; rm -rf /` is a filename.

Your own source drops into `sources.d/` — any command that prints JSON works:

```json
{
  "my-crawler": {
    "label": "我的来源",
    "ident": "帖子 ID",
    "run": "python3 /absolute/path/to/source.py {ident} --limit {limit}",
    "items": "data.comments",
    "text": "content", "author": "user.name"
  }
}
```

Two optional fields do a lot of work: `ident_from` (a regex that pulls the id out of a pasted URL, so
a link and an id both work) and `skip` (drop rows that are not opinions — the story itself, replies,
`[+N more replies]` placeholders — before they reach the denominator).

---

## What you get

- **A denominator.** `46.7%` means 14 of 30 counted comments. The number of comments pulled and the
  number actually judged are both visible.
- **A per-comment trail.** Every comment with its verdict and how sure Jev was, sortable by least
  certain first — read the disagreements before you quote the percentage.
- **Filters that cost nothing.** Click a category, search text or author, re-sort: it is redisplay of
  a judgment already paid for, not another request.
- **Comments vs people.** One author posting five times is one opinion; both tallies are reported.
- **Caching.** The pull is cached per source + id + limit. Asking a second question reuses it;
  only an explicit *Refetch* hits the site again.

---

## What this is not

- **A judgment is not ground truth.** Jev returns a probability per comment. Re-run twice and some
  comments will flip; that uncertainty is why the per-comment trail exists.
- **A sample is not a population.** Bilibili and Weibo return one page sorted by heat by default.
  `46.7% of 30 fetched comments` is not `46.7% of the commenters on that video`.
- **`无关` matters.** Ask only 喜欢/反对 and every comment that never mentions the subject gets
  counted as opposition. The unrelated bucket keeps that error visible instead of silent.
- **Large threads.** This version does not split oversized jobs into multiple requests; if the state
  exceeds the context budget, lower the limit.

---

## Development

```bash
python3 test_tally.py     # engine: extraction, retrieval, counting, token handling, static files
python3 test_sources.py   # source layer: argv building, caching, id rules, plugin loading
python3 test_e2e.py       # 23 scenarios against a live server with a fake Jev and a fake source
cd frontend
npm run dev               # Vite dev server, proxies the API to 127.0.0.1:8020
npm test                  # 32 Playwright scenarios driving the real UI
npm run build             # type check + production build
```

Nothing in the test suite calls a paid model or a real platform: the fake upstream and fixture
sources make the percentages exact and the run free, and the suite isolates `HOME` so a developer's
own token can never be sent anywhere. That also means a green suite proves the pipeline, not that
some external adapter is up.

```
tally.py                     engine + CLI + static/API server (stdlib only)
sources.py, sources.json     the source protocol and bundled sources
sources.d/                   yours
frontend/src/                React workspace (Vite, Tailwind, shadcn/ui, i18next)
frontend/tests/              Playwright scenarios and the isolated fixture backend
scripts/package_release.py   build a local, Node-free runtime archive
```

Environment overrides: `JEV_TALLY_API_BASE` (a different Jev-compatible endpoint — it will receive
your key), `JEV_TALLY_SOURCES_D`, `JEV_TALLY_CACHE_DIR`, `JEV_TALLY_TOKEN_FILE`.

## Licence

MIT — see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for component licences.

# tally

**Turn a conversation into an auditable distribution.**

[简体中文](README.zh-CN.md)

tally fetches comments, asks Jev a typed question about each item, and counts the answers in Python.
Every percentage has a denominator and a path back to the original words. Labels are model judgments,
not ground truth or a representative survey of everyone on a platform.

## Workspace

- Compact source and question configuration, with a large results workspace.
- Five presets: like/dislike, sentiment, satisfaction, topic relevance and content quality.
- Custom passage, yes/no, classification and score questions; whole-page or per-item scope.
- Distribution filters, author/text search, original-order and certainty sorting, full-text expansion.
- Separate comment and author denominators; source content and session history tabs.
- Language and API credentials live in **Settings**, not in the analysis form.

The frontend uses **React + TypeScript + Vite + Tailwind CSS v4**, official **shadcn/ui / Radix**
components, **Lucide** icons and **i18next**. The Python engine, command-line interface and plugin
protocol remain independent of the frontend.

## Build from source

Requires Python 3.11+ and Node.js 22.12+ with npm to **build** the frontend:

```bash
cd frontend
npm ci
npm run build
cd ..
python3 tally.py --serve
# http://127.0.0.1:8020
```

The production page and its assets are served by Python from `frontend/dist/`.
**Node is not needed to run a prebuilt runtime archive.** A missing build returns an actionable
503 message instead of falling back to a different UI.

### Development

Run the Python server as above, then in another terminal:

```bash
cd frontend
npm run dev
```

Vite proxies `/sources`, `/sourcedata`, `/fetch`, `/ask` and `/token` to `127.0.0.1:8020`.
The repository has a public npm registry setting and a lockfile. Use `npm ci` for reproducible installs.

### Local runtime archive

After building:

```bash
python3 scripts/package_release.py
# artifacts/tally-runtime.zip
```

Unzip and run `python3 tally/tally.py --serve`. The archive includes Python runtime files, the built
frontend, bundled source definitions, the example plugin and license notices. It excludes credentials,
node_modules, caches, test artifacts and private plugins. This command creates a local archive only;
it does not publish anything.

## Credentials and privacy

Open Settings and enter your own [TypeSafe API key](https://console.typesafe.ai/). Verification uses
`GET /v1/models`, without a model inference request. A verified key is stored in `~/.jev-tally/token`
with mode `0600`; the status API returns configuration metadata, never the value. The UI never
loads an existing secret into its input, and does not store secrets in localStorage.

Lookup order: `TYPESAFE_API_KEY` → `JEV_TOKEN` → `~/.jev-tally/token` → legacy `~/jev_token`.
An environment key takes precedence over a file saved through Settings.

This is a **trusted, local tool**, not a multi-tenant service. Source plugins execute local commands;
install only plugins you trust. Source content and questions are sent to TypeSafe when you run an
analysis. Source tools may use your logged-in browser. Do not expose the server publicly without
adding authentication and a deployment security review.

## Command line

No frontend build or Node is needed for CLI use:

```bash
python3 tally.py --source v2ex --ident https://www.v2ex.com/t/1243550 \
  --limit 30 --kind choice --options '喜欢,中立,讨厌,无关' '这条评论是什么态度？'

python3 tally.py https://example.com 'What is this page about?'
```

## Sources and plugins

Bundled definitions: Bilibili, Weibo, Xiaohongshu, Zhihu answer comments, YouTube, V2EX and Hacker News.
Xueqiu is intentionally not bundled. Most definitions invoke [OpenCLI](https://github.com/jackwener/OpenCLI).
Install the required commands separately; availability and login requirements vary by adapter.
The Bilibili definition requires `comments-all`, which may require a separately installed adapter.
V2EX and Hacker News use public reads; other adapters may require a logged-in browser or extension.

A source is an argv command that prints JSON, plus field paths. Add a **valid JSON object** to
`sources.d/my-source.json`, using absolute script paths (no shell or `~` expansion):

```json
{
  "my-source": {
    "label": "我的来源",
    "label_en": "My source",
    "ident": "帖子 ID",
    "ident_en": "Post ID",
    "run": "python3 /absolute/path/to/source.py {ident} --limit {limit}",
    "items": "data.comments",
    "text": "content",
    "author": "user.name"
  }
}
```

- `ident_from`: regex with a capture group to extract an ID from a pasted URL.
- `skip`: field → value or list; any matching field excludes the row from the denominator.
- `subject`: optional `{ "run": "command {ident}", "at": "title" }` for the source title.
- `label_en`, `ident_en`, `note_en`: optional English metadata, falling back to Chinese.

Commands are passed as argv, never through a shell. The example plugin uses `curl` rather than OpenCLI.
Hacker News defaults to top-level comments and excludes story/placeholder rows. Zhihu's bundled
adapter expects an **answer** link, not a question link. Xiaohongshu requires a full signed note URL.

## Counting, caching and language

Source pulls are cached by source, identifier, limit and selected source-definition fields.
Asking a different question reuses that pull; **Refetch** explicitly bypasses the cache. Model judgments
are not cached, and repeated real-model runs can differ. Whole-page URL analysis fetches the page again
on each question. Filters, search, sorting and language changes make no inference requests.

An author's repeated comments count once in the author distribution: yes/no values are averaged;
classification uses the author's most frequent label. A tie is not a strong opinion. Without author
metadata, only comment counts are available. A biased source sample remains biased after counting.

Switch Chinese/English in Settings. Unedited preset questions and options follow the language.
Edited questions, custom labels, original comments and existing results are preserved. Backend or
adapter error details retain their original language.

Large datasets may exceed the upstream context budget; this version does **not** implement automatic
multi-request batching. Set an appropriate limit. Percentages describe only the fetched sample.

## Tests

Build first, then run:

```bash
python3 test_tally.py
python3 test_sources.py
python3 test_e2e.py
cd frontend
npx playwright install chromium   # one-time browser download
npm test
npm run format:check
```

- Python tests cover extraction, typed-request mapping, counting, token handling, plugin/cache rules,
  HTTP flows and static-asset containment (including traversal/symlinks).
- Playwright drives the **built React page in Chromium** against the real Python server, with a
  deterministic local fake Jev and fixture source. It covers settings/focus, languages, token errors,
  five presets, four question kinds, both scopes, history, filters/search/sorting, source changes,
  consecutive and 1–2-comment pulls, errors, loading states, text escaping and responsive layouts.
- Test HOME, credentials, plugins and cache are isolated. Tests do not call real paid inference.
  Installing dependencies/browser binaries requires network access; running these suites does not
  require access to real platforms. Passing fixtures does **not** prove every external adapter works.
- Failure traces and screenshots live in `frontend/test-results/`; these are not release files.

The old hand-written `ui.html` and fake-DOM `test_ui.mjs` have been replaced, not kept as parallel UIs.

## Repository map

```text
tally.py                   Python engine, CLI and static/API server
sources.py / sources.json  Data-source protocol and bundled definitions
sources.d/                 Local plugins
frontend/src/App.tsx       Source/question workflow and session state
frontend/src/Results.tsx   Distribution and auditable comment list
frontend/src/Settings.tsx  Radix settings dialog and token form
frontend/src/components/ui/  Generated shadcn/ui components
frontend/src/locales/      Chinese and English i18next dictionaries
frontend/tests/            Playwright scenarios and isolated backend launcher
scripts/package_release.py  Allowlisted runtime archive
```

Environment overrides: `JEV_TALLY_API_BASE` (trusted Jev-compatible host; it receives the key),
`JEV_TALLY_SOURCES_D`, `JEV_TALLY_CACHE_DIR`, `JEV_TALLY_TOKEN_FILE`.

## License

MIT for tally. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for component and dependency licenses.

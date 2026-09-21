import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ArrowDownToLine,
  ArrowRight,
  BarChart3,
  Check,
  ChevronRight,
  Database,
  FileText,
  History,
  Loader2,
  MessageSquareText,
  Play,
  RefreshCw,
  Settings2,
  ShieldCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Settings } from "./Settings";
import { Results, dollars } from "./Results";
import {
  api,
  preference,
  remember,
  type Source,
  type TokenStatus,
  type Dataset,
  type SourceData,
  type PageData,
  type Kind,
  type Scope,
  type SavedResult,
  type Result,
} from "./api";

const PRESETS = ["favor", "mood", "rate", "onTopic", "junk", "custom"] as const;
export default function App() {
  const { t, i18n } = useTranslation();
  const english = i18n.language === "en";
  const [sources, setSources] = useState<Source[]>([]);
  const [status, setStatus] = useState<TokenStatus | null>(null);
  const [settings, setSettings] = useState(false);
  const [mode, setMode] = useState("source");
  const [source, setSource] = useState(preference("jev.source", "v2ex"));
  const [ident, setIdent] = useState("");
  const [limit, setLimit] = useState("200");
  const [preset, setPreset] = useState<string>("favor");
  const [subject, setSubject] = useState("");
  const [customKind, setCustomKind] = useState<Kind>("choice");
  const [customScope, setCustomScope] = useState<Scope>("page");
  const [questionOverride, setQuestionOverride] = useState<string | null>(null);
  const [optionsOverride, setOptionsOverride] = useState<string | null>(null);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [busy, setBusy] = useState<"fetch" | "ask" | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("analysis");
  const [history, setHistory] = useState<SavedResult[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const sequence = useRef(0);
  const settingsTrigger = useRef<HTMLButtonElement>(null);
  const active = history.find((entry) => entry.id === activeId);
  const entry = sources.find((entry) => entry.name === source);
  const sourceText = (entry: Source, key: "label" | "ident" | "note") =>
    english && entry[`${key}_en`] ? entry[`${key}_en`] : entry[key];
  const kind: Kind =
    preset === "custom" ? customKind : preset === "rate" ? "score" : "choice";
  const scope: Scope =
    mode === "source" || preset !== "custom"
      ? "items"
      : kind === "passage"
        ? "page"
        : customScope;
  const autoQuestion =
    preset === "custom"
      ? ""
      : t(`presets.${preset}.question`, {
          subject: subject.trim() || t("genericSubject"),
        });
  const question = questionOverride ?? autoQuestion;
  const presetOptions =
    preset === "custom"
      ? []
      : (t(`presets.${preset}.options`, { returnObjects: true }) as string[]);
  const optionsText = optionsOverride ?? presetOptions.join("\n");
  const options = [
    ...new Set(
      optionsText
        .split(/[\n,，、]+/)
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  ];
  const estimate = dataset
    ? dataset.tokens +
      (scope === "items" ? dataset.count * (question.length + 95) : 1200)
    : 0;
  function resetResult() {
    setHistory([]);
    setActiveId(null);
    setTab("analysis");
  }
  function invalidate() {
    setDataset(null);
    setSubject("");
    setError("");
    resetResult();
  }
  function choosePreset(value: string) {
    setPreset(value);
    setQuestionOverride(null);
    setOptionsOverride(null);
  }
  async function initialize() {
    setLoading(true);
    setError("");
    try {
      const [list, token] = await Promise.all([
        api<Source[]>("/sources"),
        api<TokenStatus>("/token"),
      ]);
      setSources(list);
      setSource((previous) =>
        list.some((item) => item.name === previous)
          ? previous
          : list[0]?.name || "",
      );
      setStatus(token);
      if (!token.configured) setSettings(true);
    } catch (error) {
      setError(error instanceof Error ? error.message : t("error"));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void initialize();
  }, []);

  async function fetchContent(refresh = false) {
    if (busy) return;
    if (!ident.trim()) {
      setError(t("enterIdentifier"));
      return;
    }
    if (mode === "source" && !entry) {
      setError(t("chooseSource"));
      return;
    }
    const count = Number(limit);
    if (
      mode === "source" &&
      (!Number.isInteger(count) || count < 1 || count > 2000)
    ) {
      setError(t("limitError"));
      return;
    }
    setBusy("fetch");
    setError("");
    setDataset(null);
    resetResult();
    try {
      let next: Dataset;
      if (mode === "source") {
        const data = await api<SourceData>("/sourcedata", {
          source,
          ident: ident.trim(),
          limit: count,
          refresh,
        });
        next = {
          request: { source, ident: data.ident, limit: count },
          title: data.label,
          titleEn: data.label_en,
          subject: data.subject,
          items: data.items,
          count: data.count,
          authors: data.authors,
          tokens: data.tokens,
          cache: data.cache,
        };
        remember("jev.source", source);
      } else {
        const data = await api<PageData>("/fetch", { url: ident.trim() });
        next = {
          request: { url: data.url },
          title: data.title,
          subject: data.title,
          items: data.items,
          count: data.item_count,
          tokens: data.item_tokens || data.tokens,
          preview: data.preview,
        };
      }
      setDataset(next);
      setSubject(next.subject || "");
      setTab("raw");
    } catch (error) {
      setError(error instanceof Error ? error.message : t("error"));
    } finally {
      setBusy(null);
    }
  }
  async function analyze() {
    if (busy || !dataset) return;
    if (!question.trim()) {
      setError(t("questionError"));
      return;
    }
    if (
      (kind === "choice" || kind === "score") &&
      (options.length < 2 || (kind === "score" && options.length > 10))
    ) {
      setError(t("optionsError"));
      return;
    }
    setBusy("ask");
    setError("");
    setTab("analysis");
    const started = performance.now();
    try {
      const result = await api<Result>("/ask", {
        ...dataset.request,
        question: question.trim(),
        kind,
        scope,
        options: kind === "choice" || kind === "score" ? options : [],
      });
      result.ms = performance.now() - started;
      const saved = {
        id: ++sequence.current,
        result,
        options: [...options],
        preset,
        language: i18n.language,
      };
      setHistory((previous) => [saved, ...previous]);
      setActiveId(saved.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : t("error");
      setError(message);
      if (/token|401/i.test(message)) setSettings(true);
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="min-h-dvh bg-background text-foreground">
      <a
        href="#workspace"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-background focus:p-4"
      >
        {t("workspace")}
      </a>
      <header className="app-header">
        <div className="flex items-center gap-3">
          <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <BarChart3 className="size-5" strokeWidth={1.8} />
          </div>
          <span className="text-xl font-semibold tracking-tight">
            tally<span className="text-primary">.</span>
          </span>
          <span className="mx-2 hidden h-5 w-px bg-border sm:block" />
          <span className="hidden text-sm text-muted-foreground sm:block">
            {t("app")}
          </span>
        </div>
        <div className="flex items-center gap-4">
          <span className="hidden items-center gap-1.5 text-xs text-muted-foreground md:flex">
            <span className="size-1.5 rounded-full bg-primary" />
            {t("local")}
          </span>
          <Button
            ref={settingsTrigger}
            variant="ghost"
            size="sm"
            onClick={() => setSettings(true)}
          >
            <Settings2 />
            {t("settings")}
            {status && !status.configured && (
              <span className="size-1.5 rounded-full bg-destructive" />
            )}
          </Button>
        </div>
      </header>
      <div className="app-layout">
        <aside className="configuration" aria-label={t("configuration")}>
          <div className="mb-6">
            <h1 className="text-lg font-semibold tracking-tight">
              {t("configuration")}
            </h1>
            <p className="mt-1 text-xs text-muted-foreground">{t("tagline")}</p>
          </div>
          <fieldset disabled={!!busy || loading} className="min-w-0 space-y-5">
            <section className="space-y-4">
              <h2 className="section-heading">
                <span className="step-number">1</span>
                {t("source")}
              </h2>
              <Tabs
                value={mode}
                onValueChange={(value) => {
                  setMode(value);
                  setIdent("");
                  invalidate();
                  choosePreset(value === "url" ? "custom" : "favor");
                }}
              >
                <TabsList className="w-full">
                  <TabsTrigger className="flex-1" value="source">
                    <Database className="size-3.5" />
                    {t("sourceMode")}
                  </TabsTrigger>
                  <TabsTrigger className="flex-1" value="url">
                    <FileText className="size-3.5" />
                    {t("urlMode")}
                  </TabsTrigger>
                </TabsList>
              </Tabs>
              {mode === "source" && (
                <div className="field">
                  <Label htmlFor="source-select">{t("selectSource")}</Label>
                  <Select
                    value={source}
                    disabled={!!busy || loading}
                    onValueChange={(value) => {
                      setSource(value);
                      invalidate();
                      remember("jev.source", value);
                    }}
                  >
                    <SelectTrigger id="source-select" className="w-full">
                      <SelectValue placeholder={t("selectSource")} />
                    </SelectTrigger>
                    <SelectContent>
                      {sources.map((item) => (
                        <SelectItem key={item.name} value={item.name}>
                          {sourceText(item, "label")}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              <div className="field">
                <Label htmlFor="identifier">
                  {t(mode === "source" ? "identifier" : "url")}
                </Label>
                <Input
                  id="identifier"
                  value={ident}
                  placeholder={mode === "source" ? t("pasteId") : "https://…"}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(e) => {
                    setIdent(e.target.value);
                    invalidate();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void fetchContent();
                    }
                  }}
                />
                {entry && mode === "source" && (
                  <p className="field-hint">{sourceText(entry, "ident")}</p>
                )}
              </div>
              {mode === "source" && (
                <div className="flex items-center justify-between gap-4">
                  <Label htmlFor="limit">{t("limit")}</Label>
                  <Input
                    id="limit"
                    type="number"
                    className="w-24 tabular-nums"
                    min="1"
                    max="2000"
                    value={limit}
                    onChange={(e) => {
                      setLimit(e.target.value);
                      invalidate();
                    }}
                  />
                </div>
              )}
              <Button
                variant="outline"
                className="w-full"
                disabled={!!busy || loading}
                onClick={() => void fetchContent()}
              >
                {busy === "fetch" ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <ArrowDownToLine />
                )}
                {t(busy === "fetch" ? "fetching" : "fetch")}
              </Button>
              {dataset && (
                <div className="source-status" role="status">
                  <div className="flex items-center justify-between gap-2">
                    <span className="flex items-center gap-1.5 text-sm font-medium">
                      <Check className="size-3.5 text-primary" />
                      {t("fetched", { count: dataset.count })}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={t("refresh")}
                      onClick={() => void fetchContent(true)}
                    >
                      <RefreshCw />
                    </Button>
                  </div>
                  {dataset.cache && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {t(dataset.cache.cached ? "cached" : "fresh")}
                    </p>
                  )}
                </div>
              )}
              {entry && mode === "source" && (
                <details className="text-xs leading-relaxed text-muted-foreground">
                  <summary className="cursor-pointer">
                    {sourceText(entry, "label")}
                  </summary>
                  <p className="mt-2">{sourceText(entry, "note")}</p>
                </details>
              )}
            </section>
            <section className="space-y-4 border-t pt-5">
              <h2 className="section-heading">
                <span className="step-number">2</span>
                {t("questionSection")}
              </h2>
              <div className="field">
                <Label htmlFor="preset">{t("preset")}</Label>
                <Select
                  value={preset}
                  disabled={!!busy}
                  onValueChange={choosePreset}
                >
                  <SelectTrigger id="preset" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PRESETS.map((value) => (
                      <SelectItem key={value} value={value}>
                        {t(`presets.${value}.name`)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {["favor", "rate", "onTopic"].includes(preset) && (
                <div className="field">
                  <Label htmlFor="subject">{t("subject")}</Label>
                  <Input
                    id="subject"
                    value={subject}
                    placeholder={t("subjectHint")}
                    onChange={(e) => setSubject(e.target.value)}
                  />
                </div>
              )}
              <div className="field">
                <Label htmlFor="question">{t("question")}</Label>
                <Textarea
                  id="question"
                  className="min-h-24 resize-y text-sm"
                  value={question}
                  placeholder={t("questionPlaceholder")}
                  onChange={(e) => setQuestionOverride(e.target.value)}
                />
              </div>
              {preset === "custom" && (
                <>
                  <div className="field">
                    <Label htmlFor="kind">{t("kind")}</Label>
                    <Select
                      value={customKind}
                      disabled={!!busy}
                      onValueChange={(value) => setCustomKind(value as Kind)}
                    >
                      <SelectTrigger id="kind" className="w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {(["passage", "noul", "choice", "score"] as Kind[])
                          .filter(
                            (value) => mode === "url" || value !== "passage",
                          )
                          .map((value) => (
                            <SelectItem key={value} value={value}>
                              {t(value)}
                            </SelectItem>
                          ))}
                      </SelectContent>
                    </Select>
                  </div>
                  {(kind === "choice" || kind === "score") && (
                    <div className="field">
                      <Label htmlFor="options">
                        {t(kind === "score" ? "levels" : "options")}
                      </Label>
                      <Textarea
                        id="options"
                        value={optionsText}
                        onChange={(e) => setOptionsOverride(e.target.value)}
                        rows={4}
                      />
                    </div>
                  )}
                  {mode === "url" && kind !== "passage" && (
                    <div className="field">
                      <Label htmlFor="scope">{t("scope")}</Label>
                      <Select
                        value={customScope}
                        disabled={!!busy}
                        onValueChange={(value) =>
                          setCustomScope(value as Scope)
                        }
                      >
                        <SelectTrigger id="scope" className="w-full">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="page">{t("page")}</SelectItem>
                          <SelectItem value="items">{t("items")}</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                </>
              )}
            </section>
          </fieldset>
          <div className="mt-6 space-y-3">
            <Button
              className="w-full"
              disabled={!!busy || !dataset || !question.trim()}
              onClick={() => void analyze()}
            >
              {busy === "ask" ? (
                <Loader2 className="animate-spin" />
              ) : (
                <Play className="size-3.5" />
              )}
              {t(busy === "ask" ? "running" : "run")}
              <ArrowRight className="ml-auto" />
            </Button>
            <p className="field-hint">{t("runHint")}</p>
            {dataset && (
              <p className="text-xs text-muted-foreground tabular-nums">
                {t("estimate", {
                  tokens: estimate.toLocaleString(),
                  cost: dollars((estimate * 0.042) / 1e6),
                })}
              </p>
            )}
          </div>
        </aside>
        <main id="workspace" className="workspace" tabIndex={-1}>
          <div className="workspace-heading">
            <div className="min-w-0">
              <p className="section-caption">{t("workspace")}</p>
              <h2 className="mt-2 break-words text-xl font-semibold tracking-tight">
                {dataset
                  ? dataset.subject ||
                    (english && dataset.titleEn
                      ? dataset.titleEn
                      : dataset.title)
                  : t("emptyTitle")}
              </h2>
            </div>
            {dataset && (
              <Badge variant="outline" className="shrink-0">
                <Database className="size-3" />
                {dataset.request.source || t("urlMode")}
              </Badge>
            )}
          </div>
          {error && (
            <div
              role="alert"
              className="mb-5 rounded-lg border border-destructive/20 bg-destructive/5 p-4 text-sm"
            >
              <p className="font-medium text-destructive">{t("error")}</p>
              <p className="mt-1 break-words text-destructive">{error}</p>
              {!sources.length && !loading && (
                <Button
                  className="mt-2"
                  variant="outline"
                  size="sm"
                  onClick={() => void initialize()}
                >
                  {t("retry")}
                </Button>
              )}
            </div>
          )}
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList className="mb-6 bg-transparent p-0">
              <TabsTrigger value="analysis" className="workspace-tab">
                <BarChart3 className="size-4" />
                {t("overview")}
              </TabsTrigger>
              <TabsTrigger value="raw" className="workspace-tab">
                <MessageSquareText className="size-4" />
                {t("raw")}
                {dataset && (
                  <span className="text-xs tabular-nums">{dataset.count}</span>
                )}
              </TabsTrigger>
              <TabsTrigger value="history" className="workspace-tab">
                <History className="size-4" />
                {t("history")}
                {history.length > 0 && (
                  <span className="text-xs tabular-nums">{history.length}</span>
                )}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="analysis">
              {busy === "ask" ? (
                <div role="status" className="space-y-6">
                  <p className="text-sm text-muted-foreground">
                    {t("running")}
                  </p>
                  <Skeleton className="h-10 w-1/3" />
                  <div className="grid grid-cols-2 gap-4">
                    <Skeleton className="h-24" />
                    <Skeleton className="h-24" />
                  </div>
                  {[0, 1, 2, 3].map((index) => (
                    <Skeleton key={index} className="h-16 w-full" />
                  ))}
                </div>
              ) : active ? (
                <Results key={active.id} data={active.result} />
              ) : (
                <Empty title={t("emptyTitle")} description={t("emptyText")} />
              )}
            </TabsContent>
            <TabsContent value="raw">
              {dataset ? (
                <section aria-label={t("sourcePreview")}>
                  <div className="mb-4 flex items-center justify-between">
                    <h3 className="font-semibold">{t("sourcePreview")}</h3>
                    <span className="text-xs text-muted-foreground">
                      {t("fetched", { count: dataset.count })}
                    </span>
                  </div>
                  <div className="divide-y">
                    {dataset.items.map((text, index) => (
                      <div
                        key={index}
                        className="grid grid-cols-[2rem_1fr] gap-3 py-5"
                        data-testid="raw-row"
                      >
                        <span className="font-mono text-xs text-muted-foreground">
                          {String(index + 1).padStart(2, "0")}
                        </span>
                        <p className="whitespace-pre-wrap break-words text-sm leading-7">
                          {text}
                        </p>
                      </div>
                    ))}
                  </div>
                </section>
              ) : (
                <Empty title={t("noData")} description={t("emptyText")} />
              )}
            </TabsContent>
            <TabsContent value="history">
              <h3 className="mb-5 font-semibold">{t("history")}</h3>
              {history.length ? (
                <div className="divide-y">
                  {history.map((entry) => (
                    <button
                      key={entry.id}
                      className="flex w-full items-center justify-between gap-4 py-5 text-left hover:bg-muted/40"
                      onClick={() => {
                        setActiveId(entry.id);
                        setTab("analysis");
                      }}
                    >
                      <div>
                        <p className="text-sm font-medium break-words">
                          {entry.result.question}
                        </p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {entry.result.model} ·{" "}
                          {dollars(entry.result.estimated_usd)}
                        </p>
                      </div>
                      <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
                    </button>
                  ))}
                </div>
              ) : (
                <p className="py-12 text-center text-muted-foreground">
                  {t("noHistory")}
                </p>
              )}
            </TabsContent>
          </Tabs>
          {!dataset && !active && (
            <div className="mt-10 flex items-center justify-center gap-2 text-xs text-muted-foreground">
              <ShieldCheck className="size-3.5" />
              {t("local")}
            </div>
          )}
        </main>
      </div>
      <Settings
        open={settings}
        onOpenChange={setSettings}
        onCloseFocus={() => settingsTrigger.current?.focus()}
        status={status}
        onStatus={setStatus}
      />
    </div>
  );
}
function Empty({ title, description }: { title: string; description: string }) {
  const { t } = useTranslation();
  return (
    <section className="empty-state">
      <div className="flex size-14 items-center justify-center rounded-2xl border border-primary/20 bg-primary/5 text-primary">
        <MessageSquareText className="size-6" strokeWidth={1.5} />
      </div>
      <h3 className="mt-6 text-xl font-semibold tracking-tight">{title}</h3>
      <p className="mt-3 max-w-md text-sm leading-7 text-muted-foreground">
        {description}
      </p>
      <ol className="mt-10 flex flex-wrap justify-center gap-5 text-xs text-muted-foreground">
        {["stepFetch", "stepJudge", "stepInspect"].map((step, index) => (
          <li key={step} className="flex items-center gap-2">
            <span className="step-number">{index + 1}</span>
            {t(step)}
          </li>
        ))}
      </ol>
    </section>
  );
}

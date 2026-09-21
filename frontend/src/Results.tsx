import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowUpDown, Search, SlidersHorizontal, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Result, Item } from "./api";

const pct = (value: number) => `${(value * 100).toFixed(1)}%`;
export const dollars = (value: number) =>
  `$${value.toFixed(value < 0.001 ? 6 : 4)}`;

export function Results({ data }: { data: Result }) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("uncertain");
  const [basis, setBasis] = useState("comments");
  // Internal band names are translated only for yes/no judgments, never for custom labels.
  const label = (value: string) =>
    data.kind === "noul"
      ? {
          看好: t("bands.positive"),
          不确定: t("bands.uncertain"),
          不看好: t("bands.negative"),
          "是 yes": t("yes"),
          "否 no": t("no"),
        }[value] || value
      : value;
  const all = data.items || [];
  const rows = all
    .filter(
      (row) =>
        (filter === null || row.label === filter) &&
        `${row.text} ${row.author}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    )
    .sort(
      (a, b) =>
        (sort === "original"
          ? a.n - b.n
          : sort === "certain"
            ? b.certainty - a.certainty
            : a.certainty - b.certainty) || a.n - b.n,
    );
  const distribution =
    basis === "authors" && data.people
      ? data.people.distribution
      : data.distribution || [];
  function toggle(value: string) {
    setFilter(filter === value ? null : value);
  }
  const lead =
    data.kind === "score"
      ? `${t("mean")} ${(data.mean ?? (data.answer as number)).toFixed(2)}`
      : data.scope === "items"
        ? `${label(data.lead || "")} ${pct(data.answer as number)}`
        : data.kind === "noul"
          ? `${t("yes")} ${pct(data.answer as number)}`
          : String(data.answer ?? "");
  return (
    <div className="space-y-7" data-testid="results">
      <section aria-label={t("overview")}>
        <div className="flex flex-wrap items-end justify-between gap-5">
          <div>
            <p className="section-caption">{t("overview")}</p>
            <h2
              className="mt-2 text-3xl font-semibold tracking-tight break-words"
              data-testid="verdict"
            >
              {data.kind === "passage"
                ? t(data.found ? "passage" : "noAnswer")
                : lead}
            </h2>
          </div>
          {data.scope === "items" && (
            <div className="flex flex-wrap gap-7" data-testid="denominator">
              <Metric
                label={t("counted")}
                value={`${data.counted} / ${data.total_items}`}
              />
              {data.people && (
                <Metric label={t("people")} value={data.people.authors} />
              )}
              <Metric label={t("cost")} value={dollars(data.estimated_usd)} />
            </div>
          )}
        </div>
        <p
          className="mt-3 text-sm text-muted-foreground break-words"
          data-testid="result-question"
        >
          {data.question}
        </p>
        {data.kind === "passage" &&
          (data.found ? (
            <blockquote className="mt-5 whitespace-pre-wrap border-l-2 border-primary bg-muted/40 p-5 text-sm leading-7">
              {data.answer}
            </blockquote>
          ) : (
            <p className="mt-3 text-sm text-muted-foreground">
              {t("noAnswerHint")} · p(none) {data.none_probability}
            </p>
          ))}
        {data.people && (
          <Tabs value={basis} onValueChange={setBasis} className="mt-5">
            <TabsList>
              <TabsTrigger value="comments">{t("byComments")}</TabsTrigger>
              <TabsTrigger value="authors">{t("byAuthors")}</TabsTrigger>
            </TabsList>
          </Tabs>
        )}
        {distribution.length > 0 && (
          <div
            className="mt-5 grid grid-cols-2 gap-x-6 gap-y-2 xl:grid-cols-4"
            data-testid="distribution"
          >
            {distribution.map(([name, share, count]) => (
              <button
                key={name}
                type="button"
                className="distribution-item"
                aria-pressed={filter === name}
                onClick={() => data.scope === "items" && toggle(name)}
                disabled={data.scope !== "items"}
              >
                <span className="flex items-center justify-between gap-3">
                  <span className="truncate text-sm font-medium">
                    {label(name)}
                  </span>
                  {count !== undefined && (
                    <span className="text-xs text-muted-foreground tabular-nums">
                      {count}
                    </span>
                  )}
                </span>
                <span className="mt-2 block text-xl font-semibold tabular-nums">
                  {pct(share)}
                </span>
                <span className="mt-3 block h-1 rounded-full bg-muted">
                  <span
                    className="block h-full rounded-full bg-primary transition-[width]"
                    style={{ width: pct(share) }}
                  />
                </span>
              </button>
            ))}
          </div>
        )}
        {data.scope === "items" && (
          <p className="mt-3 text-xs text-muted-foreground">
            {t(basis === "authors" ? "authorNote" : "distributionHint")}
          </p>
        )}
        {data.top && data.top.length > 0 && (
          <details className="mt-4 text-sm">
            <summary className="cursor-pointer text-muted-foreground">
              {t("passage")} · {data.top.length}
            </summary>
            {data.top.map((item, index) => (
              <p key={index} className="mt-3 whitespace-pre-wrap border-t pt-3">
                {item.text}{" "}
                <Badge variant="outline">{pct(item.probability)}</Badge>
              </p>
            ))}
          </details>
        )}
      </section>
      {data.scope === "items" && (
        <section aria-label={t("audit")} className="border-t pt-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h3 className="text-base font-semibold">
              {t("audit")}{" "}
              <span
                className="ml-2 text-xs font-normal text-muted-foreground"
                data-testid="shown-count"
              >
                {t("showing", { shown: rows.length, total: all.length })}
              </span>
            </h3>
            {(filter !== null || search) && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setFilter(null);
                  setSearch("");
                }}
              >
                <X />
                {t("clearFilters")}
              </Button>
            )}
          </div>
          <div className="mb-5 flex flex-wrap gap-2">
            <div className="relative min-w-40 flex-1">
              <Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" />
              <Input
                aria-label={t("search")}
                placeholder={t("search")}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9"
              />
            </div>
            <Select
              value={
                filter === null
                  ? "all"
                  : `label-${(data.distribution || []).findIndex((row) => row[0] === filter)}`
              }
              onValueChange={(value) =>
                setFilter(
                  value === "all"
                    ? null
                    : (data.distribution || [])[
                        Number(value.replace("label-", ""))
                      ][0],
                )
              }
            >
              <SelectTrigger aria-label={t("filter")} className="w-40">
                <SlidersHorizontal className="size-3.5" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{t("all")}</SelectItem>
                {(data.distribution || []).map(([name], index) => (
                  <SelectItem key={name} value={`label-${index}`}>
                    {label(name)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={sort} onValueChange={setSort}>
              <SelectTrigger aria-label={t("sort")} className="w-48">
                <ArrowUpDown className="size-3.5" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["uncertain", "certain", "original"].map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(value)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="audit-table" data-testid="audit-list">
            <div className="audit-head">
              <span>#</span>
              <span>{t("comment")}</span>
              <span>{t("verdict")}</span>
            </div>
            {rows.map((row) => (
              <CommentRow key={row.n} row={row} label={label(row.label)} />
            ))}
            {rows.length === 0 && (
              <p className="py-14 text-center text-sm text-muted-foreground">
                {t("noMatches")}
              </p>
            )}
          </div>
        </section>
      )}
      <footer className="flex flex-wrap gap-x-4 gap-y-2 border-t pt-4 text-xs text-muted-foreground tabular-nums">
        <span>{data.model}</span>
        <span>{data.usage?.input_tokens?.toLocaleString() || 0} tokens</span>
        <span>{dollars(data.estimated_usd)}</span>
        {data.ms != null && <span>{(data.ms / 1000).toFixed(1)}s</span>}
        <span>{t("oneRequest")}</span>
        {data.confidence != null && (
          <span>
            {t("confidence")} {data.confidence.toFixed(2)}
          </span>
        )}
      </footer>
    </div>
  );
}
function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold tabular-nums">{value}</p>
    </div>
  );
}
function CommentRow({ row, label }: { row: Item; label: string }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  return (
    <article
      className="audit-row"
      data-testid="audit-row"
      data-order={row.n}
      data-certainty={row.certainty}
      data-label={row.label}
    >
      <span className="pt-1 font-mono text-xs text-muted-foreground">
        {String(row.n).padStart(2, "0")}
      </span>
      <div className="min-w-0">
        <p className="mb-1 text-xs font-medium text-muted-foreground">
          {row.author || t("anonymous")}
        </p>
        <p
          className={`whitespace-pre-wrap break-words text-sm leading-7 ${expanded ? "" : "line-clamp-3"}`}
        >
          {row.text}
        </p>
        <button
          type="button"
          aria-expanded={expanded}
          className="mt-1 text-xs text-primary underline-offset-4 hover:underline"
          onClick={() => setExpanded(!expanded)}
        >
          {t(expanded ? "collapse" : "expand")}
        </button>
      </div>
      <div className="min-w-0 text-right">
        <Badge
          variant="secondary"
          className="max-w-full whitespace-normal text-left"
        >
          {label}
        </Badge>
        <p className="mt-2 text-xs text-muted-foreground tabular-nums">
          {row.value == null ? row.certainty.toFixed(2) : row.value.toFixed(2)}
        </p>
      </div>
    </article>
  );
}

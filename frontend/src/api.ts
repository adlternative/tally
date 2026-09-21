export type Kind = "passage" | "noul" | "choice" | "score";
export type Scope = "page" | "items";
export interface Source {
  name: string;
  label: string;
  label_en: string;
  ident: string;
  ident_en: string;
  note: string;
  note_en: string;
  url_ok: boolean;
}
export interface TokenStatus {
  configured: boolean;
  source: string;
  where: string;
  can_write: boolean;
}
export interface Cache {
  cached: boolean;
  count: number;
  age_seconds: number | null;
}
export interface SourceData {
  source: string;
  ident: string;
  label: string;
  label_en: string;
  count: number;
  authors: number;
  items: string[];
  tokens: number;
  subject: string;
  cache: Cache;
}
export interface PageData {
  url: string;
  title: string;
  items: string[];
  item_count: number;
  tokens: number;
  item_tokens: number;
  preview: string;
  mode: string;
}
export interface Dataset {
  request: { url?: string; source?: string; ident?: string; limit?: number };
  title: string;
  titleEn?: string;
  subject: string;
  items: string[];
  count: number;
  authors?: number;
  tokens: number;
  cache?: Cache;
  preview?: string;
}
export interface Item {
  n: number;
  text: string;
  value: number | null;
  label: string;
  certainty: number;
  author: string;
}
export type Distribution = [string, number, number?][];
export interface Result {
  kind: Kind;
  scope?: Scope;
  found: boolean;
  answer: string | number | null;
  question: string;
  title: string;
  lead?: string;
  counted?: number;
  total_items?: number;
  mean?: number | null;
  band?: number;
  distribution?: Distribution;
  items?: Item[];
  people?: { authors: number; distribution: Distribution } | null;
  none_probability?: number;
  confidence?: number | null;
  top?: { text: string; probability: number }[];
  model: string;
  usage?: { input_tokens: number };
  estimated_usd: number;
  ms?: number;
}
export interface SavedResult {
  id: number;
  result: Result;
  options: string[];
  preset: string;
  language: string;
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    path,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data as T;
}
export function preference(key: string, fallback: string) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch {
    return fallback;
  }
}
export function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* Storage may be disabled; this session still works. */
  }
}

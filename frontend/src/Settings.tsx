import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  CheckCircle2,
  ExternalLink,
  KeyRound,
  Languages,
  Loader2,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogClose,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, type TokenStatus } from "./api";

export function Settings({
  open,
  onOpenChange,
  onCloseFocus,
  status,
  onStatus,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCloseFocus: () => void;
  status: TokenStatus | null;
  onStatus: (status: TokenStatus) => void;
}) {
  const { t, i18n } = useTranslation();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      onStatus(await api<TokenStatus>("/token", { token }));
      setToken("");
      setSaved(true);
    } catch (error) {
      setError(error instanceof Error ? error.message : t("error"));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!busy) {
          setToken("");
          setError("");
          setSaved(false);
          onOpenChange(next);
        }
      }}
    >
      <DialogContent
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onCloseFocus();
        }}
        showCloseButton={false}
        className="max-h-[90dvh] overflow-y-auto sm:max-w-[480px]"
      >
        <DialogHeader>
          <DialogTitle>{t("settings")}</DialogTitle>
          <DialogDescription>{t("settingsHint")}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-3">
          <Label className="flex items-center gap-2">
            <Languages className="size-4" />
            {t("language")}
          </Label>
          <Tabs
            value={i18n.language}
            onValueChange={(language) => {
              void i18n.changeLanguage(language);
            }}
          >
            <TabsList className="w-full">
              <TabsTrigger value="zh" className="flex-1">
                中文
              </TabsTrigger>
              <TabsTrigger value="en" className="flex-1">
                English
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
        <Separator />
        <form onSubmit={save} className="space-y-4 py-3">
          <div className="flex items-center justify-between">
            <Label htmlFor="token-input">
              <KeyRound className="size-4" />
              {t("token")}
            </Label>
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              {status?.configured && (
                <CheckCircle2 className="size-3.5 text-primary" />
              )}
              {t(status?.configured ? "configured" : "notConfigured")}
            </span>
          </div>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {t("tokenHint")}
          </p>
          {status?.where && (
            <code className="block break-all rounded-md bg-muted p-2 text-xs text-muted-foreground">
              {status.where}
            </code>
          )}
          {status?.source === "环境变量" && (
            <p className="text-xs text-muted-foreground">
              {t("environmentToken")}
            </p>
          )}
          <Input
            id="token-input"
            type="password"
            value={token}
            autoComplete="off"
            placeholder={t("tokenPlaceholder")}
            onChange={(e) => {
              setToken(e.target.value);
              setSaved(false);
            }}
            disabled={busy}
          />
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {saved && (
            <p role="status" className="text-sm text-primary">
              {t("savedToken")}
            </p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <a
              href="https://console.typesafe.ai/"
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-sm text-muted-foreground underline underline-offset-4"
            >
              {t("getToken")}
              <ExternalLink className="size-3" />
            </a>
            <Button type="submit" disabled={busy || token.trim().length < 8}>
              {busy && <Loader2 className="animate-spin" />}
              {t(busy ? "saving" : "saveToken")}
            </Button>
          </div>
        </form>
        <DialogClose asChild>
          <Button variant="outline" disabled={busy}>
            {t("close")}
          </Button>
        </DialogClose>
      </DialogContent>
    </Dialog>
  );
}

import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
const zh = JSON.parse(
  readFileSync(new URL("../src/locales/zh.json", import.meta.url), "utf8"),
);
const en = JSON.parse(
  readFileSync(new URL("../src/locales/en.json", import.meta.url), "utf8"),
);

async function select(page: Page, label: string, option: string) {
  await page.getByRole("combobox", { name: label, exact: true }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
}
async function fetchSource(
  page: Page,
  id = "topic",
  source = "样例源（有标题）",
) {
  await select(page, "选择数据源", source);
  await page.getByLabel("链接或标识", { exact: true }).fill(id);
  await page.getByRole("button", { name: "获取内容", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeEnabled();
}
async function run(page: Page) {
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(page.getByTestId("results")).toBeVisible();
}
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("jev.lang", "zh");
    localStorage.setItem("jev.source", "fixture-subject");
  });
  page.on("pageerror", (error) => {
    throw error;
  });
  // Keep this suite off the public internet, even if a UI regression tries to navigate there.
  await page.route("**/*", (route) =>
    new URL(route.request().url()).origin === "http://127.0.0.1:8034"
      ? route.continue()
      : route.abort(),
  );
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "获取内容", exact: true }),
  ).toBeEnabled();
});

test("settings owns language and token; keyboard and focus return work", async ({
  page,
}) => {
  await expect(
    page.getByLabel("Jev API token", { exact: true }),
  ).not.toBeVisible();
  await expect(
    page.getByRole("tab", { name: "English", exact: true }),
  ).not.toBeVisible();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("已配置", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("Jev API token", { exact: true })).toHaveValue(
    "",
  );
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "设置", exact: true }),
  ).toBeFocused();
});

test("language switch persists without altering custom question", async ({
  page,
}) => {
  await fetchSource(page);
  await page.getByLabel("问题", { exact: true }).fill("保留我写的问题");
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.getByRole("tab", { name: "English", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByLabel("Question", { exact: true })).toHaveValue(
    "保留我写的问题",
  );
  await expect(
    page.getByRole("button", { name: "Run analysis", exact: true }),
  ).toBeEnabled();
  // Remove only the test's forced locale script by using a new page in the same context.
  const next = await page.context().newPage();
  await next.goto("/");
  await expect(next.locator("html")).toHaveAttribute("lang", "en");
});

test("token rejection does not close dialog; saving verified token clears input", async ({
  page,
}) => {
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const input = page.getByLabel("Jev API token", { exact: true });
  await input.fill("bad-token");
  await page.getByRole("button", { name: "验证并保存", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toBeVisible();
  await input.fill("good-token");
  await page.getByRole("button", { name: "验证并保存", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("status")).toHaveText(
    "验证成功，已保存。",
  );
  await expect(input).toHaveValue("");
  await expect(page.getByRole("dialog")).not.toContainText("good-token");
});

test("first run automatically opens settings without exposing credentials", async ({
  page,
}) => {
  await page.route("**/token", (route) =>
    route.fulfill({
      json: {
        configured: false,
        source: "",
        where: "/isolated/token",
        can_write: true,
      },
    }),
  );
  await page.reload();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByRole("dialog").getByText("未配置", { exact: true }),
  ).toBeVisible();
});

for (const [id, count] of [
  ["single", 1],
  ["short", 2],
  ["normal", 4],
] as const) {
  test(`source with ${count} comments can be fetched twice and judged`, async ({
    page,
  }) => {
    const sent: unknown[] = [];
    page.on("request", (request) => {
      if (request.url().endsWith("/sourcedata"))
        sent.push(request.postDataJSON().source);
    });
    await fetchSource(page, id);
    await expect(page.getByTestId("raw-row")).toHaveCount(count);
    await expect(page.getByLabel("主题 / 对象")).toHaveValue(
      "样例贴：一个小游戏",
    );
    await page.getByRole("button", { name: "获取内容", exact: true }).click();
    await expect(page.getByText("使用本机缓存", { exact: true })).toBeVisible();
    expect(sent).toEqual(["fixture-subject", "fixture-subject"]);
    await run(page);
    await expect(page.getByTestId("audit-row")).toHaveCount(count);
    await expect(page.getByTestId("denominator")).toContainText(
      `${count} / ${count}`,
    );
  });
}

test("fresh fetch, cache hit, explicit refresh; judgment never refetches", async ({
  page,
  request,
}) => {
  await fetchSource(page, `cache-${Date.now()}`);
  await expect(page.getByText("刚刚获取并缓存", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "获取内容", exact: true }).click();
  await expect(page.getByText("使用本机缓存", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "重新获取", exact: true }).click();
  await expect(page.getByText("刚刚获取并缓存", { exact: true })).toBeVisible();
  let pulls = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/sourcedata")) pulls++;
  });
  await run(page);
  await run(page);
  expect(pulls).toBe(0);
  expect((await request.get("/sources")).ok()).toBeTruthy();
});

test("filters, sorting, author search, expansion and clear do not call the model", async ({
  page,
}) => {
  await fetchSource(page);
  await select(page, "分析方式", "自定义问题");
  await page.getByLabel("问题", { exact: true }).fill("这条评论喜欢吗？");
  await page
    .getByLabel("选项（每行一个）", { exact: true })
    .fill("喜欢\n一般\n讨厌");
  await run(page);
  let requests = 0;
  page.on("request", (request) => {
    if (request.url().endsWith("/ask")) requests++;
  });
  await page
    .getByTestId("distribution")
    .getByRole("button", { name: /讨厌/ })
    .click();
  await expect(page.getByTestId("audit-row")).toHaveCount(1);
  await expect(page.getByTestId("audit-row")).toHaveAttribute(
    "data-label",
    "讨厌",
  );
  await expect(page.getByTestId("shown-count")).toHaveText("显示 1 / 4 条");
  await page.getByRole("button", { name: "展开全文", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "收起", exact: true }),
  ).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("button", { name: "清除筛选", exact: true }).click();
  await select(page, "排序", "原始顺序");
  await expect(page.getByTestId("audit-row").first()).toHaveAttribute(
    "data-order",
    "1",
  );
  await select(page, "排序", "最确定优先");
  const values = await page
    .getByTestId("audit-row")
    .evaluateAll((rows) =>
      rows.map((row) => Number(row.getAttribute("data-certainty"))),
    );
  expect(values).toEqual([...values].sort((a, b) => b - a));
  await page.getByRole("textbox", { name: "搜索评论或作者" }).fill("alice");
  await expect(page.getByTestId("audit-row")).toHaveCount(2);
  await page
    .getByRole("textbox", { name: "搜索评论或作者" })
    .fill("no-such-comment");
  await expect(page.getByText("没有匹配的评论")).toBeVisible();
  expect(requests).toBe(0);
});

test("switching dataset invalidates old results, but switching rules does not refetch", async ({
  page,
}) => {
  await fetchSource(page);
  await run(page);
  await select(page, "分析方式", "情绪分布");
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeEnabled();
  await page.getByLabel("链接或标识", { exact: true }).fill("different");
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeDisabled();
  await expect(page.getByTestId("results")).not.toBeVisible();
});

for (const preset of [
  "喜欢 / 讨厌",
  "情绪分布",
  "满意度",
  "主题相关性",
  "内容质量",
]) {
  test(`preset: ${preset}`, async ({ page }) => {
    await fetchSource(page);
    await select(page, "分析方式", preset);
    await run(page);
    await expect(page.getByTestId("audit-row")).toHaveCount(4);
    await expect(page.getByTestId("denominator")).toContainText("4 / 4");
  });
}

test("authors have their own denominator", async ({ page }) => {
  await fetchSource(page);
  await run(page);
  await page.getByRole("tab", { name: "按作者", exact: true }).click();
  await expect(page.getByTestId("distribution")).toContainText("33.3%");
  await expect(page.getByTestId("denominator")).toContainText("去重作者3");
});

test("history restores old result and new runs keep unique entries", async ({
  page,
}) => {
  await fetchSource(page);
  await page.getByLabel("问题", { exact: true }).fill("first question");
  await run(page);
  await page.getByLabel("问题", { exact: true }).fill("second question");
  await run(page);
  await page.getByRole("tab", { name: /本次会话/ }).click();
  await page.getByRole("button", { name: /first question/ }).click();
  await expect(page.getByTestId("result-question")).toHaveText(
    "first question",
  );
  await page.getByLabel("问题", { exact: true }).fill("third question");
  await run(page);
  await page.getByRole("tab", { name: /本次会话/ }).click();
  for (const name of ["first question", "second question", "third question"])
    await expect(
      page.getByRole("button", { name: new RegExp(name) }),
    ).toBeVisible();
});

for (const kind of ["查找原文", "是 / 否", "分类", "打分"]) {
  test(`URL / whole page: ${kind}`, async ({ page }) => {
    const { pageUrl } = JSON.parse(readFileSync(".test/backend.json", "utf8"));
    await page.getByRole("tab", { name: "网页", exact: true }).click();
    await page.getByLabel("网页地址", { exact: true }).fill(pageUrl);
    await page.getByRole("button", { name: "获取内容", exact: true }).click();
    await expect(page.getByTestId("raw-row")).toHaveCount(4);
    await select(page, "问题类型", kind);
    await page.getByLabel("问题", { exact: true }).fill("喜欢吗？");
    if (kind === "分类")
      await page.getByLabel("选项（每行一个）").fill("喜欢\n一般\n讨厌");
    if (kind === "打分")
      await page
        .getByLabel("档位（每行一个，由低到高）")
        .fill("差\n一般\n好\n很好");
    await run(page);
    await expect(page.getByTestId("verdict")).not.toBeEmpty();
    if (kind === "查找原文")
      await expect(page.locator("blockquote")).toContainText("很好玩");
  });
}

test("URL per-item yes/no and passage-none paths", async ({ page }) => {
  const { pageUrl } = JSON.parse(readFileSync(".test/backend.json", "utf8"));
  await page.getByRole("tab", { name: "网页", exact: true }).click();
  await page.getByLabel("网页地址").fill(pageUrl);
  await page.getByRole("button", { name: "获取内容", exact: true }).click();
  await expect(page.getByTestId("raw-row")).toHaveCount(4);
  await select(page, "问题类型", "是 / 否");
  await select(page, "判断范围", "逐条");
  await page.getByLabel("问题", { exact: true }).fill("喜欢吗？");
  await run(page);
  await expect(page.getByTestId("audit-row")).toHaveCount(4);
  await select(page, "问题类型", "查找原文");
  await page.getByLabel("问题", { exact: true }).fill("没有答案的问题");
  await run(page);
  await expect(page.getByTestId("verdict")).toHaveText(
    "没有原文直接回答这个问题",
  );
});

test("failed pull keeps chosen source and permits retry", async ({ page }) => {
  await select(page, "选择数据源", "样例源（空）");
  await page.getByLabel("链接或标识").fill("empty");
  await page.getByRole("button", { name: "获取内容", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("没拿到任何文本");
  await expect(
    page.getByRole("combobox", { name: "选择数据源" }),
  ).toContainText("样例源（空）");
  await fetchSource(page);
  await expect(page.getByTestId("raw-row")).toHaveCount(4);
  await expect(page.getByRole("alert")).not.toBeVisible();
});

test("invalid input is rejected before model requests", async ({ page }) => {
  await page.getByRole("button", { name: "获取内容", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("请填写链接或标识");
  await fetchSource(page);
  await select(page, "分析方式", "自定义问题");
  await page.getByLabel("问题", { exact: true }).fill("test");
  await page.getByLabel("选项（每行一个）").fill("only one");
  let calls = 0;
  page.on("request", (request) => {
    if (request.url().endsWith("/ask")) calls++;
  });
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("至少提供两个不同选项");
  expect(calls).toBe(0);
});

test("remaining sources are selectable and Xueqiu is absent", async ({
  page,
}) => {
  await page.getByRole("combobox", { name: "选择数据源" }).click();
  await expect(page.getByRole("option", { name: /雪球/ })).toHaveCount(0);
  for (const name of [
    "B站视频评论",
    "微博评论",
    "小红书笔记评论",
    "知乎回答评论",
    "YouTube 视频评论",
    "V2EX 帖子回复",
    "Hacker News 讨论",
  ])
    await expect(page.getByRole("option", { name, exact: true })).toBeVisible();
});

test("locales have matching keys and interpolation variables", () => {
  function walk(a: unknown, b: unknown) {
    expect(typeof a).toBe(typeof b);
    if (typeof a === "string" && typeof b === "string") {
      expect(a.match(/{{\w+}}/g)?.sort()).toEqual(b.match(/{{\w+}}/g)?.sort());
      return;
    }
    if (a && b && typeof a === "object" && typeof b === "object") {
      expect(Object.keys(a)).toEqual(Object.keys(b));
      for (const key of Object.keys(a))
        walk(a[key as keyof typeof a], b[key as keyof typeof b]);
    }
  }
  walk(zh, en);
});

for (const width of [1440, 1024, 390]) {
  test(`responsive workspace and settings at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await fetchSource(page);
    await run(page);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    const box = await page.getByRole("dialog").boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await page.screenshot({ path: `test-results/settings-${width}.png` });
    await page.getByRole("button", { name: "关闭", exact: true }).click();
    await page.screenshot({
      path: `test-results/workspace-${width}.png`,
      fullPage: true,
    });
  });
}

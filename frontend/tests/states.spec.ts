import { test, expect } from "@playwright/test";

test("English end-to-end uses English preset and settings without mutating results", async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem("jev.lang", "en");
    localStorage.setItem("jev.source", "fixture-subject");
  });
  await page.goto("/");
  await page.getByLabel("Link or identifier", { exact: true }).fill("english");
  await page
    .getByRole("button", { name: "Fetch content", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Run analysis", exact: true }),
  ).toBeEnabled();
  const sent = page.waitForRequest((request) => request.url().endsWith("/ask"));
  await page.getByRole("button", { name: "Run analysis", exact: true }).click();
  expect((await sent).postDataJSON().options).toEqual([
    "Like",
    "Neutral",
    "Dislike",
    "Unrelated",
  ]);
  await expect(page.getByTestId("audit-row")).toHaveCount(4);
  const counts = await page.getByTestId("distribution").innerText();
  await page.screenshot({
    path: "test-results/english-workspace.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.screenshot({ path: "test-results/english-settings.png" });
  await page.getByRole("tab", { name: "中文", exact: true }).click();
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(page.getByTestId("audit-row")).toHaveCount(4);
  const rawLabels = await page
    .getByTestId("audit-row")
    .evaluateAll((rows) => rows.map((row) => row.getAttribute("data-label")));
  expect(
    rawLabels.every((label) =>
      ["Like", "Neutral", "Dislike", "Unrelated"].includes(label!),
    ),
  ).toBeTruthy();
  expect(counts).toContain("50.0%");
});

test("pending requests lock configuration; upstream error permits retry", async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem("jev.source", "fixture");
    localStorage.setItem("jev.lang", "zh");
  });
  await page.goto("/");
  await page.getByLabel("链接或标识").fill("failure");
  await page.getByRole("button", { name: "获取内容", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "开始分析", exact: true }),
  ).toBeEnabled();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/ask", async (route) => {
    await gate;
    await route.fulfill({
      status: 503,
      json: { error: "Test upstream unavailable" },
    });
  });
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "正在逐条判断…", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("combobox", { name: "选择数据源" }),
  ).toBeDisabled();
  await expect(page.getByLabel("链接或标识")).toBeDisabled();
  release();
  await expect(page.getByRole("alert")).toContainText(
    "Test upstream unavailable",
  );
  await page.unroute("**/ask");
  await page.getByRole("button", { name: "开始分析", exact: true }).click();
  await expect(page.getByTestId("audit-row")).toHaveCount(4);
  await expect(page.getByRole("alert")).not.toBeVisible();
});

test("source content is rendered as text, never executed as markup", async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem("jev.source", "fixture");
    localStorage.setItem("jev.lang", "zh");
  });
  const text =
    '<img src=x onerror="document.body.dataset.compromised=1"> & raw text';
  await page.route("**/sourcedata", (route) =>
    route.fulfill({
      json: {
        source: "fixture",
        ident: "x",
        label: "Fixture",
        label_en: "Fixture",
        subject: text,
        count: 1,
        authors: 1,
        tokens: 30,
        items: [text],
        cache: { cached: false, count: 1, age_seconds: 0 },
      },
    }),
  );
  await page.goto("/");
  await page.getByLabel("链接或标识").fill("x");
  await page.getByRole("button", { name: "获取内容", exact: true }).click();
  await expect(page.getByTestId("raw-row")).toContainText(text);
  await expect(page.getByTestId("raw-row").locator("img")).toHaveCount(0);
  expect(
    await page.locator("body").getAttribute("data-compromised"),
  ).toBeNull();
});

import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const routes = ["/my-team", "/assistant", "/planner", "/journal", "/settings"];
const dashboardRoutes = [
  "/my-team", "/assistant", "/v5-lab", "/journal", "/planner",
  "/league?league=58005", "/league?league=131997", "/elite?league=131997", "/compare?league=131997",
  "/transfers?league=58005", "/transfers?league=131997", "/analytics?league=131997", "/players", "/settings",
];

// Retired routes and where they now land: the Autopilot bridge + Telegram
// execution channel fold into the read-only Assistant; Model XIs folds into
// the single Models page (/v5-lab).
const retiredRoutes: Array<{ from: string; to: RegExp }> = [
  { from: "/autopilot", to: /\/assistant$/ },
  { from: "/shadow-v3", to: /\/assistant$/ },
  { from: "/model-compare", to: /\/v5-lab$/ },
];

for (const route of routes) {
  test(`${route} renders its primary navigation and content`, async ({ page }, testInfo) => {
    const severeConsoleErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") severeConsoleErrors.push(message.text());
    });
    const response = await page.goto(route, { waitUntil: "domcontentloaded" });
    expect(response?.status()).toBe(200);
    await expect(page.getByRole("main")).toBeVisible();
    const home = testInfo.project.name.includes("mobile")
      ? page.locator(".mobile-header").getByRole("link", { name: "Fantasy Scout" })
      : page.getByRole("link", { name: "Fantasy Scout home" });
    await expect(home).toBeVisible();
    await expect(page.locator("body")).not.toHaveCSS("font-family", "Times New Roman");
    expect(severeConsoleErrors).toEqual([]);
  });
}

test("every dashboard tab renders useful content without a route error", async ({ page }) => {
  for (const route of dashboardRoutes) {
    const response = await page.goto(route, { waitUntil: "domcontentloaded" });
    expect(response?.status(), route).toBe(200);
    await expect(page.getByRole("main"), route).toBeVisible();
    await expect(page.getByRole("heading", { name: "We could not load this view." }), route).toHaveCount(0);
    await expect(page.locator("h1"), route).toBeVisible();
  }
});

test("retired routes redirect to their replacement page", async ({ page }) => {
  for (const { from, to } of retiredRoutes) {
    await page.goto(from, { waitUntil: "domcontentloaded" });
    await expect(page, from).toHaveURL(to);
    await expect(page.locator("h1"), from).toBeVisible();
  }
});

test("mobile dashboard tabs do not create page-level horizontal overflow", async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.includes("mobile"), "mobile-only layout check");
  for (const route of dashboardRoutes) {
    await page.goto(route, { waitUntil: "domcontentloaded" });
    await expect(page.locator("h1"), route).toBeVisible();
    const sizes = await page.evaluate(() => ({ viewport: window.innerWidth, page: document.documentElement.scrollWidth }));
    expect(sizes.page, route).toBeLessThanOrEqual(sizes.viewport);
  }
});

test("My Team displays official live values instead of placeholder states", async ({ page }) => {
  await page.goto("/my-team", { waitUntil: "domcontentloaded" });
  await expect(page.getByText("official live data", { exact: false })).toBeVisible();
  await expect(page.getByText("In progress", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Pending", { exact: true })).toHaveCount(0);
});

test("Planner uses the official 15-player squad and current fixture horizon", async ({ page }) => {
  await page.goto("/planner", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".horizon-cards article").first()).not.toContainText("0.0 avg FDR");
  await expect(page.locator(".fixture-chip:not(.blank)").first()).toBeVisible();
  await expect(page.locator(".fixture-matrix tbody tr")).toHaveCount(15);
});

test("public league transfers remain usable without a personalized optimizer", async ({ page }) => {
  await page.goto("/transfers?league=131997", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "Transfers & chips" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "League market data is ready" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Popular transfer combinations" })).toBeVisible();
});

test("journal opens a frozen archived gameweek", async ({ page }) => {
  await page.goto("/journal", { waitUntil: "domcontentloaded" });
  const archived = page.locator('a.journal-week[href="/journal/2026-27/gw/1"]');
  await expect(archived).toContainText("GW1");
  await archived.click();
  await expect(page).toHaveURL(/\/journal\/2026-27\/gw\/1$/);
  await expect(page.getByRole("heading", { name: "GW1 Review" })).toBeVisible();
  await expect(page.getByText("Complete research record")).toHaveCount(0);
});

test("season week navigation routes archive, live, planning and future weeks by purpose", async ({ page }) => {
  await page.goto("/journal", { waitUntil: "domcontentloaded" });
  await page.locator("details.week-rail summary").click();
  const rail = page.locator("details.week-rail");
  const archived = rail.locator("a.archived");
  await expect(archived.first()).toHaveAttribute("href", /\/journal\/2026-27\/gw\/\d+$/);
  await expect(rail.locator("a.live")).toHaveAttribute("href", "/my-team");
  const planning = rail.locator("a.planning");
  if (await planning.count()) await expect(planning).toHaveAttribute("href", "/assistant");
  const future = rail.locator('a[href^="/planner?gw="]').last();
  await expect(future).toHaveAttribute("href", "/planner?gw=38");
});

test("mobile navigation exposes primary and overflow destinations", async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.includes("mobile"), "mobile-only control check");
  await page.goto("/my-team", { waitUntil: "domcontentloaded" });
  const mobileNav = page.getByRole("navigation", { name: "Mobile navigation" });
  await expect(mobileNav.getByRole("link", { name: "My Team" })).toBeVisible();
  const more = mobileNav.getByRole("button", { name: "More" });
  await expect(more).toBeEnabled();
  await more.click();
  await expect(more).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("link", { name: "Settings" })).toBeVisible();
});

test("player artwork resolves to an image or deterministic fallback", async ({ page }) => {
  await page.goto("/my-team", { waitUntil: "domcontentloaded" });
  const art = page.locator(".player-art").first();
  await expect(art).toBeVisible();
  await expect.poll(async () => art.evaluate((node) => {
    const image = node.querySelector("img") as HTMLImageElement | null;
    return Boolean((image && image.complete && image.naturalWidth > 0) || node.querySelector("span"));
  })).toBe(true);
});

test("critical pages have no serious or critical automated accessibility violations", async ({ page }) => {
  for (const route of ["/my-team", "/league", "/journal"]) {
    await page.goto(route, { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("main")).toBeVisible();
    const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    const blocking = result.violations.filter((violation) => ["serious", "critical"].includes(violation.impact ?? ""));
    expect(blocking, `${route}: ${blocking.map((item) => `${item.id} (${item.nodes.length})`).join(", ")}`).toEqual([]);
  }
});

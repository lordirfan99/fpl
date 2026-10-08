import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { encode } from "next-auth/jwt";
test.beforeEach(async ({ request }) => { await request.get("http://127.0.0.1:4185/__test/mode/normal"); });

test("anonymous bundle is private/no-store and never exposes account data", async ({ request }) => {
  const response = await request.get("/api/week?league=131997");
  expect(response.headers()["cache-control"]).toBe("private, no-store");
  expect(response.headers().vary).toContain("Cookie");
  const sections = (await response.text()).trim().split("\n").map(line => JSON.parse(line));
  expect(sections.find(s => s.section === "league").data.league_id).toBe(131997);
  expect(sections.find(s => s.section === "private").data).toEqual({ status: "signed_out", packet: null });
  expect((await request.get("/api/week?league=999")).status()).toBe(400);
});

test("league renders during slow private check; editing package makes no backend requests", async ({ page, context, request }) => {
  await request.get("http://127.0.0.1:4185/__test/mode/slow-private");
  const token = await encode({ token: { sub: "fpl-owner", email: "owner@fpl.local", ownerVerified: true }, secret: "test-only-secret-not-for-production-123456789", salt: "authjs.session-token" });
  await context.addCookies([{ name: "authjs.session-token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  const calls: string[] = [];
  page.on("request", req => { if (new URL(req.url()).pathname.startsWith("/api/")) calls.push(req.url()); });
  await page.goto("/this-week");
  await expect(page.getByRole("heading", { name: "6 points to the top-10% cutoff" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Test transfer scenarios" })).not.toBeVisible();
  await expect(page.getByRole("heading", { name: "Test transfer scenarios" })).toBeVisible();
  expect(calls.filter(url => url.includes("/api/week?"))).toHaveLength(1);
  const before = calls.length;
  await page.getByRole("button", { name: "Add transfer", exact: true }).click();
  await page.getByLabel("Sell player 1", { exact: true }).selectOption("11");
  await page.getByLabel("Buy player 1", { exact: true }).selectOption("16");
  await expect(page.getByText("Scenario bank", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Add transfer", exact: true }).click();
  await page.getByLabel("Sell player 2", { exact: true }).selectOption("2");
  await expect(page.getByLabel("Buy player 2", { exact: true })).toHaveValue("");
  await expect(page.getByText("Choose an owned outgoing player and an unowned incoming player.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Remove transfer 2" }).click();
  await expect(page.getByText("Net horizon gain", { exact: true })).toBeVisible();
  expect(calls.length).toBe(before);
  const a11y = await new AxeBuilder({ page }).include(".transfer-simulator").analyze();
  expect(a11y.violations.filter(v => v.impact === "critical" || v.impact === "serious")).toEqual([]);
  const check = await page.request.get("/api/private/dashboard?view=check");
  const identity = await check.json();
  expect(identity.packet).toEqual({ plan_id: "synthetic-test-plan", account_fingerprint: "synthetic-fingerprint" });
  await page.route("**/api/private/dashboard?view=check", route => route.fulfill({ json: { status: "unavailable", packet: null } }));
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByRole("heading", { name: "Test transfer scenarios" })).not.toBeVisible();
  await expect(page.getByLabel("Gameweek checklist")).not.toBeVisible();
  await expect(page.getByRole("button", { name: "Reload verified plan" })).toBeVisible();
});

test("overview preserves freshness and rival coverage", async ({ request, page }) => {
  const response = await request.get("/api/week?league=58005");
  const league = (await response.text()).trim().split("\n").map(line => JSON.parse(line)).find(s => s.section === "league").data;
  expect(league.freshness.stale).toBe(false);
  expect(league.ownership).toMatchObject({ sample_count: 100, population: 100, cohort_sample: 10, cohort_count: 10 });
  // Public evidence is cached briefly by Next. Inject an aged response at the
  // browser boundary to verify the stale UI without depending on cache expiry.
  await page.route("**/api/week?league=*", route => route.fulfill({
    contentType: "application/x-ndjson",
    body: JSON.stringify({ section: "league", data: { ...league, status: "historical", freshness: { ...league.freshness, stale: true } } }) + "\n"
      + JSON.stringify({ section: "private", data: { status: "signed_out", packet: null } }) + "\n",
  }));
  await page.goto("/this-week");
  await expect(page.getByText("Older or unverified capture: not current standings.", { exact: false })).toBeVisible();
  await expect(page.getByText("Squad coverage: 100 / 100 managers.", { exact: false })).toBeVisible();
});

test("authenticated stalled private body becomes retryable and recovers", async ({ page, context, request }) => {
  await request.get("http://127.0.0.1:4185/__test/mode/stalled-private");
  const token = await encode({ token: { sub: "fpl-owner", ownerVerified: true }, secret: "test-only-secret-not-for-production-123456789", salt: "authjs.session-token" });
  await context.addCookies([{ name: "authjs.session-token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  await page.goto("/this-week");
  await expect(page.getByRole("heading", { name: "6 points to the top-10% cutoff" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Checking your private plan" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Unlock private plan" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry private plan" })).toBeVisible({ timeout: 12000 });
  await expect(page.getByLabel("Gameweek checklist")).toHaveCount(0);
  await request.get("http://127.0.0.1:4185/__test/mode/hold");
  await page.getByRole("button", { name: "Retry private plan" }).click();
  const checklist = page.getByLabel("Gameweek checklist");
  await expect(checklist).toBeVisible();
  await expect(checklist.getByText("Transfers", { exact: true }).locator("..").locator("strong")).toHaveText("HOLD");
});

test("player market loads without requesting the league or personal team", async ({ page, request }) => {
  await page.goto("/players");
  await expect(page.getByRole("heading", { name: "Player market" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Test Player 1", exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Search players or teams" }).fill("Test Player 16");
  await expect(page.getByRole("cell", { name: "Test Player 16", exact: true })).toBeVisible();
  const calls: string[] = await (await request.get("http://127.0.0.1:4185/__test/calls")).json();
  expect(calls.filter(url => url.startsWith("/v1/"))).toEqual(["/v1/catalog/compact"]);
});

test("stalled player catalog exits loading with a useful recovery action", async ({ page, request }) => {
  await request.get("http://127.0.0.1:4185/__test/mode/stalled-catalog");
  await page.goto("/players", { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("heading", { name: "We could not load this view." })).toBeVisible({ timeout: 12000 });
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Open This Week" })).toBeVisible();
});

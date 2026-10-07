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

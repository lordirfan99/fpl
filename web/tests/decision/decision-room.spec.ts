import { test, expect } from "@playwright/test";
import { encode } from "next-auth/jwt";
import AxeBuilder from "@axe-core/playwright";
test.beforeEach(async ({ request }) => { await request.get("http://127.0.0.1:4185/__test/mode/normal"); });

for (const width of [390, 1440]) {
  test(`verified owner flow at ${width}px`, async ({ page, context }) => {
    const token = await encode({ token: { email: "owner@example.com", ownerVerified: true }, secret: "test-only-secret-not-for-production-123456789", salt: "authjs.session-token" });
    await context.addCookies([{ name: "authjs.session-token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
    await page.setViewportSize({ width, height: 900 });
    const errors: string[] = [];
    page.on("pageerror", e => errors.push(e.message));
    const response = await page.goto("/this-week");
    expect(response?.headers()["cache-control"]).toContain("no-store");
    await expect(page.getByRole("heading", { name: "Your actual team" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "6 points to the top-10% cutoff" })).toBeVisible();
    await expect(page.getByText("Plan synthetic-test-plan", { exact: false })).toBeVisible();
    await expect(page.getByText("Recorded target-group captaincy: 50.0% in GW3.")).toBeVisible();
    await page.getByRole("button", { name: "Proposed squad", exact: true }).click();
    await expect(page.getByLabel("Proposed starting eleven")).toContainText("IN");
    await page.getByRole("button", { name: /Test Player 16/ }).click();
    await expect(page.getByText("Evidence for Test Player 16")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    const a11y = await new AxeBuilder({ page }).include(".decision-home").analyze();
    expect(a11y.violations.filter(v => v.impact === "critical" || v.impact === "serious")).toEqual([]);
    await page.screenshot({ path: `test-results/decision-${width}.png`, fullPage: true });
    expect(errors).toEqual([]);
  });
}
test("signed-out and signed-in non-owner cannot retrieve the private packet", async ({ page, context }) => {
  await page.goto("/this-week");
  await expect(page.getByText("Your personal plan stays private")).toBeVisible();
  await expect(page.getByRole("heading", { name: "6 points to the top-10% cutoff" })).toBeVisible();
  await expect(page.getByText("Test Player 16", { exact: true })).toBeVisible();
  expect((await page.request.get("/api/private/dashboard")).status()).toBe(401);
  const token = await encode({ token: { email: "attacker@example.com", ownerVerified: true }, secret: "test-only-secret-not-for-production-123456789", salt: "authjs.session-token" });
  await context.addCookies([{ name: "authjs.session-token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  await page.reload();
  await expect(page.getByText("Your personal plan stays private")).toBeVisible();
  expect((await page.request.get("/api/private/dashboard")).status()).toBe(401);
  await expect(page.getByText("synthetic-test-plan", { exact: false })).toHaveCount(0);
});

for (const mode of ["wildcard", "freehit", "unavailable"]) {
  test(`correct decision state for ${mode}`, async ({ page, context, request }) => {
    await request.get(`http://127.0.0.1:4185/__test/mode/${mode}`);
    const token = await encode({ token: { email: "owner@example.com", ownerVerified: true }, secret: "test-only-secret-not-for-production-123456789", salt: "authjs.session-token" });
    await context.addCookies([{ name: "authjs.session-token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
    await page.goto("/this-week");
    await expect(page.getByRole("heading", { name: mode === "unavailable" ? "Plan unavailable" : /compare your full squad/ })).toBeVisible();
    if (mode === "unavailable") await expect(page.getByText("synthetic-test-plan", { exact: false })).toHaveCount(0);
    else {
      await expect(page.getByText("Unlimited", { exact: true })).toBeVisible();
      if (mode === "freehit") await expect(page.getByText("Returning squad").first()).toBeVisible();
    }
  });
}

test("Plan navigation uses the same canonical private packet", async ({ page, context }) => {
  const token = await encode({ token: { email: "owner@example.com", ownerVerified: true }, secret: "test-only-secret-not-for-production-123456789", salt: "authjs.session-token" });
  await context.addCookies([{ name: "authjs.session-token", value: token, domain: "localhost", path: "/", httpOnly: true, sameSite: "Lax" }]);
  const response = await page.goto("/planner");
  expect(response?.headers()["cache-control"]).toContain("no-store");
  await expect(page.getByRole("heading", { name: "Your current decision plan" })).toBeVisible();
  await expect(page.getByText("Plan synthetic-test-plan", { exact: false })).toBeVisible();
  await expect(page.getByText("Hold the transfer for now")).toHaveCount(0);
});

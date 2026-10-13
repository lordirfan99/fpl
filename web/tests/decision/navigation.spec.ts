import { test, expect } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.get("http://127.0.0.1:4185/__test/mode/normal");
});

test("active GW resolves to finalized League and Compare snapshots", async ({ page }) => {
  const league = await page.goto("/league?league=58005");
  expect(league?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "KK Old Boys", exact: true })).toBeVisible();
  await expect(page.getByText("GW2 finalized standings", { exact: false })).toBeVisible();
  const compare = await page.goto("/compare?league=58005");
  expect(compare?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "Compare managers" })).toBeVisible();
  await expect(page.getByText("GW2 finalized", { exact: false })).toBeVisible();
});

test("historical GW remains selected through league switches and searches", async ({ page }) => {
  await page.goto("/league?league=58005&gw=3");
  await expect(page.getByText("GW3 is not finalized", { exact: false })).toBeVisible();
  await expect(page.locator(".league-switcher").getByRole("link", { name: /Overall IFE/ })).toHaveAttribute("href", "/league?league=131997&gw=3");
  await page.getByPlaceholder("Search manager, team or FPL ID").fill("Test FC");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await expect(page).toHaveURL(/gw=3/);
});

test("mobile research More drawer can close, reopen, escape and navigate", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/settings");
  const more = page.getByRole("navigation", { name: "Mobile navigation" }).getByRole("button", { name: "More" });
  await expect(page.locator("#mobile-more-menu")).toHaveCount(0);
  await expect(more).toHaveAttribute("aria-expanded", "false");
  await more.click();
  await expect(page.locator("#mobile-more-menu")).toBeVisible();
  await more.click();
  await expect(page.locator("#mobile-more-menu")).toHaveCount(0);
  await more.click();
  await page.keyboard.press("Escape");
  await expect(page.locator("#mobile-more-menu")).toHaveCount(0);
  await more.click();
  await page.locator("#mobile-more-menu").getByRole("link", { name: "Review" }).click();
  await expect(page).toHaveURL(/\/journal$/);
  await expect(more).toHaveAttribute("aria-expanded", "false");
});

test("missing journal record shows a useful state instead of crashing", async ({ page }) => {
  const response = await page.goto("/journal/2026-27/gw/1");
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "GW1 has not been archived" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Back to season timeline" })).toBeVisible();
});

test("This Week uses JSON fallback if the serverless NDJSON endpoint fails", async ({ page }) => {
  await page.route("**/api/week?**", route => route.fulfill({ status: 503, body: "stream not available" }));
  const response = await page.goto("/this-week");
  expect(response?.status()).toBe(200);
  await expect(page.getByRole("heading", { name: "6 points to the top-10% cutoff" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Your personal plan stays private" })).toBeVisible();
});

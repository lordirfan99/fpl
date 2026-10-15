import { test, expect } from '@playwright/test';

const routes = ['/v2', '/v2/team', '/v2/league?manager=2797967', '/v2/elite',
  '/v2/players', '/v2/compare', '/v2/planner', '/v2/journal?gw=3'];

for (const width of [320, 390, 768, 1280]) {
  test(`V2 retains all fields without horizontal scrolling at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    for (const route of routes) {
      await page.goto(route);
      await expect(page.locator('h1')).toBeVisible();
      for (const details of await page.locator('details').all()) await details.evaluate(el => el.setAttribute('open', ''));
      const overflow = await page.evaluate(() => {
        const elements = [document.documentElement, ...document.querySelectorAll('.fplv2-table-wrap, .fplv2-pitch, .fplv2-bench, .fplv2-toolbar')];
        return elements.filter(el => el.scrollWidth > el.clientWidth + 1).map(el => el.className || el.tagName);
      });
      expect(overflow, route).toEqual([]);
      for (const cell of await page.locator('td').all()) {
        await expect(cell).toBeVisible();
        await expect(cell).toHaveAttribute('data-label', /.+/);
      }
      const nav = page.getByRole('navigation', { name: 'FPL V2 navigation' });
      await expect(nav.getByRole('link')).toHaveCount(8);
      for (const link of await nav.getByRole('link').all()) {
        const box = await link.boundingBox();
        expect(box!.x).toBeGreaterThanOrEqual(0);
        expect(box!.x + box!.width).toBeLessThanOrEqual(width + 1);
        expect(box!.height).toBeGreaterThanOrEqual(44);
      }
      await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
    }
  });
}

test('mobile team shows all 15 picks and search preserves complete player records', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/v2/team');
  await expect(page.locator('.fplv2-pitch .fplv2-pitch-player')).toHaveCount(11);
  await expect(page.locator('.fplv2-bench .fplv2-pitch-player')).toHaveCount(4);
  await expect(page.locator('.fplv2-shirt b').first()).toHaveText('C');
  await page.screenshot({ path: testInfo.outputPath('team-mobile.png'), fullPage: true });
  await page.getByText('All player details', { exact: true }).click();
  await expect(page.getByRole('row')).toHaveCount(16);
  await page.goto('/v2/players');
  await page.getByRole('searchbox', { name: 'Player search' }).fill('Test Player 16');
  await page.getByRole('button', { name: 'Apply filters' }).click();
  await expect(page.locator('tbody tr')).toHaveCount(1);
  await expect(page.locator('td[data-label="Price"]')).toBeVisible();
  await page.goto('/v2/elite');
  await expect(page.getByRole('figure', { name: 'Elite captain share' })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('elite-mobile.png'), fullPage: true });
  await page.goto('/v2/planner');
  await expect(page.locator('.fplv2-fdr').first()).toBeVisible();
});

import { test,expect } from '@playwright/test';
const routes = [['/this-week','This gameweek'],['/planner','Explore the next five gameweeks'],['/league','KK Old Boys'],
  ['/players','Player market'],['/v5-lab','Models'],['/journal','Decision Journal'],['/my-team','Test FC 2797967'],
  ['/assistant','Your next deadline, made clear'],['/transfers','Transfers & chips'],['/elite','KK Old Boys · Top 5%'],
  ['/analytics','KK Old Boys data lab'],['/compare','Compare managers'],['/settings','Your dashboard preferences']];
for (const [route,heading] of routes) test(`static data renders ${route} with no reachable VM`,async({page})=>{
  await page.goto(route);
  await expect(page.getByRole('heading',{name:heading,exact:true})).toBeVisible();
  await expect(page.getByText('This view could not load',{exact:true})).toHaveCount(0);
  await expect(page.getByText('Data: Scheduled public snapshots; not live scores',{exact:true})).toBeVisible();
});
test('search, comparison and both leagues use the static dataset',async({page})=>{
  await page.goto('/league?league=131997&q=101010');
  await expect(page.locator('tbody tr')).toHaveCount(1);
  await page.locator('tbody tr').click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.goto('/compare?league=131997&left=2797967&right=101010');
  await expect(page.getByRole('heading',{name:'Compare managers',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Test FC 101010',exact:true})).toBeVisible();
});
test('anonymous personal plan stays locked and capture labels stay truthful',async({page})=>{
  await page.goto('/this-week');
  await expect(page.getByRole('link',{name:'Unlock private plan'})).toBeVisible();
  await page.goto('/my-team');
  await expect(page.getByText('GW3 official public capture',{exact:true})).toBeVisible();
  await expect(page.getByText('Check your verified plan',{exact:true})).toBeVisible();
});

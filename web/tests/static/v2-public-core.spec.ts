import { test,expect } from '@playwright/test';

const screens: Array<[string,string]> = [
 ['/v2','Your next FPL decision'],
 ['/v2/team','My FPL team'],
 ['/v2/league','Complete standings'],
 ['/v2/elite','The top five percent'],
 ['/v2/players','Official recorded player pool'],
];
for(const [route,heading] of screens){
 test(`V2 public route ${route} renders with unreachable VM`,async({page})=>{
  const errors:string[]=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(route);
  await expect(page.getByRole('heading',{name:heading,exact:true})).toBeVisible();
  await expect(page.getByText('This view could not load',{exact:true})).toHaveCount(0);
  expect(errors).toEqual([]);
 });
}
test('V2 archived league rejects missing weeks truthfully, keeps navigation clickable',async({page})=>{
 await page.goto('/v2/league?league=131997&gw=6');
 await expect(page.getByText(/GW6 is not archived/)).toBeVisible();
 await expect(page.getByRole('link',{name:'GW3'})).toBeVisible();
 await page.getByRole('link',{name:'GW3'}).click();
 await expect(page.getByText(/Recorded Gameweek/)).toBeVisible();
 await expect(page.getByText('This view could not load',{exact:true})).toHaveCount(0);
});
test('V2 league search is server rendered with predictable URL state',async({page})=>{
 await page.goto('/v2/league?league=58005');
 await page.getByRole('textbox',{name:'Search manager'}).fill('2797967');
 await page.getByRole('button',{name:'Search'}).click();
 await expect(page).toHaveURL(/q=2797967/);
 await expect(page.locator('tbody tr')).toHaveCount(1);
 await page.getByRole('link',{name:'Test FC 2797967'}).click();
 await expect(page.getByText(/Full squad/)).toBeVisible();
});
test('V2 public routes never ask unreachable private VM for public page content',async({page})=>{
 const remote:string[]=[];
 page.on('request',r=>{if(r.url().includes('4196'))remote.push(r.url())});
 await page.goto('/v2/elite');
 await expect(page.getByRole('heading',{name:'The top five percent'})).toBeVisible();
 await page.goto('/v2/players');
 await expect(page.getByRole('heading',{name:'Official recorded player pool'})).toBeVisible();
 expect(remote).toEqual([]);
});

// Read-only production audit. No credentials, no private FPL actions.
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const base = 'https://fpl-scout-intelligence.netlify.app';
const routes = ['/','/this-week','/planner','/league','/players','/v5-lab','/journal',
  '/my-team','/assistant','/transfers','/elite','/analytics','/compare','/settings',
  '/league?league=131997','/compare?league=131997','/elite?league=131997',
  '/league?league=131997&gw=6','/compare?league=131997&gw=6',
  '/elite?league=131997&gw=6','/planner?gw=7'];
const failurePatterns = [
  /this view could not load/i, /application error/i, /internal server error/i,
  /client-side exception/i, /league evidence temporarily unavailable/i,
  /no league snapshot available/i, /something went wrong/i,
  /failed to fetch/i, /error loading/i
];
const results = [];
await mkdir('production-audit-artifacts', { recursive: true });
const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
try {
  for (const profile of [
    {name:'desktop', viewport:{width:1365,height:900},isMobile:false,hasTouch:false},
    {name:'mobile', viewport:{width:390,height:844},isMobile:true,hasTouch:true}
  ]) {
    const context = await browser.newContext({viewport:profile.viewport,isMobile:profile.isMobile,hasTouch:profile.hasTouch});
    for (const route of routes) {
      const page = await context.newPage(), errors=[], networkFailures=[], httpFailures=[];
      page.on('pageerror',e=>errors.push(String(e.message).slice(0,250)));
      page.on('console',m=>{if(m.type()==='error') errors.push('console:'+m.text().slice(0,220));});
      page.on('requestfailed',r=>{if(!/google-analytics|favicon|image\//i.test(r.url())) networkFailures.push(r.url().split('?')[0]+' '+r.failure()?.errorText);});
      page.on('response',r=>{if(r.status()>=400&&!/\.jpg|\.png|\.webp|favicon|\/image/i.test(r.url())) httpFailures.push(r.status()+' '+r.url().split('?')[0]);});
      const entry={profile:profile.name,route,status:null,heading:null,bodyLength:0,issueTexts:[],errors,networkFailures,httpFailures,interactions:[]};
      try {
        const resp=await page.goto(base+route,{waitUntil:'domcontentloaded',timeout:45000});
        entry.status=resp?.status()??null;
        await page.waitForTimeout(2400);
        entry.heading=await page.locator('h1').first().textContent({timeout:8000}).catch(()=>null);
        const body=(await page.locator('body').innerText({timeout:5000}).catch(()=>''))||'';
        entry.bodyLength=body.length;
        entry.issueTexts=failurePatterns.filter(re=>re.test(body)).map(re=>re.source);
        if(route==='/this-week') {
          await page.waitForTimeout(8500);
          const lateBody=await page.locator('body').innerText().catch(()=>body);
          entry.issueTexts=[...new Set([...entry.issueTexts,...failurePatterns.filter(re=>re.test(lateBody)).map(re=>re.source)])];
          entry.interactions.push('league-state:'+ (lateBody.includes('Recorded rival picks')?'ready':lateBody.includes('Loading recorded league evidence')?'stuck-loading':lateBody.includes('League evidence temporarily unavailable')?'unavailable':'other'));
          const link=page.getByRole('link',{name:/Overall IFE/i}).first();
          if(await link.count()) {
            await link.click({timeout:12000}).catch(e=>entry.interactions.push('switch-league-error:'+e.message.slice(0,90)));
            await page.waitForTimeout(1200);
            entry.interactions.push('after-switch:'+new URL(page.url()).searchParams.get('league'));
          }
        }
        if(route==='/league') {
          const search=page.getByPlaceholder('Search manager, team or FPL ID');
          if(await search.count()) {
            await search.fill('2797967');
            await page.getByRole('button',{name:'Search',exact:true}).click({timeout:10000});
            await page.waitForTimeout(1600);
            entry.interactions.push('search-query:'+new URL(page.url()).searchParams.get('q'));
            entry.interactions.push('table-rows:'+await page.locator('tbody tr').count());
          }
        }
        if(route==='/players') {
          const inputs=page.locator('input');entry.interactions.push('inputs:'+await inputs.count());
        }
        if(route==='/')entry.interactions.push('home-url:'+page.url());
        if(profile.name==='mobile' && route==='/elite'){
          const more=page.getByRole('button',{name:'More'});
          if(await more.count()) {
            await more.click({timeout:8000});
            entry.interactions.push('more-menu-visible:'+await page.locator('#mobile-more-menu').isVisible());
            const leagueLink=page.locator('#mobile-more-menu a[href="/league"]');
            if(await leagueLink.count()){await leagueLink.click({timeout:10000}); await page.waitForTimeout(700);entry.interactions.push('more-nav-url:'+new URL(page.url()).pathname);}
          }
        }
        const marker=route.slice(1).replace(/[^a-z0-9]+/gi,'-')||'home';
        await page.screenshot({path:path.join('production-audit-artifacts',profile.name+'-'+marker+'.png'),fullPage:false,timeout:20000}).catch(e=>entry.interactions.push('screenshot-error:'+e.message.slice(0,90)));
      }catch(e){entry.errors.push('audit-exception:'+String(e.message).slice(0,250));}
      results.push(entry);
      console.log('AUDIT '+JSON.stringify(entry));
      await page.close();
    }
    await context.close();
  }
} finally { await browser.close(); }
await writeFile('production-audit-artifacts/results.json',JSON.stringify(results,null,2));
const broken=results.filter(x=>!x.heading||!x.status||x.status>=500||x.issueTexts.length||x.errors.some(e=>/uncaught|exception|failed to fetch|hydration|error loading|internal server/i.test(e))||x.interactions.some(i=>/stuck-loading|unavailable|error:|more-menu-visible:false/.test(i)));
console.log('SUMMARY '+JSON.stringify({total:results.length,flagged:broken.length,flaggedRoutes:broken.map(x=>x.profile+' '+x.route),http500:results.filter(x=>x.status>=500).length}));
if(broken.length) process.exitCode=1;

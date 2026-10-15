import { chromium } from "@playwright/test";
const origin=process.env.FPL_V2_PREVIEW_URL;
if(!origin?.startsWith("https://")||!origin.includes("--fpl-scout-intelligence.netlify.app"))throw new Error("Preview site URL required; refusing production");
const routes=[
 ["/","This gameweek"],
 ["/v2","Your next FPL decision"],
 ["/v2/team","My FPL team"],
 ["/v2/league","Complete standings"],
 ["/v2/league?league=131997","Complete standings"],
 ["/v2/league?league=131997&gw=6","Complete standings"],
 ["/v2/elite","The top five percent"],
 ["/v2/elite?league=131997","The top five percent"],
 ["/v2/elite?league=131997&gw=6","The top five percent"],
 ["/v2/players","Official recorded player pool"],
 ["/v2/compare?league=131997","Compare recorded managers"],
 ["/v2/planner","Five gameweeks ahead"],
 ["/v2/journal","Season decision journal"],
 ["/v2/journal?gw=7","Season decision journal"],
];
const browser=await chromium.launch({headless:true,args:["--no-sandbox"]});
const errors=[];
try{
 for(const viewport of [{width:1365,height:900},{width:390,height:844}]){
  const context=await browser.newContext({viewport});
  for(const [route,heading] of routes){
   const page=await context.newPage(),exceptions=[];
   page.on("pageerror",e=>exceptions.push(e.message.slice(0,240)));
   const response=await page.goto(origin+route,{waitUntil:"domcontentloaded",timeout:35000}).catch(e=>{exceptions.push(e.message);return null});
   await page.getByRole("heading",{name:heading,exact:true}).waitFor({timeout:12000}).catch(e=>exceptions.push("main-heading: "+e.message.slice(0,100)));
   const body=await page.locator("body").innerText().catch(()=>"");
   if(response?.status()!==200)exceptions.push("HTTP status "+(response?.status()||"none"));
   if(body.includes("This view could not load")||body.includes("Application error"))exceptions.push("Visible application error");
   if(route.includes("gw=6")&&route.includes("league=131997")){
     // GW6 can transition from unavailable to a real frozen archive during
     // hourly publication. Accept EITHER truthful state, never wrong-week fallback.
     const missing=body.includes("GW6 is not archived");
     const exact=body.includes("Verified finalized archive: GW6. Requested GW6.");
     if(!missing&&!exact)exceptions.push("GW6 neither verified nor explicitly unavailable");
   }
   if(route==="/v2/league"&&!body.includes("Manager directory"))exceptions.push("League has no directory");
   if(route==="/v2/players"&&!body.includes("Player finder"))exceptions.push("Player finder missing");
   if(route==="/v2/compare?league=131997"&&!body.includes("Select two managers"))exceptions.push("Comparison selector missing");
   if(viewport.width===390 && route==="/v2" && !body.includes("FPL Scout"))exceptions.push("Mobile shell absent");
   // Test actual browser interactions, not just an HTTP 200 or heading.
   try{
     if(route==="/") {
       if(new URL(page.url()).pathname!=="/this-week")exceptions.push("Homepage did not route to V1");
     }
     if(route==="/v2") {
       await page.locator('.fplv2-header nav a[href="/v2/league"]').click({timeout:10000});
       await page.waitForURL(/\/v2\/league/, {timeout:12000});
       if(!page.url().includes("/v2/league"))exceptions.push("Primary navigation did not open League");
     }
     if(route==="/v2/league") {
       const search=page.getByRole("searchbox",{name:"Search manager"});
       await search.fill("2797967");
       await page.getByRole("button",{name:"Search",exact:true}).click({timeout:10000});
       await page.waitForURL(/q=2797967/, {timeout:12000});
       if(await page.locator("tbody tr").count()!==1)exceptions.push("League manager search did not filter to one result");
     }
     if(route==="/v2/elite") {
       await page.locator('.fplv2-tabs a[href="/v2/elite?league=131997"]').click({timeout:10000});
       await page.waitForURL(/league=131997/, {timeout:12000});
     }
     if(route==="/v2/players") {
       await page.getByRole("searchbox",{name:"Player search"}).fill("a");
       await page.getByRole("button",{name:"Apply filters"}).click({timeout:10000});
       await page.waitForURL(/q=a/, {timeout:12000});
     }
     if(route==="/v2/compare?league=131997") {
       await page.getByRole("button",{name:"Compare",exact:true}).click({timeout:10000});
       await page.waitForURL(/left=\d+.*right=\d+|right=\d+.*left=\d+/, {timeout:12000});
     }
   }catch(e){exceptions.push("interaction: "+String(e.message).slice(0,220));}
   if(exceptions.length)errors.push({viewport:viewport.width,route,exceptions});
   console.log(JSON.stringify({viewport:viewport.width,route,status:response?.status(),heading:heading,errors:exceptions}));
   await page.close();
  }
  await context.close();
 }
}finally{await browser.close();}
console.log("PREVIEW_SMOKE_SUMMARY",JSON.stringify({observations:routes.length*2,errors:errors.length,details:errors}));
if(errors.length)process.exitCode=1;

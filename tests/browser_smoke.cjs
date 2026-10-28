// Run with NODE_PATH pointing to an installed Playwright package.
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');
let browser;

(async () => {
  const base = process.env.AI_TOWN_TEST_URL || 'http://127.0.0.1:8301';
  browser = await chromium.launch({channel:'msedge', headless:true});
  const page = await browser.newPage({viewport:{width:1440,height:1000}, deviceScaleFactor:1});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => {if(m.type()==='error'&&!m.text().includes('favicon'))errors.push(m.text());});
  const output=path.resolve(__dirname,'../docs/previews');fs.mkdirSync(output,{recursive:true});
  await page.request.post(base+'/api/control',{data:{cmd:'reset'}});
  await page.goto(base);
  await page.locator('.pcard').first().waitFor();
  await page.screenshot({path:path.join(output,'house.png'),fullPage:true,animations:'disabled'});
  await page.locator('[data-mode="bicycle"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-mode="bicycle"]').classList.contains('active'));
  await page.locator('#sceneTabs [data-scene="town"]').click();
  await page.waitForFunction(()=>document.querySelector('#cv').width===728);
  await page.screenshot({path:path.join(output,'town.png'),fullPage:true,animations:'disabled'});
  await page.locator('#btnGuide').click();
  if(!await page.locator('#guideOverlay').evaluate(e=>e.classList.contains('show')))throw Error('Guide did not open');
  await page.locator('#guideBox .ghead button').click();
  for(const tab of ['relations','logs','api','settings','players'])await page.locator(`nav [data-panel="${tab}"]`).click();
  await page.locator('#btnStep').click();
  await page.waitForFunction(()=>document.querySelector('#clockbox').textContent.includes('08:10'));
  await page.locator('#btnStart').click();
  await page.locator('#btnPause:not([disabled])').waitFor();
  await page.locator('#btnPause').click();
  await page.locator('#btnStart:not([disabled])').waitFor();
  // 模型或日志中的 HTML 必须按纯文字呈现。
  await page.evaluate(()=>{allEvents=[{type:'dialogue',day:1,clock:'00:00',text:'<img src=x onerror="window.__xss=1">'}];renderEvents();});
  if(await page.locator('#logList img').count())throw Error('Unsafe HTML in log');
  await page.setViewportSize({width:390,height:844});
  await page.locator('#sceneTabs [data-scene="house"]').click();
  await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true,animations:'disabled'});
  const geometry=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,canvas:document.querySelector('#cv').getBoundingClientRect().toJSON()}));
  if(geometry.scrollWidth>geometry.width)throw Error('Mobile overflow: '+JSON.stringify(geometry));
  if(errors.length)throw Error(errors.join('\n'));
  console.log(JSON.stringify({result:'passed',screenshots:output,mobile:geometry,consoleErrors:errors},null,2));
  await browser.close();
})().catch(async e=>{console.error(e);if(browser)await browser.close();process.exitCode=1;});

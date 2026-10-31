const {chromium}=require('playwright');
const path=require('path');

(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1280,height:900}});
    const errors=[];let saved=null;
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/api/mechanics',route=>{
      if(route.request().method()==='PUT'){
        saved=route.request().postDataJSON();
        return route.fulfill({json:{ok:true}});
      }
      return route.continue();
    });
    await page.goto('http://127.0.0.1:8300/');
    await page.locator('#btnMechanics').click();
    await page.locator('#mechanicsDialog summary').filter({hasText:'所有活动'}).click();
    const cook=page.locator('#mechanicsActivities details[data-activity-row="cook"]');
    await cook.locator('summary').first().click();
    await cook.locator('[data-activity-scene="town"]').check();
    await cook.locator('.mechanics-place-menu').filter({hasText:'室外地点'}).locator('summary').click();
    await cook.locator('[data-activity-location="town:J"]').check();
    await cook.locator('[data-position-option="near_player"]').check();
    await cook.locator('[data-place-join="cook"]').click();
    if(!await cook.locator('[data-place-join="cook"]').textContent().then(text=>text.trim()==='且'))throw Error('AND toggle did not change');
    await cook.screenshot({path:path.resolve('docs/previews/activity-place-editor.png')});
    await page.locator('#saveMechanics').click();
    await page.waitForFunction(()=>document.querySelector('#eventNotifications')?.textContent.includes('机制已保存'));
    if(!saved)throw Error('Mechanics were not submitted');
    const row=saved.activity_catalog.cook;
    if(row.place_join!=='and'||row.target_mode!=='required'||!row.position_options.includes('near_player')||!row.places.town.includes('town:J')||!row.places.house.includes('house:K'))throw Error('Place rules were not preserved: '+JSON.stringify({join:row.place_join,mode:row.target_mode,options:row.position_options,places:row.places}));
    await page.locator('#closeMechanics').click();
    await page.locator('#mechanicsDialog').waitFor({state:'hidden'});
    await page.locator('nav [data-panel="replays"]').click();
    await page.evaluate(()=>{
      replayMode=true;replayIndex=0;replayTotal=6;
      document.querySelector('#replayControls').hidden=false;
      window.showReplayFrame=async index=>{replayIndex=index;};
    });
    await page.locator('#btnReplaySpeed').click();
    if(!await page.locator('#btnReplaySpeed').textContent().then(text=>text.includes('2×')))throw Error('Replay speed label did not update');
    await page.locator('#btnPlayReplay').click();
    await page.waitForFunction(()=>replayIndex>=2,{timeout:2500});
    await page.locator('#btnPlayReplay').click();
    if(errors.length)throw Error(errors.join('; '));
    console.log('place editor and replay speed passed');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});

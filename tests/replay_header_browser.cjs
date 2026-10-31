const {chromium}=require('playwright');
const path=require('path');

(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:2200,height:900}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/api/control',route=>route.fulfill({json:{ok:true,running:false}}));
    await page.goto('http://127.0.0.1:8300/');
    await page.locator('nav [data-panel="replays"]').click();
    const entry=page.locator('#saveEntries details').first();
    await entry.locator('summary').click();
    await entry.locator('[data-action="replay"]').click();
    await page.waitForFunction(()=>document.querySelector('#replayPosition').textContent.includes('帧'));
    if(!await page.locator('header #replayControls').isVisible())throw Error('Replay controls not in the header');
    const layout=await page.evaluate(()=>{
      const title=document.querySelector('header h1').getBoundingClientRect();
      const replay=document.querySelector('#replayControls').getBoundingClientRect();
      const clock=document.querySelector('#clockbox').getBoundingClientRect();
      const seek=document.querySelector('#replaySeek').getBoundingClientRect();
      const buttons=document.querySelector('.header-replay-actions').getBoundingClientRect();
      const replayButton=document.querySelector('#btnReplaySpeed').getBoundingClientRect();
      const mechanismButton=document.querySelector('#btnMechanics').getBoundingClientRect();
      return {titleRight:title.right,replayLeft:replay.left,replayRight:replay.right,clockLeft:clock.left,
        seekRight:seek.right,buttonsLeft:buttons.left,replayButtonHeight:replayButton.height,mechanismButtonHeight:mechanismButton.height};
    });
    if(layout.replayLeft<layout.titleRight-1||layout.replayRight>layout.clockLeft+1)throw Error('Header replay controls overlap other controls: '+JSON.stringify(layout));
    if(layout.buttonsLeft<layout.seekRight||Math.abs(layout.replayButtonHeight-layout.mechanismButtonHeight)>2)throw Error('Replay buttons are not beside the seek bar at standard size: '+JSON.stringify(layout));
    await page.locator('header').screenshot({path:path.resolve('docs/previews/replay-header.png')});
    await page.locator('nav [data-panel="players"]').click();
    if(!await page.locator('#replayControls').isVisible())throw Error('Replay controls disappeared outside archive tab');
    await page.locator('#btnReplaySpeed').click();
    if(!await page.locator('#btnReplaySpeed').textContent().then(text=>text.includes('2×')))throw Error('Replay speed stopped working');
    await page.locator('#btnStepReplay').click();
    await page.waitForFunction(()=>document.querySelector('#replayPosition').textContent.startsWith('第 2 /'));
    await page.setViewportSize({width:1000,height:844});
    const medium=await page.evaluate(()=>({header:document.querySelector('header').getBoundingClientRect().bottom,
      replay:document.querySelector('#replayControls').getBoundingClientRect().bottom}));
    if(medium.replay>medium.header+1)throw Error('Replay controls extend below medium header: '+JSON.stringify(medium));
    await page.setViewportSize({width:390,height:844});
    const mobile=await page.evaluate(()=>({header:document.querySelector('header').getBoundingClientRect().width,
      replay:document.querySelector('#replayControls').getBoundingClientRect().width,
      visible:document.querySelector('#btnExitReplay').getBoundingClientRect().right<=innerWidth+1}));
    if(!mobile.visible||mobile.replay>mobile.header+1)throw Error('Mobile replay controls overflow: '+JSON.stringify(mobile));
    await page.locator('#btnExitReplay').click();
    if(await page.locator('#replayControls').isVisible())throw Error('Replay controls remain visible after exit');
    if(errors.length)throw Error(errors.join('; '));
    console.log('header replay controls passed');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});

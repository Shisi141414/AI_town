const {chromium}=require('playwright');
const path=require('path');
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const base='http://127.0.0.1:8301';
 await page.request.post(base+'/api/control',{data:{cmd:'reset'}});
 await page.goto(base);await page.locator('.pcard').first().waitFor();
 await page.locator('nav [data-panel="replays"]').click();
 await page.locator('#btnSaveWorld').click();
 await page.waitForFunction(()=>document.querySelector('#currentSave').textContent.includes('2026'));
 const old=(await (await page.request.get(base+'/api/state')).json()).save_id;
 await page.locator('#btnStep').click();
 await page.waitForFunction(()=>document.querySelector('#clockbox').textContent.includes('08:10'));
 page.on('dialog',d=>d.accept());
 await page.locator('#btnReset').click();
 await page.waitForFunction(()=>document.querySelector('#clockbox').textContent.includes('08:00'));
 const fresh=await (await page.request.get(base+'/api/state')).json();
 if(fresh.save_id===old||fresh.players.some(p=>Object.values(p.memory_counts).some(Boolean)))throw Error('New save retained memories');
 await page.locator('#btnRefreshSaves').click();
 const entry=page.locator('#saveEntries details[data-id="'+old+'"]');
 await entry.locator('summary').click();await entry.locator('[data-action="continue"]').click();
 await page.waitForFunction(()=>document.querySelector('#clockbox').textContent.includes('08:10'));
 const loaded=await (await page.request.get(base+'/api/state')).json();
 if([old,fresh.save_id].includes(loaded.save_id))throw Error('Not a new save branch');
 await page.locator('nav [data-panel="players"]').click();
 await page.evaluate(()=>{
   players[0].gender='男';players[1].gender='女';renderPlayers();draw();
   const sample=document.createElement('canvas');sample.width=320;sample.height=160;
   sample.style.cssText='position:fixed;left:20px;top:20px;z-index:9999;border:4px solid #79583f;background:#fff5dd';
   document.body.appendChild(sample);sample.id='spriteSample';
   const g=sample.getContext('2d');g.imageSmoothingEnabled=false;g.fillStyle='#fff5dd';g.fillRect(0,0,320,160);
   for(let i=0;i<2;i++){
     const p=players[i],sx=p.x*32,sy=p.y*32;
     g.drawImage(cv,sx-8,sy-16,48,64,25+i*150,0,96,128);
   }
   g.fillStyle='#79583f';g.font='16px sans-serif';g.fillText('男性',55,148);g.fillText('女性',205,148);
 });
 await page.locator('#spriteSample').screenshot({path:path.resolve('docs/previews/characters.png')});
 if(errors.length)throw Error(errors.join('\n'));
 console.log(JSON.stringify({result:'passed',resetEmptyMemory:true,restoreBranch:true,genderSpritePreview:true}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});

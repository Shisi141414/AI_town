const {chromium}=require('playwright'),path=require('path');
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true});try{
 const p=await browser.newPage({viewport:{width:1500,height:1080}}),errors=[];
 p.on('pageerror',e=>errors.push(e.message));const base='http://127.0.0.1:8301';
 const catalog=await p.request.get(base+'/api/town/buildings').then(r=>r.json());catalog.U.parameters.study_computer.work_xp_per_hour=3;
 await p.request.put(base+'/api/town/buildings/U',{data:{parameters:catalog.U.parameters}});
 await p.goto(base);await p.locator('.pcard').first().waitFor();await p.locator('#sceneTabs [data-scene="town"]').click();
 const cv=p.locator('#cv'),b=await cv.boundingBox();
 await cv.click({position:{x:b.width*4.5/26,y:b.height*22.5/26}});
 await p.waitForFunction(()=>document.querySelector('#buildingName').textContent==='池塘'&&document.querySelector('#buildingDialog').open);
 if(await p.locator('.building-service').count()!==2)throw Error('Pond activities missing');
 await p.locator('#closeBuilding').click();
 await cv.click({position:{x:b.width*13.5/26,y:b.height*10.5/26}});
 await p.waitForFunction(()=>document.querySelector('#buildingName').textContent==='小镇花园');
 if(await p.locator('.building-service').count()!==3)throw Error('Garden activities missing');await p.locator('#closeBuilding').click();
 await p.evaluate(async()=>openTownBuilding('O'));
 const engineer=p.locator('.building-service').filter({has:p.locator('input[data-service="job_programmer_2"][data-field="min_level"]')});
 await engineer.locator('summary').click();
 if(await engineer.locator('input[data-field="min_level"]').inputValue()!=='4')throw Error('Engineer requirement wrong');
 if(!await engineer.textContent().then(s=>s.includes('二级软件工程师')&&s.includes('工作经验3')))throw Error('Grade info missing');
 await p.screenshot({path:path.resolve('docs/previews/career-grades.png'),fullPage:true});await p.locator('#closeBuilding').click();
 await p.evaluate(async()=>openTownBuilding('U'));
 const teacher=p.locator('.building-service').filter({has:p.locator('input[data-service="job_teacher_computer"][data-field="min_level"]')});
 if(!await teacher.textContent().then(s=>s.includes('计算机3级')&&s.includes('工作经验6')))throw Error('Teacher rules missing');
 await p.locator('input[data-service="study_computer"][data-field="work_xp_per_hour"]').fill('4');
 await p.locator('#saveBuilding').click();await p.waitForFunction(()=>townConfig.U.study_computer.work_xp_per_hour===4);
 await p.waitForFunction(()=>Array.from(document.querySelectorAll('.building-service')).some(el=>el.querySelector('input[data-service="job_teacher_computer"]')&&el.textContent.includes('工作经验8')));
 await p.locator('#closeBuilding').click();await p.evaluate(async()=>openTownBuilding('O'));
 if(!await p.locator('#buildingServices').textContent().then(s=>s.includes('工作经验4')))throw Error('Ordinary job rate stale');
 await p.locator('#closeBuilding').click();
 const geometry=await p.evaluate(()=>{
   const fills=[],proto=CanvasRenderingContext2D.prototype,original=proto.fillRect;
   proto.fillRect=function(x,y,w,h){if(this.canvas.width===416)fills.push({x,y,w,h,color:this.fillStyle});return original.call(this,x,y,w,h)};
   const rows=MAPS.town.map(r=>typeof r==='string'?r.split(''):[...r]);rows[24][10]=rows[24][10]==='.'?'J':'.';
   try{PixelScene.draw(ctx,rows,tilePx(),'town',players,vehicles,null)}finally{proto.fillRect=original}
   const windows=fills.filter(f=>f.color==='#8b8469'),doors=fills.filter(f=>f.color==='#6e634f');
   let checked=0;
   for(const door of doors){
     // 影院门位于中央南部，维持原有画法。
     if(door.x>=160&&door.x<272&&door.y>=224&&door.y<288)continue;
     const nearby=windows.filter(w=>Math.abs((w.y+w.h)-(door.y+door.h))<=10&&Math.abs(w.x-door.x)<52);
     if(!nearby.length)throw Error('No windows by door');
     for(const w of nearby){if(w.x<door.x+door.w&&w.x+w.w>door.x)throw Error('Window overlaps door');}
     checked++;
   }
   if(checked!==7)throw Error('Did not verify every other facade: '+checked);
   draw();return {facadesChecked:checked};
 });
 await cv.screenshot({path:path.resolve('docs/previews/town-windows-leisure.png')});
 await p.setViewportSize({width:390,height:844});await p.evaluate(async()=>openTownBuilding('U'));
 if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow');
 if(errors.length)throw Error(errors.join('\n'));
 console.log(JSON.stringify({result:'passed',pondClick:true,gardenClick:true,seniorJobs:true,teacherRatioAfterEdit:true,mobile:true,...geometry}));
 }finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});

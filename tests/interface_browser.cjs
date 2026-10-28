const {chromium}=require('playwright');const path=require('path');
(async()=>{const b=await chromium.launch({channel:'msedge',headless:true});try{
 const p=await b.newPage({viewport:{width:1440,height:1000}}),errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.goto('http://127.0.0.1:8301/');await p.locator('.pcard').first().waitFor();await p.waitForFunction(()=>typeof ws!=='undefined'&&!!ws);
 const checks=await p.evaluate(()=>{
  ws.onmessage=null;
  players=players.slice(0,2);players[0].model='provider/Full-Model-Name-Visible';players[1].model='Other-Model';
  players[0].affinity={[players[1].name]:90};players[1].affinity={[players[0].name]:10};renderPlayers();
  const head=document.querySelector('.pcard .head b');
  if(!head.nextElementSibling.classList.contains('model')||head.nextElementSibling.textContent!==players[0].model)throw Error('Model not shown after name');
  const g=document.getElementById('relCanvas').getContext('2d'),original=g.stroke,calls=[];
  g.stroke=function(...args){calls.push(this.strokeStyle);return original.apply(this,args)};
  drawRelations();g.stroke=original;
  if(calls[0]!=='#27ae60'||calls[1]!=='#c0392b')throw Error('Directed relation colors incorrect '+calls);
  const sample=[{day:1,clock:'08:00',scope:'user',type:'memory',text:'小舟整合了记忆'},{day:1,clock:'08:00',scope:'game',type:'activity',text:'小舟开始吃饭'}];
  eventScope='game';renderEvents(sample);
  if(document.querySelectorAll('#logList .ev').length!==1||!document.querySelector('#logList .meta').textContent.includes('事件'))throw Error('Game tab scope incorrect');
  eventScope='user';renderEvents(sample);
  if(document.querySelectorAll('#logList .ev').length!==1||!document.querySelector('#logList .meta').textContent.includes('提示'))throw Error('User tab scope incorrect');
  return {fullModelAfterName:true,directedColors:calls.slice(0,2),noticeLabels:true};
 });
 await p.locator('nav [data-panel="relations"]').click();
 await p.screenshot({path:path.resolve('docs/previews/directed-relations.png'),fullPage:true});
 await p.setViewportSize({width:390,height:844});await p.locator('nav [data-panel="players"]').click();
 if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow');
 if(errors.length)throw Error(errors.join('\n'));console.log(JSON.stringify({result:'passed',...checks,consoleErrors:errors}));
 }finally{await b.close()}})().catch(e=>{console.error(e);process.exitCode=1});

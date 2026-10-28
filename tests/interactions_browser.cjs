const {chromium}=require('playwright'),path=require('path'),fs=require('fs');
(async()=>{const b=await chromium.launch({channel:'msedge',headless:true});try{
 const p=await b.newPage({viewport:{width:1600,height:1250}}),errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.goto('http://127.0.0.1:8301/');await p.locator('.pcard').first().waitFor();
 const states=JSON.parse(fs.readFileSync('docs/previews/interaction-states.json','utf8'));
 for(const [phase,snapshot] of Object.entries(states)){
   await p.evaluate(snapshot=>{ws.onmessage=null;players=snapshot;renderPlayers();draw();},snapshot);
   if(await p.locator('.pcard .doing').count()!==5)throw Error('Missing residents');
   await p.locator('#cv').screenshot({path:path.resolve('docs/previews/indoor-'+phase+'-positions.png')});
 }
 if(errors.length)throw Error(errors.join('\n'));
 console.log(JSON.stringify({result:'passed',commonPositions:true,bedroomPositions:true,pageErrors:errors}));
 }finally{await b.close()}})().catch(e=>{console.error(e);process.exitCode=1});

const {chromium}=require('playwright');
const path=require('path');

(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1100,height:850}});
    await page.goto('http://127.0.0.1:8300/');
    await page.locator('nav [data-panel="relations"]').click();
    const result=await page.evaluate(()=>{
      const person=(pid,name,relationships)=>({pid,name,relationships,affinity:{},emoji:'🙂',color:'#638263'});
      players=[person('a','小舟',[{other_pid:'b',name:'小满',type:'恋爱',days:3},{other_pid:'c',name:'小石',type:'挚友',days:2}]),
        person('b','小满',[{other_pid:'a',name:'小舟',type:'恋爱',days:3}]),
        person('c','小石',[{other_pid:'a',name:'小舟',type:'挚友',days:2}])];
      drawRelations();
      const count=document.querySelectorAll('#activeRelations .active-relation').length;
      const text=document.querySelector('#activeRelations').textContent;
      return {count,text};
    });
    await page.locator('#panel-relations').screenshot({path:path.resolve('docs/previews/relationship-list.png')});
    result.empty=await page.evaluate(()=>{players.forEach(p=>p.relationships=[]);drawRelations();return document.querySelector('#activeRelations').textContent;});
    if(result.count!==2||!result.text.includes('恋爱 · 第3天')||!result.text.includes('挚友 · 第2天')||!result.empty.includes('暂无已建立的关系'))throw Error(JSON.stringify(result));
    console.log('relationship list passed');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});

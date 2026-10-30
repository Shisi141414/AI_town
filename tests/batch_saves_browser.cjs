const {chromium}=require('playwright');

(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:900,height:800}});
    const base='http://127.0.0.1:8300';
    const ids=['2026-10-30_08-00-00_000001','2026-10-30_08-00-00_000002','2026-10-30_08-00-00_000003'];
    let items=ids.map((id,i)=>({id,name:`测试存档${i}`,favorite:false,day:1,clock:'08:00',residents:3,
      updated:new Date().toISOString(),reason:'测试',memory_count:0,context_complete:true}));
    let deleted=[];
    await page.route('**/api/saves',route=>route.fulfill({json:{current:ids[0],items}}));
    await page.route('**/api/saves/delete-selected',async route=>{
      deleted=route.request().postDataJSON().ids;
      items=items.filter(item=>!deleted.includes(item.id));
      await route.fulfill({json:{ok:true,deleted:deleted.length}});
    });
    await page.goto(base);
    await page.locator('nav [data-panel="replays"]').click();
    await page.locator('#saveEntries .archive-select').first().waitFor();
    if(!(await page.locator('#saveEntries .archive-select').first().isDisabled()))throw Error('Current save can be selected');
    await page.locator('#btnSelectAllSaves').click();
    if(await page.locator('#saveEntries .archive-select:checked').count()!==2)throw Error('Select all did not skip current save');
    await page.locator('#btnClearSaveSelection').click();
    if(await page.locator('#saveEntries .archive-select:checked').count())throw Error('Clear selection failed');
    await page.locator('#saveEntries .archive-select').nth(1).check();
    await page.locator('#saveEntries .archive-select').nth(2).check();
    page.once('dialog',dialog=>dialog.accept());
    await page.locator('#btnDeleteSelectedSaves').click();
    await page.waitForFunction(()=>document.querySelectorAll('#saveEntries .archive-select').length===1);
    if(deleted.join(',')!==ids.slice(1).join(','))throw Error('Wrong saves submitted for deletion');
    if(!(await page.locator('#btnDeleteSelectedSaves').isDisabled()))throw Error('Delete button still enabled');
    console.log('batch save selection and delete UI passed');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});

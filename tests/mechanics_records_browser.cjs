const {chromium}=require('playwright');

(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('dialog',dialog=>dialog.type()==='prompt'?dialog.accept('我的机制草稿'):dialog.accept());
  await page.goto('http://127.0.0.1:8301/');
  await page.locator('#btnMechanics').click();
  const field=page.locator('input[data-mechanic="rules.walk_speed"]');
  const original=Number(await field.inputValue());
  const originalTemplate=await page.locator('#mechanicsTemplate').inputValue();
  await page.locator('#mechanicsRules details').filter({has:field}).locator('summary').first().click();
  await field.fill(String(original+1));
  await page.locator('#saveMechanicsRecord').click();
  await page.locator('#mechanicsRecordsDialog[open]').waitFor();
  await page.locator('.mechanics-record-row').first().waitFor();
  if((await page.request.get('http://127.0.0.1:8301/api/mechanics').then(r=>r.json())).rules.walk_speed!==original)throw Error('Saving a record changed the world');
  await page.locator('.mechanics-record-row [data-record-action="rename"]').first().click();
  await page.locator('.mechanics-record-row b').filter({hasText:'我的机制草稿'}).waitFor();
  await page.locator('.mechanics-record-row [data-record-action="open"]').first().click();
  if(Number(await field.inputValue())!==original+1)throw Error('Opening a record lost its draft');
  if((await page.request.get('http://127.0.0.1:8301/api/mechanics').then(r=>r.json())).rules.walk_speed!==original)throw Error('Opening a record applied it');
  await page.locator('#mechanicsTemplate').locator('xpath=ancestor::details[1]').locator('summary').click();
  await page.locator('#mechanicsTemplate').fill('无效模板 {missing_field}');
  await page.locator('#saveMechanics').click();
  await page.waitForFunction(async()=>{const d=await fetch('/api/mechanics/records').then(r=>r.json());return d.items.some(r=>r.status==='failed'&&r.name.includes('未成功'));});
  await page.locator('#openMechanicsRecords').click();
  const failed=page.locator('.mechanics-record-row').filter({hasText:'未成功'}).first();
  if(!await failed.textContent().then(t=>t.includes('占位符')))throw Error('Failure reason missing');
  await page.locator('#closeMechanicsRecords').click();
  await page.locator('#mechanicsTemplate').fill(originalTemplate);
  await page.locator('#saveMechanics').click();
  await page.waitForFunction(async expected=>{
   const d=await fetch('/api/mechanics').then(r=>r.json());return d.rules.walk_speed===expected;
  },original+1);
  await page.locator('#openMechanicsRecords').click();
  await page.locator('.mechanics-record-row').filter({hasText:'已应用'}).first().waitFor();
  await page.locator('#selectAllMechanicsRecords').click();
  if(!await page.locator('#deleteMechanicsRecords').isEnabled())throw Error('Batch delete unavailable');
  await page.locator('#deleteMechanicsRecords').click();
  await page.waitForFunction(()=>document.querySelectorAll('.mechanics-record-row').length===0);
  if(errors.length)throw Error(errors.join('; '));
  console.log('mechanics records browser passed');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});

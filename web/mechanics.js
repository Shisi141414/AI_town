"use strict";
let mechanicsData=null;
const mechanicLabels={base_mood_drain:'每刻基础心情消耗',illness_mood_drain:'患病每刻心情消耗',work_mood_per_hour:'工作每小时心情消耗',study_mood_per_hour:'学习每小时心情消耗',low_energy_threshold:'低精力影响心情阈值',low_mood_threshold:'低心情损伤生命阈值',low_mood_delay_ticks:'连续低心情刻数',low_mood_hp_loss:'低心情每刻生命损伤',walk_speed:'步行每刻格数',bicycle_speed:'骑行每刻格数',car_speed:'汽车道路最高每刻格数',walk_satiety_per_tile:'步行每格饱食消耗',bicycle_satiety_per_tile:'骑行每格饱食消耗',walk_energy_per_tile:'步行每格精力消耗',bicycle_energy_per_tile:'骑行每格精力消耗',literature_mood_chance_per_level:'文学每级阅读加心情概率',literature_mood_bonus:'文学触发时心情收益',disease_daily_chance:'每天患病概率',affinity_daily_chance:'每天好感波动概率',disease_worsen_chance:'疾病每次检查加重概率',positive_affinity_threshold:'正面同处好感阈值',positive_mood_at_threshold:'刚过正面阈值每刻心情增加',positive_mood_at_80:'好感80每刻心情增加',negative_affinity_threshold:'负面同处好感阈值',negative_mood_at_threshold:'刚低于负面阈值每刻心情减少',negative_mood_at_0:'好感0每刻心情减少'};
function mechanicsLocked(){return simRunning||identityPhase||replayMode;}
Object.assign(mechanicLabels,{
 base_satiety_drain:'每刻基础饱食消耗',base_energy_regen:'每刻基础精力恢复',
 high_satiety_hp_threshold:'饱食超过此值时恢复生命',high_satiety_hp_gain:'高饱食时每刻生命恢复',
 low_energy_mood_per_point:'低于精力阈值后每少1点精力的心情损失',
 starvation_hp_loss:'饱食耗尽时每刻生命损失',decor_mood_per_200:'每200点装饰度带来的每刻心情增益',
 disease_hp_loss_mild:'轻症每刻生命损失',disease_hp_loss_moderate:'中症每刻生命损失',
 disease_hp_loss_severe:'重症每刻生命损失',disease_energy_loss_per_level:'疾病每级每刻精力损失',
 car_energy_per_tile:'开车每格精力消耗',car_fuel_per_tick:'开车每刻油费（该刻行驶时）',
 phone_fee_per_tick:'通话每刻话费',
 meal_block_satiety:'吃饭/送饭的饱食上限',dialogue_energy_per_round:'对话每轮精力消耗',meal_shelf_life_ticks:'饭菜保质期（刻）',hospital_trigger_hp:'紧急送医生命阈值',hospital_cost:'急救费',hospital_fee_halfday:'住院房费（每半天）',
 hospital_heal_multiplier:'住院生命恢复倍率（×自然恢复）',hospital_mood_gain:'每半天住院恢复心情',
 post_hospital_hp:'出院生命下限',post_hospital_satiety:'出院饱食下限',
 post_hospital_energy:'出院精力下限',post_hospital_mood:'出院心情下限'
});
const mechanicGroups=[
 ['饱食、饭菜与生命',['base_satiety_drain','meal_block_satiety','meal_shelf_life_ticks','high_satiety_hp_threshold','high_satiety_hp_gain','starvation_hp_loss','low_mood_threshold','low_mood_delay_ticks','low_mood_hp_loss']],
 ['精力与心情',['base_energy_regen','base_mood_drain','illness_mood_drain','low_energy_threshold','low_energy_mood_per_point','work_mood_per_hour','study_mood_per_hour','decor_mood_per_200']],
 ['室友关系带来的心情变化',['positive_affinity_threshold','positive_mood_at_threshold','positive_mood_at_80','negative_affinity_threshold','negative_mood_at_threshold','negative_mood_at_0','affinity_decisions_per_tick']],
 ['电话与对话',['phone_fee_per_tick','dialogue_energy_per_round']],
 ['室外出行',['walk_speed','bicycle_speed','car_speed','walk_satiety_per_tile','bicycle_satiety_per_tile','walk_energy_per_tile','bicycle_energy_per_tile','car_energy_per_tile','car_fuel_per_tick']],
 ['疾病、文学与随机事件',['disease_daily_chance','disease_worsen_chance','disease_hp_loss_mild','disease_hp_loss_moderate','disease_hp_loss_severe','disease_energy_loss_per_level','affinity_daily_chance','literature_mood_chance_per_level','literature_mood_bonus']],
 ['急救与住院',['hospital_trigger_hp','hospital_cost','hospital_fee_halfday','hospital_heal_multiplier','hospital_mood_gain','post_hospital_hp','post_hospital_satiety','post_hospital_energy','post_hospital_mood']]
];
mechanicLabels.affinity_decisions_per_tick='每刻给模型的自主好感判断总额度（0～5）';
function renderMechanicRules(rules){
 const shown=new Set();
 const groups=mechanicGroups.map(([title,keys],index)=>{
  const fields=keys.filter(key=>Object.hasOwn(rules,key));fields.forEach(key=>shown.add(key));
  return `<details class="mechanic-group" ${index<2?'open':''}><summary>${esc(title)}</summary><div class="building-fields">${fields.map(key=>mechanicsField('rules.'+key,mechanicLabels[key]||key,rules[key],key.endsWith('speed')||key.endsWith('ticks')||key==='affinity_decisions_per_tick'?1:.01)).join('')}</div></details>`;
 });
 const extra=Object.keys(rules).filter(key=>!shown.has(key));
 if(extra.length)groups.push(`<details class="mechanic-group"><summary>其他基础机制</summary><div class="building-fields">${extra.map(key=>mechanicsField('rules.'+key,mechanicLabels[key]||key,rules[key])).join('')}</div></details>`);
 return groups.join('');
}
function mechanicsField(path,label,value,step=.01){return `<label>${esc(label)}<input type="number" data-mechanic="${esc(path)}" value="${value}" step="${step}" aria-label="${esc(label)}"></label>`;}
const activityStatNames={hp:'生命',satiety:'饱食',energy:'精力',mood:'心情',money:'金钱'};
const activityLocations={own_computer:'自己的电脑',own_bedroom:'自己的卧室',house_common:'室内公共区','house:K':'厨房','house:D':'餐厅','house:L':'客厅','house:a':'阳台','house:b':'浴室','house:w':'洗衣机','house:h':'卧室'};
function activityLocationChoices(scene,current=[]){
 const options={...activityLocations};
 for(const [code,b] of Object.entries(mechanicsData.buildings))options['town:'+code]=b.name;
 const choices=Object.entries(options).filter(([value])=>(scene==='house'&&value.startsWith('house:'))||(scene==='house'&&['own_computer','own_bedroom','house_common'].includes(value))||(scene==='town'&&value.startsWith('town:')));
 for(const value of current)if(!['here','near_player','house_patient'].includes(value)&&!choices.some(([item])=>item===value))choices.push([value,value]);
 return choices;
}
function activityInput(key,field,label,value,type='text',step=''){
 return `<label>${esc(label)}<input data-activity-key="${esc(key)}" data-activity-field="${esc(field)}" type="${type}" value="${esc(String(value))}" ${step?`step="${step}"`:''}></label>`;
}
function collectActivityFields(){
 if(!mechanicsData?.activity_catalog)return;
 for(const el of $('mechanicsActivities').querySelectorAll('[data-activity-key][data-activity-field]')){
  const row=mechanicsData.activity_catalog[el.dataset.activityKey];if(!row)continue;
  const field=el.dataset.activityField;
  const value=el.type==='checkbox'?el.checked:el.type==='number'?Number(el.value):el.value;
  if(field.startsWith('effects.'))row.effects[field.slice(8)]=value;else row[field]=value;
 }
 for(const [key,row] of Object.entries(mechanicsData.activity_catalog)){
  const box=[...$('mechanicsActivities').querySelectorAll('[data-activity-scene]')].filter(el=>el.dataset.activityKey===key);
  if(!box.length)continue;
  row.places={};
  row.place_join=$('mechanicsActivities').querySelector(`[data-place-join="${key}"]`)?.dataset.joinValue||'or';
  row.position_options=[...$('mechanicsActivities').querySelectorAll('[data-position-option]')].filter(el=>el.dataset.activityKey===key&&el.checked).map(el=>el.dataset.positionOption);
  for(const sceneBox of box.filter(el=>el.checked)){
   const scene=sceneBox.dataset.activityScene;
   const concrete=[...$('mechanicsActivities').querySelectorAll('[data-activity-location]')].filter(el=>el.dataset.activityKey===key&&el.dataset.activityLocationScene===scene&&el.checked).map(el=>el.dataset.activityLocation);
   if(scene==='any')concrete.push('here');
   if(row.place_join==='or'&&scene!=='any')for(const option of row.position_options)concrete.push(option==='near_player'&&key==='home_treat'&&scene==='house'?'house_patient':option);
   row.places[scene]=concrete;
  }
  if(row.position_options.includes('near_player')&&row.target_mode==='none')row.target_mode='optional';
  if(row.place_join==='and'&&row.position_options.length===1&&row.position_options[0]==='near_player')row.target_mode='required';
 }
}
function renderActivities(){
 const locked=mechanicsLocked();
 const open=new Set([...$('mechanicsActivities').querySelectorAll('details.building-service[open]')].map(el=>el.dataset.activityRow));
 $('mechanicsActivities').innerHTML=Object.entries(mechanicsData.activity_catalog).map(([key,row])=>{
  const places=row.places||{[row.scene]:[row.location]};
  const sceneLabel=Object.keys(places).map(scene=>({house:'室内',town:'室外',any:'任意'}[scene])).join('、');
  const positionOptions=row.position_options||[...new Set(Object.values(places).flat().map(loc=>loc==='house_patient'?'near_player':loc).filter(loc=>['near_player','here'].includes(loc)))];
  const sceneChecks=Object.entries({house:'室内',town:'室外',any:'任意'}).map(([scene,label])=>`<label class="mechanics-check"><input type="checkbox" data-activity-key="${esc(key)}" data-activity-scene="${scene}" ${places[scene]?'checked':''}>${label}</label>`).join('');
  const locationMenus=['house','town'].filter(scene=>places[scene]).map(scene=>{
   const chosen=places[scene].filter(loc=>!['here','near_player','house_patient'].includes(loc));
   const title=scene==='house'?'室内地点':'室外地点';
   return `<details class="mechanics-place-menu"><summary>${title}：${chosen.length?`已选 ${chosen.length} 处`:'请选择'}</summary><div class="mechanics-place-options">${activityLocationChoices(scene,chosen).map(([value,name])=>`<label class="mechanics-check"><input type="checkbox" data-activity-key="${esc(key)}" data-activity-location="${esc(value)}" data-activity-location-scene="${scene}" ${chosen.includes(value)?'checked':''}>${esc(name)}</label>`).join('')}</div></details>`;
  }).join('');
  const join=row.place_join||'or';
  const locationControl=`<div class="mechanics-places"><b>活动类别与地点</b><div class="mechanics-place-scenes">${sceneChecks}</div><div class="mechanics-place-selects">${locationMenus}${places.any?'<span class="mechanics-any-note">任意地点，无需选择具体地点</span>':''}</div><div class="mechanics-place-condition"><button class="btn mechanics-join" type="button" data-place-join="${esc(key)}" data-join-value="${join}" aria-label="具体地点与独立条件的连接方式：${join==='and'?'且':'或'}">${join==='and'?'且':'或'}</button><label class="mechanics-check"><input type="checkbox" data-activity-key="${esc(key)}" data-position-option="near_player" ${positionOptions.includes('near_player')?'checked':''}>目标角色旁</label><label class="mechanics-check"><input type="checkbox" data-activity-key="${esc(key)}" data-position-option="here" ${positionOptions.includes('here')?'checked':''}>原地</label></div><small>“或”表示满足其中一项即可；“且”表示具体地点和独立条件同时满足。</small></div>`;
  const special=Object.entries(row.special||{}).filter(([k,v])=>typeof v==='number'&& !['price','minutes','affinity_per_hour','hourly_wage','energy_gain_per_hour','energy_cost_per_hour','mood_per_hour','satiety_per_hour','hp_per_hour'].includes(k)).map(([k,v])=>`${esc(k)}：${esc(String(v))}`).join('；');
  return `<details class="building-service" data-activity-row="${esc(key)}" ${open.has(key)?'open':''}><summary><b>${esc(row.emoji)} ${esc(row.name)}</b><small>${esc(key)} · ${sceneLabel}${row.enabled?'':' · 已关闭'}</small></summary><div class="building-fields">
   ${activityInput(key,'name','名称',row.name)}${activityInput(key,'emoji','图标',row.emoji)}
   ${locationControl}
   <label>活动对象<select data-activity-key="${esc(key)}" data-activity-field="target_mode"><option value="none" ${row.target_mode==='none'?'selected':''}>无</option><option value="optional" ${row.target_mode==='optional'?'selected':''}>可选择室友</option><option value="required" ${row.target_mode==='required'?'selected':''}>必须选择室友</option></select></label>
   ${activityInput(key,'duration','默认时长（刻）',row.duration,'number','1')}
   ${activityInput(key,'cost','开始时一次性花费',row.cost,'number','0.01')}
   ${Object.entries(activityStatNames).map(([stat,label])=>activityInput(key,'effects.'+stat,label+'每刻变化',row.effects[stat],'number','0.01')).join('')}
   ${activityInput(key,'joint_affinity_per_tick','两人一起时对彼此好感每刻变化',row.joint_affinity_per_tick,'number','0.01')}
   ${activityInput(key,'self_affinity_per_tick','对对象的好感每刻变化',row.self_affinity_per_tick??0,'number','0.01')}
   ${activityInput(key,'target_affinity_per_tick','对象对我的好感每刻变化',row.target_affinity_per_tick??0,'number','0.01')}
   <label style="grid-column:1/-1">模型可见说明<textarea data-activity-key="${esc(key)}" data-activity-field="description" maxlength="240" rows="2">${esc(row.description)}</textarea></label>
   <label class="mechanics-switch-label">启用<span class="mechanics-switch"><input type="checkbox" data-activity-key="${esc(key)}" data-activity-field="enabled" ${row.enabled?'checked':''}><span class="mechanics-switch-track"></span></span></label>
   <button class="btn" type="button" data-delete-activity="${esc(key)}">${row.builtin?'关闭活动':'删除活动'}</button>
   ${special?`<p class="hint" style="grid-column:1/-1">专属条件：${special}。场所项目的专属参数可在建筑编辑中修改。</p>`:''}
  </div></details>`;
 }).join('');
 $('mechanicsActivities').querySelectorAll('input,select,textarea,button').forEach(el=>el.disabled=locked);
 $('newActivityCode').disabled=locked;$('newActivityName').disabled=locked;$('addMechanicsActivity').disabled=locked;
}
async function openMechanics(){
 mechanicsData=await jget('/api/mechanics');
 const d=mechanicsData;
 $('mechanicsRules').innerHTML=renderMechanicRules(d.rules);
 $('mechanicsHours').innerHTML=Object.entries(d.hours).map(([code,h])=>`<div class="mechanic-hour"><b>${esc(d.buildings[code].name)}</b>${mechanicsField('hours.'+code+'.0','开始（整点）',h[0],1)}${mechanicsField('hours.'+code+'.1','结束（整点）',h[1],1)}</div>`).join('');
 renderActivities();
 $('mechanicsBuildings').innerHTML=Object.entries(d.buildings).map(([code,b])=>`<div class="mechanic-building"><b>${esc(b.name)}</b><small>${Object.keys(b.services).length} 个项目</small><button class="btn" data-code="${esc(code)}">查看与编辑</button></div>`).join('');
 $('mechanicsSystem').value=d.system_prompt;
 $('mechanicsPreview').value=d.prompt_preview;
 $('mechanicsResident').textContent=d.resident_name?`（${d.resident_name}）`:'';
 $('mechanicsTemplate').value=d.prompt_template;
 const locked=mechanicsLocked();
 $('mechanicsDialog').querySelectorAll('input,textarea:not([readonly])').forEach(el=>el.disabled=locked);
 $('saveMechanics').disabled=locked;
 $('saveMechanicsRecord').disabled=replayMode;
 $('mechanicsHint').textContent=locked?'模拟进行中只能查看；暂停后可修改。':'暂停中可修改。修改后会写入当前存档，新活动使用新参数。';
 if(!$('mechanicsDialog').open)$('mechanicsDialog').showModal();
}
$('btnMechanics').onclick=()=>openMechanics().catch(e=>toast(e.message,false));
$('closeMechanics').onclick=()=>$('mechanicsDialog').close();
$('mechanicsBuildings').onclick=e=>{const b=e.target.closest('[data-code]');if(!b)return;$('mechanicsDialog').close();window.openTownBuilding(b.dataset.code).catch(err=>toast(err.message,false));};
$('addMechanicsActivity').onclick=()=>{
 if(mechanicsLocked())return;
 collectActivityFields();const key=$('newActivityCode').value.trim(),name=$('newActivityName').value.trim();
 if(!/^[a-z][a-z0-9_]{1,39}$/.test(key)||!name)return toast('请输入小写英文活动代码和活动名称',false);
 if(mechanicsData.activity_catalog[key])return toast('活动代码已存在',false);
 mechanicsData.activity_catalog[key]={name,emoji:'✨',description:`在当前位置${name}`,scene:'any',location:'here',places:{any:['here']},target_mode:'none',duration:1,cost:0,effects:{hp:0,satiety:0,energy:0,mood:0,money:0},joint_affinity_per_tick:0,self_affinity_per_tick:0,target_affinity_per_tick:0,enabled:true,builtin:false,special:{}};
 renderActivities();$('newActivityCode').value='';$('newActivityName').value='';
 $('mechanicsActivities').querySelector(`[data-activity-key="${key}"]`)?.closest('details')?.setAttribute('open','');
};
$('mechanicsActivities').onclick=e=>{
 const join=e.target.closest('[data-place-join]');
 if(join&&!mechanicsLocked()){
  collectActivityFields();const row=mechanicsData.activity_catalog[join.dataset.placeJoin];
  row.place_join=row.place_join==='and'?'or':'and';
  if(row.place_join==='and'&&row.position_options.length===1&&row.position_options[0]==='near_player')row.target_mode='required';
  renderActivities();return;
 }
 const button=e.target.closest('[data-delete-activity]');if(!button||mechanicsLocked())return;
 collectActivityFields();const key=button.dataset.deleteActivity,row=mechanicsData.activity_catalog[key];
 if(row.builtin){row.enabled=!row.enabled;}else delete mechanicsData.activity_catalog[key];
 renderActivities();
};
$('mechanicsActivities').onchange=e=>{
 const el=e.target;
 if(el.dataset.activityLocation){
  const menu=el.closest('.mechanics-place-menu'),count=menu.querySelectorAll('[data-activity-location]:checked').length;
  menu.querySelector('summary').textContent=`${el.dataset.activityLocationScene==='house'?'室内':'室外'}地点：${count?`已选 ${count} 处`:'请选择'}`;
  return;
 }
 if(el.dataset.positionOption){
  collectActivityFields();
  const mode=$('mechanicsActivities').querySelector(`select[data-activity-key="${el.dataset.activityKey}"][data-activity-field="target_mode"]`);
  mode.value=mechanicsData.activity_catalog[el.dataset.activityKey].target_mode;
  return;
 }
 if(!el.dataset.activityScene)return;
 collectActivityFields();const row=mechanicsData.activity_catalog[el.dataset.activityKey];
 if(el.checked)row.places[el.dataset.activityScene]=el.dataset.activityScene==='any'?['here']:[];else delete row.places[el.dataset.activityScene];
 renderActivities();
};
function mechanicsPayload(){
 collectActivityFields();
 const payload={rules:structuredClone(mechanicsData.rules),hours:structuredClone(mechanicsData.hours),activities:structuredClone(mechanicsData.activities),activity_catalog:structuredClone(mechanicsData.activity_catalog),prompt_template:$('mechanicsTemplate').value};
 for(const el of $('mechanicsDialog').querySelectorAll('input[data-mechanic]')){
  const parts=el.dataset.mechanic.split('.');let target=payload;
  for(const part of parts.slice(0,-1))target=target[part];
  target[parts.at(-1)]=Number(el.value);
 }
 return payload;
}
function mechanicsRawFields(){
 const fields={};
 for(const el of $('mechanicsDialog').querySelectorAll('input[data-mechanic], [data-activity-key][data-activity-field]')){
  const key=el.dataset.mechanic||`activity.${el.dataset.activityKey}.${el.dataset.activityField}`;
  fields[key]=el.type==='checkbox'?String(el.checked):el.value;
 }
 return fields;
}
async function createMechanicsRecord(status='draft',reason=''){
 const record=await jpost('/api/mechanics/records',{payload:mechanicsPayload(),status,reason,raw_fields:mechanicsRawFields()});
 await showMechanicsRecords();
 return record;
}
function renderMechanicsRecordList(items){
 $('mechanicsRecordsList').innerHTML=items.map(row=>`<div class="mechanics-record-row" data-id="${esc(row.id)}"><input type="checkbox" class="mechanics-record-select" aria-label="选择记录 ${esc(row.name)}" ${replayMode?'disabled':''}><div><b>${esc(row.name)}</b><small>${row.status==='applied'?'已应用':row.status==='failed'?'未成功':'手动保存'}${row.reason?' · '+esc(row.reason):''}</small><div class="mechanics-record-actions"><button class="btn" data-record-action="open" type="button">打开</button><button class="btn" data-record-action="rename" type="button" ${replayMode?'disabled':''}>重命名</button></div></div></div>`).join('')||'<p class="hint">暂无机制记录</p>';
 updateMechanicsRecordSelection();
}
function selectedMechanicsRecordIds(){return [...$('mechanicsRecordsList').querySelectorAll('.mechanics-record-select:checked')].map(el=>el.closest('[data-id]').dataset.id);}
function updateMechanicsRecordSelection(){
 const count=selectedMechanicsRecordIds().length;
 $('deleteMechanicsRecords').disabled=replayMode||!count;
 $('deleteMechanicsRecords').textContent=`删除所选（${count}）`;
}
async function showMechanicsRecords(){
 const d=await jget('/api/mechanics/records');renderMechanicsRecordList(d.items);
 if(!$('mechanicsRecordsDialog').open)$('mechanicsRecordsDialog').showModal();
}
function openMechanicsRecord(record){
 const p=record.payload;
 mechanicsData.rules=structuredClone(p.rules);mechanicsData.hours=structuredClone(p.hours);
 mechanicsData.activities=structuredClone(p.activities);
 if(p.activity_catalog)mechanicsData.activity_catalog=structuredClone(p.activity_catalog);
 $('mechanicsRules').innerHTML=renderMechanicRules(mechanicsData.rules);
 $('mechanicsHours').innerHTML=Object.entries(mechanicsData.hours).map(([code,h])=>`<div class="mechanic-hour"><b>${esc(mechanicsData.buildings[code]?.name||code)}</b>${mechanicsField('hours.'+code+'.0','开始（整点）',h[0],1)}${mechanicsField('hours.'+code+'.1','结束（整点）',h[1],1)}</div>`).join('');
 renderActivities();$('mechanicsTemplate').value=p.prompt_template;
 for(const el of $('mechanicsDialog').querySelectorAll('input[data-mechanic], [data-activity-key][data-activity-field]')){
  const key=el.dataset.mechanic||`activity.${el.dataset.activityKey}.${el.dataset.activityField}`;
  if(Object.hasOwn(record.raw_fields||{},key)){
   if(el.type==='checkbox')el.checked=record.raw_fields[key]==='true';else el.value=record.raw_fields[key];
  }
 }
 const locked=mechanicsLocked();
 $('mechanicsDialog').querySelectorAll('input,textarea:not([readonly])').forEach(el=>el.disabled=locked);
 $('saveMechanics').disabled=locked;
 $('mechanicsHint').textContent=`已打开“${record.name}”${record.status==='failed'?'（未成功：'+record.reason+'）':''}；当前世界未改变。${locked?'暂停模拟后可修改并应用。':'检查后按“保存机制”应用。'}`;
 $('mechanicsRecordsDialog').close();
}
$('saveMechanicsRecord').onclick=()=>createMechanicsRecord().then(()=>toast('机制记录已保存')).catch(e=>toast(e.message,false));
$('openMechanicsRecords').onclick=()=>showMechanicsRecords().catch(e=>toast(e.message,false));
$('closeMechanicsRecords').onclick=()=>$('mechanicsRecordsDialog').close();
$('mechanicsRecordsList').onchange=updateMechanicsRecordSelection;
$('selectAllMechanicsRecords').onclick=()=>{$('mechanicsRecordsList').querySelectorAll('.mechanics-record-select:not(:disabled)').forEach(el=>el.checked=true);updateMechanicsRecordSelection();};
$('clearMechanicsRecords').onclick=()=>{$('mechanicsRecordsList').querySelectorAll('.mechanics-record-select').forEach(el=>el.checked=false);updateMechanicsRecordSelection();};
$('deleteMechanicsRecords').onclick=async()=>{
 const ids=selectedMechanicsRecordIds();if(!ids.length||!confirm(`删除所选 ${ids.length} 条机制记录？此操作不可撤销。`))return;
 try{await jpost('/api/mechanics/records/delete-selected',{ids});await showMechanicsRecords();toast(`已删除 ${ids.length} 条记录`);}catch(e){toast(e.message,false);}
};
$('mechanicsRecordsList').onclick=async e=>{
 const button=e.target.closest('[data-record-action]');if(!button)return;
 const id=button.closest('[data-id]').dataset.id;
 try{
  if(button.dataset.recordAction==='open')openMechanicsRecord(await jget(`/api/mechanics/records/${encodeURIComponent(id)}`));
  else if(button.dataset.recordAction==='rename'){
   const row=button.closest('[data-id]'),name=prompt('机制记录名称',row.querySelector('b').textContent);
   if(name===null)return;
   await jput(`/api/mechanics/records/${encodeURIComponent(id)}`,{name:name.trim()});await showMechanicsRecords();
  }
 }catch(err){toast(err.message,false);}
};
$('saveMechanics').onclick=async()=>{
 if(mechanicsLocked())return toast('请先暂停模拟',false);
 const payload=mechanicsPayload();
 for(const el of $('mechanicsDialog').querySelectorAll('input[data-mechanic], input[data-activity-key]')){
  if(!el.reportValidity()){
   const reason=`输入无效：${el.getAttribute('aria-label')||el.closest('label')?.textContent?.trim()||el.dataset.mechanic||el.dataset.activityField}`;
   try{await createMechanicsRecord('failed',reason);}catch(err){toast('记录失败：'+err.message,false);}
   return;
  }
 }
 try{await jput('/api/mechanics',payload);await openMechanics();toast('机制已保存');}
 catch(e){toast(e.message,false);}
};

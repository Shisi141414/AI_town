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
 hospital_trigger_hp:'紧急送医生命阈值',hospital_cost:'急救费',hospital_fee_halfday:'住院房费（每半天）',
 hospital_heal_multiplier:'住院生命恢复倍率（×自然恢复）',hospital_mood_gain:'每半天住院恢复心情',
 post_hospital_hp:'出院生命下限',post_hospital_satiety:'出院饱食下限',
 post_hospital_energy:'出院精力下限',post_hospital_mood:'出院心情下限'
});
const mechanicGroups=[
 ['饱食与生命',['base_satiety_drain','high_satiety_hp_threshold','high_satiety_hp_gain','starvation_hp_loss','low_mood_threshold','low_mood_delay_ticks','low_mood_hp_loss']],
 ['精力与心情',['base_energy_regen','base_mood_drain','illness_mood_drain','low_energy_threshold','low_energy_mood_per_point','work_mood_per_hour','study_mood_per_hour','decor_mood_per_200']],
 ['室友关系带来的心情变化',['positive_affinity_threshold','positive_mood_at_threshold','positive_mood_at_80','negative_affinity_threshold','negative_mood_at_threshold','negative_mood_at_0']],
 ['电话与社交费用',['phone_fee_per_tick']],
 ['室外出行',['walk_speed','bicycle_speed','car_speed','walk_satiety_per_tile','bicycle_satiety_per_tile','walk_energy_per_tile','bicycle_energy_per_tile','car_energy_per_tile','car_fuel_per_tick']],
 ['疾病、文学与随机事件',['disease_daily_chance','disease_worsen_chance','disease_hp_loss_mild','disease_hp_loss_moderate','disease_hp_loss_severe','disease_energy_loss_per_level','affinity_daily_chance','literature_mood_chance_per_level','literature_mood_bonus']],
 ['急救与住院',['hospital_trigger_hp','hospital_cost','hospital_fee_halfday','hospital_heal_multiplier','hospital_mood_gain','post_hospital_hp','post_hospital_satiety','post_hospital_energy','post_hospital_mood']]
];
function renderMechanicRules(rules){
 const shown=new Set();
 const groups=mechanicGroups.map(([title,keys],index)=>{
  const fields=keys.filter(key=>Object.hasOwn(rules,key));fields.forEach(key=>shown.add(key));
  return `<details class="mechanic-group" ${index<2?'open':''}><summary>${esc(title)}</summary><div class="building-fields">${fields.map(key=>mechanicsField('rules.'+key,mechanicLabels[key]||key,rules[key],key.endsWith('speed')||key.endsWith('ticks')?1:.01)).join('')}</div></details>`;
 });
 const extra=Object.keys(rules).filter(key=>!shown.has(key));
 if(extra.length)groups.push(`<details class="mechanic-group"><summary>其他基础机制</summary><div class="building-fields">${extra.map(key=>mechanicsField('rules.'+key,mechanicLabels[key]||key,rules[key])).join('')}</div></details>`);
 return groups.join('');
}
function mechanicsField(path,label,value,step=.01){return `<label>${esc(label)}<input type="number" data-mechanic="${esc(path)}" value="${value}" step="${step}" aria-label="${esc(label)}"></label>`;}
const activityStatNames={hp:'生命',satiety:'饱食',energy:'精力',mood:'心情',money:'金钱'};
const activityLocations={here:'原地',own_computer:'自己的电脑',own_bedroom:'自己的卧室',house_common:'室内公共区',house_patient:'病人旁',near_player:'目标角色旁','house:K':'厨房','house:D':'餐厅','house:L':'客厅','house:a':'阳台','house:b':'浴室','house:w':'洗衣机','house:h':'卧室'};
function activityLocationOptions(scene,current){
 const options={...activityLocations};
 for(const [code,b] of Object.entries(mechanicsData.buildings))options['town:'+code]=b.name;
 const allowed=Object.entries(options).filter(([value])=>value==='here'||value==='near_player'||(scene==='house'&&value.startsWith('house:'))||(scene==='house'&&['own_computer','own_bedroom','house_common','house_patient'].includes(value))||(scene==='town'&&value.startsWith('town:')));
 if(!allowed.some(([value])=>value===current))allowed.push([current,current]);
 return allowed.map(([value,label])=>`<option value="${esc(value)}" ${value===current?'selected':''}>${esc(label)}</option>`).join('');
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
}
function renderActivities(){
 const locked=mechanicsLocked();
 $('mechanicsActivities').innerHTML=Object.entries(mechanicsData.activity_catalog).map(([key,row])=>{
  const sceneLabel={house:'室内',town:'小镇',any:'独立'}[row.scene];
  const sceneControl=row.builtin?`<span>${esc(sceneLabel)}</span>`:`<select data-activity-key="${esc(key)}" data-activity-field="scene"><option value="house" ${row.scene==='house'?'selected':''}>室内</option><option value="town" ${row.scene==='town'?'selected':''}>小镇</option><option value="any" ${row.scene==='any'?'selected':''}>独立</option></select>`;
  const locationControl=row.builtin?`<span>${esc(activityLocations[row.location]||mechanicsData.buildings[row.location.split(':')[1]]?.name||row.location)}</span>`:`<select data-activity-key="${esc(key)}" data-activity-field="location">${activityLocationOptions(row.scene,row.location)}</select>`;
  const special=Object.entries(row.special||{}).filter(([k,v])=>typeof v==='number'&& !['price','minutes','affinity_per_hour','hourly_wage','energy_gain_per_hour','energy_cost_per_hour','mood_per_hour','satiety_per_hour','hp_per_hour'].includes(k)).map(([k,v])=>`${esc(k)}：${esc(String(v))}`).join('；');
  return `<details class="building-service"><summary><b>${esc(row.emoji)} ${esc(row.name)}</b><small>${esc(key)} · ${sceneLabel}${row.enabled?'':' · 已关闭'}</small></summary><div class="building-fields">
   ${activityInput(key,'name','名称',row.name)}${activityInput(key,'emoji','图标',row.emoji)}
   <label>类别${sceneControl}</label><label>活动地点${locationControl}</label>
   ${activityInput(key,'duration','默认时长（刻）',row.duration,'number','1')}
   ${activityInput(key,'cost','开始时一次性花费',row.cost,'number','0.01')}
   ${Object.entries(activityStatNames).map(([stat,label])=>activityInput(key,'effects.'+stat,label+'每刻变化',row.effects[stat],'number','0.01')).join('')}
   ${activityInput(key,'joint_affinity_per_tick','两人一起时对彼此好感每刻变化',row.joint_affinity_per_tick,'number','0.01')}
   <label style="grid-column:1/-1">模型可见说明<textarea data-activity-key="${esc(key)}" data-activity-field="description" maxlength="240" rows="2">${esc(row.description)}</textarea></label>
   <label>启用<input type="checkbox" data-activity-key="${esc(key)}" data-activity-field="enabled" ${row.enabled?'checked':''}></label>
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
 mechanicsData.activity_catalog[key]={name,emoji:'✨',description:`在当前位置${name}`,scene:'any',location:'here',duration:1,cost:0,effects:{hp:0,satiety:0,energy:0,mood:0,money:0},joint_affinity_per_tick:0,enabled:true,builtin:false,special:{}};
 renderActivities();$('newActivityCode').value='';$('newActivityName').value='';
 $('mechanicsActivities').querySelector(`[data-activity-key="${key}"]`)?.closest('details')?.setAttribute('open','');
};
$('mechanicsActivities').onclick=e=>{
 const button=e.target.closest('[data-delete-activity]');if(!button||mechanicsLocked())return;
 collectActivityFields();const key=button.dataset.deleteActivity,row=mechanicsData.activity_catalog[key];
 if(row.builtin){row.enabled=!row.enabled;}else delete mechanicsData.activity_catalog[key];
 renderActivities();
};
$('mechanicsActivities').onchange=e=>{
 const el=e.target;if(el.dataset.activityField!=='scene')return;
 collectActivityFields();const row=mechanicsData.activity_catalog[el.dataset.activityKey];
 row.location='here';renderActivities();
};
$('saveMechanics').onclick=async()=>{
 if(mechanicsLocked())return toast('请先暂停模拟',false);
 collectActivityFields();
 const payload={rules:structuredClone(mechanicsData.rules),hours:structuredClone(mechanicsData.hours),activities:structuredClone(mechanicsData.activities),activity_catalog:structuredClone(mechanicsData.activity_catalog),prompt_template:$('mechanicsTemplate').value};
 for(const el of $('mechanicsDialog').querySelectorAll('input[data-mechanic]')){
  if(!el.reportValidity())return;
  const parts=el.dataset.mechanic.split('.');let target=payload;
  for(const part of parts.slice(0,-1))target=target[part];
  target[parts.at(-1)]=Number(el.value);
 }
 try{await jput('/api/mechanics',payload);await openMechanics();toast('机制已保存');}
 catch(e){toast(e.message,false);}
};

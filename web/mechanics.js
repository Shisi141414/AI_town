"use strict";
let mechanicsData=null;
const mechanicLabels={base_mood_drain:'每刻基础心情消耗',illness_mood_drain:'患病每刻心情消耗',work_mood_per_hour:'工作每小时心情消耗',study_mood_per_hour:'学习每小时心情消耗',low_energy_threshold:'低精力影响心情阈值',low_mood_threshold:'低心情损伤生命阈值',low_mood_delay_ticks:'连续低心情刻数',low_mood_hp_loss:'低心情每刻生命损伤',walk_speed:'步行每刻格数',bicycle_speed:'骑行每刻格数',car_speed:'汽车道路最高每刻格数',walk_satiety_per_tile:'步行每格饱食消耗',bicycle_satiety_per_tile:'骑行每格饱食消耗',walk_energy_per_tile:'步行每格精力消耗',bicycle_energy_per_tile:'骑行每格精力消耗',literature_mood_chance_per_level:'文学每级阅读加心情概率',literature_mood_bonus:'文学触发时心情收益',disease_daily_chance:'每天患病概率',affinity_daily_chance:'每天好感波动概率',disease_worsen_chance:'疾病每次检查加重概率',positive_affinity_threshold:'正面同处好感阈值',positive_mood_at_threshold:'刚过正面阈值每刻心情增加',positive_mood_at_80:'好感80每刻心情增加',negative_affinity_threshold:'负面同处好感阈值',negative_mood_at_threshold:'刚低于负面阈值每刻心情减少',negative_mood_at_0:'好感0每刻心情减少'};
function mechanicsLocked(){return simRunning||identityPhase||replayMode;}
Object.assign(mechanicLabels,{base_satiety_drain:'每刻基础饱食消耗',high_satiety_hp_threshold:'饱食恢复生命的门槛',high_satiety_hp_gain:'高饱食时每刻生命恢复',hospital_trigger_hp:'紧急送医生命阈值',hospital_cost:'急救费',hospital_fee_halfday:'住院房费（每半天）',hospital_heal_hp:'每半天住院恢复生命',hospital_mood_gain:'每半天住院恢复心情',post_hospital_hp:'出院生命下限',post_hospital_satiety:'出院饱食下限',post_hospital_energy:'出院精力下限',post_hospital_mood:'出院心情下限'});
function mechanicsField(path,label,value,step=.01){return `<label>${esc(label)}<input type="number" data-mechanic="${esc(path)}" value="${value}" step="${step}" aria-label="${esc(label)}"></label>`;}
async function openMechanics(){
 mechanicsData=await jget('/api/mechanics');
 const d=mechanicsData;
 $('mechanicsRules').innerHTML=Object.entries(d.rules).map(([k,v])=>mechanicsField('rules.'+k,mechanicLabels[k]||k,v,k.endsWith('speed')||k.endsWith('ticks')?1:.01)).join('');
 $('mechanicsHours').innerHTML=Object.entries(d.hours).map(([code,h])=>`<div class="mechanic-hour"><b>${esc(d.buildings[code].name)}</b>${mechanicsField('hours.'+code+'.0','开始（整点）',h[0],1)}${mechanicsField('hours.'+code+'.1','结束（整点）',h[1],1)}</div>`).join('');
 $('mechanicsActivities').innerHTML=Object.entries(d.activities).map(([key,row])=>`<details class="building-service"><summary><b>${esc(d.activity_names[key])}</b><small>${esc(key)}</small></summary><div class="building-fields">${mechanicsField('activities.'+key+'.duration','默认时长（刻）',row.duration,1)}${mechanicsField('activities.'+key+'.cost','一次性费用',row.cost)}${Object.entries(row.effects).map(([stat,v])=>mechanicsField('activities.'+key+'.effects.'+stat,stat+' 每刻变化',v)).join('')}</div></details>`).join('');
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
$('saveMechanics').onclick=async()=>{
 if(mechanicsLocked())return toast('请先暂停模拟',false);
 const payload={rules:structuredClone(mechanicsData.rules),hours:structuredClone(mechanicsData.hours),activities:structuredClone(mechanicsData.activities),prompt_template:$('mechanicsTemplate').value};
 for(const el of $('mechanicsDialog').querySelectorAll('input[data-mechanic]')){
  if(!el.reportValidity())return;
  const parts=el.dataset.mechanic.split('.');let target=payload;
  for(const part of parts.slice(0,-1))target=target[part];
  target[parts.at(-1)]=Number(el.value);
 }
 try{await jput('/api/mechanics',payload);await openMechanics();toast('机制已保存');}
 catch(e){toast(e.message,false);}
};

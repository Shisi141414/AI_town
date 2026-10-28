/* 小镇建筑参数：只展示参数，暂停后才允许保存。 */
let openBuildingCode=null, buildingCatalog={};
window.refreshBuildingLock=function(force=null){
 const locked=replayMode||(force===null?(simRunning||identityPhase):force);
 const dialog=document.getElementById('buildingDialog');
 dialog.querySelectorAll('input').forEach(input=>input.disabled=locked);
 document.getElementById('saveBuilding').disabled=locked;
 document.getElementById('buildingHint').textContent=replayMode?(townConfig?'回放参数仅供查看。':'此旧帧没有记录场所参数，显示默认参数，仅供参考。'):locked?'模拟进行中，只能查看。暂停后可修改。':'暂停中，可修改参数。已开始的课程、工作及消费保留原约定，新活动使用新参数。';
};
window.openTownBuilding=async function(code){
 buildingCatalog=await jget('/api/town/buildings');
 if(!buildingCatalog[code])return;
 openBuildingCode=code;
 const data=buildingCatalog[code],values=(townConfig&&townConfig[code])||(replayMode?data.defaults:data.parameters);
 document.getElementById('buildingName').textContent=data.name;
 document.getElementById('buildingServices').innerHTML=Object.entries(values).map(([key,row],i)=>{
  const meta=data.services[key];if(!meta)return '';
  const school=(townConfig&&townConfig.U)||(replayMode?buildingCatalog.U.defaults:buildingCatalog.U.parameters);
  const workXP=(school?.['study_'+meta.skill_key]?.work_xp_per_hour??3)*(meta.teacher?2:1);
  const summary=[row.minutes!=null?row.minutes+'分钟':'',row.price!=null?row.price+'元':'',row.hourly_wage!=null?'时薪'+row.hourly_wage+'元':'',meta.skill?(meta.kind==='course'?'学习'+meta.skill:meta.skill+(row.min_level??0)+'级'):'',meta.kind==='job'?'工作经验'+workXP+' /小时':''].filter(Boolean).join(' · ');
  return `<details class="building-service" ${i===0?'open':''}><summary><b>${esc(meta.name)}</b><small>${esc(summary)}</small></summary><div class="building-fields">${Object.entries(row).map(([field,value])=>{
   const f={...data.fields[field]};if(field==='minutes')f.name=meta.kind==='job'?'最短班次':'默认活动时长';return `<label>${esc(f.name)} <small>${esc(f.unit)}</small><input type="number" data-service="${esc(key)}" data-field="${esc(field)}" min="${f.min}" max="${f.max}" step="${f.step}" value="${value}" aria-label="${esc(meta.name+' '+f.name)}"></label>`;
  }).join('')}</div>${meta.kind==='course'?'<p class="hint">每100经验升一级，最高5级；选课开始时一次性付清学费，中途不再扣费；经验按实际学习时间累计，中断保留已学经验。该学科工作经验率统一作用于相关职业，教师始终为普通职业的两倍。</p>':''}${meta.kind==='job'?'<p class="hint">可延长班次，工资和经验均按实际工作时间结算；上方为基础精力消耗率，相关技能每级额外节约2%精力。工作经验率在学校对应学科中统一调整，教师为普通职业的两倍，技能最高5级。</p>':''}${['eat_out','coffee'].includes(key)?'<p class="hint">只有和室友同时在这里进行相同活动时，才按共同活动时长增加对对方的好感。</p>':''}${['fish','swim'].includes(key)?'<p class="hint">钓鱼在岸边进行，游泳会进入水面，结束后返回空闲岸边；普通出行不能进入水域。</p>':''}</details>`;
 }).join('')||'<p class="hint">这里暂时没有可调整的服务参数。</p>';
 const dialog=document.getElementById('buildingDialog');if(!dialog.open)dialog.showModal();
 refreshBuildingLock();
};
document.getElementById('closeBuilding').onclick=()=>document.getElementById('buildingDialog').close();
document.getElementById('saveBuilding').onclick=async()=>{
 if(replayMode||simRunning||identityPhase)throw Error('请先暂停模拟');
 const parameters={};
 for(const input of document.querySelectorAll('#buildingServices input')){
  if(!input.reportValidity())return;
  (parameters[input.dataset.service]??={})[input.dataset.field]=Number(input.value);
 }
 await jput('/api/town/buildings/'+openBuildingCode,{parameters});
 townConfig??={};townConfig[openBuildingCode]=parameters;
 await refreshState();await openTownBuilding(openBuildingCode);toast('参数已保存，新活动使用新数值');
};

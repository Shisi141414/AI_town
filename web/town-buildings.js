/* 小镇建筑参数：只展示参数，暂停后才允许保存。 */
let openBuildingCode=null, buildingCatalog={},shopDraft={};
function renderShop(){
 document.getElementById('shopProducts').innerHTML=Object.entries(shopDraft).map(([key,row])=>`<div class="building-service"><b>${esc(key)}</b><div class="building-fields"><label>名称<input data-shop-key="${esc(key)}" data-shop-field="name" value="${esc(row.name)}"></label><label>图标<input data-shop-key="${esc(key)}" data-shop-field="emoji" value="${esc(row.emoji)}"></label><label>售价<input type="number" min="0" step="0.01" data-shop-key="${esc(key)}" data-shop-field="price" value="${row.price}"></label><label class="mechanics-switch-label">上架<span class="mechanics-switch"><input type="checkbox" data-shop-key="${esc(key)}" data-shop-field="enabled" ${row.enabled?'checked':''}><span class="mechanics-switch-track"></span></span></label></div></div>`).join('');
 refreshBuildingLock();
}
function collectShop(){for(const input of document.querySelectorAll('#shopProducts [data-shop-field]')){const row=shopDraft[input.dataset.shopKey];if(!row)continue;row[input.dataset.shopField]=input.type==='checkbox'?input.checked:input.type==='number'?Number(input.value):input.value;}}
window.refreshBuildingLock=function(force=null){
 const locked=replayMode||(force===null?(simRunning||identityPhase):force);
 const dialog=document.getElementById('buildingDialog');
 dialog.querySelectorAll('input').forEach(input=>input.disabled=locked);
 dialog.querySelectorAll('#shopEditor button').forEach(button=>button.disabled=locked);
 document.getElementById('saveBuilding').disabled=locked;
 document.getElementById('buildingHint').textContent=replayMode?(townConfig?'回放参数仅供查看。':'此旧帧没有记录场所参数，显示默认参数，仅供参考。'):locked?'模拟进行中，只能查看。暂停后可修改。':'暂停中，可修改参数。已开始的课程、工作及消费保留原约定，新活动使用新参数。';
};
window.openTownBuilding=async function(code){
 const [buildings,mechanics]=await Promise.all([jget('/api/town/buildings'),jget('/api/mechanics')]);buildingCatalog=buildings;
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
 const labels={hp:'生命',satiety:'饱食',energy:'精力',mood:'心情',money:'金钱'};
 const located=Object.entries(mechanics.activity_catalog).filter(([,row])=>(row.places?.town||[]).includes('town:'+code));
 document.getElementById('buildingUnifiedActivities').innerHTML=`<h4>此处的活动规则</h4>${located.map(([key,row])=>`<div class="building-service"><b>${esc(row.emoji)} ${esc(row.name)}</b> <small>${esc(key)} · ${row.enabled?'启用':'已关闭'}</small><p class="hint">地点：${esc((row.places?.town||[]).join('、'))} · 对象：${esc(({none:'无',optional:'可选',required:'必选'})[row.target_mode]||'无')} · 默认${row.duration}刻 · 一次性费用${row.cost}元</p><p class="hint">${Object.entries(labels).map(([stat,label])=>`${label} ${row.effects[stat]??0}/刻`).join(' · ')}</p><p class="hint">共同活动好感 ${row.joint_affinity_per_tick??0}/刻 · 我对对象 ${row.self_affinity_per_tick??0}/刻 · 对象对我 ${row.target_affinity_per_tick??0}/刻</p><button class="btn" type="button" data-edit-activity="${esc(key)}" ${replayMode?'disabled':''}>在机制中编辑</button></div>`).join('')||'<p class="hint">这里暂无活动</p>'}`;
 document.getElementById('shopEditor').hidden=code!=='S';
 if(code==='S'){shopDraft=replayMode?structuredClone(shopProducts):await jget('/api/town/shop');renderShop();}
 const dialog=document.getElementById('buildingDialog');if(!dialog.open)dialog.showModal();
 refreshBuildingLock();
};
document.getElementById('buildingUnifiedActivities').onclick=async e=>{const button=e.target.closest('[data-edit-activity]');if(!button||replayMode)return;document.getElementById('buildingDialog').close();await openMechanics();const row=[...document.querySelectorAll('#mechanicsActivities details')].find(el=>el.querySelector('summary small')?.textContent.includes(button.dataset.editActivity));if(row){row.open=true;row.scrollIntoView({block:'center'});}};
document.getElementById('addShopProduct').onclick=()=>{if(simRunning||replayMode)return;collectShop();const code=document.getElementById('newShopCode').value.trim();if(!/^[a-z][a-z0-9_]{1,34}$/.test(code))return toast('商品代码只能是小写英文、数字与下划线',false);const key='buy_'+code;if(shopDraft[key])return toast('商品代码已存在',false);shopDraft[key]={name:document.getElementById('newShopName').value.trim(),emoji:document.getElementById('newShopEmoji').value.trim(),price:Number(document.getElementById('newShopPrice').value),enabled:true,builtin:false};renderShop();};
document.getElementById('saveShopProducts').onclick=async()=>{if(simRunning||identityPhase||replayMode)return toast('请先暂停模拟',false);for(const input of document.querySelectorAll('#shopEditor input'))if(!input.reportValidity())return;collectShop();try{await jput('/api/town/shop',{products:shopDraft});await refreshState();await openTownBuilding('S');toast('商店货架已保存');}catch(err){toast(err.message,false);}};
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

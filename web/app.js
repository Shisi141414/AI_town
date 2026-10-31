"use strict";
let replayMode=false, replayTimer=null, replayId="", replayIndex=0, replayTotal=0, replayRequest=0, replayPlayVersion=0, replaySource="replay", replayCanContinue=false;
const replaySpeeds=[1,2,4,8];
let replaySpeed=1;
/* ================= 工具 ================= */
const $ = id => document.getElementById(id);
const API = "/api";
const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
window.addEventListener("unhandledrejection", e => { toast(e.reason?.message || "操作失败，请稍后重试", false); });
async function jpost(url, body, allowReplay=false) {
  if(replayMode&&!allowReplay)throw Error("回放期间无法修改实时世界，请先返回实时");
  const r = await fetch(url, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body||{})});
  if (!r.ok) throw new Error((await r.json().catch(()=>({detail:r.statusText}))).detail);
  return r.json();
}
async function jput(url, body) {
  if(replayMode)throw Error("回放期间无法修改实时世界，请先返回实时");
  const r = await fetch(url, {method:"PUT", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body||{})});
  if (!r.ok) throw new Error((await r.json().catch(()=>({detail:r.statusText}))).detail);
  return r.json();
}
async function jget(url) { const r = await fetch(url); if (!r.ok) throw new Error("读取失败：" + r.status); return r.json(); }
function toast(msg, ok=true) {showEventPopup({scope:'user',text:msg},ok);}

/* ================= 场景与地图 ================= */
let curScene = "house";
let MAPS = {house: null, town: null};      // rows
let townConfig=null, marketPrice=10, stocks={}, shopProducts={};
let players = [], vehicles = [], selectedPid = null, bubbles = [];
let decorCommon = 0, identityPhase = false;

const TILE_HOUSE = 32, TILE_TOWN = 28;     // 室内比例尺更大
const cv = $("cv"), ctx = cv.getContext("2d");

function tilePx() { return curScene === "house" ? TILE_HOUSE : TILE_TOWN; }

function resize() {
  const rows = MAPS[curScene]; if (!rows) return;
  cv.width = rows.length * tilePx(); cv.height = cv.width;
  const stage = $("stage"), mobile = window.innerWidth <= 760;
  const availableHeight = mobile ? Math.min(window.innerWidth - 40, 600) : stage.clientHeight - 170;
  const scale = Math.max(.1, Math.min((stage.clientWidth - 56) / cv.width, availableHeight / cv.height, 1.6));
  cv.style.width = Math.floor(cv.width * scale) + "px";
  cv.style.height = Math.floor(cv.height * scale) + "px";
  if (mobile) stage.style.minHeight = (Math.floor(cv.height*scale)+170)+"px";
  else stage.style.minHeight = "";
  draw();
}
window.addEventListener("resize", resize);
function draw() {
  const rows = MAPS[curScene]; if (!rows) return;
  PixelScene.draw(ctx, rows, tilePx(), curScene, players, vehicles, selectedPid);
  $("stageTitle").textContent = `${curScene === "house" ? "合租之家" : "小镇地图"} · ${players.filter(p=>p.scene===curScene).length} 位室友 · 装饰 ${Math.round(decorCommon||0)}`;
  $("sceneCaption").textContent = curScene === "house" ? "一屋灯火，几位室友。" : "出门逛逛，日子慢慢。";
}
cv.addEventListener("click", e => {
  const rect = cv.getBoundingClientRect(), x = (e.clientX-rect.left)/rect.width*26, y = (e.clientY-rect.top)/rect.height*26;
  const p = players.filter(p=>p.scene===curScene).sort((a,b)=>Math.hypot(a.x+.5-x,a.y+.5-y)-Math.hypot(b.x+.5-x,b.y+.5-y))[0];
  if(curScene==='town'){
    const code=MAPS.town?.[Math.floor(y)]?.[Math.floor(x)];
    if(window.openTownBuilding&&'SCROUBMGPFWJ~'.includes(code||'!')){openTownBuilding(code==='~'?'U':code);return;}
  }
  if(p && Math.hypot(p.x+.5-x,p.y+.5-y)<1.6){ selectedPid=p.pid;renderPlayers();renderTravel();draw(); }
});

/* ================= 场景切换 ================= */
document.querySelectorAll("#sceneTabs button").forEach(btn => {
  btn.onclick = async () => {
    document.querySelectorAll("#sceneTabs button").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    curScene = btn.dataset.scene;
    if (!MAPS[curScene]) await loadMap(curScene);
    resize(); draw();
  };
});
async function loadMap(scene) {
  const m = await jget(`${API}/map?scene=${scene}`);
  MAPS[scene] = m.rows;
}

/* ================= 玩家面板 ================= */
const STAT_STYLE = {
  hp: ["生命", "#e74c3c"], satiety: ["饱食", "#e67e22"], energy: ["精力", "#f1c40f"],
  mood: ["心情", "#9b59b6"], money: ["金钱", "#27ae60"],
};
const memoryTiers = new Map(), memoryPages = new Map();
let memorySaveId = null;
function memoryPanel(p, opened) {
  const pid = p.pid, tier = memoryTiers.get(pid) || "1";
  const page = replayMode ? null : memoryPages.get(`${pid}:${tier}`);
  const options = [["1", "一级 · 日常经历"], ["2", "二级 · 精简记忆"], ["3", "三级 · 深层记忆（永久）"]]
    .map(([value, label]) => `<option value="${value}" ${tier===value?'selected':''}>${label}</option>`).join("");
  return `<details class="resident-memory" data-open-key="memory-${esc(pid)}" ${opened.has('memory-'+pid)?'open':''}>
    <summary>记忆提取 · ${p.memory_counts?.["1"]||0} / ${p.memory_counts?.["2"]||0} / ${p.memory_counts?.["3"]||0}</summary>
    <div class="resident-memory-body">
      <select class="mem-tier" aria-label="${esc(p.name)}的记忆层级">${options}</select>
      <p class="hint mem-stats">${page?.stats||'选择层级后读取该居民的记忆。'}</p>
      <button class="btn mem-load" type="button" ${replayMode?'disabled':''}>读取记忆流</button>
      <div class="mem-list">${page?.html||''}</div>
      <button class="btn mem-more" type="button" ${page?.hasMore&&!replayMode?'':'hidden'}>继续读取</button>
      ${replayMode?'<p class="hint">回放期间无法读取该帧的记忆流。</p>':''}
    </div>
  </details>`;
}
function renderPlayers() {
  const opened=new Set([...document.querySelectorAll("#playerList details[open]")].map(e=>e.dataset.openKey));
  $("playerList").innerHTML = players.map(p => {
    const bars = Object.entries(STAT_STYLE).map(([k, [label, color]]) => {
      const v = k === "money" ? p.stats.money : p.stats[k];
      const pct = k === "money" ? Math.min(100, v/2) : v;
      return `<div class="bar"><label>${label}</label><div class="track"><div class="fill" style="width:${pct}%;background:${color}"></div></div><span class="val">${v.toFixed(k === "money" ? 2 : 0)}</span></div>`;
    }).join("");
    const items = Object.entries(p.items||{}).filter(([,v])=>v>0)
      .map(([k,v]) => esc(({flower:"💐",plant:"🪴",meal:"🍱",medicine:"💊"}[k]||shopProducts['buy_'+k.slice(5)]?.emoji||k)+"×"+v)+(k==="meal"&&(p.meal_expiry||[]).length?`(剩${Math.min(...p.meal_expiry.slice(0,v))}刻)`:"")).join(" ");
    const decor = (p.decor_room||0) > 0 ? ` · 🌸装饰${p.decor_room.toFixed(0)}` : "";
    const doing = p.activity
      ? `<div class="doing">${esc(p.activity_emoji)} 正在<b>${esc(p.activity)}</b> · 剩余${p.activity_minutes_left||0}分钟${p.target_name ? " → " + esc(p.target_name) : ""}</div>`
      : p.hospitalized
      ? `<div class="doing">🏥 住院治疗中 · 费用与恢复按当前机制结算</div>`
      : p.awaiting_admission
      ? `<div class="doing">📞 正在向室友借钱凑急救费…</div>`
      : `<div class="doing">💭 ${p.state === "riding" ? "搭乘室友的汽车" : p.state === "moving" ? "前往目的地 · " + esc(({walk:"步行",bicycle:"骑行",car:"驾车",passenger:"乘车"})[p.transport]||"步行") : ["deciding","waiting_model"].includes(p.state) ? esc(p.decision_status||"等待模型回复…") : esc(p.plan || "休息片刻")}</div>`;
    const sceneTag = p.scene === "house" ? "🏠" : "🌤";
    return `<div class="pcard ${p.pid === selectedPid ? "selected" : ""}" data-pid="${esc(p.pid)}">
      <div class="head"><span class="dot" style="background:${p.color}"></span><b>${esc(p.emoji)} ${esc(p.name)}</b><span class="model" title="${esc(p.model||"未绑定模型，使用规则模式")}">${p.model ? esc(p.model) : "规则模式"}</span><span class="tag">${esc(p.gender || "男")}</span></div>
      ${bars}${doing}
      ${(p.relationships||[]).length?`<div class="hint resident-relations">💞 ${p.relationships.map(r=>`${esc(r.name)} · ${esc(r.type)}（第${r.days}天）`).join('　')}</div>`:''}
      ${p.cycle_status?`<details class="resident-cycle" data-open-key="cycle-${esc(p.pid)}" ${opened.has('cycle-'+p.pid)?'open':''}><summary>身体节律 · ${esc(p.cycle_status.split('：')[0])}</summary><p>${esc(p.cycle_status)}</p></details>`:''}
      <div class="hint resident-skills">📚 ${Object.entries(p.skills||{}).filter(([,v])=>v>0).map(([k,v])=>esc(({computer:'计算机',science:'理学',literature:'文学',medicine:'医学',economics:'经济学',cooking:'烹饪'})[k]||k)+' '+Math.min(5,Math.floor(v/100))+'级 ('+Number(v.toFixed(2))+'/500)').join(' · ')||'尚未学习技能'}</div>
      <div class="hint">📈 小镇股市：${Object.entries(stocks).map(([k,r])=>esc(r.name)+' '+Number(r.price).toFixed(2)).join('｜')||('指数 '+marketPrice.toFixed(2))}${(()=>{const port=Object.entries(p.stock_portfolio||{}).filter(([,v])=>v.shares>0);return port.length?'<br>持仓：'+port.map(([k,v])=>esc((stocks[k]||{}).name||k)+'×'+v.shares+'份(成本'+Number(v.cost).toFixed(2)+')').join('，'):'';})()}</div>
      <details class="resident-computer" data-open-key="computer-${esc(p.pid)}" ${opened.has('computer-'+p.pid)?'open':''}><summary>💻 ${esc(p.name)}的电脑 · ${(p.computer_files||[]).length}篇记录 <small>只读</small></summary>
      ${(p.computer_files||[]).map(f=>`<details data-open-key="file-${esc(p.pid)}-${esc(f.id)}" ${opened.has('file-'+p.pid+'-'+f.id)?'open':''}><summary>${esc(f.title)}</summary><pre class="computer-content">${esc(f.content)}</pre></details>`).join('')||'<p class="hint">还没有主动记录</p>'}</details>
      <details class="resident-transfers" data-open-key="money-${esc(p.pid)}" ${opened.has('money-'+p.pid)?'open':''}><summary>资金往来 · ${(p.transactions||[]).length}笔</summary>${[...(p.transactions||[])].reverse().map(t=>{const amt=Number.isFinite(Number(t.amount))?Number(t.amount).toFixed(2):""+(t.delta??"?");const from=t.from||"我",to=t.to||"诊所";return `<p class="hint">第${t.day??"?"}天 ${esc(t.clock??"")} · ${esc(from)} → ${esc(to)} · ${amt}元<br>${esc(t.actor??"诊所")}：${esc(t.reason||"")}</p>`}).join('')||'<p class="hint">暂无资金往来</p>'}</details>
      <details class="resident-computer" data-open-key="prompt-${esc(p.pid)}" ${opened.has('prompt-'+p.pid)?'open':''}><summary>🧠 最近一次模型提示词与原始回复</summary><details><summary>系统提示词</summary><pre class="computer-content">${esc(p.last_system_prompt||'尚未发送')}</pre></details><details><summary>用户提示词</summary><pre class="computer-content">${esc(p.last_user_prompt||'尚未发送')}</pre></details><details><summary>原始回复</summary><pre class="computer-content">${esc(p.last_raw_reply||'尚未收到')}</pre></details></details>
      <div class="hint">${esc(p.illness || "健康")}</div>
      <div class="belong">${sceneTag} ${esc(p.home)}${decor}${items ? " · " + items : ""}${p.model ? ` · <span title="${esc(p.model)}">${esc(p.model)}</span>` : ""}</div>
      ${memoryPanel(p, opened)}
    </div>`;
  }).join("");
  // 兜底：面板刷新重建后，已展开但还没读过缓存的记忆面板自动补加载
  document.querySelectorAll("#playerList .resident-memory[open]").forEach(panel=>{
    const pid=panel.closest('.pcard')?.dataset.pid; if(!pid||replayMode)return;
    if(!memoryPages.has(`${pid}:${memoryTiers.get(pid)||'1'}`))loadMemories(pid);
  });
  renderTravel();
}

/* ================= 关系网络 ================= */
function renderActiveRelationships(nodes){
  const found=new Map();
  for(const person of nodes)for(const relation of person.relationships||[]){
    const other=nodes.find(p=>p.pid===relation.other_pid||!relation.other_pid&&p.name===relation.name);
    const pair=[person.pid,other?.pid||relation.other_pid||relation.name].sort().join('|');
    if(!found.has(pair))found.set(pair,{person,otherName:other?.name||relation.name,
      type:relation.type,days:relation.days});
  }
  $('activeRelationsTitle').textContent=`当前关系 · ${found.size}段`;
  const records=[...found.values()];
  $('activeRelations').innerHTML=records.length?records.map(r=>`<div class="active-relation ${r.type==='恋爱'?'love':'friend'}"><span class="relation-icon">${r.type==='恋爱'?'♥':'✦'}</span><div><b>${esc(r.person.name)} 与 ${esc(r.otherName)}</b><small>${esc(r.type)} · 第${Number(r.days)||1}天</small></div></div>`).join(''):
    `<p class="hint">${nodes.length&&nodes.every(p=>!Object.hasOwn(p,'relationships'))?'此回放帧未记录正式关系':'暂无已建立的关系'}</p>`;
}
function drawRelations() {
  const c = $("relCanvas"), g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  const nodes = players, n = nodes.length;
  renderActiveRelationships(nodes);
  if (!n) return;
  const cx = c.width/2, cy = c.height/2, R = Math.min(cx, cy) - 52;
  const pos = nodes.map((_, i) => {
    const ang = -Math.PI/2 + i * 2*Math.PI/n;
    return [cx + R*Math.cos(ang), cy + R*Math.sin(ang)];
  });
  // 边
  for (let i = 0; i < n; i++) for (let j = i+1; j < n; j++) {
    const ab = nodes[i].affinity[nodes[j].name], ba = nodes[j].affinity[nodes[i].name];
    if (ab === undefined && ba === undefined) continue;
    const [ax,ay]=pos[i],[bx,by]=pos[j],mx=(ax+bx)/2,my=(ay+by)/2;
    const segment=(value,x1,y1,x2,y2)=>{
      const color=value===undefined?'#aaa69a':value>=80?'#27ae60':value>=60?'#3498db':value<30?'#c0392b':'#95a5a6';
      g.beginPath();g.moveTo(x1,y1);g.lineTo(x2,y2);
      g.strokeStyle=color;g.lineWidth=Math.min(4,.8+Math.abs((value??50)-50)/25);
      g.globalAlpha=.85;g.stroke();g.globalAlpha=1;
      const x=x1+(x2-x1)*.56,y=y1+(y2-y1)*.56;
      g.font='bold 10px monospace';g.textAlign='center';g.textBaseline='middle';
      g.fillStyle='#fff9eb';g.fillRect(x-11,y-7,22,14);
      g.fillStyle=color;g.fillText(value===undefined?'?':value.toFixed(0),x,y);
    };
    segment(ab,ax,ay,mx,my);
    segment(ba,bx,by,mx,my);
  }
  // 节点
  nodes.forEach((p, i) => {
    const [x, y] = pos[i];
    g.beginPath(); g.arc(x, y, 16, 0, Math.PI*2);
    g.fillStyle = p.color; g.fill(); g.lineWidth = 2.5; g.strokeStyle = "#fff"; g.stroke();
    g.font = "14px serif"; g.textAlign = "center"; g.textBaseline = "middle";
    g.fillStyle = "#fff"; g.fillText(p.emoji, x, y+1);
    g.font = "bold 11.5px sans-serif"; g.fillStyle = "#333";
    g.fillText(p.name, x, y + 27);
  });
  // 边明细列表
  const rows = [];
  for (let i = 0; i < n; i++) for (let j = i+1; j < n; j++) {
    const ab = nodes[i].affinity[nodes[j].name], ba = nodes[j].affinity[nodes[i].name];
    if (ab === undefined && ba === undefined) continue;
    const established=(nodes[i].relationships||[]).find(r=>r.name===nodes[j].name);
    rows.push(`<div class="ev"><span class="meta"></span>${esc(nodes[i].name)} ⇄ ${esc(nodes[j].name)}${established?` · ${esc(established.type)}第${established.days}天`:''}：
      ${ab!==undefined ? ab.toFixed(0) : "?"} / ${ba!==undefined ? ba.toFixed(0) : "?"}</div>`);
  }
  $("relList").innerHTML = rows.join("") || '<div class="hint">还没有人际互动</div>';
}

/* ================= 日志 ================= */
let allEvents = [], eventScope='game', eventQueryVersion=0;
const notificationSince=Date.now()/1000, notifiedEvents=new Set();
function showEventPopup(e,ok=true){
  const area=$("eventNotifications"),el=document.createElement('div');
  el.className='event-popup '+(e.scope==='game'?'game':'user')+(ok?'':' failed');
  const label=document.createElement('b');label.textContent=e.scope==='game'?'游戏事件':'提示';
  const text=document.createElement('span');text.textContent=e.text;
  const close=document.createElement('button');close.textContent='×';close.setAttribute('aria-label','关闭消息');close.onclick=()=>el.remove();
  el.append(label,text,close);area.append(el);
  while(area.children.length>3)area.firstElementChild.remove();
  setTimeout(()=>el.remove(),8000);
}
function notifyEvents(evs,worldId=''){
  for(const e of evs||[]){
    const key=worldId+':'+e.id;
    if(e.wall<notificationSince||notifiedEvents.has(key))continue;
    notifiedEvents.add(key);showEventPopup(e);
  }
  if(notifiedEvents.size>1000){const keep=[...notifiedEvents].slice(-500);notifiedEvents.clear();keep.forEach(k=>notifiedEvents.add(k));}
}
document.querySelectorAll('.event-tabs [data-scope]').forEach(button=>button.onclick=()=>{
  eventScope=button.dataset.scope;
  document.querySelectorAll('.event-tabs [data-scope]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-selected',String(b===button));});
  queryEvents();
});
function renderEvents(evs) {
  const list = $("logList");
  const html = (evs || allEvents).filter(e=>(e.scope==='game'?'game':'user')===eventScope)
    .filter(e=>!$("fType").value||e.type===$("fType").value)
    .filter(e=>!$("fDayFrom").value||e.day>=+$("fDayFrom").value)
    .filter(e=>!$("fDayTo").value||e.day<=+$("fDayTo").value)
    .filter(e=>!$("fQ").value||e.text.includes($("fQ").value)).slice(0, 300).map(e => {
    const cls = ["combat","dialogue","system","error","social"].includes(e.type) ? e.type : "";
    return `<div class="ev ${cls}"><span class="meta">D${e.day} ${e.clock} · ${e.scope==="game"?"事件":"提示"}</span>${esc(e.text)}</div>`;
  }).join("");
  list.innerHTML = html || '<div style="color:#999;padding:20px;text-align:center">还没有事件</div>';
}
async function queryEvents() {
  const version=++eventQueryVersion;
  if(replayMode){renderEvents(allEvents);return;}
  const params = new URLSearchParams({scope:eventScope});
  if ($("fType").value) params.set("type_", $("fType").value);
  if ($("fDayFrom").value) params.set("day_from", $("fDayFrom").value);
  if ($("fDayTo").value) params.set("day_to", $("fDayTo").value);
  if ($("fQ").value) params.set("q", $("fQ").value);
  const result=await jget(`${API}/events?` + params.toString());
  if(version!==eventQueryVersion)return;
  allEvents = result;
  renderEvents(allEvents);
}

/* ================= WebSocket ================= */
let ws;
function connectWS() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onmessage = msg => {
    if(replayMode)return;
    const d = JSON.parse(msg.data);
    if (d.type === "tick") {
      $("recordingTag").textContent=d.recording?"● 自动录制中":"○ 尚未录制";
      if(d.maps) MAPS = d.maps;
      townConfig=d.town_config||townConfig;marketPrice=d.market_price??marketPrice;stocks=d.stocks||stocks;shopProducts=d.shop_products||shopProducts;
      window.refreshBuildingLock?.(d.running||d.identity_phase);
      players = d.players; vehicles = d.vehicles || [];
      bubbles=players.filter(p=>p.speech).map(p=>({name:p.name,text:p.speech,until:Date.now()+3500}));
      decorCommon = d.decor_common;
      identityPhase = d.identity_phase;
      setRunning(d.running);
      simRunning = d.running;
      $("clockbox").textContent = `第${d.day}天 ${d.clock}`;
      $("modeTag").textContent = identityPhase ? "居民选择身份中…" : (d.running ? (players.some(p=>p.state==="waiting_model")?"等待模型 · 时钟暂缓":"运行中") : "已暂停");
      $("stageTitle").textContent = `公共装饰度：${(decorCommon||0).toFixed(0)} · ${players.filter(p=>p.scene===curScene).length} 人在此场景`;
      renderPlayers(); draw();
      renderPlayerAdmin();
      if ($("panel-relations").classList.contains("active")) drawRelations();
      if (d.events && d.events.length) {
        notifyEvents(d.events,d.save_id||"");
        allEvents = [...new Map([...d.events].reverse().concat(allEvents).map(e=>[e.id,e])).values()].slice(0, 1000);
        if ($("panel-logs").classList.contains("active")) renderEvents(allEvents);
      }
    } else if (d.type === "dialogue") {
      for (const ln of d.lines) {
        const m = ln.match(/^(.+?):\s*(.+)$/);
        if (m) bubbles.push({name: m[1], text: m[2], until: Date.now() + 12000});
      }
      notifyEvents([d.event]);
      allEvents.unshift(d.event);
      if ($("panel-logs").classList.contains("active")) renderEvents(allEvents);
    }
  };
  ws.onclose = () => setTimeout(connectWS, 2000);
}
function renderBubbles() {
  document.querySelectorAll(".bubble").forEach(b => b.remove());
  const now = Date.now();
  bubbles = bubbles.filter(b => b.until > now);
  const rect = cv.getBoundingClientRect();
  const scale = cv.width / rect.width;
  const tp = tilePx();
  for (const b of bubbles) {
    const p = players.find(p => p.name === b.name && p.scene === curScene);
    if (!p) continue;
    const el = document.createElement("div");
    el.className = "bubble"; el.textContent = b.text;
    el.style.left = (cv.offsetLeft + (p.x+0.5)*tp/scale) + "px";
    el.style.top = (cv.offsetTop + p.y*tp/scale) + "px";
    $("stage").appendChild(el);
  }
}
setInterval(renderBubbles, 400);

/* ================= 控制 ================= */
$("btnStart").onclick = async () => { await jpost(`${API}/control`, {cmd:"start"}); await refreshState(); };
$("btnPause").onclick = async () => { await jpost(`${API}/control`, {cmd:"pause"}); await refreshState(); };
$("btnStep").onclick = async () => { await jpost(`${API}/control`, {cmd:"step"}); await refreshState(); };
$("btnReset").onclick = async () => {
  if (!confirm("先自动保存当前世界，再创建新存档？新档将从早上八点开始，居民重新选择身份，所有层级的记忆清空。")) return;
  await jpost(`${API}/control`, {cmd:"reset"});
  MAPS = {house:null, town:null};
  await loadMap(curScene); resize();
  allEvents = []; players = [];
  toast("旧世界已存档，新档记忆为空"); await refreshState(); await loadSaves();
};
$("speedSel").onchange = e => jpost(`${API}/control`, {cmd:"speed", speed:+e.target.value});
function setRunning(r) { simRunning=r; if(replayMode){$("btnStart").disabled=true;$("btnPause").disabled=true;$("btnStep").disabled=true;return;} $("btnStart").disabled = r; $("btnPause").disabled = !r; $("btnStep").disabled = r || identityPhase; }

/* ================= 指南 ================= */
$("btnGuide").onclick = () => $("guideOverlay").classList.add("show");
window.closeGuide = () => $("guideOverlay").classList.remove("show");
$("guideOverlay").onclick = e => { if (e.target.id === "guideOverlay") closeGuide(); };

/* ================= Tab ================= */
document.querySelectorAll("nav button").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll("nav button").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $("panel-" + btn.dataset.panel).classList.add("active");
    if (btn.dataset.panel === "replays") {loadReplays();loadSaves();}
    if (btn.dataset.panel === "logs") renderEvents(allEvents);
    if (btn.dataset.panel === "relations") drawRelations();
  };
});

/* ================= 档案 ================= */
let profiles = [], activeProfile = null, editingProfileId = null;
const testingProfiles = new Set();
async function loadProfiles() {
  const d = await jget(`${API}/profiles`);
  profiles = d.profiles; activeProfile = d.active;
  $("profileList").innerHTML = profiles.map(p => `
    <div class="profile-item ${p.id === activeProfile ? "active" : ""}">
      <div class="row">
        <b>${esc(p.name)}</b>
        <span class="tag ${esc(p.format)}">${esc(p.format)}</span>
        <div class="ops">
          ${p.id !== activeProfile ? `<button class="btn" data-action="activate" data-id="${esc(p.id)}">启用</button>` : '<span style="color:var(--green);font-size:11px">✓ 当前</span>'}
          <button class="btn" data-action="test" data-id="${esc(p.id)}" ${testingProfiles.has(p.id)?'disabled':''}>${testingProfiles.has(p.id)?'测试中…':'测试'}</button>
          <button class="btn" data-action="edit" data-id="${esc(p.id)}">编辑</button>
          <button class="btn" data-action="delete" data-id="${esc(p.id)}">删除</button>
        </div>
      </div>
      <div style="font-size:11px;color:#999;margin-top:4px">${esc(p.base_url)} · 默认 ${esc(p.default_model || "-")}</div>
    </div>`).join("") || '<div class="hint">还没有档案。新增一个，填入网关地址和 Key，小镇居民就会接入大模型。</div>';
  await loadBinds();
}
window.activateProfile = async id => {
  await jpost(`${API}/profiles/active`, {id});
  toast("已切换档案"); loadProfiles(); refreshState();
};
window.testProfile = async id => {
  if(testingProfiles.has(id)) return;
  testingProfiles.add(id);
  const button=()=>Array.from($("profileList").querySelectorAll('[data-action="test"]')).find(b=>b.dataset.id===id);
  if(button()){button().disabled=true;button().textContent='测试中…';}
  toast("正在等待模型回答，测试沿用设置中的模型等待时间…");
  try {
    const r = await jpost(`${API}/profiles/test`, {id});
    toast(r.message, r.ok);
  } catch(e) { toast("无法连接本地小镇服务，请检查服务是否运行",false); }
  finally {testingProfiles.delete(id);if(button()){button().disabled=false;button().textContent='测试';}}
};
window.delProfile = async id => {
  if (!confirm("删除该档案？")) return;
  await fetch(`${API}/profiles/${id}`, {method:"DELETE"});
  loadProfiles();
};
function showProfileForm(p=null) {
  editingProfileId=p?.id||null;
  $("pfName").value=p?.name||"";
  $("pfFormat").value=p?.format||"openai";
  $("pfUrl").value=p?.base_url||"";
  $("pfModel").value=p?.default_model||"";
  $("pfKey").value="";
  $("pfKey").placeholder=p?"留空保留原密钥":"填写 API Key";
  $("btnSaveProfile").textContent=p?"保存修改":"保存档案";
  $("fmStatus").textContent="";
  $("profileForm").style.display = "block";
  $("pfModelList").style.display = "none";
  $("profileForm").scrollIntoView({block:'nearest',behavior:'smooth'});
}
window.editProfile = id => {const p=profiles.find(p=>p.id===id);if(p)showProfileForm(p);};
$("btnAddProfile").onclick = () => showProfileForm();
$("btnCancelProfile").onclick = () => { $("profileForm").style.display = "none"; $("pfKey").value=""; editingProfileId=null; };
$("btnSaveProfile").onclick = async () => {
  const body = {
    name: $("pfName").value.trim(), format: $("pfFormat").value,
    base_url: $("pfUrl").value.trim(), api_key: $("pfKey").value.trim(),
    default_model: $("pfModel").value.trim(),
  };
  if (!body.name || !body.base_url || (!editingProfileId && !body.api_key)) return toast("名称/地址/Key 不能为空（编辑时可留空保留原密钥）", false);
  const id=editingProfileId;
  const r = await fetch(`${API}/profiles${id?'/'+encodeURIComponent(id):''}`, {method:id?"PUT":"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)});
  if (!r.ok) return toast("保存失败", false);
  $("profileForm").style.display = "none";
  $("pfKey").value="";
  if(id)delete modelCache[id];
  editingProfileId=null;
  toast("档案已保存"); loadProfiles(); refreshState();
};

/* --- 获取模型列表 --- */
$("btnFetchModels").onclick = async () => {
  const url = $("pfUrl").value.trim(), key = $("pfKey").value.trim(), fmt = $("pfFormat").value;
  if (!url || (!key && !editingProfileId)) return toast("请先填写 API 地址和 Key", false);
  $("fmStatus").textContent = "获取中…";
  // 密钥放入请求体，避免进入网址与访问日志。
  const r = await fetch(`${API}/models`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:"draft",base_url:url,api_key:key,format:fmt,profile_id:editingProfileId})}).catch(()=>null);
  let d = null;
  if (r && r.ok) d = await r.json();
  if (!d || !d.ok) {
    $("fmStatus").textContent = d ? ("获取失败: " + (d.message||"").slice(0,80)) : "请求失败";
    $("pfModelList").style.display = "none";
    return;
  }
  $("fmStatus").textContent = `获取到 ${d.count} 个模型，点击选择：`;
  const sel = $("pfModelList");
  sel.innerHTML = d.models.map(m => `<option>${esc(m)}</option>`).join("");
  sel.style.display = "block";
  sel.onchange = () => { $("pfModel").value = sel.value; };
  toast(`获取到 ${d.count} 个模型`);
};

/* ================= 模型绑定（档案+模型两级选择） ================= */
let cfgProfiles = [];        // [{id, name, default_model}]
let modelCache = {};         // profile_id → [model,...]（懒加载缓存）

async function fetchModelsOf(pid_profile) {
  if (modelCache[pid_profile]) return modelCache[pid_profile];
  try {
    const d = await jget(`${API}/models?profile_id=${pid_profile}`);
    modelCache[pid_profile] = d.ok ? d.models : [];
  } catch (e) { modelCache[pid_profile] = []; }
  return modelCache[pid_profile];
}

async function loadBinds() {
  const d = await jget(`${API}/config`);
  cfgProfiles = d.profiles || [];
  const playersCfg = d.players || [];
  const act = d.active_profile;
  $("modelBinds").innerHTML = playersCfg.map(p => {
    const profId = p.binding?.profile_id || act || "";
    const model = p.binding?.model || "";
    const opts = cfgProfiles.map(pr =>
      `<option value="${esc(pr.id)}" ${pr.id === profId ? "selected" : ""}>${esc(pr.name)}</option>`).join("");
    return `<div class="bind-row" data-pid="${esc(p.pid)}" style="border:1px solid #e0e4e8;border-radius:8px;padding:8px;margin-bottom:8px">
      <div style="display:flex;gap:6px;align-items:center;margin-bottom:5px">
        <b style="font-size:12.5px;width:56px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.name)}</b>
        <select class="bind-prof" style="flex:1;font-size:12px;padding:4px;border:1px solid #d0d7de;border-radius:5px">
          <option value="">（跟随默认档案）</option>${opts}
        </select>
      </div>
      <div style="display:flex;gap:6px;align-items:center">
        <select class="bind-model" style="flex:1;font-size:12px;padding:4px;border:1px solid #d0d7de;border-radius:5px">
          <option value="">${esc(model ? model : "（档案默认模型）")}</option>
        </select>
        <button class="btn bind-load" style="padding:3px 8px;font-size:11px;white-space:nowrap">↻ 载模型</button>
      </div>
    </div>`;
  }).join("") || '<div class="hint">还没有玩家</div>';

  // 已有模型选择的直填
  document.querySelectorAll(".bind-row").forEach(row => {
    const pid = row.dataset.pid;
    const cfg = playersCfg.find(p => p.pid === pid);
    if (cfg?.binding?.model) {
      const sel = row.querySelector(".bind-model");
      const opt = document.createElement("option");
      opt.value = cfg.binding.model; opt.textContent = cfg.binding.model; opt.selected = true;
      sel.prepend(opt);
    }
    // 载模型按钮：拉取该档案模型列表填充下拉
    row.querySelector(".bind-load").onclick = async e => {
      const btn = e.target; btn.disabled = true; btn.textContent = "…";
      const profId = row.querySelector(".bind-prof").value || act || "";
      if (!profId) { toast("请先选择档案（或启用一个默认档案）", false); btn.disabled = false; btn.textContent = "↻ 载模型"; return; }
      const models = await fetchModelsOf(profId);
      const sel = row.querySelector(".bind-model");
      const cur = sel.value;
      const def = cfgProfiles.find(p => p.id === profId)?.default_model || "";
      sel.innerHTML = `<option value="">${esc(def ? "默认：" + def : "（档案默认模型）")}</option>` +
        models.map(m => `<option value="${esc(m)}" ${m === cur ? "selected" : ""}>${m}</option>`).join("");
      btn.disabled = false; btn.textContent = "↻ 载模型";
      toast(models.length ? `载入 ${models.length} 个模型` : "该档案无模型或获取失败", !!models.length);
    };
  });
  $("setDialogue").checked = d.settings.dialogue_generation;
  $("setCycle").checked = d.settings.cycle_enabled !== false;
  $("setTemp").value = d.settings.temperature;
  $("setTimeout").value = d.settings.decision_timeout;
}
$("btnSaveBinds").onclick = async () => {
  const binds = {};
  document.querySelectorAll(".bind-row").forEach(row => {
    binds[row.dataset.pid] = {
      profile_id: row.querySelector(".bind-prof").value,
      model: row.querySelector(".bind-model").value,
    };
  });
  await jput(`${API}/config`, {player_bindings: binds});
  toast("绑定已保存"); refreshState();
};
$("btnSaveSettings").onclick = async () => {
  const cycleEnabled = $("setCycle").checked;
  await jput(`${API}/config`, {
    dialogue_generation: $("setDialogue").checked,
    cycle_enabled: cycleEnabled,
    temperature: +$("setTemp").value,
    decision_timeout: +$("setTimeout").value,
  });
  const confirmed = await jget(`${API}/config`);
  if (confirmed.settings?.cycle_enabled !== cycleEnabled) {
    toast("服务尚未加载新版本，请重启后重新保存设置", false);
    return;
  }
  toast("设置已保存");
};

/* ================= 提示词 ================= */
async function loadPrompt() {
  const d = await jget(`${API}/prompt`);
  $("sysPrompt").value = d.system_prompt;
  $("sysPrompt").dataset.default = d.default_prompt;
}
$("btnSavePrompt").onclick = async () => {
  await jput(`${API}/prompt`, {system_prompt: $("sysPrompt").value});
  toast("提示词已保存，下次决策生效");
};
$("btnResetPrompt").onclick = async () => {
  $("sysPrompt").value = $("sysPrompt").dataset.default || "";
  await jput(`${API}/prompt`, {system_prompt: ""});
  toast("已恢复默认提示词");
};

/* ================= 玩家管理（未运行时） ================= */
let simRunning = false;
function renderPlayerAdmin() {
  $("playerAdmin").innerHTML = players.map(p => `
    <div style="display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px dashed #eee">
      <span class="dot" style="width:10px;height:10px;border-radius:50%;background:${p.color};display:inline-block"></span>
      <span style="font-size:12.5px;flex:1">${esc(p.emoji)} ${esc(p.name)}</span>
      <button class="btn" style="padding:2px 9px;font-size:11px" ${simRunning ? "disabled" : ""}
              data-remove="${esc(p.pid)}">移除</button>
    </div>`).join("");
  $("btnAddPlayer").disabled = simRunning;
}
window.removePlayer = async name => {
  if (!confirm(`移除 ${name}？`)) return;
  try {
    const r = await fetch(`${API}/players/${encodeURIComponent(name)}`, {method:"DELETE"});
    if (!r.ok) { const d = await r.json(); throw new Error(d.detail || "失败"); }
    toast(`${name} 已离开`);
    await refreshState(); loadBinds(); renderPlayerAdmin();
  } catch (e) { toast(e.message, false); }
};
$("btnAddPlayer").onclick = async () => {
  const name = $("npName").value.trim();
  if (!name) return toast("名字不能为空", false);
  try {
    await jpost(`${API}/players`, {name, color: $("npColor").value, emoji: $("npEmoji").value || "🙂",
      join_identity: $("npJoinIdentity").checked});
    $("npName").value = "";
    toast(`${name} 已入住`);
    await refreshState(); loadBinds(); renderPlayerAdmin();
  } catch (e) { toast(e.message, false); }
};

/* ================= 每位居民的记忆 ================= */
async function loadMemories(pid, more=false) {
  if(replayMode)return;
  const tier=memoryTiers.get(pid)||"1", key=`${pid}:${tier}`;
  const page=memoryPages.get(key)||{offset:0,html:"",hasMore:false,stats:"",request:0};
  if(!more){page.offset=0;page.html="";}
  const request=++page.request, saveId=memorySaveId;
  memoryPages.set(key,page);
  // 读取状态可见化：发起即显示"读取中"，失败显示原因——不静默空白
  const cardNow=[...$("playerList").querySelectorAll('.pcard')].find(el=>el.dataset.pid===pid);
  if(cardNow&&!more)cardNow.querySelector('.mem-stats').textContent='读取中…';
  let d;
  try{
    d=await jget(`${API}/players/${encodeURIComponent(pid)}/memories?tier=${tier}&offset=${page.offset}&limit=40`);
  }catch(err){
    if(page.request===request)page.stats=`读取失败：${err?.message||err}`;
    const c=[...$("playerList").querySelectorAll('.pcard')].find(el=>el.dataset.pid===pid);
    if(c)c.querySelector('.mem-stats').textContent=page.stats;
    return;
  }
  if(page.request!==request||memorySaveId!==saveId)return;
  page.stats=`一级 ${d.counts["1"]} · 二级 ${d.counts["2"]} · 三级 ${d.counts["3"]}（永久）`;
  page.html+=d.items.map(m=>`<div class="mi"><span class="t">[L${m.tier}·D${m.day}·${m.tick}] ${esc(m.selected_by)}${m.tier>1?" · 权重"+m.selection_weight:""}</span><br>${esc(m.content)}</div>`).join("");
  page.offset+=d.items.length;page.hasMore=d.has_more;
  if(!page.offset)page.html='<div class="hint">这一层还没有记忆</div>';
  const card=[...$("playerList").querySelectorAll('.pcard')].find(el=>el.dataset.pid===pid);
  // 守卫与取值对称：未切换过层级的居民 memoryTiers 里没有条目（undefined），
  // 必须同样按默认 "1" 比较，否则首次加载的结果永远被丢弃
  if(!card||(memoryTiers.get(pid)||"1")!==tier)return;
  card.querySelector('.mem-stats').textContent=page.stats;
  card.querySelector('.mem-list').innerHTML=page.html;
  card.querySelector('.mem-more').hidden=!page.hasMore;
}
$("playerList").addEventListener("click",e=>{
  const card=e.target.closest('.pcard'), panel=e.target.closest('.resident-memory');
  if(!card||!panel)return;
  if(e.target.closest('.mem-load'))loadMemories(card.dataset.pid);
  else if(e.target.closest('.mem-more'))loadMemories(card.dataset.pid,true);
});
// 展开「记忆提取」即自动读取（toggle 不冒泡，走捕获阶段；比 click+setTimeout 稳定，
// 不受事件默认行为时序与面板刷新竞态影响）
$("playerList").addEventListener("toggle",e=>{
  const panel=e.target.closest('.resident-memory');
  if(!panel||!e.target.open)return;
  const card=panel.closest('.pcard'); if(!card)return;
  const pid=card.dataset.pid;
  if(!memoryPages.has(`${pid}:${memoryTiers.get(pid)||'1'}`))loadMemories(pid);
},true);
$("playerList").addEventListener("change",e=>{
  if(!e.target.matches('.mem-tier'))return;
  const card=e.target.closest('.pcard'), pid=card.dataset.pid;
  memoryTiers.set(pid,e.target.value);
  const page=memoryPages.get(`${pid}:${e.target.value}`);
  card.querySelector('.mem-stats').textContent=page?.stats||'选择层级后读取该居民的记忆。';
  card.querySelector('.mem-list').innerHTML=page?.html||'';
  card.querySelector('.mem-more').hidden=!page?.hasMore;
  if(!page&&!replayMode)loadMemories(pid);
});
$("btnFilter").onclick = queryEvents;

/* ================= AI 居民出行与车辆 ================= */
function renderTravel() {
  if (!players.some(p=>p.pid===selectedPid)) selectedPid=players[0]?.pid||null;
  const sel=$("travelResident"), before=sel.value;
  const options=players.map(p=>`<option value="${esc(p.pid)}">${esc(p.name)} · ${p.scene==="town"?"在小镇":"在家"}</option>`).join("");
  if(sel.innerHTML!==options)sel.innerHTML=options;
  sel.value=selectedPid||before;
  const p=players.find(p=>p.pid===selectedPid);
  for(const b of document.querySelectorAll("#travelModes button")){b.disabled=replayMode||!p||!!p.vehicle_driver||(b.dataset.mode==='car'&&!p.driver_license);b.classList.toggle("active",p?.travel_mode===b.dataset.mode);}
  if(!p)return;
  const owner=vehicles.find(v=>v.owner_id===p.pid),shared=vehicles.find(v=>!v.owner_id),driver=players.find(p=>p.pid===shared?.driver_id);
  $("travelInfo").textContent=p.vehicle_driver ? `正在搭乘 ${players.find(o=>o.pid===p.vehicle_driver)?.name||"室友"} 的汽车。` : p.scene==="house" ? "室内始终步行。可以提前安排室外出行。" : "设置该 AI 居民的出行方式，正在进行的路线也会更新。";
  if(!p.vehicle_driver&&!p.driver_license)$("travelInfo").textContent+=' 开车需要先在学校完成驾照课程；乘车无需驾照。';
  $("carStatus").textContent=`共用车：${driver?driver.name+" 使用中":"停在 ("+(shared?.x??2)+", "+(shared?.y??12)+")"}。${owner?"已拥有私家车。":"尚无私家车。"}`;
  const carPrice=townConfig?.S?.car?.price??1000;
  $("buyCar").disabled=replayMode||!!owner||p.stats.money<carPrice;
  $("buyCar").textContent=owner?"已拥有私家车":`购买私家车 · ${carPrice}${p.stats.money<carPrice?"（余额不足）":""}`;
  const nearby=players.filter(d=>d.pid!==p.pid&&d.scene==="town"&&p.scene==="town"&&d.car_id&&!d.vehicle_driver&&d.travel_mode==="car"&&d.passengers.length<3&&Math.max(Math.abs(d.x-p.x),Math.abs(d.y-p.y))<=2&&vehicles.some(v=>v.id===d.car_id&&v.x===d.x&&v.y===d.y));
  const ride=$("rideDriver"),old=ride.value;
  ride.innerHTML=nearby.map(d=>`<option value="${esc(d.pid)}">${esc(d.name)} · 余 ${3-d.passengers.length} 座</option>`).join("")||'<option value="">附近没有可搭乘的司机</option>';
  if(nearby.some(d=>d.pid===old))ride.value=old;
  $("boardCar").disabled=replayMode||!nearby.length||!!p.vehicle_driver||!!p.passengers.length;
  $("leaveCar").disabled=replayMode||!p.vehicle_driver;
  $("rideInfo").textContent=p.passengers.length?`车上室友：${p.passengers.map(id=>players.find(o=>o.pid===id)?.name||id).join("、")}`:"在室外两格范围内上车；回家自动下车。";
}
$("travelResident").onchange=e=>{selectedPid=e.target.value;renderPlayers();draw();};
$("playerList").addEventListener("click",e=>{if(e.target.closest("details"))return;const card=e.target.closest(".pcard");if(card){selectedPid=card.dataset.pid;renderPlayers();draw();}});
$("travelModes").addEventListener("click",async e=>{const b=e.target.closest("[data-mode]");if(!b||!selectedPid)return;await jput(`${API}/players/${selectedPid}/travel`,{mode:b.dataset.mode});await refreshState();toast("出行方式已更新");});
$("buyCar").onclick=async()=>{await jpost(`${API}/players/${selectedPid}/car`,{});await refreshState();toast("私家车购买成功，已停在附近道路");};
$("boardCar").onclick=async()=>{await jput(`${API}/players/${selectedPid}/ride`,{driver_id:$("rideDriver").value});await refreshState();toast("已上车，将跟随室友出行");};
$("leaveCar").onclick=async()=>{await jput(`${API}/players/${selectedPid}/ride`,{driver_id:null});await refreshState();toast("已下车");};
$("playerAdmin").addEventListener("click",e=>{const b=e.target.closest("[data-remove]");if(b){const p=players.find(p=>p.pid===b.dataset.remove);if(p)removePlayer(p.name);}});
$("profileList").addEventListener("click",e=>{const b=e.target.closest("[data-action]");if(b)({activate:activateProfile,test:testProfile,edit:editProfile,delete:delProfile})[b.dataset.action](b.dataset.id);});

/* ================= 初始化 ================= */
async function refreshState() {
  if(replayMode)return;
  const d = await jget(`${API}/state`);
  if(memorySaveId!==d.save_id){memorySaveId=d.save_id;memoryTiers.clear();memoryPages.clear();}
  $("recordingTag").textContent=d.recording?"● 自动录制中":"○ 尚未录制";
  townConfig=d.town_config||townConfig;marketPrice=d.market_price??marketPrice;stocks=d.stocks||stocks;shopProducts=d.shop_products||shopProducts;
  window.refreshBuildingLock?.(d.running||d.identity_phase);
  players = d.players; vehicles = d.vehicles || []; decorCommon = d.decor_common;
  identityPhase = d.identity_phase;
  $("clockbox").textContent = `第${d.day}天 ${d.clock}`;
  $("modeTag").textContent = d.identity_phase ? "居民选择身份中…"
    : (d.llm_mode ? `LLM·${d.active_profile}` : "规则引擎模式");
  setRunning(d.running);
  $("speedSel").value=String(d.speed);
  renderPlayers(); draw();
}
(async function init() {
  await Promise.all([loadMap("house"), loadMap("town")]);
  resize(); draw();
  await refreshState();
  await loadProfiles();
  await loadPrompt();
  renderPlayerAdmin();
  connectWS();
  queryEvents();
})();



/* ================= 过程回放（只读） ================= */
function selectedSaveIds(){
  return [...document.querySelectorAll('#saveEntries .archive-select:checked')].map(input=>input.value);
}
function updateSaveSelection(){
  const count=selectedSaveIds().length;
  $("btnDeleteSelectedSaves").textContent=`删除所选（${count}）`;
  $("btnDeleteSelectedSaves").disabled=replayMode||count===0;
  $("btnSelectAllSaves").disabled=replayMode||!document.querySelector('#saveEntries .archive-select');
  $("btnClearSaveSelection").disabled=replayMode||count===0;
}
async function loadSaves(){
  const d=await jget(`${API}/saves`);
  const open=new Set([...document.querySelectorAll('#saveEntries details[open]')].map(e=>e.dataset.id));
  const selected=new Set(selectedSaveIds());
  $("currentSave").textContent=`当前世界：${d.items.find(s=>s.id===d.current)?.name||d.current||"未创建"}`;
  $("saveEntries").innerHTML=d.items.map(s=>`<div class="archive-row">
    <input class="archive-select" type="checkbox" value="${esc(s.id)}" aria-label="选择存档 ${esc(s.name)}" ${selected.has(s.id)&&s.id!==d.current?'checked':''} ${s.id===d.current||replayMode?'disabled':''} title="${s.id===d.current?'当前存档不可删除':'选择此存档进行批量删除'}">
    <details class="archive-entry" data-id="${esc(s.id)}" ${open.has(s.id)?'open':''}>
    <summary><span class="archive-name">${s.favorite?'★ ':''}${esc(s.name)}</span>${s.id===d.current?'<span class="tag">当前</span>':''}<small>第${s.day}天 ${esc(s.clock)} · ${s.residents}位居民</small></summary>
    <div class="archive-body"><p class="hint">保存于 ${esc(new Date(s.updated).toLocaleString())}<br>${esc(s.reason)} · ${s.memory_count}条记忆<br>${s.context_complete?'完整世界与模型上下文':'早期存档：未记录当时的全部运行设置'}</p>
    <div class="archive-actions">
      <button class="btn" data-action="replay">回放</button>
      <button class="btn primary" data-action="continue" ${replayMode?'disabled':''}>从此处继续</button>
      <button class="btn" data-action="rename" ${replayMode?'disabled':''}>重命名</button>
      <button class="btn" data-action="favorite" data-favorite="${s.favorite}" ${replayMode?'disabled':''}>${s.favorite?'★ 已收藏':'☆ 收藏'}</button>
      <button class="btn warn" data-action="delete" ${s.id===d.current||replayMode?'disabled':''} title="${s.id===d.current?'先切换到其他存档后可删除':'删除此存档'}">删除存档</button>
    </div></div></details></div>`).join('')||'<p class="hint">还没有存档</p>';
  updateSaveSelection();
}
async function refreshRestoredWorld(){
  stopReplay();replayMode=false;replayRequest++;replayLocks(false);$("replayControls").hidden=true;
  await Promise.all([loadMap("house"),loadMap("town")]);
  bubbles=[];await refreshState();await queryEvents();await loadSaves();
  await loadProfiles();await loadPrompt();await loadBinds();resize();
  toast("已恢复所选位置，点击开始继续");
}
$("btnSaveWorld").onclick=async()=>{
  await jpost(`${API}/saves/current`);await refreshState();await loadSaves();toast("已创建独立存档，保持暂停");
};
$("btnRefreshSaves").onclick=loadSaves;
$("saveEntries").onchange=e=>{if(e.target.matches('.archive-select'))updateSaveSelection();};
$("btnSelectAllSaves").onclick=()=>{
  document.querySelectorAll('#saveEntries .archive-select:not(:disabled)').forEach(input=>input.checked=true);
  updateSaveSelection();
};
$("btnClearSaveSelection").onclick=()=>{
  document.querySelectorAll('#saveEntries .archive-select').forEach(input=>input.checked=false);
  updateSaveSelection();
};
$("btnDeleteSelectedSaves").onclick=async()=>{
  const ids=selectedSaveIds();if(!ids.length||replayMode)return;
  const names=ids.map(id=>document.querySelector(`#saveEntries details[data-id="${id}"] .archive-name`)?.textContent.trim()||id);
  const preview=names.slice(0,5).join('、')+(names.length>5?'等':'');
  if(!confirm(`确定删除 ${ids.length} 个存档（${preview}）？居民、记忆和世界检查点将一并删除，此操作不能撤销。`))return;
  const result=await jpost(`${API}/saves/delete-selected`,{ids});
  await loadSaves();toast(`已删除 ${result.deleted} 个存档`);
};
$("saveEntries").onclick=async e=>{
  const button=e.target.closest('button[data-action]');if(!button)return;
  const entry=button.closest('[data-id]'),id=entry.dataset.id,action=button.dataset.action;
  if(action==='replay'){await openReplay(id,'save');return;}
  if(action==='continue'){
    if(!confirm("先保存当前世界，再从此存档创建副本继续？"))return;
    await jpost(`${API}/saves/${encodeURIComponent(id)}/load`);await refreshRestoredWorld();return;
  }
  if(action==='rename'){
    const name=prompt('输入存档名称（1～80字）',entry.querySelector('.archive-name').textContent.replace(/^★ /,''));
    if(name===null)return;
    await jput(`${API}/saves/${encodeURIComponent(id)}/metadata`,{name:name.trim()});
  }else if(action==='favorite'){
    await jput(`${API}/saves/${encodeURIComponent(id)}/metadata`,{favorite:button.dataset.favorite!=='true'});
  }else if(action==='delete'){
    if(!confirm('删除此存档？居民、记忆和世界检查点将一并删除，此操作不能撤销。'))return;
    if(replayMode)throw Error('请先返回实时');
    const r=await fetch(`${API}/saves/${encodeURIComponent(id)}`,{method:'DELETE'});
    if(!r.ok)throw Error((await r.json()).detail);
    toast('存档已删除');
  }
  await loadSaves();
};
async function loadReplays(){
  const list=await jget(`${API}/replays`);
  $("replayEntries").innerHTML=list.map(r=>`<details class="archive-entry"><summary><span class="archive-name">${esc(new Date(r.started*1000).toLocaleString())}</span><small>${r.frames}帧 · ${(r.bytes/1024/1024).toFixed(1)}MB</small></summary><div class="archive-body"><button class="btn" data-replay="${esc(r.id)}">回放</button></div></details>`).join('')||'<p class="hint">尚无录制</p>';
}
$("replayEntries").onclick=e=>{const button=e.target.closest('[data-replay]');if(button)openReplay(button.dataset.replay,'replay');};
async function openReplay(id,source='replay'){
  stopReplay();
  if(!replayMode)await jpost(`${API}/control`,{cmd:"pause"});
  replayId=id;replaySource=source;replayMode=true;replayLocks(true);$("replayControls").hidden=false;
  await showReplayFrame(0);
}
function stopReplay(){replayPlayVersion++;clearTimeout(replayTimer);replayTimer=null;$("btnPlayReplay").textContent="▶ 播放";}
function replayLocks(active){
  for(const id of ["btnReset","speedSel","btnSaveWorld"])$(id).disabled=active;
  for(const button of document.querySelectorAll('#saveEntries button:not([data-action="replay"])'))button.disabled=active||button.dataset.action==='delete'&&!!button.closest('details').querySelector('.tag');
  for(const input of document.querySelectorAll('#saveEntries .archive-select'))input.disabled=active||!!input.closest('.archive-row').querySelector('.tag');
  updateSaveSelection();
  for(const panel of ["api","settings"])document.querySelector(`nav [data-panel="${panel}"]`).disabled=active;
}
async function showReplayFrame(index){
  const request=++replayRequest,id=replayId;
  const d=await jget(`${API}/${replaySource==="save"?"saves":"replays"}/${encodeURIComponent(id)}/frames/${index}`);
  if(!replayMode||request!==replayRequest||id!==replayId)return;
  replayCanContinue=!!d.can_continue;$("btnContinueReplay").disabled=!replayCanContinue;
  $("btnContinueReplay").title=replayCanContinue?"从当前帧的完整上下文创建新存档":"此旧录制缺少完整上下文，可从世界存档保存位置继续";
  replayIndex=d.index;replayTotal=d.total;
  const f=d.frame;townConfig=f.town_config||null;marketPrice=f.market_price??10;stocks=f.stocks||stocks;shopProducts=f.shop_products||{};window.refreshBuildingLock?.(true);MAPS=f.maps;players=f.players;vehicles=f.vehicles||[];decorCommon=f.decor_common;identityPhase=false;
  allEvents=[...(f.events||[])].reverse();bubbles=players.filter(p=>p.speech).map(p=>({name:p.name,text:p.speech,until:Date.now()+3500}));
  $("recordingTag").textContent="▶ 本地档案";
  $("clockbox").textContent=`第${f.day}天 ${f.clock}`;$("modeTag").textContent="只读回放 · 实时世界已暂停";
  $("replaySeek").max=Math.max(0,d.total-1);$("replaySeek").value=d.index;
  $("replayPosition").textContent=`第 ${d.index+1} / ${d.total} 帧 · 第${f.day}天 ${f.clock}`;
  setRunning(false);renderPlayers();resize();drawRelations();renderEvents(allEvents);
  if(replayIndex>=replayTotal-1)stopReplay();
}
$("btnListReplays").onclick=loadReplays;
$("btnContinueReplay").onclick=async()=>{
  if(!replayCanContinue)return;
  if(!confirm('从当前回放位置创建新存档继续？实时世界会先自动保存，原档保持不变。'))return;
  stopReplay();
  await jpost(`${API}/${replaySource==='save'?'saves':'replays'}/${encodeURIComponent(replayId)}/frames/${replayIndex}/continue`,{},true);
  await refreshRestoredWorld();
};
$("replaySeek").oninput=()=>{stopReplay();showReplayFrame(+$("replaySeek").value);};
$("btnStepReplay").onclick=()=>{stopReplay();showReplayFrame(Math.min(replayTotal-1,replayIndex+1));};
$("btnReplaySpeed").onclick=()=>{
  replaySpeed=replaySpeeds[(replaySpeeds.indexOf(replaySpeed)+1)%replaySpeeds.length];
  $("btnReplaySpeed").textContent=`${replaySpeed}× 倍速`;
  if(replayTimer){clearTimeout(replayTimer);replayTimer=setTimeout(()=>advanceReplay(replayPlayVersion).catch(e=>{stopReplay();toast(e.message,false);}),Math.max(40,500/replaySpeed));}
};
async function advanceReplay(version){
  if(version!==replayPlayVersion)return;
  if(!replayMode||replayIndex>=replayTotal-1){stopReplay();return;}
  await showReplayFrame(replayIndex+1);
  if(version!==replayPlayVersion)return;
  if(replayIndex<replayTotal-1)replayTimer=setTimeout(()=>advanceReplay(version).catch(e=>{stopReplay();toast(e.message,false);}),Math.max(40,500/replaySpeed));
}
$("btnPlayReplay").onclick=async()=>{
  if(replayTimer){stopReplay();return;}
  if(replayIndex>=replayTotal-1)await showReplayFrame(0);
  $("btnPlayReplay").textContent="⏸ 暂停回放";
  const version=++replayPlayVersion;
  replayTimer=setTimeout(()=>advanceReplay(version).catch(e=>{stopReplay();toast(e.message,false);}),Math.max(40,500/replaySpeed));
};
$("btnExitReplay").onclick=async()=>{
  stopReplay();replayMode=false;replayRequest++;replayLocks(false);$("replayControls").hidden=true;
  await Promise.all([loadMap("house"),loadMap("town")]);await refreshState();await queryEvents();resize();
};

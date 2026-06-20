function scrollToTop(smooth=true){
  const opt = {top:0, behavior:smooth ? "smooth" : "auto"};
  try{ window.scrollTo(opt); }catch(e){}
  try{ document.documentElement.scrollTo(opt); }catch(e){}
}
["topBtn","topFloat"].forEach(id=>{
  const btn = document.getElementById(id);
  if(btn) btn.addEventListener("click", ()=>scrollToTop(true));
});
// Show the floating back-to-top once the page is scrolled a little. Track both
// window scroll and the document scroller (covers the native WebView2 case where
// scroll may report on document.scrollingElement rather than window).
function _scrollTopPos(){
  return window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0;
}
function _updateBacktop(){
  const btn = document.getElementById("topFloat");
  if(btn) btn.classList.toggle("show", _scrollTopPos() > 200);
}
window.addEventListener("scroll", _updateBacktop, {passive:true});
document.addEventListener("scroll", _updateBacktop, {passive:true, capture:true});
document.getElementById("tabs").addEventListener("click", e=>{
  const tab = e.target.closest("button[data-v]");
  const v = tab && tab.dataset.v; if(!v) return;
  document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("active", b.dataset.v===v));
  document.querySelectorAll(".view").forEach(s=>s.classList.toggle("active", s.id==="view-"+v));
  document.getElementById("rangebar").style.display = (v==="settings"||v==="report") ? "none" : "flex";
  scrollToTop(false);
  if(v==="settings") loadSettings();
  else if(v==="sequence"){ seqResetFilters(); loadSequence(); }  // entering the view restores all apps + keyword
  else if(v==="fun") loadFun();
  else if(v==="lexicon") loadLexicon();
  else if(v==="report") loadReportCurrent();
  else if(v==="search"){ loadTracked(); document.getElementById("searchInput").focus(); }
});

// ---- range / refresh ----
function syncDateFields(){
  document.querySelectorAll('.datefield input[type="date"]').forEach(inp=>{
    const box = inp.closest(".datefield");
    if(box) box.classList.toggle("empty", !inp.value);
  });
}
document.addEventListener("input", e=>{
  if(e.target && e.target.matches('.datefield input[type="date"]')) syncDateFields();
});
document.addEventListener("change", e=>{
  if(e.target && e.target.matches('.datefield input[type="date"]')) syncDateFields();
});
syncDateFields();
document.getElementById("ranges").addEventListener("click", e=>{
  if(!e.target.dataset.r) return;
  range = e.target.dataset.r;
  document.querySelectorAll("#ranges button").forEach(b=>b.classList.toggle("active", b===e.target));
  refreshActive();
});
// Switch the shared range to a custom window and reflect it in the top bar.
// Used by the date-range "应用日期" button and the sequence "跳到某一天" picker.
function setCustomRange(s, en){
  range = "custom"; custom = {start:s, end:en};
  document.querySelectorAll("#ranges button").forEach(b=>b.classList.remove("active"));
  const ds = document.getElementById("d-start"), de = document.getElementById("d-end");
  if(ds) ds.value = s || ""; if(de) de.value = en || "";
  syncDateFields();
  refreshActive();
}
document.getElementById("d-apply").addEventListener("click", ()=>{
  const s = document.getElementById("d-start").value, en = document.getElementById("d-end").value;
  if(!s && !en) return;
  setCustomRange(s, en);
});
document.getElementById("refreshBtn").addEventListener("click", ()=>{ refreshActive(); checkHealth(); });
function refreshActive(){
  const v = document.querySelector("#tabs button.active").dataset.v;
  if(v==="board") refreshBoard();
  else if(v==="sequence") loadSequence();
  else if(v==="fun") loadFun();
  else if(v==="lexicon") loadLexicon();
  else if(v==="report") loadReportCurrent();
  else if(v==="search"){ loadTracked(); if(document.getElementById("searchInput").value.trim()) doSearch(); }
}

// ---- demo / sample data ----
let demoOn = false;
function applyDemoUI(){
  document.getElementById("demoBar").classList.toggle("on", demoOn);
  const btn = document.getElementById("demoToggleBtn");
  if(btn) btn.textContent = demoOn ? "退出演示数据" : "加载演示数据";
  if(demoOn) document.getElementById("demoInvite").classList.remove("on");
}
async function setDemo(on){
  const btn = document.getElementById("demoToggleBtn");
  if(btn){ btn.disabled = true; btn.textContent = on ? "加载中…" : "退出中…"; }
  try{ const r = await DT.demo_set(!!on); demoOn = !!(r && r.on); }
  catch(e){}
  if(btn) btn.disabled = false;
  applyDemoUI();
  boardLoaded = false;            // active database changed -> rebuild the board
  refreshActive(); checkHealth();
}
document.getElementById("demoExit").addEventListener("click", ()=> setDemo(false));
document.getElementById("demoStart").addEventListener("click", ()=> setDemo(true));
document.getElementById("demoToggleBtn").addEventListener("click", ()=> setDemo(!demoOn));

// ---- board ----
function deltaHTML(pct){
  if(pct===null||pct===undefined) return "";
  if(pct===0) return `<div class="d flat">→ 持平</div>`;
  const up = pct>0;
  return `<div class="d ${up?"up":"down"}">${up?"▲":"▼"} ${Math.abs(pct)}%</div>`;
}
function renderCards(o, trend){
  const d = (trend && trend.delta_pct) || {};
  const cards = [
    ["总字数", o.total_chars, "", d.chars],
    ["不同汉字", o.distinct_chars, "", null],
    ["平均速度", o.cpm, "字/分", d.cpm],
    ["峰值速度", o.peak_cpm, "字/分", null],
    ["活跃时长", o.active_minutes, "分钟", d.active_minutes],
    ["修改率", (o.edit_ratio*100).toFixed(1), "%", (d.edit_ratio===null||d.edit_ratio===undefined)?null:-d.edit_ratio],
    ["删除键", o.backspace + o.delete, "次", null],
    ["输入会话", o.sessions, "次", null],
  ];
  document.getElementById("cards").innerHTML = cards.map(
    ([l,v,u,dl]) => `<div class="card" title="${l}"><div class="v">${v}${u?`<span class="u">${u}</span>`:""}</div><div class="l">${l}</div>${deltaHTML(dl)}</div>`
  ).join("");
  document.getElementById("since").textContent =
    o.tracking_since ? ("自 " + o.tracking_since + " 起记录") : "暂无数据";
  // Invite first-time users (no data, not already in demo) to load sample data.
  document.getElementById("demoInvite").classList.toggle("on", !o.total_chars && !demoOn);
}
let boardLoaded = false;
let boardReqSeq = 0;
let appReqSeq = 0;
function setBoardLoading(show){
  if(!show) return;
  document.getElementById("cards").innerHTML = Array.from({length:8}, ()=>
    '<div class="card skel"><div class="skel-line" style="width:58%;height:24px"></div><div class="skel-line" style="width:42%;margin-top:12px"></div></div>'
  ).join("");
  document.getElementById("dailyPanelTitle").textContent = "正在整理看板";
  document.getElementById("dailyPanelSub").textContent = "";
  document.getElementById("dailyChartTools").style.display = "none";
  document.getElementById("todayCloud").hidden = false;
  document.getElementById("todayCloud").innerHTML = '<div class="chart-skel">正在加载统计数据…</div>';
  document.getElementById("todayCloudDetail").className = "clouddetail";
  document.getElementById("dailyChart").style.display = "none";
  document.getElementById("heatmap").innerHTML = '<div class="chart-skel">正在生成热力图…</div>';
  document.getElementById("topics").innerHTML = '<div class="skel-line" style="width:70%"></div><div class="skel-line" style="width:48%;margin-top:10px"></div>';
}
function setBoardHeavyLoading(){
  document.getElementById("topics").innerHTML =
    '<div class="skel-line" style="width:70%"></div><div class="skel-line" style="width:48%;margin-top:10px"></div>';
  document.getElementById("posDetail").textContent = "正在分析词性和高频词…";
  if(range === "today"){
    document.getElementById("dailyPanelTitle").textContent = "今日主题云";
    document.getElementById("dailyPanelSub").textContent = "";
    document.getElementById("dailyChartTools").style.display = "none";
    document.getElementById("dailyChart").style.display = "none";
    document.getElementById("todayCloud").hidden = false;
    document.getElementById("todayCloud").innerHTML = '<div class="chart-skel">正在提取今日词语和主题…</div>';
  }
}
function renderHeat(grid){
  let max = 1; grid.forEach(row => row.forEach(v => { if(v>max) max=v; }));
  const emptyBg = cssVar("--cell-empty");
  const heatRgb = cssVar("--heat-rgb") || "255,206,51";
  const heatMin = parseFloat(cssVar("--heat-min")) || 0.12;
  let html = "<table class='heat'><tr><th></th>";
  for(let h=0;h<24;h++) html += `<th>${h%6===0?h:""}</th>`;
  html += "</tr>";
  for(let dd=0;dd<7;dd++){
    html += `<tr><td class='dow'>${DOW[dd]}</td>`;
    for(let h=0;h<24;h++){
      const v = grid[dd][h];
      const a = v? (heatMin + (1-heatMin)*v/max) : 0;
      const bg = v? `rgba(${heatRgb},${a.toFixed(3)})` : emptyBg;
      html += `<td><div class='cell' style='background:${bg}' title='${DOW[dd]} ${h}:00 · ${v} 字'></div></td>`;
    }
    html += "</tr>";
  }
  document.getElementById("heatmap").innerHTML = html + "</table>";
}
function renderTopics(rows){
  const el = document.getElementById("topics");
  if(!rows.length){ el.innerHTML = "<div class='empty'>暂无（数据较少时无法提取关键词）</div>"; return; }
  const max = rows[0].weight || 1;
  el.innerHTML = rows.map((r, i) => `<span class="t" title="点击查看「${escapeAttr(r.word)}」详情" data-w="${escapeAttr(r.word)}" style="font-size:${(13+16*(r.weight/max)).toFixed(0)}px;color:${cloudColor(i)}">${escapeHtml(r.word)}</span>`).join("");
  el.querySelectorAll(".t").forEach(t => t.addEventListener("click", ()=>gotoSearch(t.dataset.w)));
}
const CLOUD_COLORS = ["#7561f2","#ef426f","#2f7ff0","#ff7a18","#20b86d","#20aaa0","#e747a2","#5868df"];
function cloudColor(i){ return CLOUD_COLORS[i % CLOUD_COLORS.length]; }
let todayCloudLayoutTimer = null;
function renderTodayCloud(words, topics){
  const el = document.getElementById("todayCloud");
  document.getElementById("todayCloudDetail").className = "clouddetail";
  const source = (words && words.length ? words.map(w=>({word:w.word, count:w.count})) :
    (topics || []).map(t=>({word:t.word, count:t.weight}))).filter(x=>x.word);
  if(!source.length){
    el.innerHTML = "<div class='empty'>今天还没有足够的词语数据。</div>";
    return;
  }
  const rows = source.slice(0, 42);
  const max = Math.max(...rows.map(x=>Number(x.count)||0), 1);
  const min = Math.min(...rows.map(x=>Number(x.count)||0), max);
  const span = Math.max(max - min, 1);
  const wordsHtml = rows.map((r, i)=>{
    const n = Number(r.count) || min;
    const t = (n - min) / span;
    const size = 15 + Math.round(30 * Math.pow(t, .68));
    const rotate = [-36, -18, 0, 18, 36, 0, -12, 12][i % 8];
    const label = escapeHtml(r.word);
    const attr = escapeAttr(r.word);
    return `<button class="cloudword" type="button" data-w="${attr}" title="查看「${attr}」相关输入" `+
      `data-base-size="${size}" data-rank="${i}" style="font-size:${size}px;color:${cloudColor(i)};--rot:${rotate}deg">${label}</button>`;
  }).join("");
  el.innerHTML = `<div class="cloudstage">${wordsHtml}</div><div class='cloudnote'>点击词语查看相关输入</div>`;
  el.querySelectorAll(".cloudword").forEach(btn => {
    btn.addEventListener("click", ()=>showCloudDetail(btn.dataset.w, btn));
  });
  scheduleTodayCloudLayout();
}
function scheduleTodayCloudLayout(){
  clearTimeout(todayCloudLayoutTimer);
  todayCloudLayoutTimer = setTimeout(()=>{
    layoutTodayCloud();
    if(document.fonts && document.fonts.ready) document.fonts.ready.then(layoutTodayCloud);
  }, 0);
}
function cloudBoxesOverlap(a, b){
  return !(a.r < b.l || a.l > b.r || a.b < b.t || a.t > b.b);
}
function measureCloudBox(stageRect, btn, x, y, pad){
  btn.style.left = x.toFixed(1) + "px";
  btn.style.top = y.toFixed(1) + "px";
  const r = btn.getBoundingClientRect();
  return {
    l:r.left - stageRect.left - pad, r:r.right - stageRect.left + pad,
    t:r.top - stageRect.top - pad, b:r.bottom - stageRect.top + pad
  };
}
function layoutTodayCloud(){
  const stage = document.querySelector("#todayCloud .cloudstage");
  if(!stage || stage.offsetParent === null) return;
  const buttons = [...stage.querySelectorAll(".cloudword")];
  const sw = stage.clientWidth, sh = stage.clientHeight;
  if(!sw || !sh || !buttons.length) return;
  const stageRect = stage.getBoundingClientRect();
  const scale = Math.max(.78, Math.min(1.12, sw / 760));
  const placed = [];
  const centerX = sw / 2, centerY = sh / 2;
  buttons.forEach((btn, i)=>{
    btn.classList.remove("placed");
    btn.style.left = centerX + "px";
    btn.style.top = centerY + "px";
    btn.style.fontSize = Math.max(13, Math.round(Number(btn.dataset.baseSize || 16) * scale)) + "px";
    const rawW = Math.max(18, btn.offsetWidth);
    const rawH = Math.max(14, btn.offsetHeight);
    const pad = i < 10 ? 7 : 4;
    let best = null;
    for(let step=0; step<1800; step++){
      const r = 3.8 * Math.sqrt(step);
      const angle = step * 0.56 + i * 1.17;
      const x = centerX + Math.cos(angle) * r * (sw / sh);
      const y = centerY + Math.sin(angle) * r;
      const box = measureCloudBox(stageRect, btn, x, y, pad);
      if(box.l < 2 || box.r > sw-2 || box.t < 2 || box.b > sh-2) continue;
      if(!placed.some(p=>cloudBoxesOverlap(box, p))){ best = {x, y, box}; break; }
    }
    if(!best){
      const cols = Math.max(3, Math.floor(sw / Math.max(rawW + 12, 82)));
      const cellW = sw / cols;
      const cellH = Math.max(34, rawH + 12);
      const row = Math.floor(i / cols), col = i % cols;
      const x = Math.min(sw - rawW/2 - 2, Math.max(rawW/2 + 2, cellW*(col+.5)));
      const y = Math.min(sh - rawH/2 - 2, Math.max(rawH/2 + 2, 18 + row*cellH));
      best = {x, y, box:measureCloudBox(stageRect, btn, x, y, 1)};
    }
    placed.push(best.box);
    btn.style.left = best.x.toFixed(1) + "px";
    btn.style.top = best.y.toFixed(1) + "px";
    btn.classList.add("placed");
  });
}
async function showCloudDetail(word, btn){
  document.querySelectorAll("#todayCloud .cloudword").forEach(b=>b.classList.toggle("active", b===btn));
  const box = document.getElementById("todayCloudDetail");
  box.className = "clouddetail show loading";
  box.innerHTML = "";
  let r;
  try{ r = await apiGet("search", {...rangeParams(), q: word}); }
  catch(e){
    box.className = "clouddetail show";
    box.innerHTML = `<div class="empty">暂时无法读取「${escapeHtml(word)}」的详情。</div>`;
    return;
  }
  const fmt = ts => ts ? new Date(ts*1000).toLocaleString() : "—";
  const examples = (r.examples||[]).slice(0,3).map(e=>
    `<div class="ex"><div class="meta">${fmt(e.ts)} · ${escapeHtml(e.app||"")}</div>`+
    renderFullExample(e)+`</div>`).join("");
  box.className = "clouddetail show";
  box.innerHTML =
    `<div class="cd-head"><div><div class="cd-title">「${escapeHtml(word)}」出现 ${r.total||0} 次</div>`+
    `<div class="cd-meta">首次 ${fmt(r.first_seen)} · 最近 ${fmt(r.last_seen)}</div></div>`+
    `<div class="cd-actions"><button class="btn" id="cloudSearchBtn">查看完整详情</button></div></div>`+
    (examples ? `<div class="exs">${examples}</div>` : `<div class="empty">当前范围内还没有可展示的上下文。</div>`);
  document.getElementById("cloudSearchBtn").addEventListener("click", ()=>gotoSearch(word));
}
function renderDailyPanel(b){
  const isToday = range === "today";
  const title = document.getElementById("dailyPanelTitle");
  const sub = document.getElementById("dailyPanelSub");
  const tools = document.getElementById("dailyChartTools");
  const cloud = document.getElementById("todayCloud");
  const canvas = document.getElementById("dailyChart");
  if(charts.dailyChart){ charts.dailyChart.destroy(); charts.dailyChart = null; }
  if(isToday){
    title.textContent = "今日主题云";
    sub.textContent = "";
    tools.style.display = "none";
    canvas.style.display = "none";
    cloud.hidden = false;
    renderTodayCloud(b.top_words, b.topics);
    return;
  }
  title.textContent = "每日输入字数";
  sub.textContent = "";
  tools.style.display = "flex";
  cloud.hidden = true;
  document.getElementById("todayCloudDetail").className = "clouddetail";
  canvas.style.display = "block";
  charts.dailyChart = new Chart(canvas, {
    type:"line",
    data:{labels:b.daily.map(d=>d.date),
      datasets:[{data:b.daily.map(d=>d.count),borderColor:"#ffce33",
        backgroundColor:"rgba(255,206,51,.15)",fill:true,tension:.3,pointRadius:2}]},
    options:{plugins:{legend:{display:false}},scales:{y:{beginAtZero:true}}}
  });
}
let gamifyReqSeq = 0;
async function loadGamify(){
  const seq = ++gamifyReqSeq;
  try{
    const g = await apiGet("gamify", {});
    if(seq !== gamifyReqSeq) return;
    renderGamify(g);
  }catch(e){}
}
const ACH_PAGE = 8;
let achFilter = "all", achPage = {locked:0, unlocked:0};
function renderGamify(g){
  window.__gamify = g;
  document.getElementById("goalRing").style.setProperty("--p", (g.goal_pct*100).toFixed(0));
  document.getElementById("goalPct").textContent = (g.goal_pct*100).toFixed(0) + "%";
  document.getElementById("streakCur").textContent = g.streak_current;
  document.getElementById("streakBest").textContent = g.streak_best;
  document.getElementById("gTotal").textContent = (g.total_chars||0).toLocaleString();
  document.getElementById("gUnlocked").textContent = g.unlocked + " / " + g.achievements.length;
  const top = document.getElementById("gUnlockedTop");
  if(top){ top.style.display = ""; top.textContent = "已解锁 " + g.unlocked + " / " + g.achievements.length; }
  maybeToast(g.achievements);
  renderAchievements(g.achievements);
}
function achDate(ts){
  if(!ts) return "";
  const d = new Date(ts*1000);
  const p = n=>String(n).padStart(2,"0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
}
function badgeIcon(unlocked){
  return unlocked
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 21l4-2 4 2v-7"/><circle cx="12" cy="8" r="5"/><path d="M9.5 8l1.7 1.7 3.3-3.4"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
}
function badgeHTML(a){
  const when = a.unlocked
    ? `<div class="when"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>${a.unlocked_at?achDate(a.unlocked_at):"已解锁"}</div>`
    : `<div class="bar"><i style="width:${(a.progress*100).toFixed(0)}%"></i></div>`;
  return `<div class="badge ${a.unlocked?"on":""}">
      <div class="n"><span class="badgeico">${badgeIcon(a.unlocked)}</span>${escapeHtml(a.name)}</div>
      <div class="dsc">${escapeHtml(a.desc)}</div>
      ${when}
    </div>`;
}
function renderAchievements(all){
  // category filter chips
  const cats = ["all", ...Array.from(new Set(all.map(a=>a.category||"其他")))];
  document.getElementById("achCats").innerHTML = cats.map(c=>
    `<span class="achCat ${c===achFilter?"active":""}" data-cat="${escapeAttr(c)}">${c==="all"?"全部":escapeHtml(c)}</span>`).join("");
  const list = achFilter==="all" ? all : all.filter(a=>(a.category||"其他")===achFilter);
  const unlocked = list.filter(a=>a.unlocked).sort((x,y)=>(y.unlocked_at||0)-(x.unlocked_at||0));
  const locked = list.filter(a=>!a.unlocked).sort((x,y)=>y.progress-x.progress);
  document.getElementById("achievements").innerHTML =
    sectionHTML("已解锁", "unlocked", unlocked) + sectionHTML("未解锁", "locked", locked);
}
function sectionHTML(title, key, items){
  if(!items.length) return "";
  const pages = Math.max(1, Math.ceil(items.length/ACH_PAGE));
  if(achPage[key] >= pages) achPage[key] = pages-1;
  const pg = achPage[key];
  const slice = items.slice(pg*ACH_PAGE, pg*ACH_PAGE+ACH_PAGE);
  const pager = pages>1 ? `<div class="pager">
      <button class="btn" data-ach-pg="${key}" data-dir="-1" ${pg<=0?"disabled":""}>← 上一页</button>
      <span class="pinfo">${pg+1} / ${pages}</span>
      <button class="btn" data-ach-pg="${key}" data-dir="1" ${pg>=pages-1?"disabled":""}>下一页 →</button>
    </div>` : "";
  return `<div class="achSection">
      <div class="achHead"><h3>${title}</h3><span class="cnt">${items.length} 个</span></div>
      <div class="badges">${slice.map(badgeHTML).join("")}</div>
      ${pager}
    </div>`;
}
// category + paging interactions (delegated)
document.getElementById("achCats").addEventListener("click", e=>{
  const c = e.target.closest(".achCat"); if(!c) return;
  achFilter = c.dataset.cat; achPage = {locked:0, unlocked:0};
  if(window.__gamify) renderAchievements(window.__gamify.achievements);
});
document.getElementById("achievements").addEventListener("click", e=>{
  const b = e.target.closest("[data-ach-pg]"); if(!b) return;
  const key = b.dataset.achPg;
  achPage[key] = (achPage[key]||0) + (+b.dataset.dir);
  if(window.__gamify) renderAchievements(window.__gamify.achievements);
});
// ---- achievement toast (bottom-right) ----
function maybeToast(achievements){
  let known = null;
  try{ known = JSON.parse(localStorage.getItem("dt-ach-known")||"null"); }catch(e){}
  const nowUnlocked = achievements.filter(a=>a.unlocked).map(a=>a.id);
  if(Array.isArray(known)){
    const set = new Set(known);
    achievements.filter(a=>a.unlocked && !set.has(a.id))
      .forEach(a=>showToast(a));
  }
  try{ localStorage.setItem("dt-ach-known", JSON.stringify(nowUnlocked)); }catch(e){}
}
function showToast(a){
  const host = document.getElementById("toastHost");
  const el = document.createElement("div");
  el.className = "toast";
  el.innerHTML = `<img src="${randomDuck()}" alt="" onerror="this.onerror=null;this.src='duck.png'">
    <div class="tbody"><div class="ttag">🎉 成就达成</div>
      <div class="tname">${escapeHtml(a.name)}</div>
      <div class="tdsc">${escapeHtml(a.desc)}</div></div>
    <button class="tclose" title="关闭" aria-label="关闭">×</button>`;
  host.appendChild(el);
  requestAnimationFrame(()=>el.classList.add("show"));
  let timer = setTimeout(close, 8000);
  function close(){ clearTimeout(timer); el.classList.remove("show");
    setTimeout(()=>el.remove(), 300); }
  el.querySelector(".tclose").addEventListener("click", close);
}
let charN = 25, wordN = 25;
function renderChars(chars){ makeRankBar("charChart", chars.map(c=>c.ch), chars.map(c=>c.count), "#ffce33", gotoSearch); }
function renderWords(words){ makeRankBar("wordChart", words.map(w=>w.word), words.map(w=>w.count), "#36d399", gotoSearch); }
async function reloadChars(){ renderChars(await getJSON("/api/top_chars?n="+charN)); }
async function reloadWords(){ renderWords(await getJSON("/api/top_words?n="+wordN)); }
let posWordN = 12, posCurrent = null;
function pct(count, total){ return total ? (count / total * 100).toFixed(1) : "0.0"; }
function posTooltip(total){
  return {displayColors:false, callbacks:{label:(ctx)=>{
    const count = ctx.raw || 0;
    return `${ctx.label}: ${count} 次 · ${pct(count, total)}%`;
  }}};
}
// Hovering a (click-to-hide) legend entry pops the matching slice out, so it's
// clear which word/category the label refers to. Used by the pie legends.
function legendHoverHighlight(){
  return {
    onHover:(_e, item, legend)=>{
      const ch = legend.chart, meta = ch.getDatasetMeta(0);
      if(!meta || !meta.data || !meta.data[item.index]) return;   // mid-rebuild
      try{ ch.setActiveElements([{datasetIndex:0, index:item.index}]); ch.update(); }catch(e){}
    },
    onLeave:(_e, _item, legend)=>{
      const ch = legend.chart;
      try{ ch.setActiveElements([]); ch.update(); }catch(e){}
    }
  };
}
function renderPosOverview(pos){
  posCurrent = null;
  document.getElementById("posBack").style.display = "none";
  document.getElementById("posWordCtl").style.display = "none";
  document.getElementById("posDetail").textContent = "点击某个词性，可查看该词性下二字及以上词语的使用分布。";
  const total = pos.reduce((s, p)=>s+p.count, 0);
  if(charts.posChart) charts.posChart.destroy();
  charts.posChart = new Chart(document.getElementById("posChart"), {
    type:"doughnut",
    data:{labels:pos.map(p=>p.label),
      datasets:[donutDataset(pos.map(p=>p.count), pos.length)]},
    options:{onClick:(_e, els)=>{ if(els.length) loadPosWords(pos[els[0].index]); },
      onHover:(e, els)=>{ e.native.target.style.cursor = els.length ? "pointer" : "default"; },
      plugins:{legend:{position:"right",labels:{boxWidth:12,padding:10},...legendHoverHighlight()},tooltip:posTooltip(total)},
      cutout:"62%"}
  });
}
async function loadPosWords(row){
  posCurrent = row;
  const n = +document.getElementById("posWordN").value || posWordN;
  posWordN = n;
  const r = await getJSON("/api/pos_words?pos="+encodeURIComponent(row.pos)+"&n="+n+"&min_len=2");
  renderPosWords(r);
}
function renderPosWords(r){
  window.__posLastR = r;
  document.getElementById("posBack").style.display = "inline-block";
  document.getElementById("posWordCtl").style.display = "inline";
  const items = (r.items || []).map(x=>({label:`${x.word} · ${x.count}次 · ${x.pct}%`, count:x.count, word:x.word}));
  if(r.other && document.getElementById("posOther").checked){
    items.push({label:`其他 · ${r.other}次 · ${pct(r.other, r.total)}%`, count:r.other, word:null});
  }
  if(charts.posChart) charts.posChart.destroy();
  charts.posChart = new Chart(document.getElementById("posChart"), {
    type:"doughnut",
    data:{labels:items.map(x=>x.label),
      datasets:[donutDataset(items.map(x=>x.count), items.length)]},
    options:{onClick:(_e, els)=>{ if(els.length && items[els[0].index].word) gotoSearch(items[els[0].index].word); },
      onHover:(e, els)=>{ e.native.target.style.cursor = (els.length && items[els[0].index].word) ? "pointer" : "default"; },
      plugins:{legend:{position:"right",labels:{boxWidth:12,padding:10},...legendHoverHighlight()},tooltip:posTooltip(r.total || 0)},
      cutout:"58%"}
  });
  const least = (r.least || []).map(x=>`${escapeHtml(x.word)} ${x.count}次`).join(" · ");
  document.getElementById("posDetail").innerHTML =
    r.total ? `<b>${escapeHtml(r.label)}</b>：二字及以上词语共 ${r.total} 次。`+
      (least ? ` 较少出现：${least}。` : "")+
      ` 点击具体词语可跳到搜索详情。`
      : `<b>${escapeHtml(r.label)}</b>：当前范围内没有二字及以上词语。`;
}
document.getElementById("posBack").addEventListener("click", ()=>{ posCurrent = null; refreshBoard(); });
document.getElementById("posWordN").addEventListener("input", e=>{
  posWordN = +e.target.value;
  document.getElementById("posWordNv").textContent = posWordN;
  if(posCurrent){ clearTimeout(window.__posWordTimer); window.__posWordTimer = setTimeout(()=>loadPosWords(posCurrent), 250); }
});
// "其他" slice often dwarfs the named words; let the user hide it. Re-render from
// the cached response (no refetch) and remember the choice.
(function(){
  const cb = document.getElementById("posOther");
  try{ if(localStorage.getItem("dt-pos-other") === "0") cb.checked = false; }catch(e){}
  cb.addEventListener("change", ()=>{
    try{ localStorage.setItem("dt-pos-other", cb.checked ? "1" : "0"); }catch(e){}
    if(posCurrent && window.__posLastR) renderPosWords(window.__posLastR);
  });
})();

async function refreshBoard(){
  const seq = ++boardReqSeq;
  if(!boardLoaded) setBoardLoading(true);
  else setBoardHeavyLoading();
  document.getElementById("refreshBtn").disabled = true;
  try{
    const params = {...rangeParams(), charN, wordN};
    const b = await apiGet("board_fast", params);
    if(seq !== boardReqSeq) return;
    renderCards(b.overview, b.trend);
    loadGamify();   // range-independent; fetched off the hot path so switching scales stays snappy
    if(range !== "today") renderDailyPanel(b);
    else setBoardHeavyLoading();
    renderChars(b.top_chars);
    loadHourly();
    makeRankBar("appChart", b.apps.map(a=>a.app), b.apps.map(a=>a.count), "#ffb454", openAppDetail);
    if(appCurrent) openAppDetail(appCurrent);
    renderHeat(b.heatmap.grid);
    loadRichness(seq, params);
    loadContrib();
    boardLoaded = true;
    apiGet("board_heavy", params).then(h=>{
      if(seq !== boardReqSeq) return;
      renderWords(h.top_words);
      if(posCurrent) loadPosWords(h.pos.find(p=>p.pos===posCurrent.pos) || posCurrent);
      else renderPosOverview(h.pos);
      renderTopics(h.topics);
      if(range === "today") renderDailyPanel({...b, ...h});
    }).catch(e=>{
      if(seq !== boardReqSeq) return;
      document.getElementById("topics").innerHTML = "<div class='empty'>主题分析暂时失败，稍后重试。</div>";
    });
  } finally {
    if(seq === boardReqSeq) document.getElementById("refreshBtn").disabled = false;
  }
}

// ---- per-app drill-down: click an app to see its words + characters ----
let appCurrent = null;
async function openAppDetail(app){
  const seq = ++appReqSeq;
  appCurrent = app;
  const r = await getJSON("/api/app_detail?app="+encodeURIComponent(app)+"&n=25");
  if(seq !== appReqSeq || appCurrent !== app) return;
  document.getElementById("appOverviewWrap").style.display = "none";
  document.getElementById("appDetail").style.display = "block";
  document.getElementById("appBack").style.display = "inline-block";
  document.getElementById("appDetailTitle").innerHTML =
    `<b>${escapeHtml(app)}</b>：共 ${(r.total||0).toLocaleString()} 字`+
    (r.words.length||r.chars.length ? "，点击词 / 字可跳到搜索详情。" : "（暂无可分词内容）。");
  makeRankBar("appWordChart", r.words.map(x=>x.word), r.words.map(x=>x.count), "#36d399", gotoSearch);
  makeRankBar("appCharChart", r.chars.map(x=>x.ch),  r.chars.map(x=>x.count), "#ffce33", gotoSearch);
}
function closeAppDetail(){
  appCurrent = null;
  document.getElementById("appDetail").style.display = "none";
  document.getElementById("appBack").style.display = "none";
  document.getElementById("appOverviewWrap").style.display = "block";
}
document.getElementById("appBack").addEventListener("click", closeAppDetail);

// ---- hourly activity (own range, independent of the board range) ----
let hourSel = "today";
let hourStyle = localStorage.getItem("dt-hour-style") === "line" ? "line" : "bar";
function fmtHourTick(ts, multiday){
  const d = new Date(ts*1000), hh = String(d.getHours()).padStart(2,"0");
  return multiday ? `${d.getMonth()+1}/${d.getDate()} ${hh}` : `${hh}:00`;
}
async function loadHourly(){
  const params = hourSel==="today" ? {bucket:"hour", range:"today"} : {bucket:"hour", hours:hourSel};
  let res; try{ res = await apiGet("timeseries", params); }
  catch(e){ return; }
  const pts = res.points || [];
  const span = pts.length ? (pts[pts.length-1].ts - pts[0].ts) : 0;
  const multiday = span > 86400;
  if(charts.hourlyChart) charts.hourlyChart.destroy();
  const isLine = hourStyle === "line";
  const ds = isLine
    ? {data:pts.map(p=>p.count),borderColor:"#36d399",backgroundColor:"rgba(54,211,153,.14)",
       fill:true,tension:.4,pointRadius:0,pointHoverRadius:4,borderWidth:2}
    : {data:pts.map(p=>p.count),backgroundColor:"#36d399",borderRadius:3,
       barThickness:"flex",maxBarThickness:26};
  charts.hourlyChart = new Chart(document.getElementById("hourlyChart"), {
    type:isLine?"line":"bar",
    data:{labels:pts.map(p=>fmtHourTick(p.ts, multiday)),datasets:[ds]},
    options:{plugins:{legend:{display:false},tooltip:{displayColors:false}},
      interaction:isLine?{mode:"index",intersect:false}:undefined,
      scales:{x:{grid:{display:false},ticks:{autoSkip:true,maxRotation:0,
                 maxTicksLimit:multiday?14:24}},
              y:{beginAtZero:true,ticks:{precision:0}}}}
  });
}
document.getElementById("hourRanges").addEventListener("click", e=>{
  if(!e.target.dataset.h) return;
  hourSel = e.target.dataset.h;
  document.querySelectorAll("#hourRanges button").forEach(b=>b.classList.toggle("active", b===e.target));
  loadHourly();
});
document.getElementById("hourStyle").addEventListener("click", e=>{
  if(!e.target.dataset.s) return;
  hourStyle = e.target.dataset.s;
  localStorage.setItem("dt-hour-style", hourStyle);
  document.querySelectorAll("#hourStyle button").forEach(b=>b.classList.toggle("active", b===e.target));
  loadHourly();
});
(function initHourStyle(){
  document.querySelectorAll("#hourStyle button").forEach(b=>
    b.classList.toggle("active", b.dataset.s===hourStyle));
})();
// ---- rank sliders ----
(function wireRankSlider(id, valId, onDone){
  const sl = document.getElementById(id), out = document.getElementById(valId);
  let t = null;
  sl.addEventListener("input", ()=>{
    out.textContent = sl.value;
    clearTimeout(t); t = setTimeout(onDone, 250);   // debounce while dragging
  });
})("charN","charNv",()=>{ charN = +document.getElementById("charN").value; reloadChars(); });
(function wireRankSlider(id, valId, onDone){
  const sl = document.getElementById(id), out = document.getElementById(valId);
  let t = null;
  sl.addEventListener("input", ()=>{
    out.textContent = sl.value;
    clearTimeout(t); t = setTimeout(onDone, 250);
  });
})("wordN","wordNv",()=>{ wordN = +document.getElementById("wordN").value; reloadWords(); });

// ---- save any board chart as a PNG ----
function downloadCanvas(id, name){
  const cv = document.getElementById(id); if(!cv || !cv.width) return;
  const tmp = document.createElement("canvas");
  tmp.width = cv.width; tmp.height = cv.height;
  const ctx = tmp.getContext("2d");
  ctx.fillStyle = cssVar("--bg") || "#0f1216";   // opaque bg so the PNG isn't see-through
  ctx.fillRect(0, 0, tmp.width, tmp.height);
  ctx.drawImage(cv, 0, 0);
  const a = document.createElement("a");
  a.href = tmp.toDataURL("image/png");
  a.download = "ducktype_" + name + "_" + new Date().toISOString().slice(0,10) + ".png";
  a.click();
}
document.addEventListener("click", e=>{
  const b = e.target.closest(".dlbtn"); if(!b) return;
  downloadCanvas(b.dataset.dl, b.dataset.name || "chart");
});

// ---- report ----

// ---- vocabulary richness trend + yearly contribution calendar (item 5) ----
async function loadRichness(seq, params){
  const cv = document.getElementById("richnessChart");
  if(!cv) return;
  let rows; try{ rows = await apiGet("richness", params); }catch(e){ return; }
  if(seq !== boardReqSeq) return;
  if(charts.richness){ charts.richness.destroy(); charts.richness = null; }
  const sub = document.getElementById("richSub");
  if(!rows || rows.length < 2){
    if(sub) sub.textContent = "（这段时间数据太少，暂不足以成趋势）";
    cv.style.display = "none";       // don't reserve a tall empty canvas
    return;
  }
  cv.style.display = "";
  if(sub) sub.textContent = "（越高=用字越多样，越低=重复越多）";
  const labels = rows.map(r=>r.date.slice(5));
  const data = rows.map(r=> +(r.ratio*100).toFixed(1));
  charts.richness = new Chart(cv, {
    type:"line",
    data:{labels, datasets:[{data, borderColor:cssVar('--accent2'),
      backgroundColor:"rgba(54,211,153,.12)", fill:true, tension:.3,
      pointRadius:2, pointHoverRadius:5, pointHitRadius:20, borderWidth:2}]},
    options:{
      // hover anywhere along the x-axis (not just exactly on a point) shows the day
      interaction:{mode:"index", intersect:false},
      plugins:{legend:{display:false},tooltip:{displayColors:false,callbacks:{
      label:(ctx)=>{ const r=rows[ctx.dataIndex]; return ` 丰富度 ${ctx.raw}% · ${r.distinct}/${r.total} 字`; }}}},
      scales:{x:{grid:{display:false}},y:{beginAtZero:true,max:100,ticks:{callback:v=>v+"%"}}}}
  });
}
let contribLoaded = false;
async function loadContrib(force){
  const host = document.getElementById("contribCal");
  if(!host || (contribLoaded && !force)) return;
  let r; try{ r = await apiGet("contrib", {days:364}); }catch(e){ return; }
  contribLoaded = true;
  renderContrib(r);
}
function contribLevel(count, max){
  if(!count || max <= 0) return 0;
  const q = count / max;
  if(q <= 0.10) return 1;
  if(q <= 0.30) return 2;
  if(q <= 0.60) return 3;
  return 4;
}
function renderContrib(r){
  const host = document.getElementById("contribCal");
  const cells = (r && r.cells) || [];
  if(!cells.length){ host.innerHTML = '<div class="empty">还没有足够的数据。</div>'; return; }
  const weeks = r.weeks || Math.ceil(cells.length / 7);
  const DOWLBL = ["日","一","二","三","四","五","六"];
  let html = '<div class="cal-wrap"><div class="cal-dow">' +
    DOWLBL.map((d,i)=> i%2 ? `<span>${d}</span>` : '<span></span>').join("") + '</div>';
  for(let w=0; w<weeks; w++){
    html += '<div class="cal-col">';
    for(let d=0; d<7; d++){
      const c = cells[w*7 + d];
      if(!c){ html += '<div class="cal-cell empty-cell"></div>'; continue; }
      const lvl = contribLevel(c.count, r.max);
      html += `<div class="cal-cell lvl${lvl}" data-date="${c.date}" title="${c.date} · ${c.count.toLocaleString()} 字"></div>`;
    }
    html += '</div>';
  }
  html += '</div>';
  host.innerHTML = html;
  const sum = document.getElementById("contribSummary");
  if(sum) sum.textContent = `${r.active_days} 天有记录 · 共 ${(r.total||0).toLocaleString()} 字`;
}
function localDateStr(d){
  const p = n=>String(n).padStart(2,"0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
}
// Switch the shared range to a named preset (e.g. "today"), clearing any custom
// date inputs so the top bar doesn't keep showing a stale custom day.
function setRangePreset(r){
  range = r; custom = {start:null, end:null};
  const ds = document.getElementById("d-start"), de = document.getElementById("d-end");
  if(ds) ds.value = ""; if(de) de.value = "";
  syncDateFields();
  document.querySelectorAll("#ranges button").forEach(b=>b.classList.toggle("active", b.dataset.r===r));
  refreshActive();
}
document.getElementById("contribCal").addEventListener("click", e=>{
  const cell = e.target.closest(".cal-cell[data-date]");
  if(!cell) return;
  // Clicking today's cell maps to the 今天 preset (no lingering custom date);
  // any past day becomes a single-day custom range.
  if(cell.dataset.date === localDateStr(new Date())) setRangePreset("today");
  else setCustomRange(cell.dataset.date, cell.dataset.date);
});

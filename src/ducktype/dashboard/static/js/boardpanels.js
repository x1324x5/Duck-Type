// ---- below-the-fold board panels (split out of board.js, 0.3.0) ----
// Richness trend, yearly contribution calendar, dashboard-usage history, plus
// the 0.3.0 additions: vocabulary growth, per-app efficiency and weekday rhythm.
// All of these sit below the first screen, so they are *lazy-loaded* via an
// IntersectionObserver -- the board's first paint no longer waits on them, and a
// panel only fetches when it actually scrolls into view.

// ---- lazy-load orchestration ----
let _lazyParams = {}, _lazySeq = 0;
const _lazyPanels = [];
let _lazyObserver = null;
function registerLazyPanel(sel, loader, opts){
  _lazyPanels.push({sel, loader, once:!!(opts&&opts.once), loaded:false, dirty:true});
}
function _lazyElOf(p){
  const t = document.querySelector(p.sel);
  return t ? (t.closest(".panel") || t) : null;
}
function _runLazy(p){
  if(p.once && p.loaded) return;
  if(p.loaded && !p.dirty) return;
  p.dirty = false; p.loaded = true; p.everLoaded = true;
  try{ p.loader(_lazyParams, _lazySeq); }catch(e){ console.warn("[DuckType] lazy panel failed", p.sel, e); }
}
function _ensureLazyObserver(){
  if(_lazyObserver || typeof IntersectionObserver === "undefined") return;
  _lazyObserver = new IntersectionObserver(ents=>{
    ents.forEach(e=>{
      if(!e.isIntersecting) return;
      const p = _lazyPanels.find(x=>_lazyElOf(x) === e.target);
      if(p) _runLazy(p);
    });
  }, {root:null, rootMargin:"240px 0px"});
}
// Called by refreshBoard: stash the current range params, mark range-dependent
// panels dirty, observe them, and eagerly run any already on-screen.
function refreshBoardPanels(params, seq){
  _lazyParams = params; _lazySeq = seq;
  _ensureLazyObserver();
  const vh = window.innerHeight || document.documentElement.clientHeight || 800;
  _lazyPanels.forEach(p=>{
    if(!p.once) p.dirty = true;
    const el = _lazyElOf(p);
    if(!el) return;
    if(_lazyObserver) _lazyObserver.observe(el);
    // Run now if: no observer support, the panel is in/near the viewport, or the
    // user has already scrolled to it once (so a range change refreshes it even
    // when it has since scrolled out of view -- the observer won't re-fire then).
    const r = el.getBoundingClientRect();
    const inView = r.top < vh + 240 && r.bottom > -240;
    if(!_lazyObserver || inView || p.everLoaded) _runLazy(p);
  });
}

// ---- vocabulary richness trend ----
async function loadRichness(seq, params){
  const cv = document.getElementById("richnessChart");
  if(!cv) return;
  let rows; try{ rows = await apiGet("richness", params || rangeParams()); }catch(e){ return; }
  if(seq !== undefined && seq !== boardReqSeq) return;
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

// ---- vocabulary growth (lifetime cumulative distinct words / chars) ----
async function loadVocab(){
  const cv = document.getElementById("vocabChart");
  if(!cv) return;
  let r; try{ r = await apiGet("vocab_growth", {}); }catch(e){ return; }
  if(charts.vocab){ charts.vocab.destroy(); charts.vocab = null; }
  const sub = document.getElementById("vocabSub");
  const pts = (r && r.points) || [];
  if(pts.length < 2){
    if(sub) sub.textContent = "（再多写一些，曲线就会长出来）";
    cv.style.display = "none";
    return;
  }
  cv.style.display = "";
  if(sub) sub.textContent = `累计 ${(+r.total_words).toLocaleString()} 个不同词 · ${(+r.total_chars).toLocaleString()} 个不同字`;
  const labels = pts.map(p=>p.date.slice(0,7));
  charts.vocab = new Chart(cv, {
    type:"line",
    data:{labels, datasets:[
      {label:"累计不同词", data:pts.map(p=>p.words), borderColor:cssVar("--accent")||"#ffce33",
       backgroundColor:"rgba(255,206,51,.14)", fill:true, tension:.25, pointRadius:0,
       pointHoverRadius:4, borderWidth:2, yAxisID:"y"},
      {label:"累计不同字", data:pts.map(p=>p.chars), borderColor:cssVar("--accent2")||"#36d399",
       backgroundColor:"rgba(54,211,153,.10)", fill:true, tension:.25, pointRadius:0,
       pointHoverRadius:4, borderWidth:2, yAxisID:"y"},
    ]},
    options:{interaction:{mode:"index", intersect:false},
      plugins:{legend:{display:true,labels:{boxWidth:12,padding:10}},tooltip:{displayColors:true}},
      scales:{x:{grid:{display:false},ticks:{autoSkip:true,maxTicksLimit:12,maxRotation:0}},
              y:{beginAtZero:true,ticks:{precision:0}}}}
  });
}

// ---- per-application typing efficiency ----
async function loadAppEff(params){
  const cv = document.getElementById("appEffChart");
  if(!cv) return;
  let rows; try{ rows = await apiGet("app_efficiency", params || rangeParams()); }catch(e){ return; }
  if(charts.appEff){ charts.appEff.destroy(); charts.appEff = null; }
  const note = document.getElementById("appEffNote");
  if(!rows || !rows.length){
    cv.parentElement.parentElement.style.height = "";
    if(note) note.textContent = "这段时间还没有足够的分应用数据。";
    cv.style.display = "none";
    return;
  }
  cv.style.display = "";
  makeRankBar("appEffChart", rows.map(r=>r.app), rows.map(r=>r.cpm), "#a78bfa", null);
  if(charts.appEff){
    charts.appEff.options.plugins.tooltip = {displayColors:false, callbacks:{
      label:(ctx)=>{ const r=rows[ctx.dataIndex]; return ` ${r.cpm} 字/分 · ${r.chars.toLocaleString()} 字 · ${r.active_minutes} 分钟`; }}};
    charts.appEff.update("none");
  }
  const fastest = rows[0];
  if(note) note.innerHTML = fastest
    ? `在 <b>${escapeHtml(fastest.app)}</b> 里你打得最快，约 <b>${fastest.cpm}</b> 字/分。条形为各应用平均速度（字/分）。`
    : "";
}

// ---- weekday vs weekend rhythm ----
async function loadWeekday(params){
  const cv = document.getElementById("weekdayChart");
  if(!cv) return;
  let r; try{ r = await apiGet("weekday_rhythm", params || rangeParams()); }catch(e){ return; }
  if(charts.weekday){ charts.weekday.destroy(); charts.weekday = null; }
  const note = document.getElementById("weekdayNote");
  if(!r || !r.has_data){
    if(note) note.textContent = "这段时间还没有足够的数据。";
    cv.style.display = "none";
    return;
  }
  cv.style.display = "";
  const wd = r.by_weekday || [];
  // weekend (Sat/Sun = index 5,6) tinted differently from weekdays
  const colors = wd.map((b,i)=> i>=5 ? "#ff6b81" : "#36d399");
  charts.weekday = new Chart(cv, {
    type:"bar",
    data:{labels:wd.map(b=>b.weekday), datasets:[{data:wd.map(b=>b.avg),
      backgroundColor:colors, borderRadius:4, maxBarThickness:34}]},
    options:{plugins:{legend:{display:false},tooltip:{displayColors:false,callbacks:{
      label:(ctx)=>{ const b=wd[ctx.dataIndex]; return ` 日均 ${b.avg} 字 · ${b.active_days} 个活跃日`; }}}},
      scales:{x:{grid:{display:false}},y:{beginAtZero:true,ticks:{precision:0}}}}
  });
  if(note){
    const ratio = r.ratio;
    let verdict = "";
    if(ratio != null){
      if(ratio >= 1.15) verdict = "你在<b>周末</b>写得更多。";
      else if(ratio <= 0.85) verdict = "你在<b>工作日</b>写得更多。";
      else verdict = "工作日与周末的产出比较接近。";
    }
    note.innerHTML = `工作日日均 <b>${r.weekday_avg}</b> 字 · 周末日均 <b>${r.weekend_avg}</b> 字。${verdict}`;
  }
}

// ---- yearly contribution calendar (range-independent) ----
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

// ---- dashboard usage history (0.2.8; range-independent) ----
let usageLoaded = false;
async function loadUsage(force){
  const host = document.getElementById("usageBody");
  if(!host || (usageLoaded && !force)) return;
  let r; try{ r = await apiGet("usage", {days:30}); }catch(e){ return; }
  usageLoaded = true;
  renderUsage(r);
}
function usageAgo(ts){
  if(!ts) return "—";
  const d = new Date(ts*1000), now = Date.now();
  const days = Math.floor((now - ts*1000)/86400000);
  const hm = String(d.getHours()).padStart(2,"0")+":"+String(d.getMinutes()).padStart(2,"0");
  if(days<=0) return "今天 "+hm;
  if(days===1) return "昨天 "+hm;
  if(days<7) return days+" 天前";
  return d.toLocaleDateString();
}
function renderUsage(r){
  const host = document.getElementById("usageBody");
  if(!host) return;
  if(charts.usageDaily){ charts.usageDaily.destroy(); charts.usageDaily=null; }
  if(charts.usageHour){ charts.usageHour.destroy(); charts.usageHour=null; }
  if(!r || !r.total){
    host.innerHTML = emptyHTML("还没有使用记录。打开看板或随身鸭后，这里会出现你的使用足迹。");
    return;
  }
  const cards = [
    [r.total.toLocaleString(), "累计打开"],
    [r.dashboard.toLocaleString(), "看板"],
    [r.mini.toLocaleString(), "随身鸭"],
    [r.active_days.toLocaleString(), "使用天数"],
    [usageAgo(r.last_ts), "最近一次"],
  ];
  const recent = (r.recent||[]).map(x=>
    `<span class="chip usage-chip ${x.kind==="mini"?"mini":""}">${x.kind==="mini"?"随身鸭":"看板"} · ${usageAgo(x.ts)}</span>`
  ).join("") || emptyHTML("暂无记录");
  const busiest = r.busiest_day ? `最常打开：${r.busiest_day.date}（${r.busiest_day.count} 次）` : "";
  host.innerHTML =
    `<div class="usage-cards">`+
      cards.map(([v,l])=>`<div class="s"><b>${v}</b><small>${l}</small></div>`).join("")+
    `</div>`+
    `<div class="usage-charts">`+
      `<div class="usage-chart"><div class="subhd">每日打开次数 <span>近 30 天</span></div><div style="position:relative;height:150px"><canvas id="usageDaily"></canvas></div></div>`+
      `<div class="usage-chart"><div class="subhd">活跃时段 <span>按小时</span></div><div style="position:relative;height:150px"><canvas id="usageHour"></canvas></div></div>`+
    `</div>`+
    `<div class="subhd">最近足迹 <span>${escapeHtml(busiest)}</span></div>`+
    `<div class="chips usage-recent">${recent}</div>`;
  const pd = r.per_day || [];
  charts.usageDaily = new Chart(document.getElementById("usageDaily"), {
    type:"bar",
    data:{labels:pd.map(d=>d.date.slice(5)),
      datasets:[
        {label:"看板",data:pd.map(d=>d.total-d.mini),backgroundColor:"#ffce33",borderRadius:3,maxBarThickness:14,stack:"u"},
        {label:"随身鸭",data:pd.map(d=>d.mini),backgroundColor:"#36d399",borderRadius:3,maxBarThickness:14,stack:"u"},
      ]},
    options:{maintainAspectRatio:false,plugins:{legend:{display:true,labels:{boxWidth:10,font:{size:11}}},tooltip:{displayColors:true}},
      scales:{x:{stacked:true,grid:{display:false},ticks:{autoSkip:true,maxRotation:0,maxTicksLimit:10}},
              y:{stacked:true,beginAtZero:true,ticks:{precision:0}}}}
  });
  charts.usageHour = new Chart(document.getElementById("usageHour"), {
    type:"bar",
    data:{labels:[...Array(24).keys()].map(h=>String(h).padStart(2,"0")),
      datasets:[{data:r.by_hour,backgroundColor:"#a78bfa",borderRadius:3,maxBarThickness:14}]},
    options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{displayColors:false}},
      scales:{x:{grid:{display:false},ticks:{autoSkip:true,maxTicksLimit:12}},y:{beginAtZero:true,ticks:{precision:0}}}}
  });
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

// Register the lazy panels (loaders defined above). vocab/contrib/usage are
// range-independent -> load once; richness/appEff/weekday follow the range.
registerLazyPanel("#richnessChart", (params, seq)=>loadRichness(seq, params));
registerLazyPanel("#appEffPanel",   (params)=>loadAppEff(params));
registerLazyPanel("#weekdayPanel",  (params)=>loadWeekday(params));
registerLazyPanel("#vocabPanel",    ()=>loadVocab(), {once:true});
registerLazyPanel("#contribCal",    ()=>loadContrib(), {once:true});
registerLazyPanel("#usagePanel",    ()=>loadUsage(), {once:true});

// Info tooltips for the 0.3.0 panels.
setTip("vocabInfo", "你累计写过的<b>不同词 / 不同字</b>随时间的增长曲线，按每个词 / 字第一次出现的日期累加。<b>不随顶部时间范围变化</b>，展示的是你词汇量一路变大的全过程。");
setTip("appEffInfo", "各应用里你的<b>平均打字速度</b>（字 / 分），用与顶部速度一致的会话 / 活跃时长口径分别计算，只统计这段时间里字数足够的应用。看看你在哪个软件里写得最顺。");
setTip("weekdayInfo", "按星期几统计的<b>每个活跃日平均字数</b>（绿色工作日 / 红色周末），以及工作日与周末的日均对比——帮你看清自己的写作节奏更偏平时还是周末。");

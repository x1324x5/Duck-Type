const DOW = ["周日","周一","周二","周三","周四","周五","周六"];
let range = "7d", custom = {start:null, end:null};
let charts = {};
let factTimer = null;
let themeMode = "system";
let tickerRefreshSeconds = 60;

Chart.defaults.font.family = '"Segoe UI","Microsoft YaHei",sans-serif';
Chart.defaults.font.size = 12;

// ---- theme ----
function cssVar(name){ return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function applyChartTheme(){
  Chart.defaults.color = cssVar("--muted");
  Chart.defaults.borderColor = cssVar("--line");
}
// brand palette used by charts (kept consistent across both themes)
const PALETTE = ["#ffce33","#36d399","#ffb454","#ff6b81","#a78bfa","#22d3ee","#f472b6","#facc15","#4ade80","#94a3b8"];
function chartColors(n){ return Array.from({length:n}, (_, i)=>PALETTE[i % PALETTE.length]); }
function themeIcon(mode, effective){
  if(mode === "system") return '<svg class="line" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/></svg>';
  if(effective === "light") return '<svg class="line" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 3v2"/><path d="M12 19v2"/><path d="M4.22 4.22l1.42 1.42"/><path d="M18.36 18.36l1.42 1.42"/><path d="M3 12h2"/><path d="M19 12h2"/><path d="M4.22 19.78l1.42-1.42"/><path d="M18.36 5.64l1.42-1.42"/></svg>';
  return '<svg class="line" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 14.5A7.5 7.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/></svg>';
}
// A doughnut dataset with a clear hover/active treatment: a thin gap between
// slices (panel-coloured border) that turns into a bright white ring and pops
// the slice outward when hovered, so the segment under the cursor stands out.
function donutDataset(data, n){
  return {
    data,
    backgroundColor: chartColors(n),
    borderColor: cssVar("--panel"),
    borderWidth: 2,
    hoverOffset: 12,
    hoverBorderColor: "#ffffff",
    hoverBorderWidth: 3,
  };
}
const themeMedia = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
function effectiveTheme(mode){
  if(mode === "light") return "light";
  if(mode === "dark") return "";
  return (themeMedia && themeMedia.matches) ? "light" : "";
}
function syncThemeModeButtons(){
  document.querySelectorAll("#themeMode button").forEach(b=>{
    b.classList.toggle("active", b.dataset.themeMode === themeMode);
  });
}
function setThemeMode(mode, persist){
  themeMode = ["system","light","dark"].includes(mode) ? mode : "system";
  const t = effectiveTheme(themeMode);
  document.documentElement.dataset.theme = t;        // "" (dark) or "light"
  document.getElementById("themeBtn").innerHTML = themeIcon(themeMode, t);
  document.getElementById("themeBtn").title =
    themeMode === "system" ? "主题：跟随系统" : (themeMode === "light" ? "主题：亮色" : "主题：暗色");
  if(persist) try{ localStorage.setItem("dt-theme-mode", themeMode); }catch(e){}
  syncThemeModeButtons();
  applyChartTheme();
}
(function initTheme(){
  let m = "system";
  try{ m = localStorage.getItem("dt-theme-mode") || "system"; }catch(e){}
  setThemeMode(m, false);
})();
if(themeMedia) themeMedia.addEventListener("change", ()=>{
  if(themeMode === "system"){ setThemeMode("system", false); refreshActive(); }
});
document.getElementById("themeBtn").addEventListener("click", ()=>{
  const order = ["system","light","dark"];
  const next = order[(order.indexOf(themeMode) + 1) % order.length];
  setThemeMode(next, true);
  saveDashboardPrefs({theme_mode: themeMode});
  refreshActive();   // re-render charts/heatmap with the new theme colours
});
document.getElementById("themeMode").addEventListener("click", e=>{
  const mode = e.target.dataset.themeMode;
  if(!mode) return;
  setThemeMode(mode, true);
  refreshActive();
});
async function loadDashboardPrefs(){
  try{
    const c = await DT.config_get();
    setThemeMode(c.theme_mode || "system", true);
    tickerRefreshSeconds = Math.max(10, Number(c.ticker_refresh_seconds || 60));
    window.__pieIncludePct = c.pie_download_include_pct !== false;
  }catch(e){}
}
async function saveDashboardPrefs(body){
  try{ await DT.config_set(body); }catch(e){}
}

// ---- backend bridge -------------------------------------------------
// In the native window data goes straight to Python via pywebview's js_api
// (no HTTP, no port). In a dev browser it falls back to the Flask shim.
function isNative(){ return !!(window.pywebview && window.pywebview.api); }
function nApi(){ return window.pywebview.api; }
function _post(b){ return {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}; }
function _j(r){ return r.json(); }
// Browser/dev download that never navigates the SPA away (a plain window.open to
// a streamed attachment can blank the page). Fetches as a blob and clicks an
// off-DOM link instead. Native builds use the OS save dialog via the bridge.
async function browserDownload(url){
  try{
    const res = await fetch(url);
    if(!res.ok) return {ok:false, error:"HTTP "+res.status};
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    const m = /filename="?([^"]+)"?/.exec(cd);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = m ? m[1] : "ducktype_backup.duckpack";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(()=>URL.revokeObjectURL(a.href), 4000);
    return {ok:true};
  }catch(e){ return {ok:false, error:String(e)}; }
}
function rangeParams(extra){
  const p = Object.assign({}, extra||{});
  p.range = range;
  if(range === "custom"){ p.start = custom.start||""; p.end = custom.end||""; }
  return p;
}
function qs(extra){ return new URLSearchParams(rangeParams(extra)).toString(); }
function pct2(count, total){ return total ? (count / total * 100).toFixed(2) : "0.00"; }
async function apiGet(endpoint, params){
  params = params || {};
  const t0 = performance.now();
  try{
    const u = new URLSearchParams(params).toString();
    const res = isNative()
      ? await nApi().get(endpoint, params)
      : await fetch("/api/"+endpoint+(u?("?"+u):"")).then(_j);
    const ms = performance.now() - t0;
    const msg = `[DuckType] api ${endpoint} ${ms.toFixed(0)}ms`;
    (ms > 700 ? console.warn : console.debug)(msg, params);
    return res;
  }catch(e){
    console.warn(`[DuckType] api ${endpoint} failed after ${(performance.now()-t0).toFixed(0)}ms`, e);
    throw e;
  }
}
async function getJSON(path){
  // Accepts the historical "/api/endpoint?a=b" form and routes it to the bridge.
  const [ep, query] = path.replace(/^\/api\//,"").split("?");
  const params = rangeParams(Object.fromEntries(new URLSearchParams(query||"")));
  return apiGet(ep, params);
}
const DT = {
  config_get: ()=> isNative()? nApi().config_get() : fetch("/api/config").then(_j),
  config_set: (b)=> isNative()? nApi().config_set(b) : fetch("/api/config",_post(b)).then(_j),
  status: ()=> isNative()? nApi().status() : fetch("/api/status").then(_j),
  ticker: ()=> apiGet("ticker", {}),
  quote_seen: (text)=> isNative()? nApi().quote_seen(text) : fetch("/api/quote_seen",_post({text})).then(_j),
  update_check: ()=> isNative()? nApi().update_check() : fetch("/api/update/check").then(_j),
  update_apply: ()=> isNative()? nApi().update_apply() : fetch("/api/update/apply",{method:"POST"}).then(_j),
  update_progress: ()=> isNative()? nApi().update_progress() : fetch("/api/update/progress").then(_j),
  data_summary: ()=> isNative()? nApi().data_summary() : fetch("/api/data/summary").then(_j),
  data_pick_dir: ()=> isNative()? nApi().data_pick_dir() : fetch("/api/data/pick_dir",{method:"POST"}).then(_j),
  data_relocate: (dir)=> isNative()? nApi().data_relocate(dir) : fetch("/api/data/relocate",_post({dir})).then(_j),
  data_relocate_progress: ()=> isNative()? nApi().data_relocate_progress() : fetch("/api/data/relocate/progress").then(_j),
  data_clear: ()=> isNative()? nApi().data_clear() : fetch("/api/data/clear",{method:"POST"}).then(_j),
  data_export: ()=> isNative()? nApi().data_export() : browserDownload("/api/data/export"),
  data_import: (file)=> isNative()? nApi().data_import() : (()=>{ const fd=new FormData(); fd.append("file", file); return fetch("/api/data/import",{method:"POST",body:fd}).then(_j); })(),
  lexicon_list: ()=> isNative()? nApi().lexicon_list() : fetch("/api/lexicon/list").then(_j),
  lexicon_create: (b)=> isNative()? nApi().lexicon_create(b.name, b.text, b.words) : fetch("/api/lexicon/create",_post(b)).then(_j),
  lexicon_update: (b)=> isNative()? nApi().lexicon_update(b.id, b.name, b.enabled) : fetch("/api/lexicon/update",_post(b)).then(_j),
  lexicon_delete: (id)=> isNative()? nApi().lexicon_delete(id) : fetch("/api/lexicon/delete",_post({id})).then(_j),
  lexicon_import: (file)=> isNative()? nApi().lexicon_import_file() : (()=>{ const fd=new FormData(); fd.append("file", file); return fetch("/api/lexicon/import",{method:"POST",body:fd}).then(_j); })(),
  lexicon_stats: (id, n)=> apiGet("lexicon_stats", rangeParams({id, n: n||50})),
  lexicon_words: (b)=> apiGet("lexicon_words", b),
  lexicon_edit_words: (b)=> isNative()? nApi().lexicon_edit_words(b.id, b.add, b.remove) : fetch("/api/lexicon/edit_words",_post(b)).then(_j),
  demo_status: ()=> isNative()? nApi().demo_status() : fetch("/api/demo/status").then(_j),
  demo_set: (on)=> isNative()? nApi().demo_set(on) : fetch("/api/demo",_post({on})).then(_j),
  data_delete: (start,end)=> isNative()? nApi().data_delete(start,end) : fetch("/api/data/delete",_post({start,end})).then(_j),
  card_png: (period,template)=> isNative()? nApi().card_png(period,template||"card") : Promise.resolve("/api/card?period="+encodeURIComponent(period)+"&template="+(template||"card")+"&t="+Date.now()),
  save_card: (period,template)=> isNative()? nApi().save_card(period,template||"card") : (window.open("/api/card?period="+encodeURIComponent(period)+"&template="+(template||"card")), Promise.resolve({ok:true})),
  export_sequence: (fmt, params)=> isNative()? nApi().export_sequence(fmt, params || rangeParams()) : (window.open("/api/export/sequence."+fmt+"?"+new URLSearchParams(params || rangeParams()).toString()), Promise.resolve({ok:true})),
  report_generate: (p)=> isNative()? nApi().report_generate(p) : fetch("/api/report/generate",_post(p)).then(_j),
  report_progress: ()=> isNative()? nApi().report_progress() : fetch("/api/report/progress").then(_j),
  mini_stats: ()=> apiGet("mini_stats", {}),
  open_mini: ()=> isNative()? nApi().open_mini() : Promise.resolve({ok:true}),
  close_mini: ()=> isNative()? nApi().close_mini() : Promise.resolve({ok:true}),
  mini_resize: (w,h)=> isNative()? nApi().mini_resize(w,h) : Promise.resolve({ok:true}),
  reveal_path: (path)=> isNative()? nApi().reveal_path(path) : Promise.resolve({ok:false}),
  export_report_md: (period,start,end)=> isNative()? nApi().export_report_md(period,start||null,end||null)
    : browserDownload("/api/export/report.md?period="+encodeURIComponent(period)+(start?("&start="+start):"")+(end?("&end="+end):"")),
};
function makeBar(id, labels, data, color){
  if(charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), {
    type:"bar",
    data:{labels, datasets:[{data, backgroundColor:color, borderRadius:4}]},
    options:{plugins:{legend:{display:false}},scales:{x:{grid:{display:false}},y:{beginAtZero:true}}}
  });
}
// Horizontal bars that grow with the row count; the panel's .hbar wrapper scrolls.
// Clicking a bar (or its label) jumps to the search view for that char/word.
function makeRankBar(id, labels, data, color, onPick){
  if(charts[id]) charts[id].destroy();
  const cv = document.getElementById(id);
  cv.parentElement.style.height = Math.max(150, labels.length*22 + 16) + "px";
  charts[id] = new Chart(cv, {
    type:"bar",
    data:{labels, datasets:[{data, backgroundColor:color, borderRadius:4,
      barThickness:"flex", maxBarThickness:18,
      // hovered (clickable) bar lights up in the brand accent for clear feedback
      hoverBackgroundColor: onPick ? cssVar("--accent") : color}]},
    options:{indexAxis:"y", maintainAspectRatio:false,
      onClick:(e,els)=>{ if(onPick && els.length) onPick(labels[els[els.length-1].index]); },
      onHover:(e,els)=>{ e.native.target.style.cursor = (onPick && els.length) ? "pointer" : "default"; },
      plugins:{legend:{display:false},tooltip:{displayColors:false,
        callbacks:{footer:()=>onPick?"点击查看详情 →":""}}},
      scales:{x:{beginAtZero:true,grid:{color:cssVar("--line")},ticks:{precision:0}},
              y:{grid:{display:false},ticks:{autoSkip:false,font:{size:12}}}}}
  });
}
let chartResizeTimer = null;
window.addEventListener("resize", ()=>{
  clearTimeout(chartResizeTimer);
  chartResizeTimer = setTimeout(()=>{
    Object.values(charts).forEach(c=>{ try{ c && c.resize(); }catch(e){} });
    if(range === "today") layoutTodayCloud();
  }, 120);
});
function gotoSearch(term){
  document.querySelector('#tabs button[data-v="search"]').click();
  const inp = document.getElementById("searchInput");
  inp.value = term; doSearch();
  // The view switch resets scroll to the top; bring the 查询 panel (and its
  // results) into view so the user lands on the query, not above it.
  const panel = document.getElementById("queryPanel");
  if(panel) requestAnimationFrame(()=>{
    try{ panel.scrollIntoView({behavior:"smooth", block:"start"}); }catch(e){}
  });
}
function escapeHtml(s){return (s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}
function escapeAttr(s){return escapeHtml(s).replace(/["']/g,c=>({"\"":"&quot;","'":"&#39;"}[c]));}

// ---- shared empty / loading-skeleton primitives ----
// One source of truth so every panel's "no data" and "loading" states look the
// same (a muted line for empties; a shimmering placeholder for loads).
function emptyHTML(text, icon){
  return `<div class="empty">${icon?`<span class="empty-ico">${icon}</span>`:""}${escapeHtml(text||"暂无数据")}</div>`;
}
function skelChart(text){ return `<div class="chart-skel">${escapeHtml(text||"加载中…")}</div>`; }
// widths: array of percentages; renders stacked shimmer lines.
function skelLines(widths){
  return (widths||[70,50]).map((w,i)=>
    `<div class="skel-line" style="width:${w}%${i?";margin-top:12px":""}"></div>`).join("");
}

// ---- info tooltips ----
function setTip(id, html){
  const e=document.getElementById(id);
  if(!e) return;
  const old = e.querySelector(".tip");
  if(old) old.remove();
  if(!e.firstChild || e.firstChild.nodeType !== Node.TEXT_NODE) e.prepend(document.createTextNode("i"));
  else e.firstChild.textContent = "i";
  e.insertAdjacentHTML("beforeend", '<div class="tip" role="tooltip">'+html+'</div>');
}
setTip("posInfo", "<b>词性标签</b>来自本机分词结果：<br>n 名词 · v 动词 · a 形容词 · d 副词 · m 数词 · q 量词 · r 代词 · p 介词 · c 连词 · t 时间词 · f 方位词 · i 成语 · nr 人名 · ns 地名 · nt 机构。<br>点击扇区，可以查看该词性下的高频词。<br><b>助词「u」</b>会继续细分为 uj「的」、ul「了」、ug「过」、uv「地」、uz「着」。");
setTip("topicInfo", "这里会从当前时间范围内提取更有代表性的关键词。字号越大，说明这个词在这段时间越突出；点击词语可查看相关输入记录。");
setTip("richInfo", "词汇丰富度＝当天的<b>不同汉字数 ÷ 总字数</b>。越高说明用字越多样，越低说明重复越多。只统计你通过输入法上屏的汉字。");
setTip("contribInfo", "近一年里你每天上屏的汉字量，颜色越深当天写得越多（仿 GitHub 贡献图）。这张图始终展示最近约 52 周，<b>不随顶部时间范围变化</b>；点任意一格可跳到那一天。");
setTip("reportInfo", "报告卡片在本机生成，可保存为 PNG。统计只包含正常上屏的输入内容，大段粘贴会被自动忽略。");
setTip("lexInfo", "词库＝一份词语清单。DuckType 在你的上屏序列里逐段最长匹配这些词，统计它们的出现占比。<br>内置「成语」约 3 万条（以四字为主，被认成成语的词不会出现在趣味页的「长词」里）；「关注词」「生僻字」「常用字」由你的关注词、数据与内置常用字表自动生成，均不可删除、可停用。<br>点每行的「查看」可浏览 / 搜索词条；自建词库还能在弹窗里增删词。「常用字」是判定生僻字的过滤表，可在其弹窗里维护「额外常用字」。<br>你也可以上传现成词库文件，或粘贴 / 逐条新建自己的词库。<br>词库统计是额外的观察层，<b>不影响看板的字数与词频</b>。");
setTip("lexShareInfo", "按已启用的词库分别展示：这段时间里，词库中各个词语的出现占比。点击饼图扇区可跳到「搜索」查看该词的详细分布。");
setTip("lexRepInfo", "这段时间里各词库被你写到的总次数排行，以及每个词库里出现最多的那个词。点词可跳到搜索看详情。");
setTip("rareInfo", "这里展示你输入过但不常见的汉字。常见语气词、姓氏、地名和外来字已经做了过滤。");
setTip("trackInfo", "关注词按你输入的原文逐字精确匹配，不依赖分词，所以人名、缩写、项目代号也能数准。统计跟随顶部时间范围；点卡片可查看详细的时段、程序和上下文分布。");
setTip("usageInfo", "记录你每次打开仪表盘或随身鸭的时间，统计使用频率、活跃时段与最近足迹。这只是 UI 使用记录，<b>与你的打字数据无关</b>，删除打字数据不会清掉它。");
setTip("cmpInfo", "选两个时段并排比较：左边 A 为重点、右边 B 为基准，箭头表示 A 相对 B 的变化。支持「本周 vs 上周」「今天 vs 某一天」等，也可各自选自定义区间。");
setTip("dayInfo", "挑某一天，集中回看那天的产出、节奏、用字、应用与片段，并和你的活跃日均对照。用 ← → 或日期框切换日期，独立于顶部时间范围。");
setTip("recInfo", "你的全部历史里那些「最」的时刻：最高产的一天、最快的速度、最长的连续……都来自真正上屏的汉字，随数据增长自动刷新。");

// ---- tabs ----

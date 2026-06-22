// ---- 回顾 (single-day review) ----
let dayCur = null;   // "YYYY-MM-DD"; null => today on first open

function _todayStr(){
  const d = new Date(); const p = n=>String(n).padStart(2,"0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
}
function dayShift(delta){
  const base = dayCur || _todayStr();
  const d = new Date(base + "T00:00:00");
  d.setDate(d.getDate() + delta);
  const p = n=>String(n).padStart(2,"0");
  dayCur = `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;
  if(dayCur > _todayStr()) dayCur = _todayStr();   // never go past today
  loadDayView();
}
function loadDayView(){
  if(!dayCur) dayCur = _todayStr();
  const pick = document.getElementById("dayPick");
  if(pick){ pick.max = _todayStr(); pick.value = dayCur; }
  const body = document.getElementById("dayBody");
  body.innerHTML = '<div class="panel"><div class="chart-skel">正在回看这一天…</div></div>';
  apiGet("day", {day: dayCur}).then(renderDayView).catch(()=>{
    body.innerHTML = '<div class="panel">'+emptyHTML("读取这一天失败，请重试。")+'</div>';
  });
}
function renderDayView(d){
  const body = document.getElementById("dayBody");
  if(!d || !d.chars){
    body.innerHTML = `<div class="panel day-empty">`+
      emptyHTML(`${d?d.day:""}${d&&d.weekday?("（"+d.weekday+"）"):""} 这天没有记录到上屏汉字。换一天看看，或用上面的 ← → 翻页。`, "🦆")+
      `</div>`;
    return;
  }
  const fmtHM = ts=> ts? new Date(ts*1000).toLocaleString("zh-CN",{hour:"2-digit",minute:"2-digit"}) : "";
  const vs = d.vs_avg_pct;
  const vsHtml = (vs===null||vs===undefined) ? "—"
    : `<span class="${vs>=0?"up":"down"}">${vs>=0?"▲":"▼"} ${Math.abs(vs)}%</span>`;
  const peak = (d.peak_hour===null||d.peak_hour===undefined) ? "—"
    : String(d.peak_hour).padStart(2,"0")+":00";
  const rankBadge = d.rank ? `<span class="day-rank">历史第 ${d.rank} 高产</span>` : "";
  const stats = [
    [d.distinct_chars.toLocaleString(), "不同汉字"],
    [d.active_minutes+" 分", "活跃时长"],
    [d.cpm, "平均速度(字/分)"],
    [d.peak_cpm, "峰值速度(字/分)"],
    [d.sessions, "输入会话"],
    [(d.edit_ratio*100).toFixed(1)+"%", "修改率"],
    [peak, "高峰时段"],
    [vsHtml, "对比活跃日均"],
  ];
  const chips = (arr, key)=> arr.length
    ? arr.map(x=>`<span class="chip lk" data-w="${escapeAttr(x[key])}">${escapeHtml(x[key])}<b>${x.count}</b></span>`).join("")
    : '<span class="empty" style="padding:0">—</span>';
  const apps = d.apps.length
    ? d.apps.map(a=>`<div class="day-approw"><span class="da-name">${escapeHtml(a.app||"未知")}</span>`+
        `<span class="da-bar"><i style="width:${a.share}%"></i></span>`+
        `<span class="da-num">${a.count} · ${a.share}%</span></div>`).join("")
    : emptyHTML("这天没有按应用区分的记录。");
  const runs = (d.runs||[]).length
    ? d.runs.map(r=>`<div class="day-run"><span class="dr-meta">${fmtHM(r.ts)} · ${escapeHtml(r.app||"")}</span>`+
        `<span class="dr-text">${escapeHtml((r.text||"").slice(0,80))}${(r.text||"").length>80?"…":""}</span></div>`).join("")
    : emptyHTML("没有可展示的片段。");
  body.innerHTML = `
    <div class="panel day-hero">
      <div class="dh-left">
        <div class="dh-date">${escapeHtml(d.day)} <span>${escapeHtml(d.weekday||"")}</span></div>
        <div class="dh-num">${d.chars.toLocaleString()}<small>字</small></div>
        <div class="dh-sub">活跃日均 ${d.avg_per_active_day.toLocaleString()} 字 · ${vsHtml} ${rankBadge}</div>
      </div>
    </div>
    <div class="panel">
      <h2>这天的几个数字</h2>
      <div class="statgrid day-statgrid">${stats.map(([v,l])=>`<div class="s"><b>${v}</b><small>${l}</small></div>`).join("")}</div>
    </div>
    <div class="panel">
      <h2>这天的输入节奏 <span>按小时</span></h2>
      <div style="position:relative;height:170px"><canvas id="dayHourChart"></canvas></div>
    </div>
    <div class="grid">
      <div class="panel"><h2>高频词 <span>2 字以上</span></h2><div class="chips">${chips(d.top_words,"word")}</div></div>
      <div class="panel"><h2>高频字</h2><div class="chips">${chips(d.top_chars,"ch")}</div></div>
    </div>
    <div class="panel"><h2>各应用输入量</h2><div class="day-apps">${apps}</div></div>
    <div class="panel"><h2>这天的片段 <span>节选</span></h2><div class="day-runs">${runs}</div></div>`;
  // hourly chart
  if(charts.dayHourChart) charts.dayHourChart.destroy();
  charts.dayHourChart = new Chart(document.getElementById("dayHourChart"), {
    type:"bar",
    data:{labels:[...Array(24).keys()].map(h=>String(h).padStart(2,"0")),
      datasets:[{data:d.by_hour, backgroundColor:cssVar("--accent")||"#ffce33", borderRadius:3, maxBarThickness:20}]},
    options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{displayColors:false,
      callbacks:{label:c=>` ${c.raw} 字`}}},
      scales:{x:{grid:{display:false}},y:{beginAtZero:true,ticks:{precision:0}}}}
  });
  body.querySelectorAll(".lk").forEach(el=>el.addEventListener("click", ()=>gotoSearch(el.dataset.w)));
}
document.getElementById("dayPrev").addEventListener("click", ()=>dayShift(-1));
document.getElementById("dayNext").addEventListener("click", ()=>dayShift(1));
document.getElementById("dayToday").addEventListener("click", ()=>{ dayCur = _todayStr(); loadDayView(); });
document.getElementById("dayPick").addEventListener("change", e=>{
  if(e.target.value){ dayCur = e.target.value; loadDayView(); }
});

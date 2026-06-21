let curPeriod = "today";
let repParams = {period:"today"};   // {period} or {period:"custom", start, end}
let reportReqSeq = 0;
function loadReportCurrent(){ loadReport(repParams); }
document.getElementById("periodBtns").addEventListener("click", e=>{
  if(!e.target.dataset.p) return;
  curPeriod = e.target.dataset.p;
  repParams = {period: curPeriod};
  document.querySelectorAll("#periodBtns button").forEach(b=>b.classList.toggle("active", b===e.target));
  loadReport(repParams);
});
document.getElementById("rep-apply").addEventListener("click", ()=>{
  const s = document.getElementById("rep-start").value, en = document.getElementById("rep-end").value;
  if(!s && !en) return;
  curPeriod = "custom";
  repParams = {period:"custom", start:s, end:en};
  document.querySelectorAll("#periodBtns button").forEach(b=>b.classList.remove("active"));
  loadReport(repParams);
});
function renderReportRows(r){
  const rows = [];
  rows.push(["这一时段", r.label]);
  rows.push(["上屏汉字", r.chars.toLocaleString() + " 字"]);
  if(r.distinct_chars) rows.push(["不同汉字", r.distinct_chars.toLocaleString() + " 个"]);
  if(r.delta_pct!==null && r.delta_pct!==undefined) rows.push(["较上一周期", (r.delta_pct>=0?"+":"")+r.delta_pct+"%"]);
  if(r.active_days) rows.push(["活跃天数", r.active_days + " 天"]);
  if(r.peak_window) rows.push(["高产时段", r.peak_window[2] + " " + String(r.peak_window[0]).padStart(2,"0")+":00–"+String(r.peak_window[1]).padStart(2,"0")+":00"]);
  else if(r.peak_hour!==null && r.peak_hour!==undefined) rows.push(["高峰时段", String(r.peak_hour).padStart(2,"0")+":00"]);
  if(r.fav_char) rows.push(["最爱的字", r.fav_char]);
  if(r.top_app) rows.push(["主力应用", r.top_app + " · " + r.top_app_share + "%"]);
  if(r.busiest_week) rows.push(["最忙周", r.busiest_week + " · " + (r.busiest_week_count||0).toLocaleString() + " 字"]);
  if(r.quietest_week && r.quietest_week !== r.busiest_week) rows.push(["最闲周", r.quietest_week + " · " + (r.quietest_week_count||0).toLocaleString() + " 字"]);
  if(r.busiest_weekday) rows.push(["最忙星期", r.busiest_weekday + " · " + (r.busiest_weekday_count||0).toLocaleString() + " 字"]);
  if(r.quietest_weekday && r.quietest_weekday !== r.busiest_weekday) rows.push(["最闲星期", r.quietest_weekday + " · " + (r.quietest_weekday_count||0).toLocaleString() + " 字"]);
  if(r.best_day) rows.push(["最高产日", r.best_day + (r.best_day_weekday?("（"+r.best_day_weekday+"，"):"（") + r.best_day_count + " 字）"]);
  if(r.longest_session_min) rows.push(["最长连续输入", r.longest_session_min + " 分钟"]);
  if(r.streak_best) rows.push(["最长连续天数", r.streak_best + " 天"]);
  document.getElementById("reportRows").innerHTML = rows.map(
    ([k,v])=>`<div class="rrow"><span>${k}</span><b>${escapeHtml(String(v))}</b></div>`).join("");
}
function highlightMetric(body, metric){
  const text = String(body || "");
  const key = String(metric || "").trim();
  if(!key || !text.includes(key)) return escapeHtml(text);
  return text.split(key).map(escapeHtml).join(`<mark>${escapeHtml(key)}</mark>`);
}
function renderReportInsights(items){
  const el = document.getElementById("reportInsights");
  if(!items || !items.length){ el.innerHTML = ""; return; }
  el.innerHTML = items.map(x=>`<div class="insight tone-${escapeAttr(x.tone||"neutral")}">
    ${x.metric ? `<span class="metric">${escapeHtml(x.metric)}</span>` : ""}
    <b>${escapeHtml(x.title||"洞察")}</b><p>${highlightMetric(x.body, x.metric)}</p>
  </div>`).join("");
}
async function loadReport(params){
  const seq = ++reportReqSeq;
  const period = params.period;
  const isCustom = period === "custom";
  // reset the on-demand full-report section whenever the range changes
  resetRichReport();
  document.getElementById("reportNarrative").className = "narrative skel";
  document.getElementById("reportNarrative").textContent = "正在生成小结…";
  document.getElementById("reportInsights").innerHTML =
    '<div class="insight"><div class="skel-line" style="width:52%"></div><div class="skel-line" style="width:86%;margin-top:10px"></div></div>'+
    '<div class="insight"><div class="skel-line" style="width:48%"></div><div class="skel-line" style="width:78%;margin-top:10px"></div></div>';
  document.getElementById("reportRows").innerHTML =
    '<div class="skel-line" style="width:76%;height:18px"></div><div class="skel-line" style="width:62%;height:18px;margin-top:16px"></div><div class="skel-line" style="width:70%;height:18px;margin-top:16px"></div>';
  // PNG card only supports the named calendar periods.
  const cardWrap = document.querySelector("#view-report .cardprev");
  if(isCustom){ cardWrap.style.display = "none"; }
  else {
    cardWrap.style.display = "";
    document.getElementById("cardImg").style.opacity = ".45";
    const cardDl = document.getElementById("cardDl");
    cardDl.removeAttribute("href");
    cardDl.onclick = (e)=>{ e.preventDefault(); DT.save_card(period, "card").then(notifyDownload).catch(()=>{}); };
    const cardDlLong = document.getElementById("cardDlLong");
    cardDlLong.removeAttribute("href");
    cardDlLong.onclick = (e)=>{ e.preventDefault(); DT.save_card(period, "long").then(notifyDownload).catch(()=>{}); };
  }
  let fast;
  try{ fast = await apiGet("report_fast", params); }
  catch(e){ if(seq===reportReqSeq){ document.getElementById("reportNarrative").className="narrative"; document.getElementById("reportNarrative").textContent="读取失败，请重试。"; } return; }
  if(seq !== reportReqSeq) return;
  window.__reportFast = fast;
  document.getElementById("reportNarrative").className = "narrative";
  document.getElementById("reportNarrative").textContent = fast.narrative || "";
  renderReportInsights(fast.insights || []);
  renderReportRows(fast);
  loadLexiconReport(params);
  if(!isCustom){
    DT.card_png(period).then(src=>{
      if(seq !== reportReqSeq) return;
      document.getElementById("cardImg").src = src;
      document.getElementById("cardImg").style.opacity = "1";
    }).catch(e=>{ if(seq===reportReqSeq) document.getElementById("cardImg").style.opacity = "1"; });
  }
}
async function loadLexiconReport(params){
  const host = document.getElementById("lexReport");
  host.innerHTML = '<div class="empty">统计中…</div>';
  let r; try{ r = await apiGet("lexicon_report", params); }
  catch(e){ host.innerHTML = '<div class="empty">读取失败。</div>'; return; }
  const lx = (r && r.lexicons) || [];
  if(!lx.length){ host.innerHTML = '<div class="empty">这段时间还没有匹配到任何词库里的词。</div>'; return; }
  const top = lx[0];
  const lead = `这段时间用得最多的词库是「<b>${escapeHtml(top.name)}</b>」（共 ${top.total} 次），`+
    `其中最常出现的是 <b class="lk" data-w="${escapeAttr(top.top_word)}">${escapeHtml(top.top_word)}</b>（${top.top_count} 次）。`;
  const rows = lx.map(it=>{
    const words = (it.top_words||[]).map(w=>
      `<span class="chip lk" data-w="${escapeAttr(w.word)}">${escapeHtml(w.word)}<b>${w.count}</b></span>`).join("");
    return `<div class="lexrep-row">
      <span class="lexrep-name">${escapeHtml(it.name)}</span>
      <span class="lexrep-bar"><i style="width:${pct(it.total, top.total)}%"></i></span>
      <span class="lexrep-num">${it.total} 次 · ${it.distinct} 词</span>
    </div>
    <div class="lexrep-words">${words || '<span class="empty" style="padding:0">—</span>'}</div>`;
  }).join("");
  host.innerHTML = `<div class="lexrep-lead">${lead}</div>${rows}`;
  host.querySelectorAll(".lk").forEach(el=>el.addEventListener("click", ()=>gotoSearch(el.dataset.w)));
}

// ---- comparison report (0.2.8): two periods side by side ----
const CMP_PRESETS = [
  ["today","今天"],["yesterday","昨天"],
  ["week","本周"],["last_week","上周"],
  ["month","本月"],["last_month","上月"],
  ["year","今年"],["last_year","去年"],
  ["day","某一天"],["custom","自定义区间"],
];
function cmpInitSelects(){
  ["a","b"].forEach((side, i)=>{
    const sel = document.getElementById("cmp"+side.toUpperCase()+"Kind");
    if(!sel || sel.options.length) return;
    sel.innerHTML = CMP_PRESETS.map(([v,l])=>`<option value="${v}">${l}</option>`).join("");
    sel.value = i===0 ? "week" : "last_week";   // default: 本周 vs 上周
    sel.addEventListener("change", ()=>cmpToggleInputs(side));
    cmpToggleInputs(side);
  });
}
function cmpToggleInputs(side){
  const S = side.toUpperCase();
  const kind = document.getElementById("cmp"+S+"Kind").value;
  document.getElementById("cmp"+S+"Day").hidden = kind!=="day";
  document.getElementById("cmp"+S+"Range").hidden = kind!=="custom";
}
function cmpSpec(side){
  const S = side.toUpperCase();
  const kind = document.getElementById("cmp"+S+"Kind").value;
  if(kind==="day") return {kind:"day", day:document.getElementById("cmp"+S+"Day").value};
  if(kind==="custom") return {kind:"custom",
    start:document.getElementById("cmp"+S+"Start").value,
    end:document.getElementById("cmp"+S+"End").value};
  return {kind};
}
// delta chip: polarity=+1 means "higher is better" (green up), -1 means lower is better.
function cmpDelta(pct, polarity){
  if(pct===null||pct===undefined) return '<span class="cmp-d flat">—</span>';
  if(pct===0) return '<span class="cmp-d flat">→ 0%</span>';
  const good = polarity>0 ? pct>0 : pct<0;
  return `<span class="cmp-d ${good?"up":"down"}">${pct>0?"▲":"▼"} ${Math.abs(pct)}%</span>`;
}
function cmpRow(label, av, bv, deltaHtml){
  return `<div class="cmp-row"><span class="cl">${label}</span>`+
    `<span class="ca">${av}</span><span class="cb">${bv}</span><span class="cd">${deltaHtml}</span></div>`;
}
function renderCompare(r){
  const host = document.getElementById("cmpResult");
  const a = r.a, b = r.b, d = r.deltas;
  const pctPts = v => (v>0?"+":"")+v+"pt";
  const editGood = d.edit_ratio_pts < 0;
  const rows =
    cmpRow("上屏汉字", a.chars.toLocaleString(), b.chars.toLocaleString(), cmpDelta(d.chars, 1))+
    cmpRow("不同汉字", a.distinct_chars.toLocaleString(), b.distinct_chars.toLocaleString(), cmpDelta(d.distinct_chars, 1))+
    cmpRow("活跃时长", a.active_minutes+" 分", b.active_minutes+" 分", cmpDelta(d.active_minutes, 1))+
    cmpRow("平均速度", a.cpm+" 字/分", b.cpm+" 字/分", cmpDelta(d.cpm, 1))+
    cmpRow("修改率", (a.edit_ratio*100).toFixed(1)+"%", (b.edit_ratio*100).toFixed(1)+"%",
      `<span class="cmp-d ${d.edit_ratio_pts===0?"flat":(editGood?"up":"down")}">${pctPts(d.edit_ratio_pts)}</span>`)+
    cmpRow("主力应用", escapeHtml(a.top_app||"—")+(a.top_app?` · ${a.top_app_share}%`:""),
      escapeHtml(b.top_app||"—")+(b.top_app?` · ${b.top_app_share}%`:""), "");
  const movers = (list, cls)=> list && list.length
    ? list.map(w=>`<span class="nw ${cls}">${escapeHtml(w.word)}<b>${w.count}</b></span>`).join("")
    : '<span class="empty" style="padding:0">—</span>';
  const moverBlock = (r.movers && (r.movers.only_a.length || r.movers.only_b.length)) ? `
    <div class="cmp-movers">
      <div><div class="subhd">A「${escapeHtml(a.label)}」更突出的词</div><div class="chips">${movers(r.movers.only_a,"fresh")}</div></div>
      <div><div class="subhd">B「${escapeHtml(b.label)}」更突出的词</div><div class="chips">${movers(r.movers.only_b,"")}</div></div>
    </div>` : "";
  host.innerHTML =
    `<div class="cmp-narr">${escapeHtml(r.narrative||"")}</div>`+
    `<div class="cmp-grid"><div class="cmp-row cmp-head"><span class="cl"></span>`+
      `<span class="ca">A · ${escapeHtml(a.label)}</span><span class="cb">B · ${escapeHtml(b.label)}</span><span class="cd">A 相对 B</span></div>`+
      rows + `</div>` + moverBlock;
}
document.getElementById("cmpRun").addEventListener("click", async ()=>{
  cmpInitSelects();
  const a = cmpSpec("a"), b = cmpSpec("b");
  if(a.kind==="day" && !a.day){ alert("请为 A 选择具体日期。"); return; }
  if(b.kind==="day" && !b.day){ alert("请为 B 选择具体日期。"); return; }
  const host = document.getElementById("cmpResult");
  host.innerHTML = skelLines([60,80,70,50]);
  // Pass specs as JSON strings so the request survives both the native bridge
  // and the dev HTTP shim (query strings can't carry nested objects).
  let r; try{ r = await apiGet("report_compare", {a:JSON.stringify(a), b:JSON.stringify(b)}); }
  catch(e){ host.innerHTML = emptyHTML("生成对比失败，请重试。"); return; }
  renderCompare(r);
});
cmpInitSelects();

// ---- on-demand full (word-level) report ----
let repGenSeq = 0;
function resetRichReport(){
  repGenSeq++;
  document.getElementById("repProgWrap").style.display = "none";
  document.getElementById("repRich").innerHTML = "";
  const btn = document.getElementById("genReportBtn");
  btn.disabled = false; btn.textContent = "⚙ 生成完整报告";
  document.getElementById("repGenHint").style.display = "";
}
document.getElementById("genReportBtn").addEventListener("click", async ()=>{
  const seq = ++repGenSeq;
  const btn = document.getElementById("genReportBtn");
  btn.disabled = true; btn.textContent = "正在生成…";
  document.getElementById("repGenHint").style.display = "none";
  const wrap = document.getElementById("repProgWrap"), bar = document.getElementById("repProg"),
        lbl = document.getElementById("repProgLabel");
  wrap.style.display = "block"; bar.style.width = "5%"; lbl.textContent = "开始生成…";
  try{ await DT.report_generate(repParams); }
  catch(e){ lbl.textContent = "启动失败：" + (e&&e.message||e); btn.disabled=false; btn.textContent="⚙ 重试"; return; }
  const poll = setInterval(async ()=>{
    if(seq !== repGenSeq){ clearInterval(poll); return; }
    let p; try{ p = await DT.report_progress(); }catch(e){ return; }
    bar.style.width = (p.pct||0) + "%";
    lbl.textContent = (p.label||"") + (p.pct?("　"+p.pct+"%"):"");
    if(p.phase === "done"){
      clearInterval(poll);
      wrap.style.display = "none";
      renderRichReport(p.result);
      btn.disabled = false; btn.textContent = "↻ 重新生成";
    } else if(p.phase === "error"){
      clearInterval(poll);
      lbl.textContent = "生成失败：" + (p.error||"未知错误");
      btn.disabled = false; btn.textContent = "⚙ 重试";
    }
  }, 400);
});
function chipsRow(items, fresh){
  if(!items || !items.length) return '<div class="empty" style="padding:8px 0">暂无</div>';
  return items.map(x=>`<span class="nw ${fresh?"fresh":""}">${escapeHtml(x.word)}<b>${x.count}</b></span>`).join("");
}
function copyIcon(done=false){
  return done
    ? `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6L9 17l-5-5"/></svg>`
    : `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
}
function repGroup(title, tag, hint, body){
  return `<div class="repGroup"><h4>${escapeHtml(title)}${tag?`<span class="tag">${escapeHtml(tag)}</span>`:""}</h4>
    ${hint?`<div class="gh">${escapeHtml(hint)}</div>`:""}${body}</div>`;
}
// 0.2.8 report redesign: a scrollable "story card" flow (hero + baseline +
// vocabulary-growth visuals) above the detailed word breakdown.
const REPORT_LAST = {today:"yesterday", week:"last_week", month:"last_month", year:"last_year"};
// Relatable "how much is that" units. Each report picks a random handful of the
// eligible ones (chars >= min), so the framing varies a little every time while
// the well-loved 微博 / A4 / 中篇 stay in the rotation.
const REPORT_UNITS = [
  {min:70,     div:70,     unit:"条短信"},
  {min:140,    div:140,    unit:"条微博"},
  {min:220,    div:220,    unit:"条朋友圈"},
  {min:350,    div:350,    unit:"首现代诗"},
  {min:500,    div:500,    unit:"页 A4 稿纸"},
  {min:900,    div:900,    unit:"封手写信"},
  {min:1500,   div:1500,   unit:"篇公众号推文"},
  {min:2000,   div:2000,   unit:"篇散文随笔"},
  {min:5000,   div:5000,   unit:"集播客逐字稿"},
  {min:8000,   div:8000,   unit:"篇短篇小说"},
  {min:20000,  div:20000,  unit:"集广播剧剧本"},
  {min:30000,  div:30000,  unit:"部中篇小说"},
  {min:60000,  div:60000,  unit:"篇学位论文"},
  {min:120000, div:120000, unit:"部长篇小说"},
];
function reportUnitsList(chars){
  const elig = REPORT_UNITS.filter(u => chars >= u.min);
  if(!elig.length) return [];
  const pick = elig.slice();
  for(let i=pick.length-1;i>0;i--){ const j=Math.floor(Math.random()*(i+1)); [pick[i],pick[j]]=[pick[j],pick[i]]; }
  return pick.slice(0, 3).sort((a,b)=>a.div-b.div).map(u=>{
    const r = chars / u.div;
    const n = r >= 10 ? Math.round(r).toLocaleString() : r.toFixed(1);
    return `${n} ${u.unit}`;
  });
}
function reportUnits(chars){ return reportUnitsList(chars).map(s=>"≈ "+s).join("　·　"); }
function repBadge(pct, polarity){
  if(pct===null||pct===undefined) return "";
  polarity = polarity||1;
  if(pct===0) return '<span class="rb flat">→ 持平</span>';
  const good = polarity>0 ? pct>0 : pct<0;
  return `<span class="rb ${good?"up":"down"}">${pct>0?"▲":"▼"} ${Math.abs(pct)}%</span>`;
}
function renderRichReport(r){
  const fast = window.__reportFast || {};
  const host = document.getElementById("repRich");
  const kw = (r.keywords||[]).slice(0,14);
  const maxW = kw.reduce((m,k)=>Math.max(m,k.weight||0), 0) || 1;
  const cloud = kw.length ? kw.map(k=>{
    const sz = (13 + (k.weight/maxW)*15).toFixed(0);
    return `<span class="t" style="font-size:${sz}px;cursor:pointer" data-w="${escapeAttr(k.word)}">${escapeHtml(k.word)}</span>`;
  }).join(" ") : '<div class="empty" style="padding:8px 0">暂无</div>';
  const posBody = (r.pos||[]).length
    ? (r.pos.slice(0,8).map(p=>`<div class="rrow"><span>${escapeHtml(p.label)}</span><b>${p.count}</b></div>`).join(""))
    : '<div class="empty" style="padding:8px 0">暂无</div>';
  const unitList = reportUnitsList(fast.chars||0);
  const heroUnits = unitList.length
    ? unitList.map(s=>`<span class="hu"><i>≈</i> ${escapeHtml(s)}</span>`).join("")
    : '<span class="hu-empty">再多写一点，就能换算成更有体感的单位。</span>';
  host.innerHTML = `<div class="repflow">
    <div class="repcard hero">
      <div class="repcard-k">这段时间，你写下了</div>
      <div class="hero-num">${(fast.chars||0).toLocaleString()}<small>字</small></div>
      ${fast.delta_pct!=null?`<div class="hero-delta">较上一周期 ${repBadge(fast.delta_pct)}</div>`:""}
      <div class="hero-units">${heroUnits}</div>
    </div>
    <div class="repcard" id="repBaseline"><div class="repcard-k">与上期对照</div><div class="empty" style="padding:6px 0">对照中…</div></div>
    <div class="repcard"><div class="repcard-k">词汇增长 <span>累计写过多少个不同词</span></div>
      <div style="position:relative;height:170px"><canvas id="repGrowth"></canvas></div></div>
    <div class="repcard"><div class="repcard-k">词长构成 <span>不同词按长度</span></div>
      <div class="repring">
        <div class="repring-pie"><canvas id="repLen"></canvas></div>
        <div class="repring-stats">
          <div class="s"><b>${(fast.distinct_chars||0).toLocaleString()}</b><small>不同汉字</small></div>
          <div class="s"><b>${(r.distinct_words||0).toLocaleString()}</b><small>不同词语</small></div>
          <div class="s"><b>${(r.new_words||[]).length}</b><small>本期新词</small></div>
        </div>
      </div></div>
    <div class="repcard" id="repTimeline"><div class="repcard-k">新词时间线 <span>第一次写下它们的日子</span></div></div>
  </div>
  <div class="repGrid">
    ${repGroup("新出现的词","NEW","这段时间第一次被你打出来的词", chipsRow(r.new_words, true))}
    ${repGroup("回归词","RETURN","以前用过、最近沉寂、这段时间又出现", chipsRow(r.returning_words, false))}
    ${repGroup("高频双字词","2字","", chipsRow(r.bigrams, false))}
    ${repGroup("高频三字词","3字","", chipsRow(r.trigrams, false))}
    ${repGroup("长词榜","4字+","四字及以上的长词", chipsRow(r.long_words, false))}
    ${repGroup("词性分布","POS","", posBody)}
    ${repGroup("主题关键词","TF-IDF","越大越能代表这段时间的主题", '<div class="cloud">'+cloud+'</div>')}
  </div>`;
  buildGrowthChart(r.vocab_growth || []);
  buildLenChart(r.length_dist || {});
  renderNewWordTimeline(r.new_word_timeline || []);
  fetchReportBaseline();
  host.querySelectorAll(".t, .lk").forEach(t=>t.addEventListener("click", ()=>gotoSearch(t.dataset.w)));
}
function buildGrowthChart(g){
  const cv = document.getElementById("repGrowth"); if(!cv) return;
  if(charts.repGrowth){ charts.repGrowth.destroy(); charts.repGrowth=null; }
  if(g.length < 2){ cv.parentElement.innerHTML = emptyHTML("这段时间数据太少，画不出增长曲线。"); return; }
  charts.repGrowth = new Chart(cv, {
    type:"line",
    data:{labels:g.map(x=>x.date.slice(5)),
      datasets:[{data:g.map(x=>x.cumulative),borderColor:cssVar('--accent'),
        backgroundColor:"rgba(255,206,51,.14)",fill:true,tension:.3,
        pointRadius:0,pointHoverRadius:4,pointHitRadius:18,borderWidth:2}]},
    options:{maintainAspectRatio:false,interaction:{mode:"index",intersect:false},
      plugins:{legend:{display:false},tooltip:{displayColors:false,
        callbacks:{label:c=>` 累计 ${c.raw} 个不同词`}}},
      scales:{x:{grid:{display:false},ticks:{maxTicksLimit:8,maxRotation:0}},
              y:{beginAtZero:true,ticks:{precision:0}}}}
  });
}
function buildLenChart(ld){
  const cv = document.getElementById("repLen"); if(!cv) return;
  if(charts.repLen){ charts.repLen.destroy(); charts.repLen=null; }
  const data = [ld.two||0, ld.three||0, ld.four_plus||0];
  if(!data.some(x=>x)){ cv.parentElement.innerHTML = emptyHTML("暂无可统计的词。"); return; }
  charts.repLen = new Chart(cv, {
    type:"doughnut",
    data:{labels:["双字词","三字词","四字及以上"],datasets:[donutDataset(data, 3)]},
    options:{plugins:{legend:{position:"bottom",labels:{boxWidth:10,padding:8,font:{size:11}},...legendHoverHighlight()},
      tooltip:{displayColors:false,callbacks:{label:c=>`${c.label}: ${c.raw} 个`}}},cutout:"60%"}
  });
}
function renderNewWordTimeline(tl){
  const el = document.getElementById("repTimeline");
  if(!tl.length){ el.innerHTML += emptyHTML("这段时间没有全新的词——都是熟面孔。"); return; }
  const rows = tl.slice(-14).reverse().map(d=>
    `<div class="tl-row"><span class="tl-date">${d.date.slice(5)}</span>`+
    `<span class="tl-words">${d.words.map(w=>`<span class="chip lk" data-w="${escapeAttr(w)}">${escapeHtml(w)}</span>`).join("")}`+
    `${d.count>d.words.length?`<span class="tl-more">+${d.count-d.words.length}</span>`:""}</span></div>`).join("");
  el.innerHTML += `<div class="tl">${rows}</div>`;
}
async function fetchReportBaseline(){
  const el = document.getElementById("repBaseline"); if(!el) return;
  const period = repParams.period, last = REPORT_LAST[period];
  if(!last){ el.style.display = "none"; return; }   // custom range has no clean baseline
  let r; try{ r = await apiGet("report_compare", {a:JSON.stringify({kind:period}), b:JSON.stringify({kind:last})}); }
  catch(e){ el.style.display = "none"; return; }
  const a = r.a, d = r.deltas;
  const rows = [
    ["上屏汉字", a.chars.toLocaleString(), d.chars, 1],
    ["不同汉字", a.distinct_chars.toLocaleString(), d.distinct_chars, 1],
    ["活跃时长", a.active_minutes+" 分", d.active_minutes, 1],
    ["平均速度", a.cpm+" 字/分", d.cpm, 1],
  ];
  el.innerHTML = `<div class="repcard-k">与上期对照 <span>${escapeHtml(a.label)} vs ${escapeHtml(r.b.label)}</span></div>`+
    `<div class="repbase">`+rows.map(([l,v,dl,pol])=>
      `<div class="bs"><small>${l}</small><b>${v}</b>${repBadge(dl,pol)}</div>`).join("")+`</div>`;
}

// ---- sequence ----
// The sequence follows the shared top range like every other view -- all
// date/day picking lives in the top bar. The only local controls are which
// app(s) to show (multi-select chips; none selected = all) and a keyword that
// keeps only runs containing it. "重置筛选" clears both.

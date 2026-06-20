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
    cardDl.onclick = (e)=>{ e.preventDefault(); DT.save_card(period, "card"); };
    const cardDlLong = document.getElementById("cardDlLong");
    cardDlLong.removeAttribute("href");
    cardDlLong.onclick = (e)=>{ e.preventDefault(); DT.save_card(period, "long"); };
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
  const rows = lx.map(it=>`<div class="lexrep-row">
      <span class="lexrep-name">${escapeHtml(it.name)}</span>
      <span class="lexrep-bar"><i style="width:${pct(it.total, top.total)}%"></i></span>
      <span class="lexrep-num">${it.total} 次</span>
      <span class="lexrep-top">最多 <b class="lk" data-w="${escapeAttr(it.top_word)}">${escapeHtml(it.top_word)}</b>·${it.top_count}</span>
    </div>`).join("");
  host.innerHTML = `<div class="lexrep-lead">${lead}</div>${rows}`;
  host.querySelectorAll(".lk").forEach(el=>el.addEventListener("click", ()=>gotoSearch(el.dataset.w)));
}

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
function renderRichReport(r){
  const fast = window.__reportFast || {};
  const kw = (r.keywords||[]).slice(0,14);
  const maxW = kw.reduce((m,k)=>Math.max(m,k.weight||0), 0) || 1;
  const cloud = kw.length ? kw.map(k=>{
    const sz = (13 + (k.weight/maxW)*15).toFixed(0);
    return `<span class="t" style="font-size:${sz}px;cursor:pointer" data-w="${escapeAttr(k.word)}">${escapeHtml(k.word)}</span>`;
  }).join(" ") : '<div class="empty" style="padding:8px 0">暂无</div>';
  const posBody = (r.pos||[]).length
    ? (r.pos.slice(0,8).map(p=>`<div class="rrow"><span>${escapeHtml(p.label)}</span><b>${p.count}</b></div>`).join(""))
    : '<div class="empty" style="padding:8px 0">暂无</div>';
  const stats = `<div class="repStats">
      <div class="s"><b>${(fast.distinct_chars||0).toLocaleString()}</b><small>不同汉字</small></div>
      <div class="s"><b>${(r.distinct_words||0).toLocaleString()}</b><small>不同词语</small></div>
      <div class="s"><b>${(r.new_words||[]).length}</b><small>本期新词</small></div>
    </div>`;
  document.getElementById("repRich").innerHTML = stats + `<div class="repGrid">
    ${repGroup("新出现的词","NEW","这段时间第一次被你打出来的词", chipsRow(r.new_words, true))}
    ${repGroup("回归词","RETURN","以前用过、最近沉寂、这段时间又出现", chipsRow(r.returning_words, false))}
    ${repGroup("高频双字词","2字","", chipsRow(r.bigrams, false))}
    ${repGroup("高频三字词","3字","", chipsRow(r.trigrams, false))}
    ${repGroup("长词榜","4字+","四字及以上的长词", chipsRow(r.long_words, false))}
    ${repGroup("词性分布","POS","", posBody)}
    ${repGroup("主题关键词","TF-IDF","越大越能代表这段时间的主题", '<div class="cloud">'+cloud+'</div>')}
  </div>`;
  document.querySelectorAll("#repRich .t").forEach(t=>t.addEventListener("click", ()=>gotoSearch(t.dataset.w)));
}

// ---- sequence ----
// The sequence follows the shared top range like every other view -- all
// date/day picking lives in the top bar. The only local controls are which
// app(s) to show (multi-select chips; none selected = all) and a keyword that
// keeps only runs containing it. "重置筛选" clears both.

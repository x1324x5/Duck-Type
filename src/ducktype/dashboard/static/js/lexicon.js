let lexEditMode = "paste";
const lexCharts = {};
function lexMsg(text, ok){
  const el = document.getElementById("lexMsg");
  el.textContent = text || "";
  el.className = "lexmsg" + (ok===false ? " errmsg" : (ok===true ? " okmsg" : ""));
}
function lexDestroyCharts(){
  Object.values(lexCharts).forEach(c=>{ try{ c.destroy(); }catch(e){} });
  for(const k in lexCharts) delete lexCharts[k];
}
async function loadLexicon(){
  try{
    const cfg = await DT.config_get();
    window.__lexRecompute = cfg.lexicon_recompute_on_exclude !== false;
    window.__commonExtra = (cfg.common_chars_extra || []).join(" ");
  }catch(e){ window.__lexRecompute = true; }
  await loadLexiconList();
  await loadLexiconStats();
}
async function loadLexiconList(){
  const res = await DT.lexicon_list();
  const items = (res && res.items) || [];
  window.__lexItems = items;
  const el = document.getElementById("lexList");
  el.innerHTML = items.map(it=>{
    const cnt = (it.count||0).toLocaleString();
    const view = `<button class="btn lexview" data-id="${escapeAttr(it.id)}" data-name="${escapeAttr(it.name)}">查看</button>`;
    const right = it.builtin
      ? '<span class="lexlock" title="内置词库不可删除">🔒</span>'
      : `<button class="btn lexdel" data-id="${escapeAttr(it.id)}">删除</button>`;
    return `<div class="lexrow${it.enabled?"":" off"}">
      <span class="switch"><input type="checkbox" class="lexen" data-id="${escapeAttr(it.id)}" ${it.enabled?"checked":""}></span>
      <div class="lexmeta"><div class="lexname">${escapeHtml(it.name)}${it.builtin?'<span class="lextag">内置</span>':""}</div>
        <div class="lexsub">${cnt} 个词</div></div>
      ${view}${right}</div>`;
  }).join("");
}
async function loadLexiconStats(){
  const items = (window.__lexItems) || [];
  const enabled = items.filter(it=>it.enabled);
  const host = document.getElementById("lexStats");
  lexDestroyCharts();
  window.__lexState = {};
  if(!enabled.length){
    host.innerHTML = '<div class="empty">没有启用的词库。勾选上面任意词库，这里就会显示它的词语占比。</div>';
    return;
  }
  host.innerHTML = enabled.map(it=>{
    const a = escapeAttr(it.id);
    return `<div class="lexstat" id="lexstat_${a}">
      <div class="lexstat-head">
        <h3>${escapeHtml(it.name)}</h3>
        <span class="lexstat-ctl" id="lexctl_${a}" hidden>
          <span class="rankctl"><input type="range" class="lexN" data-id="${a}" min="5" max="30" step="1" value="${LEX_TOP_DEFAULT}"><b class="lexNv">${LEX_TOP_DEFAULT}</b></span>
          <label class="lexother"><input type="checkbox" class="lexOther" data-id="${a}" checked> 显示其他</label>
          <button class="dlbtn" data-dl="lexchart_${a}" data-name="lex_${a}" title="保存为图片">⬇</button>
        </span>
      </div>
      <div class="lexstat-body" id="lexbody_${a}">
        <div class="lexchartwrap"><canvas id="lexchart_${a}"></canvas></div>
        <div class="lexlegend" id="lexlegend_${a}"></div>
      </div>
      <div class="lexstat-info" id="lexinfo_${a}">统计中…</div>
    </div>`;
  }).join("");
  for(const it of enabled){ await loadOneLexStat(it).catch(()=>{}); }
}
const LEX_TOP_DEFAULT = 10;
async function loadOneLexStat(it){
  const r = await DT.lexicon_stats(it.id, 50);
  const stat = document.getElementById("lexstat_"+it.id);
  const body = document.getElementById("lexbody_"+it.id);
  const ctl = document.getElementById("lexctl_"+it.id);
  const info = document.getElementById("lexinfo_"+it.id);
  if(!body) return;
  const words = (r && r.words) || [];
  if(!r || !r.total || !words.length){
    if(stat) stat.classList.add("empty-stat");
    if(ctl) ctl.hidden = true;
    body.innerHTML = `<div class="lexempty">这段时间没有匹配到「${escapeHtml(it.name)}」里的词。换个时间范围试试。</div>`;
    if(info) info.textContent = "";
    return;
  }
  if(stat) stat.classList.remove("empty-stat");
  if(ctl) ctl.hidden = false;
  window.__lexState[it.id] = {id:it.id, name:it.name, words, total:r.total,
    distinct:r.distinct, size:r.size||0, n:LEX_TOP_DEFAULT, showOther:true};
  buildLexChart(it.id);
  if(info) info.innerHTML = `共匹配 <b>${r.total.toLocaleString()}</b> 次 · 覆盖 <b>${r.distinct.toLocaleString()}</b> 个词（词库 ${(r.size||0).toLocaleString()} 词）`;
}
// Slices = the top-N matched words (+ an optional 其他 bucket for the rest).
function lexSlices(st){
  const top = st.words.slice(0, st.n);
  const slices = top.map(w=>({word:w.word, count:w.count}));
  if(st.showOther){
    const other = st.total - top.reduce((s,w)=>s+w.count, 0);
    if(other > 0) slices.push({word:null, count:other});
  }
  return slices;
}
function buildLexChart(id){
  const st = window.__lexState[id];
  const cv = document.getElementById("lexchart_"+id);
  if(!st || !cv) return;
  const slices = lexSlices(st);
  if(lexCharts[id]){ try{ lexCharts[id].destroy(); }catch(e){} }
  lexCharts[id] = new Chart(cv, {
    type:"doughnut",
    data:{labels:slices.map(s=> s.word!=null ? s.word : "其他"),
      datasets:[donutDataset(slices.map(s=>s.count), slices.length)]},
    options:{
      onClick:(_e, els)=>{ if(els.length){ const s=slices[els[0].index]; if(s.word!=null) gotoSearch(s.word); } },
      onHover:(e, els)=>{ e.native.target.style.cursor = (els.length && slices[els[0].index].word!=null) ? "pointer" : "default"; },
      plugins:{legend:{display:false}, tooltip:lexShareTooltip(st.total)},
      cutout:"58%"}
  });
  lexCharts[id].__slices = slices;
  renderLexLegend(id);
}
// Custom aligned legend (swatch · word · count · %). Avoids Chart.js right-legend
// truncation, keeps the % column visible regardless of word length, and supports
// hover-highlight + click-to-hide (item 6). % uses the visible-slice denominator
// when "排除后重算" is on, so the shares always sum to 100%.
function renderLexLegend(id){
  const st = window.__lexState[id], chart = lexCharts[id];
  const host = document.getElementById("lexlegend_"+id);
  if(!st || !chart || !host) return;
  const slices = chart.__slices, ds = chart.data.datasets[0];
  const denom = lexVisibleDenom(chart, st.total);
  host.innerHTML = slices.map((s, i)=>{
    const hidden = !chart.getDataVisibility(i);
    const name = s.word!=null ? s.word : "其他";
    const tail = hidden ? "隐藏" : (pct2(s.count, denom) + "%");
    return `<div class="ll-row${hidden?" off":""}${s.word!=null?" link":""}" data-idx="${i}">
      <span class="ll-swatch" style="background:${ds.backgroundColor[i]}"></span>
      <span class="ll-word" title="${escapeAttr(name)}">${escapeHtml(name)}</span>
      <span class="ll-cnt">${s.count.toLocaleString()}次</span>
      <span class="ll-pct">${tail}</span>
    </div>`;
  }).join("");
}
function _lexIdFromLegend(el){
  const host = el.closest(".lexlegend");
  return host ? host.id.replace("lexlegend_", "") : null;
}
// editor
function lexShowEditor(show){
  document.getElementById("lexEditor").style.display = show ? "block" : "none";
  if(show){
    document.getElementById("lexName").value = "";
    document.getElementById("lexBody").value = "";
    document.getElementById("lexEditMsg").textContent = "";
    lexSetMode("paste");
    document.getElementById("lexName").focus();
  }
}
function lexSetMode(mode){
  lexEditMode = mode;
  document.querySelectorAll("#lexEditor .segbtn").forEach(b=>b.classList.toggle("active", b.dataset.mode===mode));
  const body = document.getElementById("lexBody");
  const hint = document.getElementById("lexBodyHint");
  if(mode==="items"){
    body.placeholder = "每行一个词，例如：\n海阔天空\n脚踏实地";
    hint.textContent = "每行算一个词（适合逐条录入，词里可含空格）。";
  } else {
    body.placeholder = "用空格、逗号或换行分隔多个词，例如：\n海阔天空 脚踏实地 行云流水";
    hint.textContent = "用空格 / 逗号 / 换行分隔，每个片段算一个词。";
  }
}
document.getElementById("lexNewBtn").addEventListener("click", ()=> lexShowEditor(true));
document.getElementById("lexCancelBtn").addEventListener("click", ()=> lexShowEditor(false));
document.querySelectorAll("#lexEditor .segbtn").forEach(b=>{
  b.addEventListener("click", ()=> lexSetMode(b.dataset.mode));
});
document.getElementById("lexSaveBtn").addEventListener("click", async ()=>{
  const name = document.getElementById("lexName").value.trim();
  const body = document.getElementById("lexBody").value;
  if(!body.trim()){ document.getElementById("lexEditMsg").textContent = "请先填入词语。"; return; }
  const payload = lexEditMode==="items" ? {name, words: body} : {name, text: body};
  document.getElementById("lexEditMsg").textContent = "保存中…";
  let r; try{ r = await DT.lexicon_create(payload); }catch(e){ r = {ok:false, error:"保存失败"}; }
  if(r && r.ok){ lexShowEditor(false); lexMsg("词库已保存。", true); await loadLexicon(); }
  else document.getElementById("lexEditMsg").textContent = "保存失败：" + ((r&&r.error)||"未知错误");
});
document.getElementById("lexUploadBtn").addEventListener("click", ()=>{
  if(isNative()) runLexImport(null);
  else document.getElementById("lexUploadFile").click();
});
document.getElementById("lexUploadFile").addEventListener("change", e=>{
  const file = e.target.files && e.target.files[0];
  if(file) runLexImport(file);
  e.target.value = "";
});
async function runLexImport(file){
  lexMsg("正在导入词库…");
  let r; try{ r = await DT.lexicon_import(file); }catch(e){ r = {ok:false, error:"导入失败"}; }
  if(r && r.cancelled){ lexMsg(""); return; }
  if(r && r.ok){ lexMsg(`已导入词库（${(r.count||0).toLocaleString()} 个词）。`, true); await loadLexicon(); }
  else lexMsg("导入失败：" + ((r&&r.error)||"未知错误"), false);
}
document.getElementById("lexList").addEventListener("click", async e=>{
  const del = e.target.closest(".lexdel");
  if(del){
    if(!confirm("删除这个词库？该操作不可恢复（不影响你的统计数据）。")) return;
    let r; try{ r = await DT.lexicon_delete(del.dataset.id); }catch(err){ r = {ok:false, error:"删除失败"}; }
    if(r && r.ok){ lexMsg("词库已删除。", true); await loadLexicon(); }
    else lexMsg("删除失败：" + ((r&&r.error)||"未知错误"), false);
  }
});
document.getElementById("lexList").addEventListener("change", async e=>{
  const cb = e.target.closest(".lexen");
  if(!cb) return;
  try{ await DT.lexicon_update({id: cb.dataset.id, enabled: cb.checked}); }catch(err){}
  // reflect enable/disable in the pies without a full list refetch
  const item = (window.__lexItems||[]).find(x=>x.id===cb.dataset.id);
  if(item) item.enabled = cb.checked;
  cb.closest(".lexrow").classList.toggle("off", !cb.checked);
  await loadLexiconStats();
});

// ---- health banner + rotating ticker ----
// Pool = code-generated data facts (from /api/ticker) + rotating phrases.
// Each auto/manual advance draws a random line from the current pool.

// ---- lexicon share-pie percentages (item 3) ----
// Denominator for share %: the whole total normally, or the sum of currently
// *visible* slices when "排除后重算" is on (so hiding a slice rebases the rest).
function lexVisibleDenom(chart, total){
  if(!window.__lexRecompute) return total;
  const data = chart.data.datasets[0].data;
  let s = 0;
  for(let i=0;i<data.length;i++){ if(chart.getDataVisibility(i)) s += (+data[i]||0); }
  return s || total;
}
function lexShareTooltip(total){
  return {displayColors:false, callbacks:{label:(ctx)=>{
    const v = ctx.raw||0;
    return `${ctx.label}: ${v} 次 · ${pct2(v, lexVisibleDenom(ctx.chart, total))}%`;
  }}};
}
// ---- custom-legend interactions (hover highlight / click hide / controls) ----
const _lexStatsEl = document.getElementById("lexStats");
_lexStatsEl.addEventListener("click", e=>{
  const row = e.target.closest(".ll-row"); if(!row) return;
  const id = _lexIdFromLegend(row), chart = lexCharts[id];
  if(!chart) return;
  chart.toggleDataVisibility(+row.dataset.idx);   // hide / show this slice
  chart.update();
  renderLexLegend(id);                            // refresh shares + struck-out style
});
_lexStatsEl.addEventListener("mouseover", e=>{
  const row = e.target.closest(".ll-row"); if(!row) return;
  const chart = lexCharts[_lexIdFromLegend(row)]; if(!chart) return;
  const idx = +row.dataset.idx, meta = chart.getDatasetMeta(0);
  if(!meta || !meta.data || !meta.data[idx]) return;
  try{ chart.setActiveElements([{datasetIndex:0, index:idx}]); chart.update(); }catch(e2){}
});
_lexStatsEl.addEventListener("mouseout", e=>{
  const row = e.target.closest(".ll-row"); if(!row) return;
  const chart = lexCharts[_lexIdFromLegend(row)]; if(!chart) return;
  try{ chart.setActiveElements([]); chart.update(); }catch(e2){}
});
_lexStatsEl.addEventListener("input", e=>{
  const sl = e.target.closest(".lexN"); if(!sl) return;
  const st = window.__lexState[sl.dataset.id]; if(!st) return;
  st.n = +sl.value;
  const v = sl.parentElement.querySelector(".lexNv"); if(v) v.textContent = st.n;
  clearTimeout(st._t); st._t = setTimeout(()=>buildLexChart(st.id), 200);
});
_lexStatsEl.addEventListener("change", e=>{
  const cb = e.target.closest(".lexOther"); if(!cb) return;
  const st = window.__lexState[cb.dataset.id]; if(!st) return;
  st.showOther = cb.checked; buildLexChart(st.id);
});

// ---- lexicon 查看/编辑 modal (item 2) ----
const LEX_PAGE = 120;
let lexModalState = null;
let lexModalSearchTimer = null;
function _lm(id){ return document.getElementById(id); }
async function lexModalOpen(id, name){
  lexModalState = {id, name, q:"", offset:0, limit:LEX_PAGE, editable:false, common:(id==="common")};
  _lm("lexModalTitle").textContent = name || "词库";
  _lm("lexModalSearch").value = "";
  _lm("lexModalAdd").value = "";
  _lm("lexModalMsg").textContent = "";
  _lm("lexModalExtra").hidden = true;
  _lm("lexModalEdit").hidden = true;
  _lm("lexModal").hidden = false;
  await lexModalFetch();
  setTimeout(()=>{ try{ _lm("lexModalSearch").focus(); }catch(e){} }, 30);
}
function lexModalClose(){
  const ov = _lm("lexModal");
  if(ov) ov.hidden = true;
  lexModalState = null;
}
async function lexModalFetch(){
  if(!lexModalState) return;
  const s = lexModalState, host = _lm("lexModalWords");
  host.innerHTML = '<div class="empty">加载中…</div>';
  let r; try{ r = await DT.lexicon_words({id:s.id, q:s.q, offset:s.offset, limit:s.limit}); }
  catch(e){ host.innerHTML = '<div class="empty">读取失败。</div>'; return; }
  if(!r || !r.found){ host.innerHTML = '<div class="empty">找不到这个词库。</div>'; return; }
  s.editable = !!r.editable;
  lexModalRender(r);
}
function lexModalRender(r){
  const s = lexModalState, host = _lm("lexModalWords"), words = r.words||[];
  host.innerHTML = words.length
    ? words.map(w=>`<span class="mw">${escapeHtml(w)}${s.editable?`<button class="x" data-w="${escapeAttr(w)}" title="移除">×</button>`:""}</span>`).join("")
    : `<div class="empty">${s.q?"没有匹配的词条。":"这个词库还没有词。"}</div>`;
  const sub = `共 <b>${(r.size||r.total||0).toLocaleString()}</b> 个词` +
    (s.q?`，匹配 <b>${(r.total||0).toLocaleString()}</b> 个`:"") +
    (s.editable?"　·　用户词库，可增删"
      :(s.common?"　·　内置常用字表（只读），可在下方维护「额外常用字」":"　·　内置词库，只读"));
  _lm("lexModalSub").innerHTML = sub;
  const pager = _lm("lexModalPager");
  const start = r.offset||0, shown = words.length, total = r.total||0;
  if(total <= shown && start === 0){ pager.innerHTML = ""; }
  else pager.innerHTML = `<button id="lexPrev" ${start>0?"":"disabled"}>上一页</button>`+
    `<span>${total?start+1:0}–${start+shown} / ${total}</span>`+
    `<button id="lexNext" ${start+shown<total?"":"disabled"}>下一页</button>`;
  _lm("lexModalEdit").hidden = !s.editable;
  _lm("lexModalExtra").hidden = !s.common;
  if(s.common){ _lm("lexExtraInput").value = window.__commonExtra||""; _lm("lexExtraMsg").textContent = ""; }
}
async function lexModalAfterEdit(){
  lexModalState.offset = 0;
  await lexModalFetch();
  await loadLexiconList();
  await loadLexiconStats();
}
_lm("lexModalClose").addEventListener("click", lexModalClose);
_lm("lexModal").addEventListener("click", e=>{ if(e.target.id==="lexModal") lexModalClose(); });
document.addEventListener("keydown", e=>{ if(e.key==="Escape" && lexModalState) lexModalClose(); });
_lm("lexModalSearch").addEventListener("input", e=>{
  if(!lexModalState) return;
  clearTimeout(lexModalSearchTimer);
  const v = e.target.value.trim();
  lexModalSearchTimer = setTimeout(()=>{ lexModalState.q = v; lexModalState.offset = 0; lexModalFetch(); }, 220);
});
_lm("lexModalPager").addEventListener("click", e=>{
  if(!lexModalState) return;
  if(e.target.id==="lexPrev"){ lexModalState.offset = Math.max(0, lexModalState.offset - lexModalState.limit); lexModalFetch(); }
  else if(e.target.id==="lexNext"){ lexModalState.offset += lexModalState.limit; lexModalFetch(); }
});
_lm("lexModalWords").addEventListener("click", async e=>{
  const x = e.target.closest(".x");
  if(!x || !lexModalState || !lexModalState.editable) return;
  x.disabled = true;
  let r; try{ r = await DT.lexicon_edit_words({id:lexModalState.id, remove:[x.dataset.w]}); }catch(err){ r = {ok:false}; }
  if(r && r.ok) await lexModalAfterEdit();
});
_lm("lexModalAddBtn").addEventListener("click", async ()=>{
  if(!lexModalState || !lexModalState.editable) return;
  const txt = _lm("lexModalAdd").value;
  if(!txt.trim()) return;
  const msg = _lm("lexModalMsg"); msg.textContent = "添加中…"; msg.className = "lexmsg";
  let r; try{ r = await DT.lexicon_edit_words({id:lexModalState.id, add:txt}); }catch(e){ r = {ok:false, error:"失败"}; }
  if(r && r.ok){ _lm("lexModalAdd").value = ""; msg.textContent = `已更新，共 ${r.count} 个词。`; msg.className = "lexmsg okmsg"; await lexModalAfterEdit(); }
  else { msg.textContent = "失败：" + ((r&&r.error)||"未知错误"); msg.className = "lexmsg errmsg"; }
});
_lm("lexExtraSave").addEventListener("click", async ()=>{
  const msg = _lm("lexExtraMsg"); msg.textContent = "保存中…"; msg.className = "lexmsg";
  let r; try{ r = await DT.config_set({common_chars_extra: _lm("lexExtraInput").value}); }catch(e){ r = {ok:false}; }
  if(r && r.ok !== false){
    try{ const cfg = await DT.config_get(); window.__commonExtra = (cfg.common_chars_extra||[]).join(" "); _lm("lexExtraInput").value = window.__commonExtra; }catch(e){}
    msg.textContent = "已保存。这些字不再算作生僻字。"; msg.className = "lexmsg okmsg";
    if(lexModalState && lexModalState.common){ lexModalState.offset = 0; await lexModalFetch(); }
    await loadLexiconList(); await loadLexiconStats();
  } else { msg.textContent = "保存失败。"; msg.className = "lexmsg errmsg"; }
});
document.getElementById("lexList").addEventListener("click", e=>{
  const v = e.target.closest(".lexview");
  if(v) lexModalOpen(v.dataset.id, v.dataset.name);
});

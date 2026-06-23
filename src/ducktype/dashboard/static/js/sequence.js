let seqFilterApps = new Set();
let seqKeyword = "";
function seqResetFilters(){
  seqFilterApps = new Set();
  seqKeyword = "";
  const kw = document.getElementById("seqKeyword"); if(kw) kw.value = "";
}
function sequenceParams(extra){
  const p = rangeParams(extra || {});
  if(seqFilterApps.size) p.apps = Array.from(seqFilterApps).join(",");
  if(seqKeyword) p.q = seqKeyword;
  return p;
}
async function loadSequenceApps(){
  const apps = await apiGet("sequence_apps", rangeParams());
  const wrap = document.getElementById("seqApps");
  const present = new Set((apps||[]).map(a=>a.app||""));
  // drop any selected app that no longer appears in this range
  seqFilterApps = new Set(Array.from(seqFilterApps).filter(a=>present.has(a)));
  if(!apps || !apps.length){ wrap.innerHTML = '<span class="seqapps-empty">暂无应用</span>'; return; }
  wrap.innerHTML = apps.map(a=>{
    const name = a.app || "";
    const on = seqFilterApps.has(name);
    const label = (name || "(unknown)") + " · " + a.count;
    return `<button class="appchip${on?" on":""}" data-app="${escapeAttr(name)}">${escapeHtml(label)}</button>`;
  }).join("");
}
// Fetch a generous window once, then reveal it in pages so the DOM never holds
// thousands of nodes at once (the old hard 300 cap silently dropped the rest;
// rendering *all* of them tanked scrolling). __seqShown tracks how many of the
// in-memory runs are currently on screen; 「加载更多」 just reveals the next page.
const SEQ_FETCH = 4000;   // max runs pulled from the backend in one go
const SEQ_PAGE = 80;      // runs revealed per page
let __seqShown = 0;
function seqRunHTML(r, i){
  const t = r.ts ? new Date(r.ts*1000).toLocaleString() : "";
  const text = seqHighlight(r.text || "", seqKeyword);
  return `<div class="run"><div class="meta"><span>${t}</span><span>${escapeHtml(r.app||"")}</span>`+
    `<button class="copyrun" data-i="${i}" title="复制这一段" aria-label="复制这一段">${copyIcon()}</button></div>`+
    `<div class="text">${text}</div></div>`;
}
// Bold every occurrence of the active keyword inside an (escaped) run.
function seqHighlight(text, kw){
  const esc = escapeHtml(text);
  if(!kw) return esc;
  const k = escapeHtml(kw);
  try{
    const re = new RegExp(k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g");
    return esc.replace(re, m=>`<mark class="seqhit">${m}</mark>`);
  }catch(e){ return esc; }
}
function seqRenderMore(){
  const runs = window.__seqRuns || [];
  const el = document.getElementById("seq");
  const next = runs.slice(__seqShown, __seqShown + SEQ_PAGE);
  const frag = next.map((r, j)=>seqRunHTML(r, __seqShown + j)).join("");
  // first page replaces the skeleton/empty; later pages append before the pager
  const pager = document.getElementById("seqPager");
  if(__seqShown === 0) el.innerHTML = frag; else el.insertAdjacentHTML("beforeend", frag);
  __seqShown += next.length;
  if(pager) pager.remove();
  if(__seqShown < runs.length){
    el.insertAdjacentHTML("beforeend",
      `<div class="seqpager" id="seqPager"><button class="btn" id="seqMore">`+
      `加载更多（剩 ${(runs.length-__seqShown).toLocaleString()} 段）</button></div>`);
  }
}
async function loadSequence(){
  const params = sequenceParams({limit:SEQ_FETCH});
  const [runs] = await Promise.all([
    apiGet("sequence", params),
    loadSequenceApps().catch(()=>{})
  ]);
  const el = document.getElementById("seq");
  const filters = [];
  if(seqFilterApps.size) filters.push(seqFilterApps.size + " 个应用");
  if(seqKeyword) filters.push("「" + seqKeyword + "」");
  const capped = runs.length >= SEQ_FETCH ? "+" : "";
  document.getElementById("seqStat").textContent =
    (filters.length ? filters.join(" · ") + " · " : "") + `${runs.length.toLocaleString()}${capped} 段`;
  window.__seqRuns = runs;
  __seqShown = 0;
  if(!runs.length){ el.innerHTML = "<div class='empty'>没有符合筛选条件的序列（不勾选应用＝全部，留空关键词＝不过滤）。</div>"; }
  else seqRenderMore();
  ["txt","csv","json"].forEach(f=>{
    const a = document.getElementById("exp-"+f);
    a.removeAttribute("href");
    a.onclick = (e)=>{ e.preventDefault(); DT.export_sequence(f, sequenceParams()); };
  });
}
document.getElementById("seq").addEventListener("click", e=>{
  if(e.target.closest("#seqMore")) seqRenderMore();
});
document.getElementById("seqApps").addEventListener("click", e=>{
  const btn = e.target.closest(".appchip");
  if(!btn) return;
  const name = btn.dataset.app || "";
  if(seqFilterApps.has(name)) seqFilterApps.delete(name); else seqFilterApps.add(name);
  btn.classList.toggle("on");
  loadSequence();
});
document.getElementById("seqKeyword").addEventListener("input", e=>{
  seqKeyword = e.target.value.trim();
  clearTimeout(window.__seqKwTimer);
  window.__seqKwTimer = setTimeout(loadSequence, 300);
});
document.getElementById("seqReset").addEventListener("click", ()=>{
  seqResetFilters();
  loadSequence();
});
document.getElementById("seq").addEventListener("click", async e=>{
  const btn = e.target.closest(".copyrun");
  if(!btn) return;
  const r = (window.__seqRuns || [])[Number(btn.dataset.i)];
  if(!r) return;
  try{
    await navigator.clipboard.writeText(r.text || "");
    btn.innerHTML = copyIcon(true);
    setTimeout(()=>btn.innerHTML=copyIcon(false), 1200);
  }catch(err){
    const ta = document.createElement("textarea");
    ta.value = r.text || ""; document.body.appendChild(ta); ta.select();
    try{ document.execCommand("copy"); btn.innerHTML = copyIcon(true); }
    catch(e2){ btn.title = "复制失败"; }
    ta.remove(); setTimeout(()=>{ btn.innerHTML=copyIcon(false); btn.title="复制这一段"; }, 1200);
  }
});

// ---- fun ----
function chips(items, fmt){ return items && items.length ? items.map(fmt).join("") : "<div class='empty'>暂无</div>"; }
async function loadFun(){
  if(typeof loadRecords === "function") loadRecords();   // 记录珍藏 lives in this view now
  const [g, f] = await Promise.all([apiGet("gamify", {}), apiGet("fun", rangeParams())]);
  renderGamify(g);
  // Every chip is clickable: tapping its box jumps to the search view for that word/char.
  const wc = w=>`<span class="chip link" data-w="${escapeAttr(w.word)}" title="搜索「${escapeAttr(w.word)}」">${escapeHtml(w.word)}<b>${w.count}</b></span>`;
  document.getElementById("longWords").innerHTML = chips(f.long_words, wc);
  document.getElementById("favWords").innerHTML = chips(f.favorite_words, wc);
  document.getElementById("rareChars").innerHTML = chips(f.rare_chars, c=>`<span class="chip link" data-w="${escapeAttr(c.ch)}" title="搜索「${escapeAttr(c.ch)}」">${escapeHtml(c.ch)}<b>${c.count}</b></span>`);
  document.getElementById("hapax").innerHTML = chips(f.hapax, c=>`<span class="chip link" data-w="${escapeAttr(c)}" title="搜索「${escapeAttr(c)}」">${escapeHtml(c)}</span>`);
}
// Delegated: click any fun-page chip to search its word/char in the 搜索 view.
document.getElementById("view-fun").addEventListener("click", e=>{
  const chip = e.target.closest(".chip.link"); if(!chip) return;
  const w = chip.dataset.w; if(w) gotoSearch(w);
});

// ---- lexicon (词库) ----

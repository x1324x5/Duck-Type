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
async function loadSequence(){
  const params = sequenceParams({limit:300});
  const [runs] = await Promise.all([
    apiGet("sequence", params),
    loadSequenceApps().catch(()=>{})
  ]);
  const el = document.getElementById("seq");
  const filters = [];
  if(seqFilterApps.size) filters.push(seqFilterApps.size + " 个应用");
  if(seqKeyword) filters.push("「" + seqKeyword + "」");
  document.getElementById("seqStat").textContent =
    (filters.length ? filters.join(" · ") + " · " : "") + `${runs.length} 段`;
  if(!runs.length){ el.innerHTML = "<div class='empty'>没有符合筛选条件的序列（不勾选应用＝全部，留空关键词＝不过滤）。</div>"; }
  else el.innerHTML = runs.map((r, i)=>{
    const t = r.ts ? new Date(r.ts*1000).toLocaleString() : "";
    return `<div class="run"><div class="meta"><span>${t}</span><span>${escapeHtml(r.app||"")}</span>`+
      `<button class="copyrun" data-i="${i}" title="复制这一段" aria-label="复制这一段">${copyIcon()}</button></div>`+
      `<div class="text">${escapeHtml(r.text)}</div></div>`;
  }).join("");
  window.__seqRuns = runs;
  ["txt","csv","json"].forEach(f=>{
    const a = document.getElementById("exp-"+f);
    a.removeAttribute("href");
    a.onclick = (e)=>{ e.preventDefault(); DT.export_sequence(f, sequenceParams()); };
  });
}
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
  const [g, f] = await Promise.all([getJSON("/api/gamify"), getJSON("/api/fun")]);
  renderGamify(g);
  const wc = w=>`<span class="chip">${escapeHtml(w.word)}<b>${w.count}</b></span>`;
  document.getElementById("longWords").innerHTML = chips(f.long_words, wc);
  document.getElementById("favWords").innerHTML = chips(f.favorite_words, wc);
  document.getElementById("rareChars").innerHTML = chips(f.rare_chars, c=>`<span class="chip">${escapeHtml(c.ch)}<b>${c.count}</b></span>`);
  document.getElementById("hapax").innerHTML = chips(f.hapax, c=>`<span class="chip">${escapeHtml(c)}</span>`);
}

// ---- lexicon (词库) ----

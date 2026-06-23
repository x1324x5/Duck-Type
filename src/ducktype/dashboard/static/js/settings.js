const CFG_BOOL = ["paused","exclude_password_fields","autostart","open_dashboard_on_start","lexicon_recompute_on_exclude","pie_download_include_pct","notify_enabled"];
const CFG_NUM = ["daily_goal","weekly_goal","monthly_goal","retention_days","run_gap_seconds","session_gap_seconds","dashboard_port","ticker_refresh_seconds"];
// ---- settings sub-section navigation (0.3.0) ----
// The settings page is split into named sub-sections; the sub-nav shows only the
// chosen one so the page isn't one long scroll. The first three live inside the
// #cfgForm panel (data-spanel="config"); 检查更新 / 数据管理 are their own panels.
let setSection = "record";
const _SET_PANEL_FOR = {record:"config", appearance:"config", data:"config",
                        update:"update", manage:"manage"};
function showSetSection(key){
  setSection = key || "record";
  document.querySelectorAll("#setTabs button").forEach(b=>
    b.classList.toggle("active", b.dataset.sset === setSection));
  const wantPanel = _SET_PANEL_FOR[setSection] || "config";
  document.querySelectorAll("#view-settings [data-spanel]").forEach(p=>{
    p.style.display = (p.dataset.spanel === wantPanel) ? "" : "none";
  });
  // within the shared config panel, reveal only the matching sub-section
  document.querySelectorAll("#cfgForm .setting-section").forEach(s=>{
    s.style.display = (s.dataset.ssec === setSection) ? "" : "none";
  });
}
(function wireSetTabs(){
  const bar = document.getElementById("setTabs");
  if(bar) bar.addEventListener("click", e=>{
    const b = e.target.closest("button[data-sset]"); if(!b) return;
    showSetSection(b.dataset.sset);
  });
})();

async function loadSettings(){
  showSetSection(setSection);
  const c = await DT.config_get();
  CFG_BOOL.forEach(k=>document.getElementById("c-"+k).checked = !!c[k]);
  CFG_NUM.forEach(k=>document.getElementById("c-"+k).value = c[k]);
  // surface the *real* registry state next to the autostart toggle so the user
  // can confirm it actually took effect (the config flag vs. what's registered)
  updateAutostartNote(c);
  setThemeMode(c.theme_mode || "system", true);
  tickerRefreshSeconds = Math.max(10, Number(c.ticker_refresh_seconds || 60));
  document.getElementById("c-blacklist_apps").value = (c.blacklist_apps||[]).join("\n");
  document.getElementById("hk-open").value = c.mini_open_hotkey || "";
  document.getElementById("hk-close").value = c.mini_close_hotkey || "";
  hkMsgClear();
  loadDataSummary();
}
function updateAutostartNote(c){
  const item = document.getElementById("c-autostart");
  if(!item) return;
  const desc = item.closest(".setting-item").querySelector(".setting-desc");
  if(!desc) return;
  const base = "登录 Windows 后自动启动 DuckType。";
  if(c.autostart && c.autostart_effective===false)
    desc.innerHTML = base + ' <b style="color:var(--warn,#e0a000)">⚠ 已开启，但注册表中未检测到，将在下次启动自动修复。</b>';
  else if(c.autostart && c.autostart_effective)
    desc.innerHTML = base + ' <span style="color:var(--ok,#36d399)">✓ 已在系统注册，重启后会自动运行。</span>';
  else
    desc.innerHTML = base;
}

// ---- mini-counter global hotkey capture ----
function hkMainKey(e){
  const c = e.code || "";
  if(/^Key[A-Z]$/.test(c)) return c.slice(3);
  if(/^Digit[0-9]$/.test(c)) return c.slice(5);
  if(/^F([1-9]|1[0-9]|2[0-4])$/.test(c)) return c;
  const map = {Space:"Space",Enter:"Enter",NumpadEnter:"Enter",Escape:"Esc",Tab:"Tab",
    Backquote:"`",ArrowUp:"Up",ArrowDown:"Down",ArrowLeft:"Left",ArrowRight:"Right",
    Insert:"Insert",Delete:"Delete",Home:"Home",End:"End",PageUp:"PageUp",PageDown:"PageDown"};
  return map[c] || null;
}
function hkFromEvent(e){
  const mods = [];
  if(e.ctrlKey) mods.push("Ctrl");
  if(e.altKey) mods.push("Alt");
  if(e.shiftKey) mods.push("Shift");
  if(e.metaKey) mods.push("Win");
  const main = hkMainKey(e);
  if(!main || !mods.length) return null;
  return [...mods, main].join("+");
}
function hkMsg(t, ok){ const el=document.getElementById("hkMsg"); if(!el) return;
  el.textContent = t || ""; el.className = "hk-msg" + (ok===false?" err":(ok===true?" ok":"")); }
function hkMsgClear(){ hkMsg("", null); }
function hkValidate(){
  const o = document.getElementById("hk-open").value, c = document.getElementById("hk-close").value;
  if(o && c && o===c){ hkMsg("打开和关闭不能是同一个组合键。", false); return false; }
  hkMsgClear(); return true;
}
["hk-open","hk-close"].forEach(id=>{
  const inp = document.getElementById(id);
  if(!inp) return;
  inp.addEventListener("keydown", e=>{
    e.preventDefault();
    if(e.key === "Escape"){ inp.value=""; hkValidate(); scheduleAutoSave(); return; }
    if(["Control","Alt","Shift","Meta"].includes(e.key)) return;   // wait for the main key
    const spec = hkFromEvent(e);
    if(spec){ inp.value = spec; if(hkValidate()) scheduleAutoSave(); }
    else hkMsg("请同时按住 Ctrl / Alt / Shift / Win，再加一个键。", false);
  });
  inp.addEventListener("focus", ()=> hkMsg("按下想用的组合键…（Esc 清除）", null));
});
document.querySelectorAll(".hk-clear").forEach(b=> b.addEventListener("click", ()=>{
  const inp = document.getElementById(b.dataset.clear); if(inp){ inp.value=""; hkValidate(); scheduleAutoSave(); }
}));
async function loadDataSummary(){
  const s = await DT.data_summary();
  const fmt = ts => ts ? new Date(ts*1000).toLocaleDateString() : "—";
  document.getElementById("dataSummary").textContent =
    `已记录 ${s.char_rows} 个汉字、${s.key_rows} 次按键，时间跨度 ${fmt(s.first_ts)} ~ ${fmt(s.last_ts)}。`;
  document.getElementById("dbPathShow").textContent = s.db_path + (s.is_default ? "（默认位置）" : "");
  const di = document.getElementById("c-data_dir");
  if(document.activeElement !== di && !di.value) di.value = s.data_dir || "";
}
function fmtMB(b){ return (Number(b||0)/1048576).toFixed(1)+" MB"; }
document.getElementById("pickDirBtn").addEventListener("click", async ()=>{
  let r; try{ r = await DT.data_pick_dir(); }catch(e){ return; }
  if(r && r.dir) document.getElementById("c-data_dir").value = r.dir;
});
document.getElementById("relocBtn").addEventListener("click", async ()=>{
  const dir = document.getElementById("c-data_dir").value.trim();
  const msg = document.getElementById("relocMsg");
  if(!dir){ msg.textContent = "请先选择目标文件夹。"; return; }
  if(!confirm(`将把全部数据移动到：\n${dir}\n\n移动后需重启 DuckType 生效。继续？`)) return;
  const bar = document.getElementById("relocBar"), prog = document.getElementById("relocProg"), lbl = document.getElementById("relocProgLabel");
  msg.textContent = ""; bar.style.display = "block"; prog.style.width = "0%"; lbl.textContent = "准备中…";
  let r; try{ r = await DT.data_relocate(dir); }catch(e){ r={ok:false,error:"请求失败"}; }
  if(!r.ok && !r.started){ bar.style.display="none"; msg.textContent = "移动失败：" + (r.error || "未知错误"); return; }
  const poll = setInterval(async ()=>{
    let p; try{ p = await DT.data_relocate_progress(); }catch(e){ return; }
    const pct = p.total ? Math.min(100, Math.round(p.done*100/p.total)) : (p.phase==="done"?100:0);
    prog.style.width = pct + "%";
    lbl.textContent = `${pct}%　${fmtMB(p.done)} / ${fmtMB(p.total)}`;
    if(p.phase==="done"){ clearInterval(poll); prog.style.width="100%";
      msg.innerHTML = `✓ 全部数据已移动到 <code>${escapeHtml(r.db_path||dir)}</code>。请<b>退出并重新启动 DuckType</b> 以使用新位置（重启后会自动清理旧位置）。`; loadDataSummary(); }
    else if(p.phase==="error"){ clearInterval(poll); bar.style.display="none"; msg.textContent = "移动失败：" + (p.error||"未知错误"); }
  }, 400);
});

// ---- tracked terms ----
let trackedTerms = [];
let trackedGroups = [];
let trackedBusy = false;
function fmtAgo(ts){
  if(!ts) return "还没出现过";
  const d = Math.floor((Date.now() - ts*1000)/86400000);
  if(d <= 0) return "今天还在用";
  if(d === 1) return "昨天用过";
  if(d < 30) return d + " 天前用过";
  return new Date(ts*1000).toLocaleDateString() + " 用过";
}
// Inline trend sparkline from a term's per-day counts.
function sparkSvg(daily){
  const vals = (daily||[]).map(d=>d.count);
  if(vals.length < 2 || Math.max(...vals) <= 0) return "";
  const W=100, H=24, pad=2, n=vals.length, mx=Math.max(...vals);
  const x = i => n===1 ? W/2 : pad + i*(W-2*pad)/(n-1);
  const y = v => H-pad - (v/mx)*(H-2*pad);
  const line = vals.map((v,i)=>`${i?"L":"M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${x(n-1).toFixed(1)} ${H-pad} L${x(0).toFixed(1)} ${H-pad} Z`;
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">`+
    `<path class="fillarea" d="${area}"/><path d="${line}"/></svg>`;
}
// 环比 badge: delta_pct may be a number (%), "new", or null (no baseline).
function deltaBadge(d){
  if(d === "new") return '<span class="delta new" title="上一周期没有出现">新增</span>';
  if(d === null || d === undefined) return "";
  if(d === 0) return '<span class="delta flat">→ 持平</span>';
  const up = d > 0;
  return `<span class="delta ${up?"up":"down"}" title="较上一周期">${up?"↑":"↓"} ${Math.abs(d)}%</span>`;
}
function trackCardHtml(r){
  const zero = !r.total;
  const sub = zero ? "当前时间范围内未出现"
    : `${fmtAgo(r.last_seen)} · ${r.active_days} 天出现${r.top_app?" · 多见于 "+escapeHtml(r.top_app):""}`;
  return `<div class="trackcard" data-term="${escapeAttr(r.term)}" title="查看「${escapeAttr(r.term)}」的详细统计">`+
    `<button class="rm" data-rm="${escapeAttr(r.term)}" title="移除关注" aria-label="移除关注">×</button>`+
    `<div class="term">${escapeHtml(r.term)}</div>`+
    `<div class="numrow"><span class="num${zero?" zero":""}">${r.total}<small>次</small></span>${deltaBadge(r.delta_pct)}</div>`+
    sparkSvg(r.daily)+
    `<div class="sub">${sub}</div></div>`;
}
function renderTracked(rows){
  const box = document.getElementById("trackedList");
  trackedTerms = rows.map(r=>r.term);
  trackedGroups = rows.map(r=>r.group||"");
  if(!rows.length){
    box.innerHTML = '<div class="trackempty">还没有关注词。添加一个人名或项目名，就能看到你输入它的频率。</div>';
    return;
  }
  // Bucket by group, preserving first-seen order; render flat if all ungrouped.
  const order = [], buckets = {};
  rows.forEach(r=>{ const g = r.group||""; if(!(g in buckets)){ buckets[g]=[]; order.push(g);} buckets[g].push(r); });
  const grouped = order.some(g=>g!=="");
  if(!grouped){ box.innerHTML = `<div class="trackgrid">${rows.map(trackCardHtml).join("")}</div>`; return; }
  // Named groups first (in order), ungrouped bucket last.
  order.sort((a,b)=> (a==="") - (b==="") );
  box.innerHTML = order.map(g=>{
    const head = `<div class="trackgroup-h">${g?escapeHtml(g):"未分组"}<span class="gcount">${buckets[g].length}</span></div>`;
    return `<div class="trackgroup">${head}<div class="trackgrid">${buckets[g].map(trackCardHtml).join("")}</div></div>`;
  }).join("");
}
async function loadTracked(){
  const box = document.getElementById("trackedList");
  if(!box) return;
  let r; try{ r = await apiGet("tracked", rangeParams()); }
  catch(e){ box.innerHTML = '<div class="trackempty">加载关注词失败。</div>'; return; }
  renderTracked((r && r.terms) || []);
}
async function saveTracked(terms, groups){
  trackedBusy = true;
  try{ await DT.config_set({tracked_terms: terms, tracked_groups: groups}); }
  finally{ trackedBusy = false; }
  await loadTracked();
}
async function addTracked(){
  if(trackedBusy) return;
  const inp = document.getElementById("trackInput");
  const ginp = document.getElementById("trackGroup");
  const term = inp.value.trim().slice(0,32);
  const group = (ginp.value.trim()||"").slice(0,16);
  if(!term) return;
  if(trackedTerms.includes(term)){ inp.value=""; gotoSearch(term); return; }
  if(trackedTerms.length >= 100){ alert("关注词最多 100 个。"); return; }
  inp.value = "";
  await saveTracked([...trackedTerms, term], [...trackedGroups, group]);
}
document.getElementById("trackAdd").addEventListener("click", addTracked);
document.getElementById("trackInput").addEventListener("keydown", e=>{ if(e.key==="Enter") addTracked(); });
document.getElementById("trackGroup").addEventListener("keydown", e=>{ if(e.key==="Enter") addTracked(); });
document.getElementById("trackedList").addEventListener("click", e=>{
  const rm = e.target.closest("[data-rm]");
  if(rm){ e.stopPropagation(); const t = rm.dataset.rm;
    const i = trackedTerms.indexOf(t);
    if(i<0) return;
    const terms = trackedTerms.slice(); const groups = trackedGroups.slice();
    terms.splice(i,1); groups.splice(i,1);
    saveTracked(terms, groups); return; }
  const card = e.target.closest(".trackcard");
  if(card) gotoSearch(card.dataset.term);
});

// ---- search ----
function renderFullExample(e){
  const pre = e.pre !== undefined ? e.pre : (e.text || "").slice(0, e.start || 0);
  const match = e.match !== undefined ? e.match : (e.text || "").slice(e.start || 0, e.end || 0);
  const post = e.post !== undefined ? e.post : (e.text || "").slice(e.end || 0);
  return `<span class="ctx">${escapeHtml(pre)}</span><span class="hl">${escapeHtml(match)}</span>`+
    `<span class="ctx">${escapeHtml(post)}</span>`;
}
async function doSearch(){
  const q = document.getElementById("searchInput").value.trim();
  const box = document.getElementById("searchResult");
  if(!q){ box.className="empty"; box.textContent="输入关键字后回车或点「搜索」。"; return; }
  box.className=""; box.textContent="搜索中…";
  let r; try{ r = await apiGet("search", rangeParams({q})); }
  catch(e){ box.className="empty"; box.textContent="搜索失败。"; return; }
  if(!r.total){ box.className="empty"; box.innerHTML = `当前范围内没有找到「${escapeHtml(q)}」。换个词或把时间范围调大试试。`; return; }
  const fmt = ts => ts ? new Date(ts*1000).toLocaleString() : "—";
  const apps = r.apps.map(a=>`<span class="chip">${escapeHtml(a.app)} · ${a.count}</span>`).join("") || "—";
  const days = r.daily.slice(-40).map(d=>`<span class="chip">${d.date} · ${d.count}</span>`).join("") || "—";
  const pk = (r.peak_hour===null||r.peak_hour===undefined) ? "—" : String(r.peak_hour).padStart(2,"0")+":00";
  const perDay = r.active_days ? (r.total/r.active_days).toFixed(1) : "0";
  const stats = [
    r.rank ? [`#${r.rank}`, "高频字排名"] : null,
    [r.share_pct + "%", "占全部输入"],
    [r.active_days + " 天", "出现天数"],
    [perDay, "活跃日均次数"],
    [pk, "高峰时段"],
  ].filter(Boolean);
  const exs = (r.examples||[]).map(e=>
    `<div class="ex"><div class="meta">${fmt(e.ts)} · ${escapeHtml(e.app)}</div>`+
    renderFullExample(e)+`</div>`).join("");
  box.className="";
  box.innerHTML =
    `<div class="bignum">「${escapeHtml(q)}」共出现 <b>${r.total}</b> 次</div>`+
    `<div class="hint" style="margin:6px 0 4px">首次输入：${fmt(r.first_seen)} ｜ 最近输入：${fmt(r.last_seen)}</div>`+
    `<div class="statgrid">${stats.map(([v,l])=>`<div class="s"><b>${v}</b><small>${l}</small></div>`).join("")}</div>`+
    `<h3 style="margin:10px 0 6px">各时段分布</h3>`+
    `<div style="position:relative;height:150px"><canvas id="searchHour"></canvas></div>`+
    (exs ? `<h3 style="margin:16px 0 6px">出现的上下文</h3><div class="exs">${exs}</div>` : "")+
    `<h3 style="margin:16px 0 6px">各程序</h3><div class="chips">${apps}</div>`+
    `<h3 style="margin:14px 0 6px">每日次数</h3><div class="chips">${days}</div>`;
  if(charts.searchHour) charts.searchHour.destroy();
  charts.searchHour = new Chart(document.getElementById("searchHour"), {
    type:"bar",
    data:{labels:[...Array(24).keys()].map(h=>String(h).padStart(2,"0")),
      datasets:[{data:r.by_hour,backgroundColor:"#a78bfa",borderRadius:3,maxBarThickness:18}]},
    options:{maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{displayColors:false}},
      scales:{x:{grid:{display:false}},y:{beginAtZero:true,ticks:{precision:0}}}}
  });
}
document.getElementById("searchBtn").addEventListener("click", doSearch);
document.getElementById("searchInput").addEventListener("keydown", e=>{ if(e.key==="Enter") doSearch(); });
// Persist all settings in one shot. Called automatically whenever a control
// changes (so the user never has to hunt for a save button), and also by the
// explicit 保存设置 button. Returns the backend result (or null if blocked by
// a hotkey-validation error).
let __cfgSaving = false;
async function saveSettings(){
  if(!hkValidate()) return null;
  const body = {};
  CFG_BOOL.forEach(k=>body[k]=document.getElementById("c-"+k).checked);
  CFG_NUM.forEach(k=>body[k]=Number(document.getElementById("c-"+k).value));
  body.theme_mode = themeMode;
  body.blacklist_apps = document.getElementById("c-blacklist_apps").value.split("\n").map(s=>s.trim()).filter(Boolean);
  body.mini_open_hotkey = document.getElementById("hk-open").value;
  body.mini_close_hotkey = document.getElementById("hk-close").value;
  __cfgSaving = true;
  let res;
  try{ res = await DT.config_set(body); }
  finally{ __cfgSaving = false; }
  window.__lexRecompute = body.lexicon_recompute_on_exclude !== false;
  window.__pieIncludePct = body.pie_download_include_pct !== false;
  tickerRefreshSeconds = Math.max(10, Number(body.ticker_refresh_seconds || 60));
  resetTickerTimer();
  // Reflect the canonicalised hotkeys + surface any OS conflict reported by the
  // live re-registration (false = the combo is already held by another app).
  if(res.mini_open_hotkey !== undefined) document.getElementById("hk-open").value = res.mini_open_hotkey;
  if(res.mini_close_hotkey !== undefined) document.getElementById("hk-close").value = res.mini_close_hotkey;
  if(res.hotkeys){
    const bad = [];
    if(res.hotkeys.open === false) bad.push("打开");
    if(res.hotkeys.close === false) bad.push("关闭");
    if(bad.length) hkMsg(`「${bad.join("、")}」热键已被其它程序占用，未能生效，请换一个组合。`, false);
    else if(document.getElementById("hk-open").value || document.getElementById("hk-close").value) hkMsg("热键已生效。", true);
    else hkMsgClear();
  }
  const saved = document.getElementById("cfgSaved");
  saved.textContent = res.restart_required ? "已保存（部分改动需重启）✓" : "已保存 ✓";
  saved.style.display = "inline"; setTimeout(()=>saved.style.display="none", 3000);
  // The goal ring / efficiency reads depend on daily_goal & gap settings; refresh
  // them now so a changed daily goal takes effect immediately, not after a delay.
  if(typeof loadGamify === "function"){ try{ loadGamify(); }catch(e){} }
  // refresh the autostart "actually registered?" note after a save
  try{ DT.config_get().then(updateAutostartNote).catch(()=>{}); }catch(e){}
  return res;
}
let __cfgSaveTimer = null;
function scheduleAutoSave(){ clearTimeout(__cfgSaveTimer); __cfgSaveTimer = setTimeout(saveSettings, 450); }
document.getElementById("cfgSave").addEventListener("click", saveSettings);
// ---- auto-save: write on every change, no need to scroll to a save button ----
const __cfgForm = document.getElementById("cfgForm");
if(__cfgForm){
  // checkboxes / number inputs commit on change (blur/enter); save right away
  __cfgForm.addEventListener("change", e=>{
    if(e.target.closest(".hotkey-input")) return;   // hotkeys persist via their own handler
    saveSettings();
  });
  // live-typed numbers / blacklist text: debounce so it saves shortly after the
  // last keystroke (so e.g. typing a new daily goal writes without leaving the field)
  __cfgForm.addEventListener("input", e=>{
    if(e.target.matches('input[type="number"], textarea')) scheduleAutoSave();
  });
}
// theme segmented buttons live in the form but change via click (core.js sets
// themeMode first); persist after that runs.
const __themeSeg = document.getElementById("themeMode");
if(__themeSeg) __themeSeg.addEventListener("click", e=>{ if(e.target.dataset.themeMode) scheduleAutoSave(); });
document.getElementById("clearAll").addEventListener("click", async ()=>{
  if(!confirm("确定清空全部已记录数据？此操作不可恢复。")) return;
  const r = await DT.data_clear();
  alert(`已删除 ${r.deleted} 个汉字。`); loadDataSummary();
});
document.getElementById("delRange").addEventListener("click", async ()=>{
  const s = document.getElementById("del-start").value, e = document.getElementById("del-end").value;
  if(!s && !e){ alert("请选择起止日期。"); return; }
  if(!confirm(`确定删除 ${s||"最早"} 至 ${e||"最新"} 的数据？`)) return;
  const r = await DT.data_delete(s, e);
  alert(`已删除 ${r.deleted} 个汉字。`); loadDataSummary();
});
function dataPackMsg(text, ok){
  const el = document.getElementById("dataPackMsg");
  el.textContent = text;
  el.className = ok===false ? "errmsg" : (ok===true ? "okmsg" : "");
}
document.getElementById("dataExport").addEventListener("click", async ()=>{
  dataPackMsg("正在打包…");
  let r; try{ r = await DT.data_export(); }catch(e){ r = {ok:false, error:"导出失败"}; }
  if(r && r.cancelled) dataPackMsg("");
  else if(r && r.ok){
    dataPackMsg(r.path ? ("已导出到 " + r.path) : "已开始下载备份文件。", true);
    // Export is read-only and never touches the live data; refresh the summary so
    // the page visibly reflects that everything is still here.
    loadDataSummary();
  }
  else dataPackMsg("导出失败：" + ((r&&r.error)||"未知错误"), false);
});
document.getElementById("gotoLexBtn").addEventListener("click", ()=>{
  document.querySelector('#tabs button[data-v="lexicon"]').click();
});
// Native: the bridge opens its own file dialog. Browser: trigger the hidden input.
document.getElementById("dataImport").addEventListener("click", ()=>{
  if(isNative()) runImport(null);
  else document.getElementById("dataImportFile").click();
});
document.getElementById("dataImportFile").addEventListener("change", e=>{
  const file = e.target.files && e.target.files[0];
  if(file) runImport(file);
  e.target.value = "";
});
async function runImport(file){
  if(!confirm("导入会用所选备份覆盖当前全部数据，且不可恢复。确定继续？")) return;
  dataPackMsg("正在导入…");
  let r; try{ r = await DT.data_import(file); }catch(e){ r = {ok:false, error:"导入失败"}; }
  if(r && r.cancelled){ dataPackMsg(""); return; }
  if(r && r.ok){
    dataPackMsg(`已导入 ${r.char_rows} 个汉字。建议重启 DuckType 以完全生效。`, true);
    boardLoaded = false; demoOn = false; applyDemoUI();
    loadDataSummary(); refreshActive(); checkHealth();
  } else {
    dataPackMsg("导入失败：" + ((r&&r.error)||"未知错误"), false);
  }
}

// ---- updates ----

// ---- mini counter (item 4 + R1 refinements) ----
// A small, always-on-top gauge. In the native app it lives in its own frameless
// on_top window (Api.open_mini hides the dashboard); in the browser/preview it
// renders inline via the #mini hash. The same #miniView markup is reused.
//
// Speed model: backend stats.mini_stats returns a decay-weighted *continuous*
// rate (no coarse 6-cpm steps, 0 for a lone keystroke). Here we additionally
// keep an EMA so the needle glides instead of jumping; the big number is the
// live rate, the small line under it is the smoothed rate, and a sparkline
// tracks the last ~minute of smoothed speed.
const MINI_MAX_CPM = 240;          // needle full-scale (right edge)
const MINI_EMA_ALPHA = 0.32;       // smoothing for needle / "平滑"
const MINI_SPARK_N = 48;           // sparkline sample count (~48s at 1s)
let miniTimer = null;
let miniSmoothCpm = null;          // EMA state (null until first sample)
let miniSamples = [];              // recent smoothed speeds for the sparkline
function isMiniMode(){ return location.hash.replace("#", "") === "mini"; }
function miniSetNeedle(cpm){
  const frac = Math.max(0, Math.min(1, (cpm || 0) / MINI_MAX_CPM));
  const deg = -90 + frac * 180;    // 0 cpm -> point left; max -> point right
  const n = document.getElementById("miniNeedle");
  if(n) n.style.transform = "rotate(" + deg.toFixed(1) + "deg)";
  const arc = document.querySelector("#miniView .mini-arc");
  if(arc){
    const len = arc.getTotalLength();
    arc.style.strokeDasharray = len;
    arc.style.strokeDashoffset = (len * (1 - frac)).toFixed(1);
    arc.style.opacity = "1";
  }
}
function miniRenderSpark(){
  const line = document.getElementById("miniSparkLine");
  const fill = document.getElementById("miniSparkFill");
  if(!line) return;
  const n = miniSamples.length;
  if(n < 2){ line.setAttribute("points",""); if(fill) fill.setAttribute("points",""); return; }
  const W = 200, H = 34, pad = 2;
  const scaleMax = Math.max(60, ...miniSamples);    // adaptive but never tiny
  const pts = miniSamples.map((v, i)=>{
    const x = n>1 ? (i/(n-1))*W : 0;
    const y = H - pad - (Math.min(v, scaleMax)/scaleMax) * (H - pad*2);
    return x.toFixed(1)+","+y.toFixed(1);
  });
  line.setAttribute("points", pts.join(" "));
  if(fill) fill.setAttribute("points", `0,${H} ` + pts.join(" ") + ` ${W},${H}`);
}
async function miniTick(){
  let r; try{ r = await DT.mini_stats(); }catch(e){ return; }
  if(!r) return;
  const raw = +r.speed_cpm || 0;
  miniSmoothCpm = (miniSmoothCpm === null) ? raw
    : miniSmoothCpm + MINI_EMA_ALPHA * (raw - miniSmoothCpm);
  const smooth = miniSmoothCpm;
  const sp = document.getElementById("miniSpeed");
  if(sp) sp.textContent = Math.round(raw).toLocaleString();
  miniSetNeedle(smooth);   // needle follows the smoothed value (no jumpy jitter)
  miniSamples.push(smooth);
  if(miniSamples.length > MINI_SPARK_N) miniSamples.shift();
  miniRenderSpark();
  const ses = document.getElementById("miniSession");
  if(ses) ses.textContent = (r.session_chars || 0).toLocaleString();
  const td = document.getElementById("miniToday");
  if(td) td.textContent = (r.today_chars || 0).toLocaleString();
  const goalPct = Math.max(0, Math.min(99.99, r.goal_pct || 0));
  const p = Math.min(1, goalPct);
  const bar = document.getElementById("miniGoalBar");
  if(bar) bar.style.width = (p * 100).toFixed(0) + "%";
  const goalWrap = bar ? bar.closest(".mini-goal") : document.querySelector("#miniView .mini-goal");
  if(goalWrap){
    goalWrap.classList.toggle("over-1", goalPct > 1);
    goalWrap.classList.toggle("over-2", goalPct >= 2);
    goalWrap.classList.toggle("over-5", goalPct >= 5);
  }
  const gt = document.getElementById("miniGoalText");
  if(gt) gt.textContent = "目标 " + Math.round(goalPct * 100) + "%";
}
function startMini(){
  document.body.classList.add("mini-mode");
  const mv = document.getElementById("miniView"); if(mv) mv.hidden = false;
  miniSmoothCpm = null; miniSamples = [];
  miniTick();
  if(miniTimer) clearInterval(miniTimer);
  miniTimer = setInterval(miniTick, 1000);
}
function stopMini(){
  document.body.classList.remove("mini-mode");
  const mv = document.getElementById("miniView"); if(mv) mv.hidden = true;
  if(miniTimer){ clearInterval(miniTimer); miniTimer = null; }
}
function enterMini(){
  // Native: spawn the separate on_top window. Browser: render inline via #mini.
  if(isNative()){ DT.open_mini(); }
  else { location.hash = "mini"; startMini(); }
}
const _miniBtn = document.getElementById("miniBtn");
if(_miniBtn) _miniBtn.addEventListener("click", enterMini);
const _miniClose = document.getElementById("miniClose");
if(_miniClose) _miniClose.addEventListener("click", ()=>{
  if(isNative()){ DT.close_mini(); }
  else { stopMini(); if(isMiniMode()) location.hash = ""; }
});
window.addEventListener("hashchange", ()=>{ if(isMiniMode()) startMini(); else stopMini(); });

// ---- corner drag-resize (frameless windows have no native resize border) ----
// The grip captures the pointer and tracks screen-coordinate deltas (CSS px, so
// they match the logical units webview.resize expects regardless of DPI). We
// drive the size through DT.mini_resize, throttled to one call per frame so the
// IPC bridge isn't flooded while dragging.
function miniInitResize(){
  const grip = document.getElementById("miniResize");
  if(!grip) return;
  let active = false, sx = 0, sy = 0, sw = 0, sh = 0, pending = null, raf = 0;
  function flush(){
    raf = 0;
    if(pending){ DT.mini_resize(pending.w, pending.h); pending = null; }
  }
  grip.addEventListener("pointerdown", (e)=>{
    active = true;
    sx = e.screenX; sy = e.screenY;
    sw = window.innerWidth; sh = window.innerHeight;
    try{ grip.setPointerCapture(e.pointerId); }catch(_){}
    e.preventDefault();
  });
  grip.addEventListener("pointermove", (e)=>{
    if(!active) return;
    pending = { w: Math.round(sw + (e.screenX - sx)), h: Math.round(sh + (e.screenY - sy)) };
    if(!raf) raf = requestAnimationFrame(flush);
    e.preventDefault();
  });
  function end(e){
    if(!active) return;
    active = false;
    try{ grip.releasePointerCapture(e.pointerId); }catch(_){}
    if(raf){ cancelAnimationFrame(raf); raf = 0; }
    flush();
  }
  grip.addEventListener("pointerup", end);
  grip.addEventListener("pointercancel", end);
}
miniInitResize();

// Native mini window loads index.html#mini -> render the gauge immediately.
if(isMiniMode()) startMini();

// ---- rotating launch-button art (R3) ----
// Three hand-drawn ducks (a / t / y), each with a light + dark variant. We cycle
// them slowly (so it feels alive but not flickery) and pick the variant that
// matches the current theme. Lives only on the dashboard (skip in #mini).
const MINI_ART_VARIANTS = ["a", "t", "y"];
let miniArtIdx = Math.floor(Math.random() * MINI_ART_VARIANTS.length);
let miniArtTimer = null;
function miniArtSuffix(){
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}
function miniArtApply(fade){
  const img = document.getElementById("miniBtnImg");
  if(!img) return;
  const src = "img/mini/mini-" + MINI_ART_VARIANTS[miniArtIdx] + "-" + miniArtSuffix() + ".png";
  if(fade){
    img.style.opacity = "0";
    setTimeout(()=>{ img.src = src; img.style.opacity = ""; img.style.display = ""; }, 280);
  } else { img.src = src; img.style.display = ""; }
}
function miniArtStart(){
  if(!document.getElementById("miniBtnImg") || isMiniMode()) return;
  miniArtApply(false);
  if(miniArtTimer) clearInterval(miniArtTimer);
  miniArtTimer = setInterval(()=>{
    miniArtIdx = (miniArtIdx + 1) % MINI_ART_VARIANTS.length;
    miniArtApply(true);
  }, 9000);
  // Re-pick the light/dark variant whenever the page theme attribute flips.
  try{
    new MutationObserver(()=>miniArtApply(false)).observe(
      document.documentElement, {attributes:true, attributeFilter:["data-theme"]});
  }catch(e){}
}
miniArtStart();

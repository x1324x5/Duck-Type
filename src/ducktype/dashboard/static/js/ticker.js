let tickerPool = [], tickerCurrent = null, tickerSeq = 0, tickerAt = 0;
let tickerPhrases = new Set(), tickerReported = -1;
// Little duck illustrations sprinkled around the UI; the spot re-rolls on every
// rotation/refresh so the page feels alive.
const DUCKS = ["01_idle","02_smile","03_wave","04_walk","05_run_fixed","06_jump",
  "07_laugh_slim","08_cry_slim","09_surprised","10_think","11_received_envelope_heart",
  "12_listen_music","13_dance","14_celebrate","15_read_book_slim","16_coding_laptop_slim",
  "17_hold_heart","18_mailbox","19_wear_hat","20_hat_slip","21_drink_mug",
  "22_eat_cookie_slim","23_peek","24_rest_belly","25_sleep"];
function duckURL(name){ return "ducks/" + name + ".svg"; }
function randomDuck(pool){ const a = pool || DUCKS; return duckURL(a[Math.floor(Math.random()*a.length)]); }
function isBlankQuote(msg){ return !(msg||"").replace(/[​\s]+/g,"").length; }
// Tell the backend a curated quote/phrase was shown (powers quote achievements).
// Data facts (server-generated, dynamic) are not in tickerPhrases, so they don't count.
function reportQuoteSeen(msg){
  if(!tickerPhrases.has(msg)) return;
  try{ DT.quote_seen(msg); }catch(e){}
}
function pickTicker(){
  if(!tickerPool.length) return null;
  if(tickerPool.length === 1) return tickerPool[0];
  let msg = tickerPool[Math.floor(Math.random()*tickerPool.length)];
  if(msg === tickerCurrent){
    const alternatives = tickerPool.filter(x=>x !== tickerCurrent);
    if(alternatives.length) msg = alternatives[Math.floor(Math.random()*alternatives.length)];
  }
  return msg;
}
async function loadTicker(){
  try{
    const t = await DT.ticker();
    tickerPhrases = new Set(t.phrases||[]);
    tickerPool = [...(t.facts||[]), ...(t.phrases||[])].filter(Boolean);
    tickerCurrent = null; tickerSeq = 0; tickerAt = Date.now(); tickerReported = -1;
  }catch(e){ tickerPool = []; tickerCurrent = null; tickerPhrases = new Set(); }
}
function renderTicker(){
  const el = document.getElementById("banner");
  if(!tickerPool.length){ el.className = "banner"; return; }
  el.className = "banner fun show";
  if(tickerCurrent === null) tickerCurrent = pickTicker();
  const msg = tickerCurrent;
  const blank = isBlankQuote(msg);   // the hidden "留白" easter-egg line
  const duck = blank ? duckURL("25_sleep") : randomDuck();
  el.innerHTML = `<img src="${duck}" alt="" onerror="this.onerror=null;this.src='duck.png'">`+
    `<div class="bn-text">${blank ? "" : escapeHtml(msg)}</div>`+
    `<button class="bn-refresh" id="bnRefresh" title="换一句">`+
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"`+
    ` stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">`+
    `<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>`+
    `<path d="M3 21v-5h5"/>`+
    `<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>`+
    `<path d="M21 3v5h-5"/></svg></button>`;
  // Count this view once per new position (renderTicker can re-run for the same
  // line, e.g. on resize, without it counting again).
  if(tickerSeq !== tickerReported){ tickerReported = tickerSeq; reportQuoteSeen(msg); }
}
function nextTicker(resetTimer=false){
  tickerCurrent = pickTicker();
  tickerSeq++;
  renderTicker();
  if(resetTimer) resetTickerTimer();
}
function resetTickerTimer(){
  clearInterval(factTimer);
  if(tickerPool.length>1){
    const ms = Math.max(10, Number(tickerRefreshSeconds || 60)) * 1000;
    factTimer = setInterval(()=>nextTicker(false), ms);
  }
}
function startTicker(){
  renderTicker();
  resetTickerTimer();
}
document.addEventListener("click", e=>{ if(e.target.closest("#bnRefresh")) nextTicker(true); });
async function checkHealth(){
  let s; try { s = await DT.status(); } catch(e){ return; }
  const el = document.getElementById("banner");
  if(s.db_recreated){
    clearInterval(factTimer);
    el.className = "banner warn show";
    el.innerHTML = "<b>检测到数据库文件丢失</b>，DuckType 已自动创建新的空白数据库。"+
      "如果你近期移动或删除过数据文件，旧统计可能无法恢复。"+
      "重新启动 DuckType 后，此提示会自动消失。";
    return;
  }
  if(s.paused || !s.hook_dll_found || !s.hook_installed ||
     ((s.chars_captured||0)===0 && (s.code_units||0)===0)){
    clearInterval(factTimer);
  }
  if(s.paused){
    el.className = "banner warn show";
    el.innerHTML = "<b>统计已暂停</b>。你可以到「设置」取消暂停，也可以通过托盘菜单恢复统计。";
  } else if(!s.hook_dll_found){
    el.className = "banner warn show";
    el.innerHTML = "<b>缺少输入捕获组件</b>，暂时无法记录上屏汉字。请使用发布版 <code>DuckType.exe</code>，或在源码环境先运行 <code>native\\build_dll.bat</code>。";
  } else if(!s.hook_installed){
    el.className = "banner warn show";
    el.innerHTML = "<b>输入捕获组件未能启动</b>。常见原因是安全软件拦截，或目标程序与 DuckType 位数不一致。详情可查看 DuckType 日志。";
  } else if((s.chars_captured||0) === 0 && (s.code_units||0) === 0){
    el.className = "banner warn show";
    el.innerHTML = "<b>输入捕获组件已启动，但还没有收到字符</b>。可以直接在下面输入几个汉字测试，或在记事本里试一下；如果刷新后仍没有记录，通常是被安全软件拦截或目标程序不兼容。"+
      `<div class="capture-test"><textarea id="captureTestInput" placeholder="在这里输入几个汉字，例如：今天开始记录"></textarea>`+
      `<button class="btn primary" id="healthRefresh">刷新检测</button></div>`;
  } else {
    // Healthy: show the ticker. Refresh the pool when stale (~3 min) or on first
    // run; otherwise just keep the existing rotation going / resume from a warning.
    if(!tickerPool.length || Date.now()-tickerAt > 180000){ await loadTicker(); startTicker(); }
    else if(!el.classList.contains("fun")) startTicker();
    if(!tickerPool.length) el.className = "banner";
  }
}
document.addEventListener("click", e=>{ if(e.target.closest("#healthRefresh")) checkHealth(); });

// ---- settings ----

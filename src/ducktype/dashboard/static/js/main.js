function setUpdateSteps(active, done){
  const wrap = document.getElementById("updSteps");
  wrap.style.display = "flex";
  const order = ["check","download","verify","restart"];
  const doneSet = new Set(done || []);
  wrap.querySelectorAll(".upstep").forEach(s=>{
    const key = s.dataset.step;
    s.classList.toggle("done", doneSet.has(key));
    s.classList.toggle("on", key === active);
  });
}
function resetUpdateUi(){
  document.getElementById("updBar").style.display = "none";
  document.getElementById("updProg").style.width = "0%";
  document.getElementById("updProgLabel").textContent = "";
  document.getElementById("updSteps").style.display = "none";
  document.getElementById("updApply").disabled = false;
}
document.getElementById("updCheck").addEventListener("click", async ()=>{
  const msg = document.getElementById("updMsg");
  resetUpdateUi();
  setUpdateSteps("check", []);
  msg.textContent = "正在连接发布页，检查是否有新版本…";
  let r; try{ r = await DT.update_check(); }catch(e){ msg.textContent="检查失败：网络不可用。"; return; }
  const applyBtn = document.getElementById("updApply"), pageBtn = document.getElementById("updPage");
  applyBtn.style.display = "none"; pageBtn.style.display = "none";
  if(!r.ok){
    setUpdateSteps(null, []);
    msg.textContent = "当前版本 v"+r.current+"。检查失败：" + (r.error||"") + "\n你仍然可以打开发布页手动查看。";
    pageBtn.href=r.releases_url; pageBtn.style.display="inline-block"; return;
  }
  if(r.has_update){
    setUpdateSteps(null, ["check"]);
    msg.textContent = `发现新版本 v${r.latest}（当前 v${r.current}）。\n\n更新说明：\n${(r.notes||"").slice(0,500) || "发布页暂未提供更新说明。"}`;
    pageBtn.href = r.html_url; pageBtn.style.display = "inline-block";
    if(r.frozen) applyBtn.style.display = "inline-block";
  } else {
    setUpdateSteps(null, ["check"]);
    msg.textContent = `已是最新版本 v${r.current}。`;
  }
});
document.getElementById("updApply").addEventListener("click", async ()=>{
  if(!confirm("将下载新版本，下载完成后 DuckType 会自动重启完成更新。确定？")) return;
  const msg = document.getElementById("updMsg");
  const bar = document.getElementById("updBar"), prog = document.getElementById("updProg"), lbl = document.getElementById("updProgLabel");
  document.getElementById("updApply").disabled = true;
  setUpdateSteps("download", ["check"]);
  msg.textContent = "下载完成后会先校验文件，再退出当前程序并替换为新版本。";
  bar.style.display = "block"; prog.style.width = "0%"; lbl.textContent = "准备下载…";
  let r; try{ r = await DT.update_apply(); }catch(e){ r={ok:false,error:"网络中断"}; }
  if(!r.ok && !r.started){
    bar.style.display="none"; document.getElementById("updApply").disabled=false;
    setUpdateSteps(null, ["check"]);
    msg.textContent = "更新失败：" + (r.error||"未知错误");
    return;
  }
  const poll = setInterval(async ()=>{
    let p; try{ p = await DT.update_progress(); }catch(e){ return; }
    if(p.phase==="downloading"||p.phase==="verifying"){
      const pct = p.total ? Math.min(100, Math.round(p.downloaded*100/p.total)) : 0;
      setUpdateSteps(p.phase==="verifying" ? "verify" : "download",
        p.phase==="verifying" ? ["check","download"] : ["check"]);
      prog.style.width = (p.total?pct:30) + "%";
      lbl.textContent = p.phase==="verifying" ? "正在校验下载文件…" :
        (p.total ? `下载中 ${pct}%　${fmtMB(p.downloaded)} / ${fmtMB(p.total)}` : `下载中 ${fmtMB(p.downloaded)}…`);
    } else if(p.phase==="staged"){
      clearInterval(poll); prog.style.width="100%"; lbl.textContent="下载完成，正在准备重启";
      setUpdateSteps("restart", ["check","download","verify"]);
      let left = 2;
      msg.innerHTML = `✓ 新版本已下载并校验完成。DuckType 会在 <b>${left}</b> 秒后退出，随后自动替换并重新启动。<br>`+
        `托盘图标重新出现后，即表示更新完成（程序名仍是 <code>DuckType.exe</code>）。`;
      const countdown = setInterval(()=>{
        left -= 1;
        if(left <= 0){ clearInterval(countdown); return; }
        msg.innerHTML = `✓ 新版本已下载并校验完成。DuckType 会在 <b>${left}</b> 秒后退出，随后自动替换并重新启动。<br>`+
          `托盘图标重新出现后，即表示更新完成（程序名仍是 <code>DuckType.exe</code>）。`;
      }, 1000);
    } else if(p.phase==="error"){
      clearInterval(poll); bar.style.display="none"; document.getElementById("updApply").disabled=false;
      setUpdateSteps(null, ["check"]);
      msg.textContent = "更新失败：" + (p.error||"未知错误");
    }
  }, 500);
});

// ---- window controls (frameless titlebar) ----
function wireWindowButtons(){
  const min = document.getElementById("winMin"), max = document.getElementById("winMax"),
        close = document.getElementById("winClose");
  if(!isNative()){
    [min,max,close].forEach(b=>{ if(b) b.style.display = "none"; });
    return;
  }
  [min,max,close].forEach(b=>{ if(b) b.style.display = ""; });
  min.onclick = ()=> nApi().window_minimize();
  max.onclick = async ()=>{
    const r = await nApi().window_toggle_maximize();
    max.textContent = r && r.maximized ? "❐" : "□";
  };
  close.onclick = ()=> nApi().window_hide();   // hide to tray; capture keeps running
  document.querySelector(".tb-drag").ondblclick = ()=> max.click();
}
// pywebview injects the bridge slightly after load; wire when it's ready.
if(isNative()) wireWindowButtons();
else window.addEventListener("pywebviewready", wireWindowButtons);
setTimeout(wireWindowButtons, 1200);   // belt-and-suspenders for the browser case

// ---- boot ----
try{ const fd=document.getElementById("footDuck"); fd.onerror=()=>{fd.onerror=null;fd.src="duck.png";}; fd.src=randomDuck(); }catch(e){}
// In the native window pywebview injects `window.pywebview.api` slightly *after*
// the page loads. If we fetched right away, isNative() would be false and the
// calls would fall back to HTTP (which the native build doesn't serve) -> a blank
// dashboard until the user clicks 刷新. So when running under pywebview we wait
// for the bridge before the first load. A dev browser (no window.pywebview) runs
// immediately.
let __booted = false;
function expectsNativeBridge(){
  return location.protocol === "file:" || !!window.pywebview;
}
function showBridgeWaiting(long=false){
  if(!boardLoaded) setBoardLoading(true);
  const el = document.getElementById("banner");
  el.className = "banner warn show";
  el.innerHTML = long
    ? "<b>仍在等待数据引擎</b>。如果这里停留太久，请从托盘退出 DuckType 后重新打开。"
    : "<b>正在连接数据引擎</b>，看板会在连接完成后自动显示。";
}
function firstLoad(){
  if(__booted) return;
  if(location.protocol === "file:" && !isNative()){
    showBridgeWaiting(false);
    return;
  }
  __booted = true;
  loadDashboardPrefs().then(()=>resetTickerTimer());
  DT.demo_status().then(r=>{ demoOn = !!(r && r.on); applyDemoUI(); }).catch(()=>{});
  refreshBoard();
  checkHealth();
}
function bootWhenReady(){
  // The native mini-counter window loads index.html#mini; mini.js renders the
  // gauge on its own, so skip the full dashboard boot here.
  if(isMiniMode()){ __booted = true; return; }
  if(isNative() || !expectsNativeBridge()){ firstLoad(); return; }
  // native but bridge not injected yet: wait for the ready event, with a poll fallback.
  showBridgeWaiting(false);
  window.addEventListener("pywebviewready", firstLoad, {once:true});
  let tries = 0;
  const t = setInterval(()=>{
    if(isNative()){ clearInterval(t); firstLoad(); return; }
    if(tries++ === 80) showBridgeWaiting(true);
  }, 100);
}
bootWhenReady();
setInterval(()=>{ if(__booted && document.querySelector("#tabs button.active").dataset.v==="board") refreshBoard(); }, 30000);
setInterval(()=>{ if(__booted) checkHealth(); }, 20000);

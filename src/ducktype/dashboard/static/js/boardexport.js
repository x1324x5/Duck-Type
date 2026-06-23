// ---- chart / image export (split out of board.js, 0.3.0) ----
// Saving board charts and 占比 pies as PNGs: a download-complete toast, the OS
// save dialog (native) / off-DOM link (browser), plain-canvas export, and the
// composite doughnut+legend export. Shared by every panel's ⬇ button.

// ---- download-complete toast (bottom-right; click to reveal the file) ----
// Native saves return {ok, path}; surface a dismissible toast that opens the
// folder on click. Browser downloads have no path, so we show a plain note.
function notifyDownload(res){
  if(res && res.cancelled) return;
  const host = document.getElementById("toastHost");
  if(!host) return;
  const ok = res && res.ok !== false;
  const path = res && res.path;
  const el = document.createElement("div");
  el.className = "toast" + (path ? " toast-link" : "");
  const where = path ? `<div class="tdsc" title="${escapeAttr(path)}">${escapeHtml(path)}</div>` : "";
  el.innerHTML = `<img src="${randomDuck()}" alt="" onerror="this.onerror=null;this.src='duck.png'">
    <div class="tbody"><div class="ttag">${ok ? "⬇ 下载完成" : "下载失败"}</div>
      <div class="tname">${ok ? (path ? "点此打开所在文件夹" : "已保存") : escapeHtml((res&&res.error)||"未知错误")}</div>
      ${where}</div>
    <button class="tclose" title="关闭" aria-label="关闭">×</button>`;
  host.appendChild(el);
  requestAnimationFrame(()=>el.classList.add("show"));
  let timer = setTimeout(close, 7000);
  function close(){ clearTimeout(timer); el.classList.remove("show"); setTimeout(()=>el.remove(), 300); }
  el.querySelector(".tclose").addEventListener("click", e=>{ e.stopPropagation(); close(); });
  if(ok && path && isNative()){
    el.addEventListener("click", ()=>{ DT.reveal_path(path).catch(()=>{}); });
  }
}
// Hand a finished PNG (data: URL) to the OS save dialog (native) or an off-DOM
// link (browser); toast the result. Shared by plain canvases and pie composites.
function saveImage(dataurl, name){
  const fname = "ducktype_" + name + "_" + new Date().toISOString().slice(0,10) + ".png";
  if(isNative()){
    nApi().save_png(fname, dataurl).then(notifyDownload).catch(()=>{});
  } else {
    const a = document.createElement("a");
    a.href = dataurl; a.download = fname;
    document.body.appendChild(a); a.click(); a.remove();
    notifyDownload({ok:true});
  }
}
// ---- save any board chart as a PNG ----
function downloadCanvas(id, name){
  const cv = document.getElementById(id); if(!cv || !cv.width) return;
  const tmp = document.createElement("canvas");
  tmp.width = cv.width; tmp.height = cv.height;
  const ctx = tmp.getContext("2d");
  ctx.fillStyle = cssVar("--bg") || "#0f1216";   // opaque bg so the PNG isn't see-through
  ctx.fillRect(0, 0, tmp.width, tmp.height);
  ctx.drawImage(cv, 0, 0);
  saveImage(tmp.toDataURL("image/png"), name);
}
// Find a live Chart.js instance by its <canvas> id across the board + lexicon
// registries (the lexicon pies live in their own `lexCharts` map).
function findChartByCanvas(id){
  if(charts[id]) return charts[id];
  // lexicon pies live in `lexCharts`, keyed by lexicon id (canvas is "lexchart_<id>")
  try{
    if(typeof lexCharts !== "undefined"){
      if(lexCharts[id]) return lexCharts[id];
      const k = id.replace(/^lexchart_/, "");
      if(lexCharts[k]) return lexCharts[k];
    }
  }catch(e){}
  return null;
}
// Labels on some pies embed "词 · 12次 · 3.4%"; strip that so our legend can
// render the name cleanly and append its own count / %.
function cleanSliceLabel(s){ return String(s).replace(/\s*·\s*[\d,]+\s*次.*$/, "").trim(); }
// Compose a doughnut + aligned legend (swatch · name · count · optional %) into
// one PNG, so the saved image carries the legend that's otherwise HTML-only
// (item: 词库占比 downloads). The pie is re-rendered off-screen without a
// built-in legend to avoid duplicating it; % follows the global setting.
function exportDoughnutWithLegend(chart, name){
  const ds = chart.data.datasets[0] || {};
  const raw = chart.data.labels || [];
  const colors = Array.isArray(ds.backgroundColor) ? ds.backgroundColor : [];
  const rows = [];
  raw.forEach((lab, i)=>{
    if(typeof chart.getDataVisibility === "function" && !chart.getDataVisibility(i)) return;
    rows.push({label: cleanSliceLabel(lab), count: +ds.data[i] || 0, color: colors[i] || "#888"});
  });
  const total = rows.reduce((s, r)=>s+r.count, 0) || 1;
  const includePct = window.__pieIncludePct !== false;
  // off-screen pie (no legend, no animation) -> a square image we paste on the left
  const PIE = 320, off = document.createElement("canvas");
  off.width = PIE; off.height = PIE;
  const tmpChart = new Chart(off, {
    type:"doughnut",
    data:{labels: rows.map(r=>r.label), datasets:[{data: rows.map(r=>r.count),
      backgroundColor: rows.map(r=>r.color), borderColor: cssVar("--panel"), borderWidth:2}]},
    options:{responsive:false, animation:false, events:[], devicePixelRatio:1,
      plugins:{legend:{display:false}, tooltip:{enabled:false}},
      cutout: chart.options && chart.options.cutout || "58%"}
  });
  tmpChart.update("none");   // animation:false -> draws synchronously, no rAF wait
  // measure legend
  const ctxm = off.getContext("2d");
  const FS = 15, ROW = 26, PAD = 24, SW = 14, GAP = 10;
  ctxm.font = FS + 'px "Segoe UI","Microsoft YaHei",sans-serif';
  let nameW = 0, cntW = 0;
  const fmtRow = rows.map(r=>{
    const cnt = r.count.toLocaleString() + "次";
    const pctv = includePct ? "  " + (r.count/total*100).toFixed(2) + "%" : "";
    nameW = Math.max(nameW, ctxm.measureText(r.label).width);
    cntW = Math.max(cntW, ctxm.measureText(cnt + pctv).width);
    return {name:r.label, tail: cnt + pctv, color:r.color};
  });
  const legendW = SW + GAP + Math.min(nameW, 260) + 18 + cntW;
  const legendH = rows.length * ROW;
  const W = PAD + PIE + 28 + legendW + PAD;
  const H = PAD + Math.max(PIE, legendH) + PAD;
  const out = document.createElement("canvas");
  out.width = W; out.height = H;
  const ctx = out.getContext("2d");
  ctx.fillStyle = cssVar("--bg") || "#0f1216";
  ctx.fillRect(0, 0, W, H);
  ctx.drawImage(off, PAD, (H - PIE) / 2);
  // legend block, vertically centred against the pie
  let lx = PAD + PIE + 28, ly = (H - legendH) / 2;
  ctx.textBaseline = "middle";
  ctx.font = FS + 'px "Segoe UI","Microsoft YaHei",sans-serif';
  fmtRow.forEach((r, i)=>{
    const cy = ly + i * ROW + ROW / 2;
    ctx.fillStyle = r.color;
    roundRectPath(ctx, lx, cy - SW/2, SW, SW, 3); ctx.fill();
    ctx.fillStyle = cssVar("--fg") || "#e8eaed";
    const maxName = 260;
    ctx.fillText(ellipsizeText(ctx, r.name, maxName), lx + SW + GAP, cy);
    ctx.fillStyle = cssVar("--muted") || "#9aa0a6";
    ctx.textAlign = "right";
    ctx.fillText(r.tail, W - PAD, cy);
    ctx.textAlign = "left";
  });
  tmpChart.destroy();
  saveImage(out.toDataURL("image/png"), name);
}
function roundRectPath(ctx, x, y, w, h, r){
  ctx.beginPath();
  ctx.moveTo(x+r, y); ctx.arcTo(x+w, y, x+w, y+h, r); ctx.arcTo(x+w, y+h, x, y+h, r);
  ctx.arcTo(x, y+h, x, y, r); ctx.arcTo(x, y, x+w, y, r); ctx.closePath();
}
function ellipsizeText(ctx, text, maxW){
  if(ctx.measureText(text).width <= maxW) return text;
  let t = text;
  while(t.length > 1 && ctx.measureText(t + "…").width > maxW) t = t.slice(0, -1);
  return t + "…";
}
document.addEventListener("click", e=>{
  const b = e.target.closest(".dlbtn"); if(!b) return;
  const id = b.dataset.dl, name = b.dataset.name || "chart";
  const chart = findChartByCanvas(id);
  if(chart && chart.config && chart.config.type === "doughnut"){
    try{ exportDoughnutWithLegend(chart, name); }
    catch(err){ console.warn("pie export failed, falling back", err); downloadCanvas(id, name); }
  } else {
    downloadCanvas(id, name);
  }
});

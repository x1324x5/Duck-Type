// ---- board customization (0.2.8): collapse / drag-reorder / hide panels + cards ----
// The board's chart panels can be folded to just their title, dragged into a new
// order, or hidden entirely (and restored from the 面板 menu). The eight summary
// cards on top can likewise be reordered by drag and closed via a corner ×. Layout
// is purely a frontend preference, persisted in localStorage -- the backend never
// sees it. Drag is pointer-based (not HTML5 DnD) so it's responsive, animates the
// swap with a FLIP transition, and auto-scrolls the page near the edges.
(function(){
  const LS_KEY = "dt-board-layout";
  const LS_CARDS = "dt-card-layout";
  // Each board panel is located by a stable inner element. `direct:true` means
  // the selector *is* the panel; otherwise we take its closest .panel ancestor.
  const DEFS = [
    {key:"gamify",   sel:"#goalRing",     name:"今日目标与连续打卡"},
    {key:"daily",    sel:"#dailyChart",    name:"每日 / 今日主题"},
    {key:"hourly",   sel:"#hourlyChart",   name:"按小时输入分布"},
    {key:"chars",    sel:"#charChart",     name:"高频字"},
    {key:"words",    sel:"#wordChart",     name:"高频词"},
    {key:"pos",      sel:"#posChart",      name:"词性分布"},
    {key:"apps",     sel:"#appChart",      name:"各应用输入量"},
    {key:"heatmap",  sel:"#heatmap",       name:"活跃热力图"},
    {key:"appeff",   sel:"#appEffPanel",   name:"各应用效率", direct:true},
    {key:"weekday",  sel:"#weekdayPanel",  name:"工作日 vs 周末", direct:true},
    {key:"richness", sel:"#richnessChart", name:"词汇丰富度趋势"},
    {key:"vocab",    sel:"#vocabPanel",    name:"词汇量成长", direct:true},
    {key:"contrib",  sel:"#contribCal",    name:"年度贡献热力图"},
    {key:"topics",   sel:"#topics",        name:"主题关键词"},
    {key:"usage",    sel:"#usagePanel",    name:"仪表盘使用历史", direct:true},
  ];
  const DEF_BY_KEY = Object.fromEntries(DEFS.map(d=>[d.key, d]));
  // Card names mirror CARD_DEFS in board.js (keys must match the data-card values).
  const CARDS = [
    ["total","总字数"],["distinct","不同汉字"],["cpm","平均速度"],["peak","峰值速度"],
    ["active","活跃时长"],["edit","修改率"],["del","删除键"],["sessions","输入会话"],
  ];
  const CARD_NAME = Object.fromEntries(CARDS.map(c=>[c[0], c[1]]));

  let state = {order:[], collapsed:{}, hidden:{}};
  let cardState = {order:[], hidden:{}};
  function load(){
    try{ const s = JSON.parse(localStorage.getItem(LS_KEY)||"null");
      if(s && typeof s==="object"){ state = Object.assign({order:[],collapsed:{},hidden:{}}, s); } }catch(e){}
    try{ const c = JSON.parse(localStorage.getItem(LS_CARDS)||"null");
      if(c && typeof c==="object"){ cardState = Object.assign({order:[],hidden:{}}, c); } }catch(e){}
  }
  function save(){ try{ localStorage.setItem(LS_KEY, JSON.stringify(state)); }catch(e){} }
  function saveCards(){ try{ localStorage.setItem(LS_CARDS, JSON.stringify(cardState)); }catch(e){} }

  function panelEl(def){
    const t = document.querySelector(def.sel);
    if(!t) return null;
    return def.direct ? t : t.closest(".panel");
  }
  function gridEl(){ return document.querySelector("#view-board .grid"); }
  function cardsEl(){ return document.getElementById("cards"); }

  // ---- generic pointer-drag sortable (used for both panels and cards) ----
  // FLIP-animates the siblings as the placeholder shifts, follows the cursor with
  // a fixed-positioned lift of the real element (so live charts keep rendering),
  // and auto-scrolls the window when the pointer nears the top/bottom edge.
  function flip(container, sel, mutate){
    const els = [...container.querySelectorAll(sel)];
    const first = new Map();
    els.forEach(el=> first.set(el, el.getBoundingClientRect()));
    mutate();
    els.forEach(el=>{
      const a = first.get(el), b = el.getBoundingClientRect();
      const dx = a.left - b.left, dy = a.top - b.top;
      if(dx || dy){
        el.style.transition = "none";
        el.style.transform = `translate(${dx}px,${dy}px)`;
        el.getBoundingClientRect();                 // force reflow so the next frame animates
        el.style.transition = "transform .22s ease";
        el.style.transform = "";
      }
    });
  }
  function makeSortable(container, opts){
    if(!container || container.__sortable) return;
    container.__sortable = true;
    container.addEventListener("pointerdown", e=>{
      if(e.button !== 0) return;
      const item = e.target.closest(opts.itemSel);
      if(!item || item.parentNode !== container) return;
      if(opts.handleSel){ if(!e.target.closest(opts.handleSel)) return; }
      else if(e.target.closest(opts.ignoreSel || "button,input,select,textarea,a,canvas")) return;
      e.preventDefault();
      startDrag(container, item, e, opts);
    });
  }
  function startDrag(container, item, downEv, opts){
    const startX = downEv.clientX, startY = downEv.clientY;
    let started = false, ph = null, offX = 0, offY = 0, raf = 0;
    let lastX = startX, lastY = startY;
    const liveSel = opts.itemSel + ",.sortable-ph";

    function begin(){
      started = true;
      const r = item.getBoundingClientRect();
      offX = startX - r.left; offY = startY - r.top;
      ph = document.createElement(item.tagName);
      ph.className = "sortable-ph";
      if(item.classList.contains("wide")) ph.classList.add("wide");
      ph.style.width = r.width + "px"; ph.style.height = r.height + "px";
      item.parentNode.insertBefore(ph, item.nextSibling);
      Object.assign(item.style, {position:"fixed", left:r.left+"px", top:r.top+"px",
        width:r.width+"px", height:r.height+"px", margin:"0", zIndex:"9999", pointerEvents:"none"});
      item.classList.add("sortable-dragging");
      document.body.classList.add("sorting");
      autoscroll();
    }
    function moveItem(){
      item.style.left = (lastX - offX) + "px";
      item.style.top  = (lastY - offY) + "px";
    }
    function reorder(){
      // Anchor to the element directly under the cursor. The dragged item has
      // pointer-events:none, so elementFromPoint returns what's beneath it.
      // Anchoring to what the pointer is *over* (instead of the nearest centre)
      // keeps multi-column grids from oscillating — the nearest-centre approach
      // reflowed on every move and flip-flopped, which read as violent shaking
      // and duplicated cards. The element under the cursor only changes when you
      // actually move onto a different card, so each swap is stable.
      const under = document.elementFromPoint(lastX, lastY);
      if(!under) return;
      const over = under.closest(opts.itemSel);
      if(!over || over === item || over === ph || over.parentNode !== container) return;
      const r = over.getBoundingClientRect();
      // multi-column row → decide by horizontal half; full-width panel → vertical half
      const multiCol = r.width < container.clientWidth - 4;
      const after = multiCol ? (lastX - r.left > r.width/2) : (lastY - r.top > r.height/2);
      const ref = after ? over.nextSibling : over;
      if(ref === ph) return;
      if(after ? (over.nextSibling === ph) : (over.previousSibling === ph)) return;  // already placed
      flip(container, liveSel, ()=> container.insertBefore(ph, ref));
    }
    function autoscroll(){
      const margin = 90, max = 16;
      function tick(){
        if(!started) return;
        const vh = window.innerHeight;
        if(lastY < margin) window.scrollBy(0, -max * (1 - lastY/margin));
        else if(lastY > vh - margin) window.scrollBy(0, max * (1 - (vh-lastY)/margin));
        moveItem();
        raf = requestAnimationFrame(tick);
      }
      raf = requestAnimationFrame(tick);
    }
    function onMove(e){
      lastX = e.clientX; lastY = e.clientY;
      if(!started){
        if(Math.hypot(e.clientX-startX, e.clientY-startY) < 5) return;
        begin();
      }
      moveItem(); reorder();
    }
    function onUp(){
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      if(raf) cancelAnimationFrame(raf);
      if(!started) return;
      container.insertBefore(item, ph);
      ph.remove();
      item.classList.remove("sortable-dragging");
      ["position","left","top","width","height","margin","zIndex","pointerEvents","transition","transform"]
        .forEach(p=> item.style[p] = "");
      document.body.classList.remove("sorting");
      if(opts.onDrop) opts.onDrop();
    }
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  // ---- apply persisted layout to the DOM ----
  function applyOrder(){
    const grid = gridEl(); if(!grid) return;
    const present = DEFS.map(d=>({d, el:panelEl(d)})).filter(x=>x.el);
    // saved order first (only keys we actually have), then any new panels.
    const ordered = [];
    (state.order||[]).forEach(k=>{ const x = present.find(p=>p.d.key===k); if(x && !ordered.includes(x)) ordered.push(x); });
    present.forEach(x=>{ if(!ordered.includes(x)) ordered.push(x); });
    ordered.forEach(x=> grid.appendChild(x.el));
  }
  function applyFlags(){
    DEFS.forEach(d=>{
      const el = panelEl(d); if(!el) return;
      el.classList.toggle("bl-collapsed", !!state.collapsed[d.key]);
      el.classList.toggle("bl-hidden", !!state.hidden[d.key]);
      const chev = el.querySelector(".bl-collapse");
      if(chev) chev.classList.toggle("on", !!state.collapsed[d.key]);
    });
  }
  // re-applied after every #cards re-render (renderCards rebuilds the nodes)
  function applyCards(){
    const c = cardsEl(); if(!c) return;
    const byKey = {};
    [...c.children].forEach(el=>{ if(el.dataset.card) byKey[el.dataset.card] = el; });
    (cardState.order||[]).forEach(k=>{ if(byKey[k]) c.appendChild(byKey[k]); });
    [...c.children].forEach(el=>{
      if(el.dataset.card) el.classList.toggle("card-hidden", !!cardState.hidden[el.dataset.card]);
    });
  }

  // ---- per-panel tools injected into the <h2> ----
  function injectTools(def){
    const el = panelEl(def); if(!el) return;
    el.dataset.panel = def.key;
    const h2 = el.querySelector("h2"); if(!h2 || h2.querySelector(".panel-tools")) return;
    const tools = document.createElement("span");
    tools.className = "panel-tools";
    tools.innerHTML =
      `<button class="bl-btn bl-collapse" title="折叠 / 展开" aria-label="折叠">`+
        `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></button>`+
      `<button class="bl-btn bl-hide" title="隐藏此面板" aria-label="隐藏">`+
        `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 10.6a2 2 0 0 0 2.8 2.8"/>`+
        `<path d="M9.4 5.2A9 9 0 0 1 12 5c5 0 9 5 9 7a12 12 0 0 1-2 2.6"/><path d="M6.3 6.3A12 12 0 0 0 3 12c0 2 4 7 9 7a9 9 0 0 0 3-.5"/></svg></button>`+
      `<button class="bl-btn bl-drag" title="拖动排序" aria-label="拖动排序">`+
        `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/>`+
        `<circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/></svg></button>`;
    h2.appendChild(tools);

    tools.querySelector(".bl-collapse").addEventListener("click", ()=>{
      state.collapsed[def.key] = !state.collapsed[def.key];
      save(); applyFlags(); resizeIfShown(el);
    });
    tools.querySelector(".bl-hide").addEventListener("click", ()=>{
      state.hidden[def.key] = true; save(); applyFlags(); renderMenu();
    });
  }
  function resizeIfShown(el){
    if(el.classList.contains("bl-collapsed")) return;
    try{ Object.values(charts||{}).forEach(c=>{ if(c && el.contains(c.canvas)) c.resize(); }); }catch(e){}
  }

  // ---- 面板 manager dropdown (show/hide checkboxes) ----
  function renderMenu(){
    const menu = document.getElementById("blMenu"); if(!menu) return;
    const grid = gridEl();
    const order = grid ? [...grid.querySelectorAll(".panel[data-panel]")].map(p=>p.dataset.panel) : DEFS.map(d=>d.key);
    const cards = cardsEl();
    const cOrder = cards ? [...cards.querySelectorAll(".card[data-card]")].map(p=>p.dataset.card) : CARDS.map(c=>c[0]);
    menu.innerHTML = `<div class="bl-menu-head">显示的面板</div>` +
      order.map(k=>{
        const d = DEF_BY_KEY[k]; if(!d) return "";
        const on = !state.hidden[k];
        return `<label class="bl-menu-row"><input type="checkbox" data-blk="${k}" ${on?"checked":""}>`+
          `<span>${escapeHtml(d.name)}</span></label>`;
      }).join("") +
      `<div class="bl-menu-head">数据卡片</div>` +
      cOrder.map(k=>{
        const name = CARD_NAME[k]; if(!name) return "";
        const on = !cardState.hidden[k];
        return `<label class="bl-menu-row"><input type="checkbox" data-blcard="${k}" ${on?"checked":""}>`+
          `<span>${escapeHtml(name)}</span></label>`;
      }).join("") +
      `<div class="bl-menu-foot"><button class="btn" id="blMenuReset">↺ 重置布局</button></div>`;
  }
  function toggleMenu(force){
    const menu = document.getElementById("blMenu"); if(!menu) return;
    const show = force!=null ? force : menu.hidden;
    if(show) renderMenu();
    menu.hidden = !show;
  }

  function resetLayout(){
    state = {order:[], collapsed:{}, hidden:{}};
    cardState = {order:[], hidden:{}};
    save(); saveCards();
    // clear collapsed/hidden flags, then restore the default DEF / card order
    const grid = gridEl();
    DEFS.forEach(d=>{ const el = panelEl(d); if(el && grid) grid.appendChild(el); });
    const cards = cardsEl();
    if(cards){
      const byKey = {}; [...cards.children].forEach(el=>{ if(el.dataset.card) byKey[el.dataset.card]=el; });
      CARDS.forEach(([k])=>{ if(byKey[k]) cards.appendChild(byKey[k]); });
    }
    applyFlags(); applyCards(); renderMenu();
  }

  // ---- init (panels are static in index.html, so wire on load) ----
  function init(){
    if(!gridEl()) return;
    load();
    DEFS.forEach(injectTools);
    applyOrder();
    applyFlags();
    applyCards();
    // panels: drag from the grip handle; cards: drag the whole card (× excluded)
    makeSortable(gridEl(), {
      itemSel:".panel[data-panel]", handleSel:".bl-drag",
      onDrop:()=>{ state.order = [...gridEl().querySelectorAll(".panel[data-panel]")].map(p=>p.dataset.panel);
        save(); renderMenu(); }
    });
    makeSortable(cardsEl(), {
      itemSel:".card[data-card]", ignoreSel:".card-corner,button,input,select,textarea,a",
      onDrop:()=>{ cardState.order = [...cardsEl().querySelectorAll(".card[data-card]")].map(p=>p.dataset.card);
        saveCards(); renderMenu(); }
    });
    // card close (corner ×) + re-apply layout whenever the cards are re-rendered
    const cards = cardsEl();
    if(cards){
      cards.addEventListener("cards:rendered", applyCards);
      cards.addEventListener("click", e=>{
        const x = e.target.closest(".card-x"); if(!x) return;
        const card = x.closest(".card[data-card]"); if(!card) return;
        cardState.hidden[card.dataset.card] = true; saveCards(); applyCards(); renderMenu();
      });
    }
    const manageBtn = document.getElementById("blManageBtn");
    const resetBtn = document.getElementById("blResetBtn");
    if(manageBtn) manageBtn.addEventListener("click", e=>{ e.stopPropagation(); toggleMenu(); });
    if(resetBtn) resetBtn.addEventListener("click", resetLayout);
    const menu = document.getElementById("blMenu");
    if(menu){
      menu.addEventListener("click", e=> e.stopPropagation());
      menu.addEventListener("change", e=>{
        const cb = e.target.closest("input[data-blk]");
        if(cb){ const k = cb.dataset.blk;
          if(cb.checked) delete state.hidden[k]; else state.hidden[k] = true;
          save(); applyFlags(); return; }
        const cc = e.target.closest("input[data-blcard]");
        if(cc){ const k = cc.dataset.blcard;
          if(cc.checked) delete cardState.hidden[k]; else cardState.hidden[k] = true;
          saveCards(); applyCards(); }
      });
      menu.addEventListener("click", e=>{ if(e.target.id==="blMenuReset") resetLayout(); });
    }
    document.addEventListener("click", ()=> toggleMenu(false));
  }
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();

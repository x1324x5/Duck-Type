// ---- 记录珍藏 (personal records / hall of fame) ----
function loadRecords(){
  const body = document.getElementById("recBody");
  body.innerHTML = '<div class="chart-skel">正在翻看你的珍藏…</div>';
  apiGet("records", {}).then(renderRecords).catch(()=>{
    body.innerHTML = emptyHTML("读取记录失败，请重试。");
  });
}
function _recDate(ts){ return ts ? new Date(ts*1000).toLocaleDateString("zh-CN") : "—"; }
function renderRecords(r){
  const body = document.getElementById("recBody");
  if(!r || !r.total_chars){
    body.innerHTML = `<div class="panel">`+
      emptyHTML("还没有足够的记录来珍藏。安静写一段中文，这里就会慢慢填满你的高光时刻。", "🦆")+`</div>`;
    return;
  }
  // hero band
  const firstD = _recDate(r.first_ts);
  const hero = `<div class="rec-hero">
    <img class="rec-hero-duck" src="${randomDuck()}" alt="" onerror="this.onerror=null;this.src='duck.png'">
    <div class="rec-hero-main">
      <div class="rec-hero-k">至今你一共写下</div>
      <div class="rec-hero-num">${r.total_chars.toLocaleString()}<small>个汉字</small></div>
      <div class="rec-hero-sub">自 ${firstD} 起 · 活跃 ${r.active_days} 天 / 陪伴 ${r.span_days} 天 · 平均每个活跃日 ${r.avg_per_active_day.toLocaleString()} 字</div>
    </div>
  </div>`;
  // record cards: [icon, title, big value, detail]
  const cards = [];
  if(r.best_day) cards.push(["🏆","最高产的一天",
    r.best_day.count.toLocaleString()+" 字", `${r.best_day.date}${r.best_day.weekday?("（"+r.best_day.weekday+"）"):""}`,"gold"]);
  cards.push(["🔥","最长连续打卡", r.best_streak+" 天",
    r.current_streak?("当前 "+r.current_streak+" 天"):"当前已中断","red"]);
  if(r.peak_cpm) cards.push(["⚡","最快的速度", r.peak_cpm+" 字/分", `平均 ${r.avg_cpm} 字/分`,"cyan"]);
  if(r.longest_session_min) cards.push(["⏱","最长连续输入", r.longest_session_min+" 分钟",
    r.longest_session_ts?_recDate(r.longest_session_ts):"", "blue"]);
  if(r.top_char) cards.push(["🈶","最常写的字",
    `<span class="rec-glyph">${escapeHtml(r.top_char.ch)}</span>`, r.top_char.count.toLocaleString()+" 次","gold"]);
  if(r.top_word) cards.push(["📝","最常写的词",
    `<span class="rec-glyph sm">${escapeHtml(r.top_word.word)}</span>`, r.top_word.count.toLocaleString()+" 次","green"]);
  if(r.peak_hour) cards.push(["🕐","最活跃的时段",
    String(r.peak_hour[0]).padStart(2,"0")+":00", r.peak_hour[1].toLocaleString()+" 字落在这一小时","violet"]);
  if(r.top_app) cards.push(["💻","你的主战场",
    escapeHtml(r.top_app.app||"未知"), r.top_app.count.toLocaleString()+" 字","blue"]);
  cards.push(["📚","累计上屏", r.total_chars.toLocaleString()+" 字", `不同汉字 ${r.distinct_chars.toLocaleString()} 个`,"green"]);
  if(r.rare_distinct) cards.push(["🌫","生僻字收藏", r.rare_distinct+" 个",
    `累计写过 ${r.rare_total.toLocaleString()} 次`,"violet"]);
  cards.push(["⌨️","累计按键", (r.key_total||0).toLocaleString()+" 次",
    `换来 ${r.total_chars.toLocaleString()} 个汉字`,"cyan"]);
  cards.push(["📅","陪伴时光", r.span_days+" 天", `其中 ${r.active_days} 天在写`,"gold"]);
  body.innerHTML = hero + `<div class="rec-grid">`+
    cards.map(([ic,t,v,d,tone])=>`<div class="rec-card tone-${tone}">
      <div class="rec-ic">${ic}</div>
      <div class="rec-t">${t}</div>
      <div class="rec-v">${v}</div>
      <div class="rec-d">${d||""}</div>
    </div>`).join("")+`</div>`;
}

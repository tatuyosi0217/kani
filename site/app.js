// ワールド・ニュースデスク フロントエンド
// セキュリティ方針: 外部由来の文字列は必ず textContent / setAttribute で扱い、
// innerHTML 等の HTML 文字列挿入は一切使わない (CSP の Trusted Types でも禁止している)。
"use strict";

(() => {
  const PAGE_SIZE = 50;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const TABS = [
    ["all", "総合"],
    ["finance", "金融"],
    ["economy", "経済"],
    ["geopolitics", "地政学"],
    ["science", "科学"],
    ["quantum", "量子"],
    ["markets", "マーケット"],
    ["status", "取得状況"],
  ];
  const CATEGORY_LABELS = Object.fromEntries(TABS.slice(1, 6));
  const RANGES = [["1M", 22], ["3M", 66], ["6M", 132], ["1Y", 260]];

  const state = {
    articles: [],
    markets: [],
    status: null,
    tab: "all",
    region: "",
    query: "",
    showOriginal: false,
    shown: PAGE_SIZE,
    chartId: null,
    range: "3M",
  };

  // ---------- 安全な DOM 生成 ----------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    setAttrs(el, attrs);
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      el.append(child instanceof Node ? child : String(child));
    }
    return el;
  }

  function s(tag, attrs, ...children) {
    const el = document.createElementNS(SVG_NS, tag);
    setAttrs(el, attrs);
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      el.append(child instanceof Node ? child : String(child));
    }
    return el;
  }

  function setAttrs(el, attrs) {
    if (!attrs) return;
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (/^on/i.test(key)) throw new Error("イベント属性は使用禁止");
      if (key === "href" && el.namespaceURI !== SVG_NS) {
        const url = safeUrl(value);
        if (!url) continue;
        el.setAttribute("href", url);
      } else {
        el.setAttribute(key, value === true ? "" : String(value));
      }
    }
  }

  function safeUrl(value) {
    if (typeof value !== "string") return null;
    if (value.startsWith("#")) return value;
    try {
      const url = new URL(value);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch {
      return null;
    }
  }

  // ---------- 書式 ----------
  const jstFormat = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
  const jstFull = new Intl.DateTimeFormat("ja-JP", {
    timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });

  function relTime(iso) {
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return "";
    const min = Math.round((Date.now() - t) / 60000);
    if (min < 1) return "たった今";
    if (min < 60) return `${min}分前`;
    if (min < 24 * 60) return `${Math.floor(min / 60)}時間前`;
    return jstFormat.format(t);
  }

  function fmtNum(v) {
    const digits = Math.abs(v) < 10 ? 4 : 2;
    return v.toLocaleString("ja-JP", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function fmtChange(m) {
    const dir = m.change > 0 ? "up" : m.change < 0 ? "down" : "flat";
    const sign = m.change > 0 ? "▲" : m.change < 0 ? "▼" : "±";
    return h("span", { class: `chg ${dir}` },
      `${sign}${fmtNum(Math.abs(m.change))} (${m.change_pct > 0 ? "+" : ""}${m.change_pct.toFixed(2)}%)`);
  }

  function fmtDate(d) {
    return d.replaceAll("-", "/");
  }

  // ---------- データ取得 ----------
  async function loadJson(path) {
    const res = await fetch(path, { cache: "no-cache", credentials: "omit", referrerPolicy: "no-referrer" });
    if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
    const type = res.headers.get("content-type") || "";
    if (!type.includes("json")) throw new Error(`${path}: 不正な Content-Type`);
    return res.json();
  }

  async function init() {
    buildTabs();
    bindControls();
    const [news, markets, status] = await Promise.allSettled([
      loadJson("data/news.json"),
      loadJson("data/markets.json"),
      loadJson("data/status.json"),
    ]);
    if (news.status === "fulfilled" && Array.isArray(news.value.articles)) {
      state.articles = news.value.articles.filter((a) => a && safeUrl(a.url) && a.title);
      document.getElementById("updated").textContent =
        `最終更新: ${jstFull.format(Date.parse(news.value.generated))} (日本時間) ・ ${state.articles.length.toLocaleString()} 件`;
    } else {
      document.getElementById("updated").textContent = "ニュースデータがまだありません(初回の取得を待っています)";
    }
    if (markets.status === "fulfilled" && Array.isArray(markets.value.instruments)) {
      state.markets = markets.value.instruments.filter((m) => Array.isArray(m.points) && m.points.length > 1);
    }
    if (status.status === "fulfilled") state.status = status.value;
    applyHash();
    window.addEventListener("hashchange", applyHash);
    let lastWidth = window.innerWidth;
    let resizeTimer;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => {
        if (state.tab === "markets" && window.innerWidth !== lastWidth) renderMarkets();
        lastWidth = window.innerWidth;
      }, 150);
    });
  }

  // ---------- ナビゲーション ----------
  function buildTabs() {
    const nav = document.getElementById("tabs");
    for (const [id, label] of TABS) nav.append(h("a", { href: `#${id}`, "data-tab": id }, label));
  }

  function applyHash() {
    const id = location.hash.slice(1);
    const known = TABS.some(([t]) => t === id);
    const target = known ? id : "all";
    if (state.tab !== target) {
      state.tab = target;
      state.region = "";
      state.shown = PAGE_SIZE;
    }
    for (const a of document.querySelectorAll("#tabs a")) {
      if (a.dataset.tab === state.tab) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    render();
  }

  function bindControls() {
    let timer;
    document.getElementById("search").addEventListener("input", (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        state.query = e.target.value.trim().toLowerCase();
        state.shown = PAGE_SIZE;
        if (state.tab === "markets" || state.tab === "status") location.hash = "#all";
        else renderNews();
      }, 200);
    });
    document.getElementById("show-original").addEventListener("change", (e) => {
      state.showOriginal = e.target.checked;
      renderNews();
    });
    document.getElementById("more").addEventListener("click", () => {
      state.shown += PAGE_SIZE;
      renderNews();
    });
  }

  function render() {
    const views = { news: "view-news", markets: "view-markets", status: "view-status" };
    const active = state.tab === "markets" ? "markets" : state.tab === "status" ? "status" : "news";
    for (const [key, id] of Object.entries(views)) document.getElementById(id).hidden = key !== active;
    renderTicker(active === "news" && ["all", "finance", "economy"].includes(state.tab));
    if (active === "news") renderNews();
    if (active === "markets") renderMarkets();
    if (active === "status") renderStatus();
  }

  // ---------- ニュース ----------
  function articlesForTab() {
    return state.tab === "all" ? state.articles : state.articles.filter((a) => a.category === state.tab);
  }

  function matchesQuery(a, q) {
    if (!q) return true;
    return [a.title_ja, a.title, a.summary_ja, a.summary, a.source, a.region]
      .some((v) => typeof v === "string" && v.toLowerCase().includes(q));
  }

  function renderFilters(list) {
    const counts = new Map();
    for (const a of list) if (a.region) counts.set(a.region, (counts.get(a.region) || 0) + 1);
    const regions = [...counts.entries()].sort((x, y) => y[1] - x[1]).slice(0, 14);
    const box = document.getElementById("filters");
    const chip = (value, label) => {
      const b = h("button", { type: "button", class: "chip", "aria-pressed": String(state.region === value) }, label);
      b.addEventListener("click", () => {
        state.region = value;
        state.shown = PAGE_SIZE;
        renderNews();
      });
      return b;
    };
    box.replaceChildren(chip("", "すべての地域"), ...regions.map(([r, n]) => chip(r, `${r} (${n})`)));
  }

  function renderNews() {
    const base = articlesForTab();
    renderFilters(base);
    const list = base.filter((a) => (!state.region || a.region === state.region) && matchesQuery(a, state.query));
    const ol = document.getElementById("news-list");
    const page = list.slice(0, state.shown);
    ol.replaceChildren(...page.map(articleCard));
    if (!list.length) {
      ol.append(h("li", { class: "empty" }, state.articles.length ? "該当する記事がありません" : "記事はまだありません"));
    }
    document.getElementById("result-count").textContent = list.length ? `${list.length.toLocaleString()} 件` : "";
    document.getElementById("more").hidden = list.length <= state.shown;
  }

  function articleCard(a) {
    const translated = Boolean(a.title_ja);
    const isJa = a.lang === "ja";
    const title = translated ? a.title_ja : a.title;
    const summary = translated ? a.summary_ja : a.summary;
    const meta = h("div", { class: "card-meta" },
      h("span", { class: `cat cat-${a.category}` }, CATEGORY_LABELS[a.category] || a.category),
      h("span", null, a.source),
      a.region ? h("span", null, a.region) : null,
      h("time", { datetime: a.published, title: jstFull.format(Date.parse(a.published)) }, relTime(a.published)),
      !isJa && a.lang ? h("span", { class: "badge", title: "原文の言語" }, a.lang.toUpperCase()) : null,
      !isJa && !translated ? h("span", { class: "badge" }, "未翻訳") : null,
    );
    const link = h("a", { href: a.url, target: "_blank", rel: "noopener noreferrer nofollow" }, title);
    const showOrig = state.showOriginal && translated;
    return h("li", { class: "card" },
      meta,
      h("h3", null, link),
      summary ? h("p", { class: "summary" }, summary) : null,
      showOrig ? h("p", { class: "original", lang: a.lang || null }, a.title, a.summary ? ` — ${a.summary}` : "") : null,
    );
  }

  // ---------- マーケット ----------
  function renderTicker(visible) {
    const box = document.getElementById("ticker");
    box.hidden = !visible || !state.markets.length;
    if (box.hidden) return;
    box.replaceChildren(...state.markets.map((m) => {
      const card = h("a", { class: "tick", href: "#markets", "data-id": m.id },
        h("div", { class: "tick-name" }, m.name),
        h("div", { class: "tick-price" }, fmtNum(m.last)),
        fmtChange(m),
        sparkline(m.points.slice(-22), m.change >= 0),
      );
      card.addEventListener("click", () => { state.chartId = m.id; });
      return card;
    }));
  }

  function renderMarkets() {
    const grid = document.getElementById("market-grid");
    if (!state.markets.length) {
      document.getElementById("chart-title").textContent = "マーケット";
      document.getElementById("chart-main").replaceChildren(h("p", { class: "empty" }, "市場データはまだありません"));
      grid.replaceChildren();
      return;
    }
    if (!state.markets.some((m) => m.id === state.chartId)) state.chartId = state.markets[0].id;

    const ranges = document.getElementById("ranges");
    ranges.replaceChildren(...RANGES.map(([label]) => {
      const b = h("button", { type: "button", class: "chip", "aria-pressed": String(state.range === label) }, label);
      b.addEventListener("click", () => { state.range = label; renderMarkets(); });
      return b;
    }));

    const m = state.markets.find((x) => x.id === state.chartId);
    const days = RANGES.find(([l]) => l === state.range)[1];
    const pts = m.points.slice(-days);
    document.getElementById("chart-title").textContent = m.name;
    document.getElementById("chart-quote").replaceChildren(
      document.createTextNode(`${fmtNum(m.last)} ${m.currency || ""}`), fmtChange(m));
    const wrap = document.getElementById("chart-main");
    wrap.replaceChildren(bigChart(pts, m.name, wrap.clientWidth));
    const first = pts[0][1];
    const periodPct = ((m.last - first) / first) * 100;
    document.getElementById("chart-note").textContent =
      `${fmtDate(pts[0][0])} 〜 ${fmtDate(m.as_of)} の日足終値 ・ 期間騰落率 ${periodPct > 0 ? "+" : ""}${periodPct.toFixed(2)}% ・ 出所: ${m.source}${m.stale ? "(更新失敗のため前回値)" : ""}`;

    grid.replaceChildren(...state.markets.map((x) => {
      const b = h("button", { type: "button", class: "market-card", "aria-pressed": String(x.id === m.id) },
        h("div", { class: "group" }, x.group),
        h("div", null, h("strong", null, x.name)),
        h("div", { class: "tick-price" }, fmtNum(x.last)),
        fmtChange(x),
        sparkline(x.points.slice(-66), x.change >= 0),
      );
      b.addEventListener("click", () => {
        state.chartId = x.id;
        renderMarkets();
        document.getElementById("chart-title").scrollIntoView({ behavior: "smooth", block: "center" });
      });
      return b;
    }));
  }

  function scale(points, w, h, pad) {
    const vals = points.map((p) => p[1]);
    let min = Math.min(...vals);
    let max = Math.max(...vals);
    if (min === max) { min -= 1; max += 1; }
    const span = max - min;
    min -= span * 0.05;
    max += span * 0.05;
    const n = points.length;
    const x = (i) => pad.l + (n === 1 ? 0 : (i / (n - 1)) * (w - pad.l - pad.r));
    const y = (v) => pad.t + (1 - (v - min) / (max - min)) * (h - pad.t - pad.b);
    return { x, y, min, max };
  }

  function pathD(points, sc) {
    return points.map((p, i) => `${i ? "L" : "M"}${sc.x(i).toFixed(1)},${sc.y(p[1]).toFixed(1)}`).join("");
  }

  function sparkline(points, up) {
    const w = 160, hgt = 40;
    const sc = scale(points, w, hgt, { l: 1, r: 1, t: 3, b: 3 });
    return s("svg", { viewBox: `0 0 ${w} ${hgt}`, preserveAspectRatio: "none", "aria-hidden": "true" },
      s("path", { d: pathD(points, sc), class: `spark ${up ? "up" : "down"}`, "vector-effect": "non-scaling-stroke" }));
  }

  function bigChart(points, name, width) {
    // 表示幅に合わせて描画し、スマホでも目盛りの文字が縮まないようにする
    const w = Math.max(300, Math.round(width || 800));
    const hgt = w < 600 ? 240 : 320;
    const pad = { l: 8, r: 64, t: 12, b: 26 };
    const sc = scale(points, w, hgt, pad);
    const svg = s("svg", { viewBox: `0 0 ${w} ${hgt}`, role: "img", "aria-label": `${name} の価格推移チャート` });

    const grid = s("g", { class: "grid" });
    const axis = s("g", { class: "axis" });
    for (let i = 0; i <= 4; i++) {
      const v = sc.min + ((sc.max - sc.min) * i) / 4;
      const yy = sc.y(v);
      grid.append(s("line", { x1: pad.l, x2: w - pad.r, y1: yy, y2: yy }));
      axis.append(s("text", { x: w - pad.r + 6, y: yy + 4 }, fmtNum(v)));
    }
    const ticks = Math.min(w < 500 ? 3 : 5, points.length);
    for (let i = 0; i < ticks; i++) {
      const idx = Math.round((i / Math.max(1, ticks - 1)) * (points.length - 1));
      const anchor = i === 0 ? "start" : i === ticks - 1 ? "end" : "middle";
      axis.append(s("text", { x: sc.x(idx), y: hgt - 6, "text-anchor": anchor }, fmtDate(points[idx][0]).slice(2)));
    }
    const line = pathD(points, sc);
    const baseY = hgt - pad.b;
    const area = `${line}L${sc.x(points.length - 1).toFixed(1)},${baseY}L${sc.x(0).toFixed(1)},${baseY}Z`;
    svg.append(grid, s("path", { d: area, class: "area" }), s("path", { d: line, class: "line" }), axis);

    // ホバー/タッチで値を表示
    const cross = s("g", { class: "cross", visibility: "hidden" });
    const vline = s("line", { y1: pad.t, y2: baseY });
    const dot = s("circle", { r: 4 });
    const tipRect = s("rect", { rx: 4, height: 22 });
    const tipText = s("text", { y: 0 });
    const tip = s("g", { class: "tip" }, tipRect, tipText);
    cross.append(vline, dot, tip);
    svg.append(cross);

    const move = (evt) => {
      const rect = svg.getBoundingClientRect();
      const px = ((evt.clientX - rect.left) / rect.width) * w;
      const ratio = (px - pad.l) / (w - pad.l - pad.r);
      const idx = Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1))));
      const cx = sc.x(idx), cy = sc.y(points[idx][1]);
      vline.setAttribute("x1", cx); vline.setAttribute("x2", cx);
      dot.setAttribute("cx", cx); dot.setAttribute("cy", cy);
      tipText.textContent = `${fmtDate(points[idx][0])}  ${fmtNum(points[idx][1])}`;
      const tw = tipText.getComputedTextLength() + 16;
      const tx = Math.min(Math.max(cx - tw / 2, pad.l), w - pad.r - tw);
      const ty = cy - 34 < pad.t ? cy + 12 : cy - 34;
      tipRect.setAttribute("x", tx); tipRect.setAttribute("y", ty); tipRect.setAttribute("width", tw);
      tipText.setAttribute("x", tx + 8); tipText.setAttribute("y", ty + 15);
      cross.setAttribute("visibility", "visible");
    };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerdown", move);
    svg.addEventListener("pointerleave", () => cross.setAttribute("visibility", "hidden"));
    return svg;
  }

  // ---------- 取得状況 ----------
  function renderStatus() {
    const body = document.getElementById("status-body");
    const summary = document.getElementById("translation-status");
    if (!state.status) {
      summary.textContent = "状況データはまだありません";
      body.replaceChildren();
      return;
    }
    const tr = state.status.translation || {};
    const engine = { claude: "Claude", deepl: "DeepL", none: "なし(APIキー未設定)" }[tr.engine] || tr.engine;
    const sources = Array.isArray(state.status.sources) ? state.status.sources : [];
    const ok = sources.filter((x) => x.ok).length;
    summary.textContent =
      `取得元 ${ok}/${sources.length} 成功 ・ 翻訳エンジン: ${engine} ・ 今回翻訳 ${tr.translated || 0} 件 / キャッシュ ${tr.cached || 0} 件 / 未翻訳 ${(tr.pending || 0) + (tr.failed || 0)} 件` +
      (tr.errors && tr.errors.length ? ` ・ エラー: ${tr.errors.join(" / ")}` : "");
    const typeLabel = { rss: "RSS", gdelt: "GDELT", market: "市場" };
    const sorted = [...sources].sort((a, b) => Number(a.ok) - Number(b.ok));
    body.replaceChildren(...sorted.map((x) => h("tr", null,
      h("td", null, x.name),
      h("td", null, typeLabel[x.type] || x.type),
      h("td", null, CATEGORY_LABELS[x.category] || (x.category === "markets" ? "マーケット" : x.category)),
      h("td", { class: x.ok ? "ok" : "ng" }, x.ok ? `✓ 正常${x.note ? `(${x.note})` : ""}` : `✕ ${x.error || "失敗"}`),
      h("td", { class: "num" }, String(x.items ?? 0)),
    )));
  }

  init().catch((err) => {
    document.getElementById("updated").textContent = "読み込みに失敗しました";
    console.error(err);
  });
})();

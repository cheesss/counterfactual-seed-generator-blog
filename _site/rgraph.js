// Interactive relationship graph: a tiny self-contained force simulation
// (no dependency) with drag, zoom/pan, hover highlight, and Obsidian-style
// force sliders. Progressive enhancement: it replaces the static fallback SVG.
(function () {
  const root = document.querySelector("[data-rgraph]");
  if (!root) return;
  const dataEl = root.querySelector(".rgraph-data");
  const stage = root.querySelector(".rgraph-stage");
  if (!dataEl || !stage) return;
  let data;
  try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }
  const NS = "http://www.w3.org/2000/svg";
  const W = data.width || 960, H = data.height || 600;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  stage.innerHTML = "";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.setAttribute("class", "rgraph-svg");
  const view = document.createElementNS(NS, "g");
  const gEdges = document.createElementNS(NS, "g");
  const gNodes = document.createElementNS(NS, "g");
  view.appendChild(gEdges); view.appendChild(gNodes);
  svg.appendChild(view); stage.appendChild(svg);

  const byId = {};
  data.nodes.forEach(function (n) { n.vx = 0; n.vy = 0; if (n.x == null) n.x = W / 2; if (n.y == null) n.y = H / 2; byId[n.id] = n; });
  const adj = {};
  data.nodes.forEach(function (n) { adj[n.id] = {}; });
  data.links.forEach(function (l) { l.s = byId[l.source]; l.t = byId[l.target]; if (l.s && l.t) { adj[l.s.id][l.t.id] = 1; adj[l.t.id][l.s.id] = 1; } });

  const edges = data.links.filter(function (l) { return l.s && l.t; });
  edges.forEach(function (l) {
    const e = document.createElementNS(NS, "line");
    e.setAttribute("class", l.kind === "entity" ? "rgraph-edge-entity" : "rgraph-edge");
    gEdges.appendChild(e); l.el = e;
  });
  data.nodes.forEach(function (n) {
    const g = document.createElementNS(NS, "g");
    g.setAttribute("class", "rgn rgn-" + n.type);
    const c = document.createElementNS(NS, "circle");
    const r = n.type === "post" ? 5.5 : (n.type === "sector" ? 9 : 8);
    c.setAttribute("r", r);
    c.setAttribute("class", n.type === "post" ? "rgraph-post" : (n.type === "sector" ? "rgraph-sector" : "rgraph-entity"));
    const t = document.createElementNS(NS, "text");
    t.textContent = n.label;
    if (n.type === "post") { t.setAttribute("class", "rgraph-post-label"); t.setAttribute("x", 9); t.setAttribute("y", 4); }
    else if (n.type === "sector") { t.setAttribute("class", "rgraph-hub-label"); t.setAttribute("text-anchor", "middle"); t.setAttribute("y", -14); }
    else { t.setAttribute("class", "rgraph-entity-label"); t.setAttribute("text-anchor", "middle"); t.setAttribute("y", 20); }
    g.appendChild(c); g.appendChild(t);
    if (n.title) { const ti = document.createElementNS(NS, "title"); ti.textContent = n.title; g.appendChild(ti); }
    gNodes.appendChild(g); n.el = g;
  });

  const params = { charge: 240, linkDist: 75, linkStr: 0.09, gravity: 0.004 };

  function draw() {
    edges.forEach(function (l) { l.el.setAttribute("x1", l.s.x); l.el.setAttribute("y1", l.s.y); l.el.setAttribute("x2", l.t.x); l.el.setAttribute("y2", l.t.y); });
    data.nodes.forEach(function (n) { n.el.setAttribute("transform", "translate(" + n.x.toFixed(1) + " " + n.y.toFixed(1) + ")"); });
  }

  let alpha = 0, raf = 0;
  function step() {
    const N = data.nodes;
    for (let i = 0; i < N.length; i++) {
      const a = N[i];
      for (let j = i + 1; j < N.length; j++) {
        const b = N[j];
        let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy;
        if (d2 < 36) d2 = 36;
        const d = Math.sqrt(d2);
        let f = params.charge / d2;
        if (f > 6) f = 6;
        const ux = dx / d, uy = dy / d;
        a.vx += ux * f; a.vy += uy * f; b.vx -= ux * f; b.vy -= uy * f;
      }
    }
    edges.forEach(function (l) {
      let dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, d = Math.hypot(dx, dy) || 0.01;
      const f = (d - params.linkDist) * params.linkStr, ux = dx / d, uy = dy / d;
      l.s.vx += ux * f; l.s.vy += uy * f; l.t.vx -= ux * f; l.t.vy -= uy * f;
    });
    N.forEach(function (n) {
      if (n.fixed) {
        if (n.tx != null) { n.x += (n.tx - n.x) * 0.3; n.y += (n.ty - n.y) * 0.3; }
        n.vx = 0; n.vy = 0; return;
      }
      n.vx += (W / 2 - n.x) * params.gravity;
      n.vy += (H / 2 - n.y) * params.gravity;
      n.vx *= 0.9; n.vy *= 0.9;
      if (n.vx > 16) n.vx = 16; if (n.vx < -16) n.vx = -16;
      if (n.vy > 16) n.vy = 16; if (n.vy < -16) n.vy = -16;
      n.x += n.vx * alpha; n.y += n.vy * alpha;
    });
    draw();
    alpha *= 0.992;
    if (alpha > 0.005) raf = requestAnimationFrame(step); else { alpha = 0; raf = 0; }
  }
  function reheat(a) { alpha = Math.max(alpha, a || 0.5); if (!raf) raf = requestAnimationFrame(step); }

  draw();
  if (!reduce) reheat(0.6);

  function bind(id, apply) {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", function () { apply(parseFloat(el.value)); reheat(0.35); });
  }
  bind("rg-charge", function (v) { params.charge = v; });
  bind("rg-linkdist", function (v) { params.linkDist = v; });
  bind("rg-linkstr", function (v) { params.linkStr = v / 100; });
  bind("rg-gravity", function (v) { params.gravity = v / 1000; });
  const reset = document.getElementById("rg-reset");
  if (reset) reset.addEventListener("click", function () {
    const set = function (id, val) { const e = document.getElementById(id); if (e) e.value = val; };
    set("rg-charge", 240); set("rg-linkdist", 75); set("rg-linkstr", 9); set("rg-gravity", 4);
    params.charge = 240; params.linkDist = 75; params.linkStr = 0.09; params.gravity = 0.004;
    reheat(0.5);
  });

  let tx = 0, ty = 0, scale = 1;
  function applyView() { view.setAttribute("transform", "translate(" + tx + " " + ty + ") scale(" + scale + ")"); }
  svg.addEventListener("wheel", function (e) {
    e.preventDefault();
    const rect = svg.getBoundingClientRect();
    const mx = (e.clientX - rect.left) / rect.width * W, my = (e.clientY - rect.top) / rect.height * H;
    const k = e.deltaY < 0 ? 1.1 : 1 / 1.1;
    const ns = Math.min(4, Math.max(0.4, scale * k));
    tx = mx - (mx - tx) * (ns / scale); ty = my - (my - ty) * (ns / scale); scale = ns; applyView();
  }, { passive: false });

  function toGraph(e) {
    const rect = svg.getBoundingClientRect();
    const mx = (e.clientX - rect.left) / rect.width * W, my = (e.clientY - rect.top) / rect.height * H;
    return { x: (mx - tx) / scale, y: (my - ty) / scale };
  }

  let dragNode = null, panning = false, panStart = null, moved = false;
  function highlight(n) {
    if (!n) { data.nodes.forEach(function (m) { m.el.classList.remove("rg-on", "rg-off"); }); edges.forEach(function (l) { l.el.classList.remove("rg-on", "rg-off"); }); return; }
    data.nodes.forEach(function (m) { const on = m.id === n.id || adj[n.id][m.id]; m.el.classList.toggle("rg-on", !!on); m.el.classList.toggle("rg-off", !on); });
    edges.forEach(function (l) { const on = l.s.id === n.id || l.t.id === n.id; l.el.classList.toggle("rg-on", on); l.el.classList.toggle("rg-off", !on); });
  }
  data.nodes.forEach(function (n) {
    n.el.addEventListener("pointerdown", function (e) { e.stopPropagation(); moved = false; dragNode = n; n.fixed = true; n.tx = n.x; n.ty = n.y; try { n.el.setPointerCapture(e.pointerId); } catch (x) {} });
    n.el.addEventListener("pointermove", function (e) { if (dragNode !== n) return; moved = true; const p = toGraph(e); n.tx = p.x; n.ty = p.y; reheat(0.3); });
    n.el.addEventListener("pointerup", function () { if (dragNode === n) { n.fixed = false; n.tx = null; n.ty = null; dragNode = null; reheat(0.25); if (!moved && n.href) location.href = n.href; } });
    n.el.addEventListener("mouseenter", function () { highlight(n); });
    n.el.addEventListener("mouseleave", function () { highlight(null); });
  });

  svg.addEventListener("pointerdown", function (e) { if (dragNode) return; panning = true; panStart = { x: e.clientX, y: e.clientY, tx: tx, ty: ty }; });
  svg.addEventListener("pointermove", function (e) { if (!panning || !panStart) return; const rect = svg.getBoundingClientRect(); tx = panStart.tx + (e.clientX - panStart.x) / rect.width * W; ty = panStart.ty + (e.clientY - panStart.y) / rect.height * H; applyView(); });
  svg.addEventListener("pointerup", function () { panning = false; panStart = null; });
  svg.addEventListener("pointerleave", function () { panning = false; panStart = null; });
})();

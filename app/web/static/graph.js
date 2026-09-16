(function () {
  var root = document.getElementById("citationGraph");
  if (!root) return;
  var note = document.getElementById("citationGraphNote");
  var baseUrl = root.getAttribute("data-url");
  var state = { nodes: [], edges: [], seen: {} };

  function load(url) {
    fetch(url, { credentials: "same-origin" })
      .then(function (response) { return response.json(); })
      .then(merge)
      .catch(function () {
        root.textContent = "Could not load the graph.";
      });
  }

  function merge(payload) {
    if (note) note.textContent = payload.disclaimer || "";
    (payload.nodes || []).forEach(function (node) {
      if (!state.seen[node.id]) {
        state.nodes.push(node);
        state.seen[node.id] = true;
      }
    });
    (payload.edges || []).forEach(function (edge) {
      var key = edge.source + "-" + edge.target;
      var reverse = edge.target + "-" + edge.source;
      if (!state.seen[key] && !state.seen[reverse]) {
        state.edges.push(edge);
        state.seen[key] = true;
      }
    });
    draw();
  }

  function draw() {
    root.innerHTML = "";
    var width = Math.max(root.clientWidth || 720, 640);
    var height = 560;
    var svg = svgEl("svg", { viewBox: "0 0 " + width + " " + height, role: "img", "aria-label": "Paper relationship graph" });
    var cx = width / 2;
    var cy = height / 2;
    var radius = Math.min(width, height) / 2 - 70;
    var positions = {};
    state.nodes.forEach(function (node, index) {
      var angle = (Math.PI * 2 * index) / Math.max(state.nodes.length, 1) - Math.PI / 2;
      var r = node.in_project ? radius : radius * 0.62;
      positions[node.id] = { x: cx + Math.cos(angle) * r, y: cy + Math.sin(angle) * r };
    });
    state.edges.forEach(function (edge) {
      var a = positions[edge.source];
      var b = positions[edge.target];
      if (!a || !b) return;
      var line = svgEl("line", {
        x1: a.x, y1: a.y, x2: b.x, y2: b.y,
        stroke: "#94a3b8",
        "stroke-width": String(Math.min(2.4, 0.8 + (edge.weight || 1) * 0.4)),
      });
      line.appendChild(svgEl("title", {}, edge.reason || "Related"));
      svg.appendChild(line);
    });
    state.nodes.forEach(function (node) {
      var pos = positions[node.id];
      var g = svgEl("g", { class: "graph-node", tabindex: "0", role: "button" });
      g.setAttribute("data-id", String(node.id));
      var circle = svgEl("circle", {
        cx: pos.x,
        cy: pos.y,
        r: node.in_project ? 10 : 7,
        fill: node.in_project ? "#1d4ed8" : "#f59e0b",
        stroke: "#0f172a",
        "stroke-width": "1",
      });
      var label = (node.title || "Paper").slice(0, 42);
      var text = svgEl("text", { x: pos.x + 12, y: pos.y + 4, "font-size": "11" }, label);
      g.appendChild(circle);
      g.appendChild(text);
      g.appendChild(svgEl("title", {}, (node.title || "") + (node.year ? " (" + node.year + ")" : "")));
      g.addEventListener("click", function () {
        load(baseUrl + (baseUrl.indexOf("?") >= 0 ? "&" : "?") + "expand=" + node.id);
      });
      svg.appendChild(g);
    });
    root.appendChild(svg);
    if (!state.nodes.length) {
      root.textContent = "Add or include papers to build a graph.";
    }
  }

  function svgEl(name, attrs, text) {
    var node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    if (text) node.textContent = text;
    return node;
  }

  load(baseUrl);
})();

(function () {
  var configEl = document.getElementById("proj-page-config");
  var config = { empty: false, running: false, autoStart: false, details: {} };
  if (configEl) {
    try {
      config = JSON.parse(configEl.textContent || "{}") || config;
    } catch (error) {
      config = { empty: false, running: false, autoStart: false, details: {} };
    }
  }
  var details = config.details || {};
  var dialog = document.getElementById("projDetailDialog");
  var titleEl = document.getElementById("projDetailTitle");
  var bodyEl = document.getElementById("projDetailBody");

  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function linkHtml(href, label) {
    if (!href) return "—";
    return (
      '<a href="' +
      escapeHtml(href) +
      '" target="_blank" rel="noopener noreferrer">' +
      escapeHtml(label || href) +
      "</a>"
    );
  }

  function renderDetail(item) {
    var topics = item.topics || [];
    var topicHtml = topics.length
      ? '<div class="paper-detail-tags">' +
        topics
          .map(function (topic) {
            return '<span class="paper-detail-tag">' + escapeHtml(topic) + "</span>";
          })
          .join("") +
        "</div>"
      : "—";
    var cats = item.categories || [];
    var catLabel = cats
      .map(function (cat) {
        return cat && (cat.label || cat.slug);
      })
      .filter(Boolean)
      .join(", ") || item.category_label || "";
    var avatar = item.avatar
      ? '<img class="proj-avatar proj-detail-avatar" src="' +
        escapeHtml(item.avatar) +
        '" alt="" width="44" height="44">'
      : "";
    return (
      '<div class="proj-detail-hero">' +
      avatar +
      "<div><div class=\"proj-owner\">" +
      escapeHtml(item.owner || "") +
      '</div><div class="proj-detail-full">' +
      escapeHtml(item.full_name || "") +
      "</div></div></div>" +
      '<div class="paper-detail-dl">' +
      '<div class="paper-detail-row"><div class="paper-detail-label">About</div><div class="paper-detail-value">' +
      escapeHtml(item.description || "No description") +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Language</div><div class="paper-detail-value">' +
      escapeHtml(item.language || "—") +
      "</div></div>" +
      (catLabel
        ? '<div class="paper-detail-row"><div class="paper-detail-label">Topic</div><div class="paper-detail-value">' +
          escapeHtml(catLabel) +
          "</div></div>"
        : "") +
      '<div class="paper-detail-row"><div class="paper-detail-label">Stars</div><div class="paper-detail-value">' +
      escapeHtml(item.stars_label || "0") +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Forks</div><div class="paper-detail-value">' +
      escapeHtml(item.forks_label || "0") +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Topics</div><div class="paper-detail-value">' +
      topicHtml +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Website</div><div class="paper-detail-value">' +
      linkHtml(item.homepage, item.homepage) +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">GitHub</div><div class="paper-detail-value">' +
      linkHtml(item.html_url, item.full_name || "Open repository") +
      "</div></div>" +
      "</div>"
    );
  }

  function openDetail(key) {
    var item = details[key];
    if (!item || !dialog) return;
    if (titleEl) titleEl.textContent = item.name || "Project";
    if (bodyEl) bodyEl.innerHTML = renderDetail(item);
    if (typeof dialog.showModal === "function") dialog.showModal();
  }

  document.addEventListener("click", function (event) {
    var closeBtn = event.target.closest("[data-proj-close]");
    if (closeBtn && dialog) {
      dialog.close();
      return;
    }
    if (event.target.closest("a")) return;
    var btn = event.target.closest("[data-proj-key]");
    if (!btn) return;
    event.preventDefault();
    openDetail(btn.getAttribute("data-proj-key") || "");
  });
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" && event.key !== " ") return;
    if (event.target.closest("a, button, input, textarea")) return;
    var row = event.target.closest(".proj-row[data-proj-key]");
    if (!row) return;
    event.preventDefault();
    openDetail(row.getAttribute("data-proj-key") || "");
  });
  if (dialog) {
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
  }

  var empty = !!config.empty;
  var running = !!config.running;
  var autoStart = !!config.autoStart;
  var alertEl = document.getElementById("projectsRefreshAlert");
  var tries = 0;
  var started = false;

  function showMsg(text) {
    if (!alertEl || !text) return;
    alertEl.hidden = false;
    alertEl.textContent = text;
  }

  function poll() {
    tries += 1;
    fetch("/api/projects-status", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (r.status === 401) throw new Error("auth");
        return r.json();
      })
      .then(function (data) {
        if (data.message) showMsg(data.message);
        if (data.running) {
          if (tries < 180) window.setTimeout(poll, 2000);
          return;
        }
        if (empty) {
          window.location.reload();
          return;
        }
        if (data.status === "ok" || data.status === "error") {
          showMsg(data.message || "Refresh finished.");
        }
      })
      .catch(function (err) {
        if (String(err && err.message) === "auth") {
          showMsg("Sign in again to load GitHub projects.");
          return;
        }
        if (!started && empty) {
          startAndPoll();
          return;
        }
        if (tries < 180) window.setTimeout(poll, 3000);
      });
  }

  function startAndPoll() {
    if (started) {
      poll();
      return;
    }
    started = true;
    showMsg("Refreshing top GitHub projects in the background…");
    fetch("/api/projects-refresh", {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      cache: "no-store",
    })
      .then(function (r) {
        if (r.status === 401) throw new Error("auth");
        return r.json();
      })
      .then(function (data) {
        if (data.message) showMsg(data.message);
        poll();
      })
      .catch(function (err) {
        if (String(err && err.message) === "auth") {
          showMsg("Sign in again to load GitHub projects.");
          return;
        }
        showMsg("Could not start refresh. Retrying…");
        poll();
      });
  }

  if (autoStart) startAndPoll();
  else if (running) poll();

  var searchEl = document.getElementById("projSearch");
  var countEl = document.getElementById("projCount");
  var emptyEl = document.getElementById("projSearchEmpty");
  var rows = Array.prototype.slice.call(document.querySelectorAll(".proj-row[data-proj-search]"));

  function applySearch() {
    var query = ((searchEl && searchEl.value) || "").trim().toLowerCase();
    var shown = 0;
    rows.forEach(function (row) {
      var hay = row.getAttribute("data-proj-search") || "";
      var match = !query || hay.indexOf(query) !== -1;
      row.classList.toggle("is-hidden", !match);
      if (match) shown += 1;
    });
    if (countEl) {
      var total = Number(countEl.getAttribute("data-total") || rows.length);
      if (query) {
        countEl.innerHTML =
          "<strong>" + shown + "</strong> of " + total + " project" + (total === 1 ? "" : "s") + " match";
      } else {
        countEl.innerHTML =
          "<strong>" + total + "</strong> project" + (total === 1 ? "" : "s") + " · ranked by GitHub stars";
      }
    }
    if (emptyEl) emptyEl.hidden = shown !== 0 || !query;
  }

  if (searchEl) searchEl.addEventListener("input", applySearch);
})();

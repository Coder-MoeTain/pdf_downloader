(function () {
  var PHASE_ORDER = ["starting", "searching", "merging", "oa", "storing", "downloading", "done"];
  var jobEl = document.getElementById("searchJob");
  if (!jobEl) return;

  var logsEl = document.getElementById("jobLogs");
  var bar = document.getElementById("jobBar");
  var messageEl = document.getElementById("jobMessage");
  var countsEl = document.getElementById("jobCounts");
  var titleEl = document.getElementById("jobTitle");
  var queryEl = document.getElementById("jobQuery");
  var resultEl = document.getElementById("jobResult");
  var statsEl = document.getElementById("jobStats");
  var submit = document.querySelector(".search-form [type=submit]");
  var fieldset = document.querySelector(".search-form fieldset");
  var steps = jobEl.querySelectorAll(".job-steps li");
  var wasActive = jobEl.getAttribute("data-active") === "true";
  var lastFingerprint = "";

  function setSteps(phase) {
    var idx = PHASE_ORDER.indexOf(phase);
    if (phase === "starting") idx = 1;
    steps.forEach(function (step) {
      var key = step.getAttribute("data-phase");
      var stepIdx = PHASE_ORDER.indexOf(key);
      step.classList.toggle("is-current", key === phase || (phase === "starting" && key === "searching"));
      step.classList.toggle("is-done", idx > stepIdx && phase !== "error");
    });
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderLogs(logs) {
    if (!logsEl) return;
    if (!logs || !logs.length) {
      if (lastFingerprint !== "empty") {
        lastFingerprint = "empty";
        logsEl.innerHTML = '<div class="log-line log-muted">Waiting for a search to start…</div>';
      }
      return;
    }
    var last = logs[logs.length - 1] || {};
    var fingerprint = logs.length + ":" + (last.time || "") + ":" + (last.message || "");
    if (fingerprint === lastFingerprint) return;
    lastFingerprint = fingerprint;
    logsEl.innerHTML = logs
      .map(function (entry) {
        return (
          '<div class="log-line log-' +
          (entry.level || "info") +
          '"><time>' +
          (entry.time || "") +
          "</time><span>" +
          escapeHtml(entry.message || "") +
          "</span></div>"
        );
      })
      .join("");
    logsEl.scrollTop = logsEl.scrollHeight;
  }

  function renderStats(stats) {
    if (!statsEl || !stats) return;
    var items = [
      ["Unique", stats.unique_papers || 0],
      ["Open access", stats.open_access_papers || 0],
      ["Paywalled", stats.paywalled || 0],
      ["PDFs", stats.pdfs_downloaded || 0],
    ];
    statsEl.innerHTML = items
      .map(function (item) {
        return (
          '<div class="col-6"><div class="result-stat"><div class="stat-label">' +
          item[0] +
          '</div><div class="stat-value tone-primary">' +
          item[1] +
          "</div></div></div>"
        );
      })
      .join("");
  }

  function setFormBusy(active) {
    if (fieldset) fieldset.disabled = !!active;
    if (!submit) return;
    submit.disabled = !!active;
    submit.textContent = active ? "Search running…" : "Run search";
    var hint = document.getElementById("searchFormHint");
    if (hint) {
      hint.textContent = active
        ? "A job is running — watch the live log."
        : "Typical run: 1–3 minutes depending on sources.";
    }
  }

  function apply(data) {
    if (!data || data.kind === "download") return;
    var active = !!data.active && data.kind === "search";
    jobEl.classList.toggle("is-live", active);
    jobEl.classList.toggle("is-done", data.phase === "done");
    jobEl.classList.toggle("is-error", data.phase === "error");
    jobEl.setAttribute("data-active", active ? "true" : "false");
    if (titleEl) {
      titleEl.textContent = active ? "Live search" : data.phase === "done" ? "Last search" : data.phase === "error" ? "Search failed" : "Search activity";
    }
    var titleWrap = document.getElementById("jobTitleWrap");
    if (titleWrap) {
      var dot = titleWrap.querySelector(".nav-live-dot");
      if (active && !dot) {
        titleWrap.insertAdjacentHTML("afterbegin", '<span class="nav-live-dot" aria-hidden="true"></span>');
      } else if (!active && dot) {
        dot.remove();
      }
    }
    if (queryEl) queryEl.textContent = data.query || "";
    if (messageEl) messageEl.textContent = data.message || "Submit a topic to watch sources respond in real time.";
    if (countsEl) countsEl.textContent = data.total ? data.current + "/" + data.total : "";
    if (bar) {
      var pct = data.percent == null ? (active ? 12 : 0) : data.percent;
      bar.style.width = pct + "%";
      bar.textContent = data.percent == null ? (active ? "…" : "") : Math.round(data.percent) + "%";
      bar.classList.toggle("progress-bar-striped", active);
      bar.classList.toggle("progress-bar-animated", active);
    }
    setSteps(data.phase || "idle");
    renderLogs(data.logs || []);
    if (data.phase === "done" || data.phase === "error") {
      if (resultEl) resultEl.classList.toggle("d-none", data.phase !== "done");
      renderStats(data.stats || {});
    }
    if (wasActive !== active) setFormBusy(active);
    wasActive = active;
  }

  function poll() {
    fetch("/api/search-progress", { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json();
      })
      .then(apply)
      .catch(function () {});
  }

  if (logsEl) logsEl.scrollTop = logsEl.scrollHeight;
  if (new URLSearchParams(window.location.search).has("live")) {
    jobEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  poll();
  setInterval(poll, 700);
})();

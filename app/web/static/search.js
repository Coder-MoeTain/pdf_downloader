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
  var steps = jobEl.querySelectorAll(".job-steps li");
  var queueBody = document.getElementById("searchQueueBody");
  var queueSummary = document.getElementById("queueSummary");
  var queueCard = document.getElementById("searchQueueCard");
  var wasActive = jobEl.getAttribute("data-active") === "true";
  var lastFingerprint = "";
  var lastQueueJson = "";
  var params = new URLSearchParams(window.location.search);
  var trackedJobId = params.get("job") || jobEl.getAttribute("data-job-id") || "";
  var stopping = false;

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

  function renderDownloadAction(phase, stats) {
    var form = document.getElementById("jobDownloadForm");
    if (!form) return;
    var oa = (stats && stats.open_access_papers) || 0;
    var pdfs = (stats && stats.pdfs_downloaded) || 0;
    form.classList.toggle("d-none", phase !== "done" || !oa || pdfs > 0);
  }

  function apply(data) {
    if (!data || data.kind === "download") return;
    if (trackedJobId && data.job_id && String(data.job_id) !== String(trackedJobId)) return;
    if (trackedJobId && !data.job_id && data.phase === "idle" && !(data.logs && data.logs.length)) return;
    if (data.job_id) trackedJobId = String(data.job_id);
    var active = !!data.active && data.kind === "search";
    jobEl.classList.toggle("is-live", active);
    jobEl.classList.toggle("is-done", data.phase === "done");
    jobEl.classList.toggle("is-error", data.phase === "error" || data.phase === "cancelled");
    jobEl.setAttribute("data-active", active ? "true" : "false");
    if (titleEl) {
      titleEl.textContent = active
        ? "Live search"
        : data.phase === "done"
          ? "Last search"
          : data.phase === "error"
            ? "Search failed"
            : data.phase === "cancelled"
              ? "Search stopped"
              : "Search activity";
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
    var stopForm = document.getElementById("jobStopForm");
    var showStop = (active || stopping) && trackedJobId;
    if (showStop) {
      if (!stopForm && queryEl && queryEl.parentElement) {
        queryEl.parentElement.insertAdjacentHTML(
          "beforeend",
          '<form method="post" action="/search/jobs/' +
            encodeURIComponent(trackedJobId) +
            '/stop" class="d-inline" id="jobStopForm"><button class="btn btn-sm btn-outline-danger" type="submit">Stop</button></form>'
        );
        stopForm = document.getElementById("jobStopForm");
      } else if (stopForm) {
        stopForm.action = "/search/jobs/" + trackedJobId + "/stop";
        stopForm.classList.remove("d-none");
      }
    } else if (stopForm) {
      stopForm.classList.add("d-none");
    }
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
      renderDownloadAction(data.phase, data.stats || {});
    }
    wasActive = active;
  }

  function renderQueue(data) {
    if (!queueBody || !data) return;
    var encoded = JSON.stringify(data);
    if (encoded === lastQueueJson) return;
    lastQueueJson = encoded;
    if (queueSummary) {
      queueSummary.textContent = (data.running || 0) + " running · " + (data.pending || 0) + " pending";
    }
    if (!data.users || !data.users.length) {
      queueBody.innerHTML = '<div class="p-3 paper-meta mb-0">No searches in the queue.</div>';
      return;
    }
    queueBody.innerHTML = data.users
      .map(function (group) {
        var jobs = (group.jobs || [])
          .map(function (item) {
            var label =
              item.status === "pending" && item.position
                ? "#" + item.position + " pending"
                : item.status;
            var badgeClass = item.status === "running" ? "primary" : "secondary";
            if (String(item.id) === String(trackedJobId) && item.status === "running") {
              trackedJobId = String(item.id);
            }
            var stop =
              item.can_stop
                ? '<form method="post" action="/search/jobs/' +
                  encodeURIComponent(item.id) +
                  '/stop" class="d-inline"><button class="btn btn-sm btn-outline-danger queue-stop" type="submit">Stop</button></form>'
                : "";
            return (
              '<li class="list-group-item d-flex justify-content-between gap-3 align-items-center py-2">' +
              '<div class="text-truncate" title="' +
              escapeHtml(item.query || "") +
              '">' +
              escapeHtml(item.query || "") +
              "</div>" +
              '<span class="d-flex align-items-center gap-2 flex-shrink-0">' +
              '<span class="status-badge status-' +
              badgeClass +
              '">' +
              escapeHtml(label) +
              "</span>" +
              stop +
              "</span></li>"
            );
          })
          .join("");
        return (
          '<div class="queue-user-group border-bottom">' +
          '<div class="px-3 py-2 bg-light fw-semibold small">' +
          escapeHtml(group.username || "Anonymous") +
          "</div>" +
          '<ul class="list-group list-group-flush">' +
          jobs +
          "</ul></div>"
        );
      })
      .join("");
  }

  function pollProgress() {
    var url = "/api/search-progress";
    if (trackedJobId) url += "?job_id=" + encodeURIComponent(trackedJobId);
    fetch(url, { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json();
      })
      .then(apply)
      .catch(function () {});
  }

  function pollQueue() {
    var previousJobId = trackedJobId;
    fetch("/api/search-queue", { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        renderQueue(data);
        if (!trackedJobId && data.users) {
          var pendingId = "";
          data.users.forEach(function (group) {
            (group.jobs || []).forEach(function (item) {
              if (item.status === "running" && !trackedJobId) {
                trackedJobId = String(item.id);
              } else if (item.status === "pending" && !pendingId) {
                pendingId = String(item.id);
              }
            });
          });
          if (!trackedJobId && pendingId) trackedJobId = pendingId;
        }
        if (trackedJobId && trackedJobId !== previousJobId) {
          pollProgress();
        }
      })
      .catch(function () {});
  }

  function postStop(action) {
    stopping = true;
    document.querySelectorAll(".queue-stop, #jobStopForm button").forEach(function (btn) {
      btn.disabled = true;
      btn.textContent = "Stopping…";
    });
    return fetch(action, { method: "POST", headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json().catch(function () {
          return {};
        });
      })
      .then(function () {
        window.location.href = "/search?live=1";
      })
      .catch(function () {
        window.location.href = "/search?live=1";
      });
  }

  function onStopSubmit(event) {
    var form = event.target;
    if (!form || form.tagName !== "FORM") return;
    var action = form.getAttribute("action") || "";
    if (action.indexOf("/search/jobs/") === -1 || action.indexOf("/stop") === -1) return;
    event.preventDefault();
    postStop(action);
  }

  jobEl.addEventListener("submit", onStopSubmit);
  if (queueCard) queueCard.addEventListener("submit", onStopSubmit);

  if (logsEl) logsEl.scrollTop = logsEl.scrollHeight;
  if (params.has("live")) {
    jobEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  pollProgress();
  pollQueue();
  setInterval(pollProgress, 700);
  setInterval(pollQueue, 1500);
})();

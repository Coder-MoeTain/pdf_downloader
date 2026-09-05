(function () {
  var PHASE_ORDER = ["starting", "crawling", "oa", "storing", "downloading", "done"];
  var jobEl = document.getElementById("crawlJob");
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
  var queueBody = document.getElementById("crawlQueueBody");
  var queueSummary = document.getElementById("queueSummary");
  var queueCard = document.getElementById("crawlQueueCard");
  var lastFingerprint = "";
  var params = new URLSearchParams(window.location.search);
  var trackedJobId = params.get("job") || jobEl.getAttribute("data-job-id") || "";

  function setSteps(phase) {
    var idx = PHASE_ORDER.indexOf(phase);
    if (phase === "starting") idx = 1;
    steps.forEach(function (step) {
      var key = step.getAttribute("data-phase");
      var stepIdx = PHASE_ORDER.indexOf(key);
      step.classList.toggle("is-current", key === phase || (phase === "starting" && key === "crawling"));
      step.classList.toggle("is-done", idx > stepIdx && phase !== "error" && phase !== "cancelled");
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
        logsEl.innerHTML = '<div class="log-line log-muted">Waiting for a crawl to start…</div>';
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
      ["Saved", stats.new_papers || 0],
      ["Existing", stats.skipped_existing || 0],
      ["No PDF", stats.no_pdf || 0],
      ["Downloaded", stats.pdfs_downloaded || 0],
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

  function apply(data) {
    if (!data || data.kind !== "crawl") return;
    if (trackedJobId && data.job_id && String(data.job_id) !== String(trackedJobId)) return;
    if (trackedJobId && !data.job_id && data.phase === "idle" && !(data.logs && data.logs.length)) return;
    if (data.job_id) trackedJobId = String(data.job_id);
    var active = !!data.active && data.kind === "crawl";
    jobEl.classList.toggle("is-live", active);
    jobEl.classList.toggle("is-done", data.phase === "done");
    jobEl.classList.toggle("is-error", data.phase === "error" || data.phase === "cancelled");
    if (titleEl) {
      titleEl.textContent = active
        ? "Live crawl"
        : data.phase === "done"
          ? "Last crawl"
          : data.phase === "cancelled"
            ? "Crawl stopped"
            : data.phase === "error"
              ? "Crawl failed"
              : "Crawl activity";
    }
    if (queryEl) queryEl.textContent = data.query || "";
    var stopForm = document.getElementById("jobStopForm");
    var stopping = data.phase === "cancelled" && data.active;
    var showStop = (active || stopping) && trackedJobId;
    if (showStop) {
      if (!stopForm && queryEl && queryEl.parentElement) {
        queryEl.parentElement.insertAdjacentHTML(
          "beforeend",
          '<form method="post" action="/crawler/jobs/' +
            trackedJobId +
            '/stop" class="d-inline" id="jobStopForm"><button class="btn btn-sm btn-outline-danger" type="submit">Stop</button></form>'
        );
        stopForm = document.getElementById("jobStopForm");
      } else if (stopForm) {
        stopForm.action = "/crawler/jobs/" + trackedJobId + "/stop";
        stopForm.classList.remove("d-none");
      }
    } else if (stopForm) {
      stopForm.classList.add("d-none");
    }
    if (messageEl) messageEl.textContent = data.message || "Select sources and start a crawl.";
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
    if (data.phase === "done" || data.phase === "error" || data.phase === "cancelled") {
      if (resultEl) resultEl.classList.toggle("d-none", data.phase !== "done");
      renderStats(data.stats || {});
    }
  }

  function renderQueue(data) {
    if (!queueBody || !data) return;
    if (queueSummary) {
      queueSummary.textContent = (data.running || 0) + " running · " + (data.pending || 0) + " pending";
    }
    if (!data.users || !data.users.length) {
      queueBody.innerHTML = '<div class="p-3 paper-meta mb-0">No crawls in the queue.</div>';
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
            return (
              '<li class="list-group-item d-flex justify-content-between gap-3 align-items-center py-2">' +
              '<div class="text-truncate" title="' +
              escapeHtml(item.source_label || item.source || "") +
              '">' +
              escapeHtml(item.source_label || item.source || "") +
              "</div>" +
              '<span class="status-badge status-' +
              (item.status === "running" ? "primary" : "secondary") +
              '">' +
              escapeHtml(label) +
              "</span>" +
              (item.status === "pending" || item.status === "running"
                ? '<form method="post" action="/crawler/jobs/' +
                  item.id +
                  '/stop" class="d-inline"><button class="btn btn-sm btn-outline-danger queue-stop" type="submit">Stop</button></form>'
                : "") +
              "</li>"
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
    var url = "/api/crawl-progress";
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
    fetch("/api/crawl-queue", { headers: { Accept: "application/json" } })
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
    if (!action) return;
    document.querySelectorAll(".queue-stop, #jobStopForm button").forEach(function (btn) {
      btn.disabled = true;
      btn.textContent = "Stopping…";
    });
    fetch(action, {
      method: "POST",
      headers: { Accept: "application/json", "X-Requested-With": "fetch" },
    })
      .then(function (response) {
        return response.json();
      })
      .then(function () {
        pollProgress();
        pollQueue();
      })
      .catch(function () {});
  }

  function onStopSubmit(event) {
    var form = event.target.closest("form");
    if (!form || !form.action.includes("/crawler/jobs/")) return;
    event.preventDefault();
    postStop(form.action);
  }

  jobEl.addEventListener("submit", onStopSubmit);
  if (queueCard) queueCard.addEventListener("submit", onStopSubmit);

  if (params.has("live")) {
    jobEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  pollProgress();
  pollQueue();
  setInterval(pollProgress, 700);
  setInterval(pollQueue, 1500);

  var crawlForm = document.getElementById("crawlForm");
  var crawlGrid = document.getElementById("crawlSourceGrid");
  var crawlFilter = document.getElementById("crawlSourceFilter");
  var crawlSelectAll = document.getElementById("crawlSelectAll");
  var crawlClearAll = document.getElementById("crawlClearAll");
  var crawlSubmit = document.getElementById("crawlSubmit");
  var crawlSummary = document.getElementById("crawlSourceSummary");
  var crawlSelectedChips = document.getElementById("crawlSelectedChips");
  var crawlSourceError = document.getElementById("crawlSourceError");
  var crawlSourceEmpty = document.getElementById("crawlSourceEmpty");
  var crawlFooterHint = document.getElementById("crawlFooterHint");
  var crawlFilterButtons = document.querySelectorAll("[data-crawl-filter]");
  var activeCrawlFilter = "crawlable";

  function sourceItems() {
    if (!crawlGrid) return [];
    return Array.prototype.slice.call(crawlGrid.querySelectorAll(".crawl-source-item"));
  }

  function crawlableInputs() {
    if (!crawlGrid) return [];
    return Array.prototype.slice.call(crawlGrid.querySelectorAll(".crawl-source-input:not(:disabled)"));
  }

  function selectedInputs() {
    return crawlableInputs().filter(function (input) {
      return input.checked;
    });
  }

  function itemMatchesFilter(item) {
    var kind = item.getAttribute("data-kind") || "";
    if (activeCrawlFilter === "crawlable") return kind === "crawlable";
    if (activeCrawlFilter === "other") return kind !== "crawlable";
    return true;
  }

  function itemMatchesSearch(item) {
    if (!crawlFilter) return true;
    var needle = crawlFilter.value.trim().toLowerCase();
    if (!needle) return true;
    var hay = (item.getAttribute("data-filter") || "").toLowerCase();
    return hay.indexOf(needle) !== -1;
  }

  function syncItemSelectedState(item) {
    var input = item.querySelector(".crawl-source-input");
    item.classList.toggle("is-selected", !!(input && input.checked));
  }

  function applySourceFilters() {
    var visibleCount = 0;
    sourceItems().forEach(function (item) {
      var visible = itemMatchesFilter(item) && itemMatchesSearch(item);
      item.classList.toggle("is-filtered-out", !visible);
      if (visible) visibleCount += 1;
    });
    if (crawlSourceEmpty) {
      crawlSourceEmpty.classList.toggle("d-none", visibleCount > 0);
    }
  }

  function renderSelectedChips() {
    if (!crawlSelectedChips) return;
    var selected = selectedInputs();
    if (!selected.length) {
      crawlSelectedChips.classList.add("d-none");
      crawlSelectedChips.innerHTML = "";
      return;
    }
    crawlSelectedChips.classList.remove("d-none");
    crawlSelectedChips.innerHTML = selected
      .map(function (input) {
        var item = input.closest(".crawl-source-item");
        var label = item ? item.querySelector("strong") : null;
        var name = label ? label.textContent : input.value;
        return (
          '<span class="chip">' +
          escapeHtml(name) +
          '<button type="button" class="crawl-selected-chip-remove" data-source="' +
          escapeHtml(input.value) +
          '" aria-label="Remove ' +
          escapeHtml(name) +
          '">×</button></span>'
        );
      })
      .join("");
  }

  function updateCrawlSelectionUi() {
    sourceItems().forEach(syncItemSelectedState);
    var count = selectedInputs().length;
    if (crawlSummary) {
      crawlSummary.textContent =
        count === 0 ? "None selected" : count === 1 ? "1 source selected" : count + " sources selected";
    }
    if (crawlSubmit) {
      crawlSubmit.disabled = count === 0;
      crawlSubmit.textContent = count > 1 ? "Start " + count + " crawls" : "Start crawl";
    }
    if (crawlFooterHint) {
      crawlFooterHint.textContent =
        count === 0
          ? "Pick one or more crawlable sources."
          : count === 1
            ? "One crawl will be queued."
            : count + " parallel crawls will be queued.";
    }
    if (crawlSourceError && count > 0) {
      crawlSourceError.classList.add("d-none");
    }
    renderSelectedChips();
  }

  function visibleCrawlableInputs() {
    return crawlableInputs().filter(function (input) {
      var row = input.closest(".crawl-source-item");
      return row && !row.classList.contains("is-filtered-out") && !row.classList.contains("is-hidden");
    });
  }

  if (crawlSelectAll) {
    crawlSelectAll.addEventListener("click", function () {
      visibleCrawlableInputs().forEach(function (input) {
        input.checked = true;
      });
      updateCrawlSelectionUi();
    });
  }

  if (crawlClearAll) {
    crawlClearAll.addEventListener("click", function () {
      crawlableInputs().forEach(function (input) {
        input.checked = false;
      });
      updateCrawlSelectionUi();
    });
  }

  if (crawlFilter) {
    crawlFilter.addEventListener("input", function () {
      applySourceFilters();
    });
  }

  crawlFilterButtons.forEach(function (button) {
    button.addEventListener("click", function () {
      activeCrawlFilter = button.getAttribute("data-crawl-filter") || "crawlable";
      crawlFilterButtons.forEach(function (peer) {
        var active = peer === button;
        peer.classList.toggle("active", active);
        peer.setAttribute("aria-selected", active ? "true" : "false");
      });
      applySourceFilters();
    });
  });

  if (crawlSelectedChips) {
    crawlSelectedChips.addEventListener("click", function (event) {
      var btn = event.target.closest(".crawl-selected-chip-remove");
      if (!btn) return;
      var slug = btn.getAttribute("data-source");
      var input = crawlGrid && crawlGrid.querySelector('.crawl-source-input[value="' + slug + '"]');
      if (input) input.checked = false;
      updateCrawlSelectionUi();
    });
  }

  if (crawlGrid) {
    crawlGrid.addEventListener("change", function (event) {
      if (event.target && event.target.classList.contains("crawl-source-input")) {
        updateCrawlSelectionUi();
      }
    });
  }

  if (crawlForm) {
    crawlForm.addEventListener("submit", function (event) {
      if (!selectedInputs().length) {
        event.preventDefault();
        if (crawlSourceError) crawlSourceError.classList.remove("d-none");
        if (crawlGrid) crawlGrid.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });
  }

  applySourceFilters();
  updateCrawlSelectionUi();
})();

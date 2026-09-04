(function () {
  var lastFingerprint = "";
  var reloaded = false;

  function formatBytes(value) {
    if (!value) return "";
    var units = ["B", "KB", "MB", "GB"];
    var size = value;
    var i = 0;
    while (size >= 1024 && i < units.length - 1) {
      size /= 1024;
      i += 1;
    }
    return i === 0 ? size + " B" : size.toFixed(1) + " " + units[i];
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderLogs(logs) {
    var logsEl = document.getElementById("downloadLogs");
    if (!logsEl) return;
    if (!logs || !logs.length) {
      if (lastFingerprint !== "empty") {
        lastFingerprint = "empty";
        logsEl.innerHTML = '<div class="log-line log-muted">Waiting for a download to start…</div>';
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

  function updateProgress(data) {
    var panel = document.getElementById("download-progress");
    if (!panel) return;
    var bar = document.getElementById("progress-bar");
    var label = document.getElementById("progress-label");
    var counts = document.getElementById("progress-counts");
    var detail = document.getElementById("progress-detail");
    var meta = document.getElementById("downloadLogMeta");
    var isDownload = !data.kind || data.kind === "download";
    var active = !!data.active && isDownload;
    var show = isDownload && (active || (data.logs && data.logs.length));

    if (active) {
      panel.classList.remove("d-none");
      panel.dataset.active = "true";
    } else if (panel.dataset.active === "true") {
      panel.dataset.active = "false";
      if (!reloaded) {
        reloaded = true;
        window.location.reload();
        return;
      }
    }
    if (show) panel.classList.remove("d-none");
    else if (!active) panel.classList.add("d-none");

    if (label) label.textContent = data.message || "Downloading…";
    if (counts) counts.textContent = data.total ? (data.current || 0) + "/" + data.total : "";
    var percent = data.percent;
    if (bar) {
      bar.style.width = (percent || (active ? 15 : 0)) + "%";
      bar.textContent = percent == null ? (active ? "…" : "") : percent + "%";
      bar.classList.toggle("progress-bar-animated", active);
      bar.classList.toggle("progress-bar-striped", active || percent > 0);
    }
    var bits = [];
    if (data.title) bits.push(data.title);
    if (data.bytes_downloaded) {
      bits.push(
        formatBytes(data.bytes_downloaded) +
          (data.bytes_total ? " / " + formatBytes(data.bytes_total) : "")
      );
    }
    if (detail) detail.textContent = bits.join(" · ");
    if (meta) {
      meta.innerHTML =
        "<span>Saved " +
        (data.downloaded || 0) +
        "</span><span>Failed " +
        (data.failed || 0) +
        "</span><span>Skipped " +
        (data.skipped || 0) +
        "</span>";
    }

    if (isDownload) renderLogs(data.logs || []);

    if (active && data.paper_id) {
      var row = document.querySelector('tr[data-paper-id="' + data.paper_id + '"] .row-progress');
      if (row) {
        var width = percent == null ? 40 : percent;
        row.innerHTML =
          '<div class="progress"><div class="progress-bar progress-bar-striped progress-bar-animated" style="width:' +
          width +
          '%">' +
          (percent == null ? "…" : percent + "%") +
          "</div></div>";
      }
    }
  }

  function pollProgress() {
    fetch("/api/download-progress", { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json();
      })
      .then(updateProgress)
      .catch(function () {});
  }

  var logsEl = document.getElementById("downloadLogs");
  if (logsEl) logsEl.scrollTop = logsEl.scrollHeight;
  pollProgress();
  setInterval(pollProgress, 800);
})();

(function () {
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

  function updateProgress(data) {
    var panel = document.getElementById("download-progress");
    if (!panel) return;
    var bar = document.getElementById("progress-bar");
    var label = document.getElementById("progress-label");
    var counts = document.getElementById("progress-counts");
    var detail = document.getElementById("progress-detail");
    if (data.active) {
      panel.classList.remove("d-none");
      panel.dataset.active = "true";
    } else if (panel.dataset.active === "true") {
      panel.dataset.active = "false";
      window.location.reload();
      return;
    }
    if (label) label.textContent = data.message || "Downloading…";
    if (counts) counts.textContent = (data.current || 0) + "/" + (data.total || 0);
    var percent = data.percent;
    if (bar) {
      bar.style.width = (percent || (data.active ? 15 : 0)) + "%";
      bar.textContent = percent == null ? (data.active ? "…" : "") : percent + "%";
      bar.classList.toggle("progress-bar-animated", !!data.active);
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

    if (data.active && data.paper_id) {
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

  pollProgress();
  setInterval(pollProgress, 800);
})();

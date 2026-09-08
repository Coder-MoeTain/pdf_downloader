(function () {
  var CIRC = 2 * Math.PI * 48;
  var POLL_MS = 2000;
  var paused = false;
  var timer = null;
  var firstSample = true;

  function $(id) {
    return document.getElementById(id);
  }

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function toneLabel(tone) {
    if (tone === "danger") return "Critical";
    if (tone === "warning") return "Elevated";
    if (tone === "info") return "Busy";
    if (tone === "success") return "Healthy";
    return "Idle";
  }

  function worstTone(tones) {
    var rank = { danger: 4, warning: 3, info: 2, success: 1, secondary: 0 };
    var best = "success";
    for (var i = 0; i < tones.length; i++) {
      var t = tones[i] || "secondary";
      if ((rank[t] || 0) > (rank[best] || 0)) best = t;
    }
    return best;
  }

  function setGauge(card, pct, tone) {
    if (!card) return;
    var value = Math.max(0, Math.min(100, Number(pct) || 0));
    card.style.setProperty("--pct", String(value));
    card.dataset.tone = tone || "secondary";
    var arc = card.querySelector(".sys-gauge-arc");
    if (arc) {
      arc.style.strokeDasharray = String(CIRC);
      arc.style.strokeDashoffset = String(CIRC * (1 - value / 100));
    }
  }

  function sparkMarkup(values, maxHint) {
    if (!values || !values.length) return "";
    var max = Math.max(maxHint || 0, 1);
    for (var i = 0; i < values.length; i++) max = Math.max(max, Number(values[i]) || 0);
    var w = 160;
    var h = 48;
    var pad = 3;
    var n = values.length;
    var line = [];
    var area = ["M0 " + (h - pad)];
    for (var j = 0; j < n; j++) {
      var x = n === 1 ? w / 2 : (j / (n - 1)) * w;
      var y = h - pad - ((Number(values[j]) || 0) / max) * (h - pad * 2);
      line.push((j === 0 ? "M" : "L") + x.toFixed(1) + " " + y.toFixed(1));
      area.push("L" + x.toFixed(1) + " " + y.toFixed(1));
    }
    area.push("L" + w + " " + (h - pad) + " Z");
    return (
      '<path class="sys-spark-fill" d="' +
      area.join(" ") +
      '"></path><path class="sys-spark-line" d="' +
      line.join(" ") +
      '" fill="none" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"></path>'
    );
  }

  function renderSpark(svg, values, maxHint) {
    if (!svg) return;
    svg.innerHTML = sparkMarkup(values, maxHint);
  }

  function fillCores(perCpu) {
    var el = $("coreBars");
    if (!el) return;
    if (!perCpu || !perCpu.length) {
      el.innerHTML = '<div class="paper-meta mb-0">No per-core data</div>';
      return;
    }
    el.innerHTML = perCpu
      .map(function (pct, idx) {
        var v = Math.max(0, Math.min(100, Number(pct) || 0));
        var tone = v >= 90 ? "danger" : v >= 75 ? "warning" : v >= 50 ? "info" : "success";
        return (
          '<div class="sys-core" data-tone="' +
          tone +
          '" title="Core ' +
          (idx + 1) +
          ": " +
          v.toFixed(1) +
          '%">' +
          '<div class="sys-core-track"><div class="sys-core-fill" style="height:' +
          v +
          '%"></div></div>' +
          "<span>" +
          (idx + 1) +
          "</span></div>"
        );
      })
      .join("");
  }

  function fillDisks(disks) {
    var list = $("diskCards");
    var mini = $("diskMini");
    if (!list) return;
    if (!disks || !disks.length) {
      list.innerHTML = '<div class="paper-meta mb-0">No volumes reported</div>';
      if (mini) mini.innerHTML = "";
      return;
    }
    list.innerHTML = disks
      .map(function (d) {
        return (
          '<article class="sys-disk-card" data-tone="' +
          esc(d.tone) +
          '">' +
          '<div class="sys-disk-card-top">' +
          "<div><code>" +
          esc(d.mount) +
          '</code><div class="paper-meta mb-0">' +
          esc(d.device) +
          (d.fstype ? " · " + esc(d.fstype) : "") +
          "</div></div>" +
          '<strong class="sys-disk-pct">' +
          d.percent +
          "%</strong></div>" +
          '<div class="progress sys-progress"><div class="progress-bar tone-fill-' +
          esc(d.tone) +
          '" style="width:' +
          d.percent +
          '%"></div></div>' +
          '<div class="sys-disk-meta"><span>' +
          esc(d.used_label) +
          " used</span><span>" +
          esc(d.free_label) +
          " free</span><span>" +
          esc(d.total_label) +
          "</span></div></article>"
        );
      })
      .join("");
    if (mini) {
      mini.innerHTML = disks
        .slice(0, 4)
        .map(function (d) {
          return (
            '<div class="sys-mini-bar" data-tone="' +
            esc(d.tone) +
            '" title="' +
            esc(d.mount) +
            ": " +
            d.percent +
            '%"><span style="width:' +
            d.percent +
            '%"></span></div>'
          );
        })
        .join("");
    }
  }

  function statusPill(status) {
    var raw = String(status || "").toLowerCase();
    var tone = "secondary";
    if (raw.indexOf("run") >= 0) tone = "success";
    else if (raw.indexOf("sleep") >= 0) tone = "info";
    else if (raw.indexOf("stop") >= 0 || raw.indexOf("zombie") >= 0) tone = "warning";
    return '<span class="sys-proc-status tone-pill-' + tone + '">' + esc(status || "—") + "</span>";
  }

  function fillProcs(rows, total) {
    var table = $("procTable");
    var count = $("procCount");
    if (count) count.textContent = total ? total.toLocaleString() + " running" : "—";
    if (!table) return;
    if (!rows || !rows.length) {
      table.innerHTML = '<tr><td colspan="6" class="paper-meta sys-table-empty">No process data</td></tr>';
      return;
    }
    table.innerHTML = rows
      .map(function (p) {
        return (
          "<tr>" +
          '<td><div class="sys-proc-cell"><span class="sys-proc-pid">#' +
          esc(p.pid) +
          '</span><span class="sys-proc-name" title="' +
          esc(p.name) +
          '">' +
          esc(p.name) +
          "</span></div></td>" +
          "<td>" +
          esc(p.user) +
          "</td>" +
          '<td><div class="sys-mini-meter"><strong>' +
          esc(p.cpu) +
          '%</strong><div class="progress"><div class="progress-bar tone-fill-info" style="width:' +
          Math.min(100, Number(p.cpu) || 0) +
          '%"></div></div></div></td>' +
          '<td><div class="sys-mini-meter"><strong>' +
          esc(p.memory) +
          '%</strong><div class="progress"><div class="progress-bar tone-fill-primary" style="width:' +
          Math.min(100, Number(p.memory) || 0) +
          '%"></div></div></div></td>' +
          "<td>" +
          esc(p.rss_label) +
          "</td>" +
          "<td>" +
          statusPill(p.status) +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function applyToneBars(bar, tone) {
    if (!bar) return;
    bar.className = "progress-bar tone-fill-" + (tone || "secondary");
  }

  function setOverallStatus(tone, title, copy) {
    var chip = $("sysStatusChip");
    var banner = $("sysStatusBanner");
    if (chip) {
      chip.dataset.tone = tone;
      if ($("sysStatusLabel")) $("sysStatusLabel").textContent = toneLabel(tone);
    }
    if (banner) banner.dataset.tone = tone;
    if ($("sysBannerTitle")) $("sysBannerTitle").textContent = title;
    if ($("sysBannerCopy")) $("sysBannerCopy").textContent = copy;
  }

  function render(data) {
    var err = $("sysError");
    if (!data || !data.ok) {
      if (err) {
        err.classList.remove("d-none");
        err.textContent = (data && data.error) || "Could not read system metrics.";
      }
      setOverallStatus("danger", "Metrics unavailable", (data && data.error) || "The health API did not return usable data.");
      return;
    }
    if (err) err.classList.add("d-none");

    var cpu = data.cpu || {};
    var mem = data.memory || {};
    var swap = data.swap || {};
    var net = data.network || {};
    var host = data.host || {};
    var storage = data.storage || {};
    var history = data.history || {};
    var primary = storage.primary || null;
    var diskTone = primary ? primary.tone : "secondary";
    var overall = worstTone([cpu.tone, mem.tone, diskTone]);

    setGauge(document.querySelector('.sys-gauge-card[data-metric="cpu"]'), cpu.percent, cpu.tone);
    setGauge(document.querySelector('.sys-gauge-card[data-metric="memory"]'), mem.percent, mem.tone);
    setGauge(
      document.querySelector('.sys-gauge-card[data-metric="storage"]'),
      primary ? primary.percent : 0,
      diskTone
    );
    var netCard = document.querySelector('.sys-gauge-card[data-metric="network"]');
    if (netCard) netCard.dataset.tone = "info";

    if ($("kpiCpu")) $("kpiCpu").textContent = cpu.percent != null ? cpu.percent : "—";
    if ($("kpiMem")) $("kpiMem").textContent = mem.percent != null ? mem.percent : "—";
    if ($("kpiDisk")) $("kpiDisk").textContent = primary ? primary.percent : "—";
    if ($("kpiRecv")) $("kpiRecv").textContent = net.recv_mbps != null ? net.recv_mbps.toFixed(2) : "—";
    if ($("kpiSent")) $("kpiSent").textContent = net.sent_mbps != null ? net.sent_mbps.toFixed(2) : "—";

    if ($("kpiCpuBadge")) $("kpiCpuBadge").textContent = toneLabel(cpu.tone);
    if ($("kpiMemBadge")) $("kpiMemBadge").textContent = toneLabel(mem.tone);
    if ($("kpiDiskBadge")) $("kpiDiskBadge").textContent = toneLabel(diskTone);

    if ($("kpiCpuMeta")) {
      var freq = cpu.frequency && cpu.frequency.current_mhz ? " · " + Math.round(cpu.frequency.current_mhz) + " MHz" : "";
      $("kpiCpuMeta").textContent =
        (cpu.count_physical || "—") + " physical · " + (cpu.count_logical || "—") + " logical" + freq;
    }
    if ($("kpiMemMeta")) {
      $("kpiMemMeta").textContent = (mem.used_label || "—") + " of " + (mem.total_label || "—");
    }
    if ($("kpiDiskMeta")) {
      $("kpiDiskMeta").textContent = primary
        ? primary.used_label + " of " + primary.total_label + " on " + primary.mount
        : "No volume";
    }
    if ($("kpiNetMeta")) {
      $("kpiNetMeta").textContent =
        "Lifetime " + (net.bytes_recv_label || "—") + "  ·  ↑ " + (net.bytes_sent_label || "—");
    }

    renderSpark($("sparkCpu"), history.cpu || [], 100);
    renderSpark($("sparkMem"), history.memory || [], 100);
    var netHist = [];
    var sent = history.net_sent_mbps || [];
    var recv = history.net_recv_mbps || [];
    var len = Math.max(sent.length, recv.length);
    for (var i = 0; i < len; i++) {
      netHist.push(Math.max(Number(sent[i]) || 0, Number(recv[i]) || 0));
    }
    renderSpark($("sparkNet"), netHist, 1);

    if ($("bannerHostname")) $("bannerHostname").textContent = host.hostname || "—";
    if ($("bannerUptime")) $("bannerUptime").textContent = host.uptime_label || "—";
    if ($("bannerPlatform")) {
      var platformLabel = host.system || "";
      if (host.release) platformLabel = (platformLabel ? platformLabel + " " : "") + host.release;
      $("bannerPlatform").textContent = platformLabel || "—";
    }
    if ($("bannerPid")) $("bannerPid").textContent = host.pid != null ? String(host.pid) : "—";

    if ($("factHostname")) $("factHostname").textContent = host.hostname || "—";
    if ($("factPlatform")) $("factPlatform").textContent = host.platform || "—";
    if ($("factCores")) {
      $("factCores").textContent =
        (cpu.count_physical || "—") + " / " + (cpu.count_logical || "—") +
        (cpu.load_avg ? " · load " + cpu.load_avg.join(", ") : "");
    }
    if ($("factPython")) $("factPython").textContent = host.python || "—";
    if ($("factProcs")) {
      $("factProcs").textContent =
        data.processes && data.processes.total != null ? Number(data.processes.total).toLocaleString() : "—";
    }
    if ($("hostUptime")) $("hostUptime").textContent = host.uptime_label || "—";
    if ($("coreSummary")) {
      $("coreSummary").textContent =
        (cpu.count_logical || 0) + " cores · avg " + (cpu.percent != null ? cpu.percent + "%" : "—");
    }

    fillCores(cpu.per_cpu || []);
    fillDisks(storage.disks || []);
    fillProcs((data.processes && data.processes.top) || [], data.processes && data.processes.total);

    if ($("memDetailPct")) $("memDetailPct").textContent = (mem.percent != null ? mem.percent : "—") + "%";
    if ($("memDetailText")) {
      $("memDetailText").textContent =
        (mem.used_label || "—") + " used · " + (mem.available_label || "—") + " available";
    }
    if ($("memBar")) {
      $("memBar").style.width = (mem.percent || 0) + "%";
      applyToneBars($("memBar"), mem.tone);
    }
    if ($("swapDetailPct")) $("swapDetailPct").textContent = (swap.percent != null ? swap.percent : "—") + "%";
    if ($("swapDetailText")) {
      $("swapDetailText").textContent = (swap.used_label || "—") + " of " + (swap.total_label || "—");
    }
    if ($("swapBar")) {
      $("swapBar").style.width = (swap.percent || 0) + "%";
      applyToneBars($("swapBar"), swap.tone);
    }
    if ($("netRecvTotal")) $("netRecvTotal").textContent = net.bytes_recv_label || "—";
    if ($("netSentTotal")) $("netSentTotal").textContent = net.bytes_sent_label || "—";

    var titles = {
      danger: "Attention needed",
      warning: "Elevated resource use",
      info: "Host is busy",
      success: "Host is healthy",
    };
    var copies = {
      danger: "One or more resources are above 90%. Check processes and storage soon.",
      warning: "Usage is elevated. Monitor downloads and crawls if this persists.",
      info: "Workload is moderate. Telemetry is updating live.",
      success: "CPU, memory, and storage are within comfortable ranges.",
    };
    setOverallStatus(overall, titles[overall] || "Host status", copies[overall] || "");

    if ($("sysUpdated")) {
      var stamp = data.collected_at ? new Date(data.collected_at * 1000) : new Date();
      $("sysUpdated").textContent =
        (firstSample ? "Live · " : "Updated ") + stamp.toLocaleTimeString();
    }
    firstSample = false;
  }

  function poll() {
    if (paused) return;
    fetch("/api/system-health", { headers: { Accept: "application/json" } })
      .then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok) throw new Error((body && body.error) || "Request failed");
          return body;
        });
      })
      .then(render)
      .catch(function (err) {
        render({ ok: false, error: err.message || "Request failed" });
      });
  }

  function start() {
    if (timer) clearInterval(timer);
    poll();
    timer = setInterval(poll, POLL_MS);
  }

  var pauseBtn = $("sysPauseBtn");
  if (pauseBtn) {
    pauseBtn.addEventListener("click", function () {
      paused = !paused;
      pauseBtn.setAttribute("aria-pressed", paused ? "true" : "false");
      pauseBtn.textContent = paused ? "Resume" : "Pause";
      pauseBtn.classList.toggle("btn-primary", paused);
      pauseBtn.classList.toggle("btn-outline-secondary", !paused);
      if ($("sysUpdated") && paused) $("sysUpdated").textContent = "Paused";
      if (!paused) poll();
    });
  }

  start();
})();

(function () {
  var CIRC = 2 * Math.PI * 48;
  var POLL_MS = 2000;

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

  function toneClass(tone) {
    return "tone-" + (tone || "secondary");
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

  function sparkPath(values, maxHint) {
    if (!values || !values.length) return "";
    var max = Math.max(maxHint || 0, 1);
    for (var i = 0; i < values.length; i++) max = Math.max(max, Number(values[i]) || 0);
    var w = 120;
    var h = 36;
    var pad = 2;
    var n = values.length;
    var parts = [];
    for (var j = 0; j < n; j++) {
      var x = n === 1 ? w / 2 : (j / (n - 1)) * w;
      var y = h - pad - ((Number(values[j]) || 0) / max) * (h - pad * 2);
      parts.push((j === 0 ? "M" : "L") + x.toFixed(1) + " " + y.toFixed(1));
    }
    return parts.join(" ");
  }

  function renderSpark(svg, values, maxHint) {
    if (!svg) return;
    var d = sparkPath(values, maxHint);
    svg.innerHTML = d
      ? '<path class="sys-spark-line" d="' + d + '" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>'
      : "";
  }

  function fillCores(perCpu) {
    var el = $("coreBars");
    if (!el) return;
    if (!perCpu || !perCpu.length) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = perCpu
      .map(function (pct, idx) {
        var v = Math.max(0, Math.min(100, Number(pct) || 0));
        return (
          '<div class="sys-core" title="Core ' +
          (idx + 1) +
          ": " +
          v +
          '%">' +
          '<div class="sys-core-fill" style="height:' +
          v +
          '%"></div>' +
          "<span>" +
          (idx + 1) +
          "</span></div>"
        );
      })
      .join("");
  }

  function fillDisks(disks) {
    var table = $("diskTable");
    var mini = $("diskMini");
    if (!table) return;
    if (!disks || !disks.length) {
      table.innerHTML = '<tr><td colspan="5" class="paper-meta">No volumes reported</td></tr>';
      if (mini) mini.innerHTML = "";
      return;
    }
    table.innerHTML = disks
      .map(function (d) {
        return (
          "<tr>" +
          "<td><code>" +
          esc(d.mount) +
          "</code><div class=\"paper-meta mb-0\">" +
          esc(d.device) +
          "</div></td>" +
          "<td>" +
          esc(d.fstype || "—") +
          "</td>" +
          "<td>" +
          esc(d.used_label) +
          "</td>" +
          "<td>" +
          esc(d.free_label) +
          "</td>" +
          '<td><div class="sys-cap"><span class="' +
          toneClass(d.tone) +
          '">' +
          d.percent +
          "%</span>" +
          '<div class="progress"><div class="progress-bar tone-fill-' +
          esc(d.tone) +
          '" style="width:' +
          d.percent +
          '%"></div></div>' +
          '<div class="paper-meta mb-0">' +
          esc(d.total_label) +
          "</div></div></td>" +
          "</tr>"
        );
      })
      .join("");
    if (mini) {
      mini.innerHTML = disks
        .slice(0, 4)
        .map(function (d) {
          return (
            '<div class="sys-mini-bar" title="' +
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

  function fillProcs(rows, total) {
    var table = $("procTable");
    var count = $("procCount");
    if (count) count.textContent = total ? total + " running" : "—";
    if (!table) return;
    if (!rows || !rows.length) {
      table.innerHTML = '<tr><td colspan="7" class="paper-meta">No process data</td></tr>';
      return;
    }
    table.innerHTML = rows
      .map(function (p) {
        return (
          "<tr>" +
          "<td>" +
          esc(p.pid) +
          "</td>" +
          "<td class=\"sys-proc-name\">" +
          esc(p.name) +
          "</td>" +
          "<td>" +
          esc(p.user) +
          "</td>" +
          "<td>" +
          esc(p.cpu) +
          "</td>" +
          "<td>" +
          esc(p.memory) +
          "</td>" +
          "<td>" +
          esc(p.rss_label) +
          "</td>" +
          "<td>" +
          esc(p.status) +
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

  function render(data) {
    var err = $("sysError");
    if (!data || !data.ok) {
      if (err) {
        err.classList.remove("d-none");
        err.textContent = (data && data.error) || "Could not read system metrics.";
      }
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

    setGauge(document.querySelector('.sys-gauge-card[data-metric="cpu"]'), cpu.percent, cpu.tone);
    setGauge(document.querySelector('.sys-gauge-card[data-metric="memory"]'), mem.percent, mem.tone);
    setGauge(
      document.querySelector('.sys-gauge-card[data-metric="storage"]'),
      primary ? primary.percent : 0,
      primary ? primary.tone : "secondary"
    );

    if ($("kpiCpu")) $("kpiCpu").textContent = cpu.percent != null ? cpu.percent : "—";
    if ($("kpiMem")) $("kpiMem").textContent = mem.percent != null ? mem.percent : "—";
    if ($("kpiDisk")) $("kpiDisk").textContent = primary ? primary.percent : "—";
    if ($("kpiRecv")) $("kpiRecv").textContent = net.recv_mbps != null ? net.recv_mbps.toFixed(2) : "—";
    if ($("kpiSent")) $("kpiSent").textContent = net.sent_mbps != null ? net.sent_mbps.toFixed(2) : "—";

    if ($("kpiCpuMeta")) {
      var freq = cpu.frequency && cpu.frequency.current_mhz ? " · " + cpu.frequency.current_mhz + " MHz" : "";
      $("kpiCpuMeta").textContent =
        (cpu.count_physical || "—") + " phys / " + (cpu.count_logical || "—") + " logical" + freq;
    }
    if ($("kpiMemMeta")) {
      $("kpiMemMeta").textContent = (mem.used_label || "—") + " / " + (mem.total_label || "—");
    }
    if ($("kpiDiskMeta")) {
      $("kpiDiskMeta").textContent = primary
        ? primary.used_label + " / " + primary.total_label + " on " + primary.mount
        : "No volume";
    }
    if ($("kpiNetMeta")) {
      $("kpiNetMeta").textContent =
        "Lifetime " + (net.bytes_recv_label || "—") + " · ↑ " + (net.bytes_sent_label || "—");
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

    if ($("factHostname")) $("factHostname").textContent = host.hostname || "—";
    if ($("factPlatform")) $("factPlatform").textContent = host.platform || "—";
    if ($("factCores")) {
      $("factCores").textContent =
        (cpu.count_physical || "—") + " / " + (cpu.count_logical || "—") +
        (cpu.load_avg ? " · load " + cpu.load_avg.join(", ") : "");
    }
    if ($("factPython")) $("factPython").textContent = host.python || "—";
    if ($("factPid")) $("factPid").textContent = host.pid != null ? String(host.pid) : "—";
    if ($("factProcs")) $("factProcs").textContent = data.processes && data.processes.total != null ? String(data.processes.total) : "—";
    if ($("hostUptime")) $("hostUptime").textContent = host.uptime_label ? "Up " + host.uptime_label : "—";

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
      $("swapDetailText").textContent =
        (swap.used_label || "—") + " / " + (swap.total_label || "—");
    }
    if ($("swapBar")) {
      $("swapBar").style.width = (swap.percent || 0) + "%";
      applyToneBars($("swapBar"), swap.tone);
    }
    if ($("netRecvTotal")) $("netRecvTotal").textContent = net.bytes_recv_label || "—";
    if ($("netSentTotal")) $("netSentTotal").textContent = net.bytes_sent_label || "—";

    if ($("sysUpdated")) {
      var stamp = data.collected_at ? new Date(data.collected_at * 1000) : new Date();
      $("sysUpdated").textContent = "Updated " + stamp.toLocaleTimeString();
    }
  }

  function poll() {
    fetch("/api/system-health", { headers: { Accept: "application/json" } })
      .then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok) {
            throw new Error((body && body.error) || "Request failed");
          }
          return body;
        });
      })
      .then(render)
      .catch(function (err) {
        render({ ok: false, error: err.message || "Request failed" });
      });
  }

  poll();
  setInterval(poll, POLL_MS);
})();

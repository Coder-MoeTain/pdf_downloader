(function () {
  var onlineBody = document.getElementById("onlineUsersBody");
  var usageBody = document.getElementById("usageLogBody");
  var countEl = document.getElementById("onlineCount");
  if (!onlineBody || !usageBody) return;

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function actionTone(action) {
    if (action === "login") return "success";
    if (action === "logout") return "danger";
    if (action === "search") return "primary";
    return "info";
  }

  function renderOnline(rows, count, minutes) {
    if (countEl) {
      countEl.textContent = count + " in the last " + (minutes || 5) + " minutes";
    }
    if (!rows || !rows.length) {
      onlineBody.innerHTML = '<tr><td colspan="4" class="paper-meta">No one is online right now.</td></tr>';
      return;
    }
    onlineBody.innerHTML = rows
      .map(function (person) {
        return (
          "<tr><td><div class=\"online-user fw-semibold\"><span class=\"nav-live-dot\" aria-hidden=\"true\"></span>" +
          escapeHtml(person.name || "") +
          '</div><div class="paper-meta mb-0">' +
          escapeHtml(person.email || "") +
          "</div></td><td><span class=\"role-badge role-" +
          escapeHtml(person.role || "user") +
          '">' +
          escapeHtml(person.role || "user") +
          '</span></td><td class="paper-meta">' +
          escapeHtml(person.ago || "") +
          '</td><td class="paper-meta">' +
          escapeHtml(person.path || "—") +
          "</td></tr>"
        );
      })
      .join("");
  }

  function renderEvents(rows) {
    if (!rows || !rows.length) {
      usageBody.innerHTML =
        '<tr><td colspan="5" class="paper-meta">No usage yet. Sign-ins, searches, and downloads will appear here.</td></tr>';
      return;
    }
    usageBody.innerHTML = rows
      .map(function (row) {
        return (
          '<tr><td class="paper-meta text-nowrap">' +
          escapeHtml(row.time || "") +
          "</td><td>" +
          escapeHtml(row.user || "") +
          '</td><td><span class="status-badge status-' +
          actionTone(row.action) +
          '">' +
          escapeHtml(row.action_label || row.action || "") +
          '</span></td><td class="paper-meta">' +
          escapeHtml(row.detail || "—") +
          '</td><td class="paper-meta">' +
          escapeHtml(row.ip || "—") +
          "</td></tr>"
        );
      })
      .join("");
  }

  function poll() {
    fetch("/api/activity", { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json();
      })
      .then(function (data) {
        renderOnline(data.online || [], data.online_count || 0, data.window_minutes);
        renderEvents(data.events || []);
      })
      .catch(function () {});
  }

  poll();
  setInterval(poll, 4000);
})();

(function () {
  var root = document.getElementById("cfpPage");
  if (!root) return;
  var empty = root.getAttribute("data-empty") === "true";
  var running = root.getAttribute("data-running") === "true";
  var autoStart = root.getAttribute("data-auto-start") === "true";
  var alertEl = document.getElementById("cfpRefreshAlert");
  var tries = 0;
  var started = false;

  function showMsg(text) {
    if (!alertEl || !text) return;
    alertEl.hidden = false;
    alertEl.textContent = text;
  }

  function poll() {
    tries += 1;
    fetch("/api/cfp-status", { credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (r.status === 401) throw new Error("auth");
        return r.json();
      })
      .then(function (data) {
        if (data.message) showMsg(data.message);
        if (data.running) {
          if (tries < 120) window.setTimeout(poll, 2000);
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
          showMsg("Sign in again to load Call for Papers.");
          return;
        }
        if (!started && empty) {
          startAndPoll();
          return;
        }
        if (tries < 120) window.setTimeout(poll, 3000);
      });
  }

  function startAndPoll() {
    if (started) {
      poll();
      return;
    }
    started = true;
    showMsg("Refreshing from WikiCFP in the background…");
    fetch("/api/cfp-refresh", {
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
          showMsg("Sign in again to load Call for Papers.");
          return;
        }
        showMsg("Could not start refresh. Retrying…");
        poll();
      });
  }

  if (autoStart || (empty && !running)) {
    window.setTimeout(startAndPoll, 100);
  } else if (running || empty) {
    window.setTimeout(poll, 500);
  }
})();

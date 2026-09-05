(function () {
  if (!document.body.classList.contains("page-library-live")) return;

  var REFRESH_MS = 12000;

  function uiBusy() {
    if (document.querySelector(".modal.show")) return true;
    if (document.querySelector("dialog[open]")) return true;
    return false;
  }

  function maybeRefresh() {
    if (uiBusy()) return;
    window.location.reload();
  }

  window.setInterval(maybeRefresh, REFRESH_MS);
})();

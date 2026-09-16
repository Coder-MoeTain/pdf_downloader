(function () {
  var sidebar = document.getElementById("appSidebar");
  var backdrop = document.querySelector("[data-sidebar-backdrop]");
  document.querySelectorAll("[data-sidebar-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      var open = sidebar && sidebar.classList.toggle("is-open");
      if (backdrop) {
        backdrop.hidden = !open;
        backdrop.classList.toggle("is-on", !!open);
      }
      button.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });
  if (backdrop) {
    backdrop.addEventListener("click", function () {
      if (sidebar) sidebar.classList.remove("is-open");
      backdrop.hidden = true;
      backdrop.classList.remove("is-on");
    });
  }
  document.querySelectorAll("select[data-auto-submit]").forEach(function (select) {
    select.addEventListener("change", function () {
      if (select.form) select.form.submit();
    });
  });
  document.querySelectorAll("img[data-fallback-src]").forEach(function (img) {
    img.addEventListener("error", function () {
      var fallback = img.getAttribute("data-fallback-src");
      if (fallback && img.src !== fallback) img.src = fallback;
    });
  });
})();

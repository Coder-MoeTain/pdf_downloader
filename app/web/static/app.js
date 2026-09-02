(function () {
  document.querySelectorAll("form[data-busy]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector("[type=submit]");
      if (!button) return;
      button.disabled = true;
      button.dataset.originalText = button.textContent;
      button.textContent = form.getAttribute("data-busy") || "Working…";
    });
  });

  document.querySelectorAll(".topic-chip").forEach(function (chip) {
    chip.addEventListener("click", function () {
      var input = document.querySelector("input[name=query]");
      if (input) {
        input.value = chip.getAttribute("data-query") || chip.textContent;
        input.focus();
      }
    });
  });

  var modalEl = document.getElementById("pdfPreviewModal");
  var frame = document.getElementById("pdfPreviewFrame");
  var title = document.getElementById("pdfPreviewTitle");
  if (modalEl && frame && typeof bootstrap !== "undefined") {
    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    document.querySelectorAll(".preview-btn").forEach(function (button) {
      button.addEventListener("click", function () {
        title.textContent = button.getAttribute("data-preview-title") || "PDF preview";
        frame.src = button.getAttribute("data-preview-url") || "";
        modal.show();
      });
    });
    modalEl.addEventListener("hidden.bs.modal", function () {
      frame.src = "about:blank";
    });
  }
})();

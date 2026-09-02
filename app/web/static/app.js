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

  document.querySelectorAll("[data-star-rating], .star-rating").forEach(function (root) {
    root.querySelectorAll(".star-btn").forEach(function (button) {
      button.addEventListener("click", function () {
        var paperId = root.getAttribute("data-paper-id");
        var value = Number(button.getAttribute("data-value"));
        var current = Number(root.getAttribute("data-rating") || 0);
        if (value === current) value = 0;
        fetch("/api/papers/" + paperId + "/rating", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ rating: value }),
        })
          .then(function (response) {
            return response.json();
          })
          .then(function (data) {
            if (!data.ok) return;
            var rating = data.rating || 0;
            root.setAttribute("data-rating", String(rating));
            root.querySelectorAll(".star-btn").forEach(function (star) {
              var on = Number(star.getAttribute("data-value")) <= rating;
              star.classList.toggle("on", on);
              star.setAttribute("aria-pressed", on ? "true" : "false");
            });
          })
          .catch(function () {});
      });
    });
  });
})();

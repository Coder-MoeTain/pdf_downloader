(function () {
  document.querySelectorAll("form[data-busy]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var button = form.querySelector("[type=submit]");
      if (!button) return;
      button.dataset.originalText = button.textContent;
      button.textContent = form.getAttribute("data-busy") || "Working…";
      window.setTimeout(function () {
        button.disabled = true;
      }, 0);
    });
  });

  document.querySelectorAll(".topic-chip, .recent-query").forEach(function (chip) {
    chip.addEventListener("click", function () {
      var input = document.querySelector("input[name=query]");
      if (input) {
        input.value = chip.getAttribute("data-query") || chip.textContent;
        input.focus();
      }
    });
  });

  var abstractModalEl = document.getElementById("abstractPreviewModal");
  if (abstractModalEl && typeof bootstrap !== "undefined") {
    var abstractModal = bootstrap.Modal.getOrCreateInstance(abstractModalEl);
    var abstractTitle = document.getElementById("abstractPreviewTitle");
    var abstractMeta = document.getElementById("abstractPreviewMeta");
    var abstractText = document.getElementById("abstractPreviewText");
    document.querySelectorAll(".abstract-btn").forEach(function (button) {
      button.addEventListener("click", function () {
        var template = button.nextElementSibling;
        var text = "";
        if (template && template.tagName === "TEMPLATE") {
          text = (template.content.textContent || "").trim();
        }
        if (abstractTitle) abstractTitle.textContent = button.getAttribute("data-abstract-title") || "Abstract";
        if (abstractMeta) {
          var meta = (button.getAttribute("data-abstract-meta") || "").trim();
          abstractMeta.textContent = meta;
          abstractMeta.hidden = !meta;
        }
        if (abstractText) {
          abstractText.textContent = text || "No abstract is stored for this paper.";
          abstractText.classList.toggle("is-empty", !text);
        }
        abstractModal.show();
      });
    });
  }

  var modalEl = document.getElementById("pdfPreviewModal");
  var title = document.getElementById("pdfPreviewTitle");
  var wrap = document.getElementById("pdfCanvasWrap");
  var statusEl = document.getElementById("pdfViewerStatus");
  var pageInfo = document.getElementById("pdfPageInfo");
  var downloadLink = document.getElementById("pdfDownloadLink");
  var pdfDoc = null;
  var pdfPage = 1;
  var pendingPreview = null;
  var pdfjsReady = typeof pdfjsLib !== "undefined";
  if (pdfjsReady) {
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
  }

  function setViewerStatus(text, show) {
    if (!statusEl) return;
    statusEl.hidden = !show;
    statusEl.textContent = text || "";
  }

  function clearViewer() {
    pdfDoc = null;
    pdfPage = 1;
    pendingPreview = null;
    if (wrap) {
      wrap.innerHTML = "";
      wrap.hidden = true;
    }
    if (pageInfo) pageInfo.textContent = "–";
    if (downloadLink) {
      downloadLink.hidden = true;
      downloadLink.removeAttribute("href");
    }
  }

  function viewerWidth() {
    var body = document.querySelector(".pdf-modal-body");
    var width = body && body.clientWidth ? body.clientWidth - 24 : 980;
    return Math.min(980, Math.max(320, width));
  }

  function renderPdfPage(num) {
    if (!pdfDoc || !wrap) return;
    pdfDoc.getPage(num).then(function (page) {
      var base = page.getViewport({ scale: 1 });
      var viewport = page.getViewport({ scale: viewerWidth() / base.width });
      wrap.hidden = false;
      wrap.innerHTML = "";
      var canvas = document.createElement("canvas");
      var context = canvas.getContext("2d");
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      wrap.appendChild(canvas);
      setViewerStatus("", false);
      if (pageInfo) pageInfo.textContent = num + " / " + pdfDoc.numPages;
      return page.render({ canvasContext: context, viewport: viewport }).promise;
    });
  }

  function loadPdf(url) {
    if (!pdfjsReady) {
      setViewerStatus("PDF preview is unavailable. Use Download instead.", true);
      return;
    }
    setViewerStatus("Loading PDF…", true);
    pdfjsLib
      .getDocument({ url: url, withCredentials: false })
      .promise.then(function (doc) {
        pdfDoc = doc;
        pdfPage = 1;
        renderPdfPage(1);
      })
      .catch(function () {
        setViewerStatus("Could not render this PDF. Use Download instead.", true);
      });
  }

  function openPdfPreview(url, paperTitle, downloadUrl) {
    if (!modalEl || typeof bootstrap === "undefined") return;
    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    pendingPreview = url;
    if (title) title.textContent = paperTitle || "PDF preview";
    if (downloadLink && downloadUrl) {
      downloadLink.href = downloadUrl;
      downloadLink.hidden = false;
    }
    setViewerStatus("Loading PDF…", true);
    if (wrap) {
      wrap.innerHTML = "";
      wrap.hidden = true;
    }
    modal.show();
  }

  if (modalEl) {
    document.querySelectorAll(".preview-btn").forEach(function (button) {
      button.addEventListener("click", function () {
        var url = button.getAttribute("data-preview-url") || "";
        var downloadUrl = url.replace(/\/preview$/, "/pdf");
        openPdfPreview(url, button.getAttribute("data-preview-title"), downloadUrl);
      });
    });
    modalEl.addEventListener("shown.bs.modal", function () {
      if (!pendingPreview) return;
      var url = pendingPreview;
      pendingPreview = null;
      loadPdf(url);
    });
    modalEl.addEventListener("hidden.bs.modal", clearViewer);
    var prevBtn = document.getElementById("pdfPrevPage");
    var nextBtn = document.getElementById("pdfNextPage");
    if (prevBtn) {
      prevBtn.addEventListener("click", function () {
        if (!pdfDoc || pdfPage <= 1) return;
        pdfPage -= 1;
        renderPdfPage(pdfPage);
      });
    }
    if (nextBtn) {
      nextBtn.addEventListener("click", function () {
        if (!pdfDoc || pdfPage >= pdfDoc.numPages) return;
        pdfPage += 1;
        renderPdfPage(pdfPage);
      });
    }
  }

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.getAttribute("data-confirm") || "Are you sure?")) {
        event.preventDefault();
      }
    });
  });

  var sourceFilter = document.getElementById("sourceFilter");
  if (sourceFilter) {
    sourceFilter.addEventListener("input", function () {
      var query = sourceFilter.value.toLowerCase();
      document.querySelectorAll(".source-row").forEach(function (row) {
        var hay = (row.getAttribute("data-filter") || row.textContent || "").toLowerCase();
        row.hidden = query !== "" && hay.indexOf(query) === -1;
      });
    });
  }

  var sourceModalEl = document.getElementById("sourceModal");
  var sourceForm = document.getElementById("sourceForm");
  if (sourceModalEl && sourceForm && typeof bootstrap !== "undefined") {
    var sourceModal = bootstrap.Modal.getOrCreateInstance(sourceModalEl);
    var titleEl = document.getElementById("sourceModalTitle");
    var slugWrap = document.getElementById("sourceSlugWrap");
    var keyEnvWrap = document.getElementById("sourceKeyEnvWrap");
    var clearWrap = document.getElementById("sourceClearKeyWrap");

    function fillSourceForm(source) {
      sourceForm.reset();
      document.getElementById("sourceClearKey").checked = false;
      if (!source) {
        sourceForm.action = "/settings/sources";
        if (titleEl) titleEl.textContent = "Add source";
        if (slugWrap) slugWrap.hidden = false;
        if (keyEnvWrap) keyEnvWrap.hidden = false;
        if (clearWrap) clearWrap.hidden = true;
        document.getElementById("sourceEnabled").checked = true;
        document.getElementById("sourceSlug").required = true;
        return;
      }
      sourceForm.action = "/settings/sources/" + source.id;
      if (titleEl) titleEl.textContent = "Edit " + source.display_name;
      if (slugWrap) slugWrap.hidden = true;
      if (keyEnvWrap) keyEnvWrap.hidden = true;
      if (clearWrap) clearWrap.hidden = false;
      document.getElementById("sourceSlug").required = false;
      document.getElementById("sourceName").value = source.display_name || "";
      document.getElementById("sourceDesc").value = source.description || "";
      document.getElementById("sourceHome").value = source.homepage_url || "";
      document.getElementById("sourceApi").value = source.api_base_url || "";
      document.getElementById("sourceDocs").value = source.docs_url || "";
      document.getElementById("sourceRps").value = source.requests_per_second || 5;
      document.getElementById("sourceRpsKey").value = source.requests_per_second_with_key || "";
      document.getElementById("sourceNotes").value = source.notes || "";
      document.getElementById("sourceEnabled").checked = !!source.enabled;
      document.getElementById("sourceRequiresKey").checked = !!source.requires_key;
      document.getElementById("sourceKey").value = "";
      document.getElementById("sourceKey").placeholder = source.has_key ? "Leave blank to keep current" : "Paste API key";
    }

    document.querySelectorAll("[data-source-create]").forEach(function (button) {
      button.addEventListener("click", function () {
        fillSourceForm(null);
        sourceModal.show();
      });
    });
    document.querySelectorAll("[data-source-edit]").forEach(function (button) {
      button.addEventListener("click", function () {
        var id = button.getAttribute("data-source-id");
        fillSourceForm(null);
        fetch("/api/sources/" + id)
          .then(function (response) { return response.json(); })
          .then(function (data) {
            if (!data.ok) return;
            fillSourceForm(data.source);
            sourceModal.show();
          })
          .catch(function () {});
      });
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

  var navAside = document.querySelector(".nav-aside");
  function renderNavLive(data) {
    if (!navAside) return;
    var existing = navAside.querySelector(".nav-live");
    if (!data || !data.active) {
      if (existing) existing.remove();
      return;
    }
    var href = data.kind === "search" ? "/search?live=1" : "/downloads";
    var label =
      data.kind === "search"
        ? "Searching…"
        : "Downloading " + (data.current || 0) + "/" + (data.total || 0);
    if (!existing) {
      existing = document.createElement("a");
      existing.className = "nav-live";
      navAside.insertBefore(existing, navAside.firstChild);
    }
    existing.href = href;
    existing.title = data.kind === "search" ? "Search in progress" : "Downloads in progress";
    existing.innerHTML = '<span class="nav-live-dot" aria-hidden="true"></span>' + label;
  }
  function pollNavLive() {
    fetch("/api/search-progress", { headers: { Accept: "application/json" } })
      .then(function (response) {
        return response.json();
      })
      .then(renderNavLive)
      .catch(function () {});
  }
  pollNavLive();
  setInterval(pollNavLive, 1000);

  document.querySelectorAll("[data-topics-toggle]").forEach(function (button) {
    var panel = button.closest(".lib-topics");
    function syncLabel() {
      var expanded = panel && panel.classList.contains("is-expanded");
      button.textContent = expanded ? button.getAttribute("data-less") : button.getAttribute("data-more");
    }
    syncLabel();
    button.addEventListener("click", function () {
      if (!panel) return;
      panel.classList.toggle("is-expanded");
      syncLabel();
    });
  });
})();

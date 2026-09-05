(function () {
  var THEME_KEY = "cs-theme";

  function resolveTheme() {
    var stored = localStorage.getItem(THEME_KEY);
    if (stored === "dark" || stored === "light") return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-bs-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
    document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
      var isDark = theme === "dark";
      button.setAttribute("aria-pressed", isDark ? "true" : "false");
      button.setAttribute("aria-label", isDark ? "Switch to light mode" : "Switch to dark mode");
      button.title = isDark ? "Switch to light mode" : "Switch to dark mode";
    });
  }

  applyTheme(resolveTheme());

  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      var next = document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark";
      applyTheme(next);
    });
  });

  document.querySelectorAll("form[data-busy]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      window.setTimeout(function () {
        if (event.defaultPrevented) return;
        var button = form.querySelector("[type=submit]");
        if (!button) return;
        button.dataset.originalText = button.textContent;
        button.textContent = form.getAttribute("data-busy") || "Working…";
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

  var paperDetailDialog = document.getElementById("paperDetailDialog");
  var paperDetailTitle = document.getElementById("paperDetailTitle");
  var paperDetailBody = document.getElementById("paperDetailBody");

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderPaperDetailBody(detail) {
    var categories = detail.categories || [];
    var categoryHtml = categories.length
      ? categories
          .map(function (tag) {
            return '<span class="paper-detail-tag">' + escapeHtml(tag) + "</span>";
          })
          .join("")
      : "—";
    var stars = "";
    var rating = Number(detail.rating || 0);
    var paperId = detail.paper_id;
    for (var n = 1; n <= 5; n += 1) {
      stars +=
        '<button type="button" class="star-btn ' +
        (rating >= n ? "on" : "") +
        '" data-value="' +
        n +
        '" aria-label="' +
        n +
        ' star' +
        (n === 1 ? "" : "s") +
        '" aria-pressed="' +
        (rating >= n ? "true" : "false") +
        '">★</button>';
    }
    return (
      '<div class="paper-detail-dl">' +
      '<div class="paper-detail-row"><div class="paper-detail-label">Authors</div><div class="paper-detail-value">' +
      escapeHtml(detail.authors || "—") +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Year</div><div class="paper-detail-value">' +
      escapeHtml(detail.year == null || detail.year === "" ? "—" : detail.year) +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Rating</div><div class="paper-detail-value"><div class="star-rating" data-paper-id="' +
      escapeHtml(paperId) +
      '" data-rating="' +
      rating +
      '" role="group" aria-label="Your rating">' +
      stars +
      "</div></div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Status</div><div class="paper-detail-value"><span class="status-badge status-' +
      escapeHtml(detail.status_tone || "secondary") +
      '">' +
      escapeHtml(detail.status_label || "Unknown") +
      "</span></div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Categories</div><div class="paper-detail-value">' +
      (categories.length ? '<div class="paper-detail-tags">' + categoryHtml + "</div>" : "—") +
      "</div></div></div>"
    );
  }

  function openPaperDetail(button) {
    if (!paperDetailDialog) return;
    var detail = {};
    try {
      detail = JSON.parse(button.getAttribute("data-detail") || "{}");
    } catch (error) {
      detail = {};
    }
    if (paperDetailTitle) {
      paperDetailTitle.textContent = button.getAttribute("data-detail-title") || "Paper";
    }
    if (paperDetailBody) {
      paperDetailBody.innerHTML = renderPaperDetailBody(detail);
    }
    if (typeof paperDetailDialog.showModal === "function") {
      paperDetailDialog.showModal();
    }
  }

  document.addEventListener("click", function (event) {
    var detailButton = event.target.closest(".detail-btn");
    if (detailButton) {
      event.preventDefault();
      openPaperDetail(detailButton);
      return;
    }
    if (event.target.closest("[data-close-detail]")) {
      paperDetailDialog && paperDetailDialog.close();
      return;
    }
    if (paperDetailDialog && event.target === paperDetailDialog) {
      paperDetailDialog.close();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && paperDetailDialog && paperDetailDialog.open) {
      paperDetailDialog.close();
    }
  });

  var modalEl = document.getElementById("pdfPreviewModal");
  var title = document.getElementById("pdfPreviewTitle");
  var frame = document.getElementById("pdfPreviewFrame");
  var statusEl = document.getElementById("pdfViewerStatus");
  var downloadLink = document.getElementById("pdfDownloadLink");
  var pendingPreview = null;

  function setViewerStatus(text, show) {
    if (!statusEl) return;
    statusEl.hidden = !show;
    statusEl.textContent = text || "";
  }

  function clearViewer() {
    pendingPreview = null;
    if (frame) {
      frame.onload = null;
      frame.onerror = null;
      frame.src = "about:blank";
    }
    setViewerStatus("", false);
    if (downloadLink) {
      downloadLink.hidden = true;
      downloadLink.removeAttribute("href");
    }
  }

  function loadPdf(url) {
    if (!frame) return;
    setViewerStatus("Loading PDF…", true);
    frame.onload = function () {
      setViewerStatus("", false);
    };
    frame.onerror = function () {
      setViewerStatus("Could not load this PDF. Use Download instead.", true);
    };
    frame.src = url;
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
    if (frame) frame.src = "about:blank";
    setViewerStatus("Loading PDF…", true);
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

  document.addEventListener("click", function (event) {
    var button = event.target.closest(".star-rating .star-btn");
    if (!button) return;
    var root = button.closest(".star-rating");
    if (!root) return;
    var paperId = root.getAttribute("data-paper-id");
    if (!paperId) return;
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
        document.querySelectorAll('.star-rating[data-paper-id="' + paperId + '"]').forEach(function (group) {
          group.setAttribute("data-rating", String(rating));
          group.querySelectorAll(".star-btn").forEach(function (star) {
            var on = Number(star.getAttribute("data-value")) <= rating;
            star.classList.toggle("on", on);
            star.setAttribute("aria-pressed", on ? "true" : "false");
          });
        });
      })
      .catch(function () {});
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
    existing.innerHTML = '<span class="nav-live-dot" aria-hidden="true"></span><span class="nav-link-label">' + label + "</span>";
  }
  function pollNavLive() {
    Promise.all([
      fetch("/api/download-progress", { headers: { Accept: "application/json" } }).then(function (response) {
        return response.json();
      }),
      fetch("/api/search-progress", { headers: { Accept: "application/json" } }).then(function (response) {
        return response.json();
      }),
    ])
      .then(function (results) {
        var download = results[0] || {};
        var search = results[1] || {};
        if (download.active && download.kind !== "search") {
          renderNavLive(download);
        } else if (search.active && search.kind === "search") {
          renderNavLive(search);
        } else {
          renderNavLive({ active: false });
        }
      })
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

(function () {
  var THEME_KEY = "cs-theme";

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  document.querySelectorAll("form").forEach(function (form) {
    var method = (form.getAttribute("method") || "get").toLowerCase();
    if (method !== "post" && method !== "put" && method !== "patch" && method !== "delete") return;
    if (form.querySelector('input[name="csrf_token"]')) return;
    var token = csrfToken();
    if (!token) return;
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = "csrf_token";
    input.value = token;
    form.appendChild(input);
  });

  var originalFetch = window.fetch;
  window.fetch = function (input, init) {
    init = init || {};
    var method = String((init.method || (typeof input === "object" && input && input.method) || "GET")).toUpperCase();
    if (method === "POST" || method === "PUT" || method === "PATCH" || method === "DELETE") {
      var headers = new Headers(init.headers || (typeof input === "object" && input && input.headers) || {});
      if (!headers.has("X-CSRF-Token") && !headers.has("x-csrf-token")) {
        headers.set("X-CSRF-Token", csrfToken());
      }
      init.headers = headers;
    }
    return originalFetch.call(this, input, init);
  };

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

  var citeModalEl = document.getElementById("citePreviewModal");
  if (citeModalEl && typeof bootstrap !== "undefined") {
    var citeModal = bootstrap.Modal.getOrCreateInstance(citeModalEl);
    var citeTitle = document.getElementById("citePreviewTitle");
    var citeText = document.getElementById("citePreviewText");
    var citeCopyBtn = document.getElementById("citeCopyBtn");
    var citeTabs = citeModalEl.querySelectorAll("[data-cite-format]");
    var citeFormats = { apa: "", mla: "", chicago: "", bibtex: "" };
    var citeFormat = "apa";

    function setCiteFormat(next) {
      citeFormat = citeFormats[next] ? next : "apa";
      citeTabs.forEach(function (tab) {
        var active = tab.getAttribute("data-cite-format") === citeFormat;
        tab.classList.toggle("is-active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
      });
      if (citeText) citeText.textContent = citeFormats[citeFormat] || "";
      if (citeCopyBtn) {
        citeCopyBtn.textContent = "Copy citation";
        citeCopyBtn.disabled = !citeFormats[citeFormat];
      }
    }

    document.querySelectorAll(".cite-btn").forEach(function (button) {
      button.addEventListener("click", function () {
        try {
          citeFormats = JSON.parse(button.getAttribute("data-cite") || "{}");
        } catch (error) {
          citeFormats = {};
        }
        if (citeTitle) citeTitle.textContent = button.getAttribute("data-cite-title") || "Cite";
        setCiteFormat("apa");
        citeModal.show();
      });
    });

    citeTabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        setCiteFormat(tab.getAttribute("data-cite-format") || "apa");
      });
    });

    if (citeCopyBtn) {
      citeCopyBtn.addEventListener("click", function () {
        var value = citeFormats[citeFormat] || "";
        if (!value) return;
        function copied() {
          citeCopyBtn.textContent = "Copied";
          window.setTimeout(function () {
            citeCopyBtn.textContent = "Copy citation";
          }, 1600);
        }
        function copyWithHelper() {
          var helper = document.createElement("textarea");
          helper.value = value;
          helper.setAttribute("readonly", "");
          helper.style.position = "fixed";
          helper.style.left = "-9999px";
          document.body.appendChild(helper);
          helper.select();
          try {
            document.execCommand("copy");
            copied();
          } catch (error) {
            citeCopyBtn.textContent = "Copy failed";
          } finally {
            helper.remove();
          }
        }
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(value).then(copied).catch(copyWithHelper);
          return;
        }
        copyWithHelper();
      });
    }
  }

  var paperDetailDialog = document.getElementById("paperDetailDialog");
  var paperDetailTitle = document.getElementById("paperDetailTitle");
  var paperDetailBody = document.getElementById("paperDetailBody");
  var projDetailDialog = document.getElementById("projDetailDialog");
  var projDetailTitle = document.getElementById("projDetailTitle");
  var projDetailBody = document.getElementById("projDetailBody");

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

  function projectDetailsMap() {
    var configEl = document.getElementById("proj-page-config");
    if (!configEl) return {};
    try {
      var parsed = JSON.parse(configEl.textContent || "{}") || {};
      return parsed.details || {};
    } catch (error) {
      return {};
    }
  }

  function renderProjectDetailBody(item) {
    var topics = item.topics || [];
    var topicHtml = topics.length
      ? '<div class="paper-detail-tags">' +
        topics
          .map(function (topic) {
            return '<span class="paper-detail-tag">' + escapeHtml(topic) + "</span>";
          })
          .join("") +
        "</div>"
      : "—";
    var avatar = item.avatar
      ? '<img class="proj-avatar proj-detail-avatar" src="' +
        escapeHtml(item.avatar) +
        '" alt="" width="44" height="44">'
      : "";
    function linkHtml(href, label) {
      if (!href) return "—";
      return (
        '<a href="' +
        escapeHtml(href) +
        '" target="_blank" rel="noopener noreferrer">' +
        escapeHtml(label || href) +
        "</a>"
      );
    }
    return (
      '<div class="proj-detail-hero">' +
      avatar +
      "<div><div class=\"proj-owner\">" +
      escapeHtml(item.owner || "") +
      '</div><div class="proj-detail-full">' +
      escapeHtml(item.full_name || "") +
      "</div></div></div>" +
      '<div class="paper-detail-dl">' +
      '<div class="paper-detail-row"><div class="paper-detail-label">About</div><div class="paper-detail-value">' +
      escapeHtml(item.description || "No description") +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Language</div><div class="paper-detail-value">' +
      escapeHtml(item.language || "—") +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Stars</div><div class="paper-detail-value">' +
      escapeHtml(item.stars_label || "0") +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Forks</div><div class="paper-detail-value">' +
      escapeHtml(item.forks_label || "0") +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Topics</div><div class="paper-detail-value">' +
      topicHtml +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">Website</div><div class="paper-detail-value">' +
      linkHtml(item.homepage, item.homepage) +
      "</div></div>" +
      '<div class="paper-detail-row"><div class="paper-detail-label">GitHub</div><div class="paper-detail-value">' +
      linkHtml(item.html_url, item.full_name || "Open repository") +
      "</div></div>" +
      "</div>"
    );
  }

  function openProjectDetail(key) {
    var item = projectDetailsMap()[key];
    if (!item || !projDetailDialog) return;
    if (projDetailTitle) projDetailTitle.textContent = item.name || "Project";
    if (projDetailBody) projDetailBody.innerHTML = renderProjectDetailBody(item);
    if (typeof projDetailDialog.showModal === "function") projDetailDialog.showModal();
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
    if (detail.paper_id) {
      loadPaperWorkspace(detail.paper_id);
    }
  }

  function csrfHeader() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") || "" : "";
  }

  function loadPaperWorkspace(paperId) {
    if (!paperDetailBody) return;
    fetch("/api/papers/" + encodeURIComponent(paperId) + "/workspace", {
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRF-Token": csrfHeader() },
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        if (!payload || !payload.ok || !paperDetailBody) return;
        paperDetailBody.insertAdjacentHTML("beforeend", renderPaperWorkspace(payload));
      })
      .catch(function () {});
  }

  function renderPaperWorkspace(payload) {
    var statuses = ["unread", "reading", "reviewed", "cited", "archived"];
    var statusOptions = statuses
      .map(function (status) {
        return (
          '<option value="' +
          status +
          '"' +
          (payload.reading_status === status ? " selected" : "") +
          ">" +
          status.charAt(0).toUpperCase() +
          status.slice(1) +
          "</option>"
        );
      })
      .join("");
    var collections = (payload.collections || [])
      .map(function (item) {
        return (
          '<option value="' +
          item.id +
          '"' +
          (item.selected ? " selected" : "") +
          ">" +
          escapeHtml(item.name) +
          "</option>"
        );
      })
      .join("");
    var related = (payload.related || [])
      .map(function (item) {
        return (
          '<li><a href="/library?q=' +
          encodeURIComponent(item.title) +
          '">' +
          escapeHtml(item.title) +
          "</a></li>"
        );
      })
      .join("");
    return (
      '<form method="post" action="/papers/' +
      payload.paper_id +
      '/reading-status" class="paper-workspace mt-3">' +
      '<input type="hidden" name="csrf_token" value="' +
      escapeHtml(csrfToken()) +
      '">' +
      '<input type="hidden" name="next" value="/library">' +
      '<label class="form-label" for="readingStatus">Reading status</label>' +
      '<div class="d-flex gap-2">' +
      '<select class="form-select form-select-sm" id="readingStatus" name="reading_status">' +
      statusOptions +
      "</select>" +
      '<button class="btn btn-sm btn-outline-primary" type="submit">Save</button></div></form>' +
      '<form method="post" action="/papers/' +
      payload.paper_id +
      '/notes" class="mt-3">' +
      '<input type="hidden" name="csrf_token" value="' +
      escapeHtml(csrfToken()) +
      '">' +
      '<input type="hidden" name="next" value="/library">' +
      '<input type="hidden" name="save_tags" value="1">' +
      (payload.for_user_id
        ? '<input type="hidden" name="for_user_id" value="' + escapeHtml(String(payload.for_user_id)) + '">'
        : "") +
      '<label class="form-label" for="paperNotes">Remark (this account)</label>' +
      '<textarea class="form-control" id="paperNotes" name="notes" rows="3">' +
      escapeHtml(payload.notes || "") +
      "</textarea>" +
      '<label class="form-label mt-2" for="paperTags">Tags</label>' +
      '<input class="form-control" id="paperTags" name="tags" value="' +
      escapeHtml(payload.tags || "") +
      '">' +
      '<button class="btn btn-sm btn-primary mt-2" type="submit">Save remark</button></form>' +
      (collections
        ? '<form method="post" action="/papers/' +
          payload.paper_id +
          '/collections" class="mt-3">' +
          '<input type="hidden" name="csrf_token" value="' +
          escapeHtml(csrfToken()) +
          '">' +
          '<input type="hidden" name="next" value="/library">' +
          '<label class="form-label" for="paperCollection">Add to collection</label>' +
          '<div class="d-flex gap-2"><select class="form-select form-select-sm" id="paperCollection" name="collection_id">' +
          collections +
          '</select><button class="btn btn-sm btn-outline-primary" type="submit">Add</button></div></form>'
        : "") +
      (related
        ? '<div class="mt-3"><div class="paper-detail-label">Related papers</div><ul class="paper-related">' +
          related +
          "</ul></div>"
        : "")
    );
  }

  document.addEventListener("click", function (event) {
    var projButton = event.target.closest("[data-proj-key]");
    if (projButton) {
      event.preventDefault();
      openProjectDetail(projButton.getAttribute("data-proj-key") || "");
      return;
    }
    if (event.target.closest("[data-proj-close]")) {
      projDetailDialog && projDetailDialog.close();
      return;
    }
    if (projDetailDialog && event.target === projDetailDialog) {
      projDetailDialog.close();
      return;
    }
    var detailButton = event.target.closest(".detail-btn");
    if (detailButton) {
      event.preventDefault();
      openPaperDetail(detailButton);
      return;
    }
    var remarkButton = event.target.closest(".remark-btn");
    if (remarkButton) {
      event.preventDefault();
      openPaperRemark(remarkButton);
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

  var remarkModalEl = document.getElementById("paperRemarkModal");
  var remarkForm = document.getElementById("paperRemarkForm");
  var remarkText = document.getElementById("paperRemarkText");
  var remarkUserId = document.getElementById("paperRemarkUserId");
  var remarkAccount = document.getElementById("paperRemarkAccount");
  var remarkAccountWrap = document.getElementById("paperRemarkAccountWrap");
  var remarkPaperTitle = document.getElementById("paperRemarkPaperTitle");
  var remarkNext = document.getElementById("paperRemarkNext");
  var remarkModal = null;
  if (remarkModalEl && typeof bootstrap !== "undefined") {
    remarkModal = bootstrap.Modal.getOrCreateInstance(remarkModalEl);
  }

  function remarkAccounts() {
    var el = document.getElementById("lib-remark-accounts");
    if (!el) return [];
    try {
      return JSON.parse(el.textContent || "[]") || [];
    } catch (error) {
      return [];
    }
  }

  function fillRemarkAccounts(selectedId) {
    if (!remarkAccount || !remarkAccountWrap) return;
    var accounts = remarkAccounts();
    remarkAccount.innerHTML = "";
    if (!accounts.length) {
      remarkAccountWrap.classList.add("d-none");
      return;
    }
    accounts.forEach(function (account) {
      var option = document.createElement("option");
      option.value = String(account.id);
      option.textContent = account.name || account.email || String(account.id);
      if (account.email && account.email !== account.name) {
        option.textContent += " · " + account.email;
      }
      if (String(account.id) === String(selectedId)) option.selected = true;
      remarkAccount.appendChild(option);
    });
    remarkAccountWrap.classList.toggle("d-none", accounts.length < 2);
  }

  function loadRemarkNotes(paperId, accountId) {
    if (!remarkText || !paperId) return;
    remarkText.value = "Loading…";
    remarkText.disabled = true;
    var url =
      "/api/papers/" +
      encodeURIComponent(paperId) +
      "/workspace?for_user_id=" +
      encodeURIComponent(accountId || "");
    fetch(url, {
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-CSRF-Token": csrfHeader() },
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (payload) {
        remarkText.disabled = false;
        if (payload && payload.ok) {
          remarkText.value = payload.notes || "";
        } else {
          remarkText.value = "";
        }
      })
      .catch(function () {
        remarkText.disabled = false;
        remarkText.value = "";
      });
  }

  function openPaperRemark(button) {
    if (!remarkForm || !remarkModalEl) return;
    var paperId = button.getAttribute("data-remark-paper-id") || "";
    var accountId = button.getAttribute("data-remark-user-id") || "";
    if (!paperId) return;
    remarkForm.action = "/papers/" + encodeURIComponent(paperId) + "/notes";
    if (remarkNext) remarkNext.value = window.location.pathname + window.location.search;
    if (remarkPaperTitle) {
      remarkPaperTitle.textContent = button.getAttribute("data-remark-title") || "";
    }
    fillRemarkAccounts(accountId);
    if (remarkAccount && remarkAccount.options.length) {
      accountId = remarkAccount.value || accountId;
    }
    if (remarkUserId) remarkUserId.value = accountId;
    if (remarkAccount) {
      remarkAccount.onchange = function () {
        if (remarkUserId) remarkUserId.value = remarkAccount.value;
        loadRemarkNotes(paperId, remarkAccount.value);
      };
    }
    loadRemarkNotes(paperId, accountId);
    if (remarkModal) {
      remarkModal.show();
    } else {
      remarkModalEl.classList.add("show");
      remarkModalEl.style.display = "block";
      remarkModalEl.removeAttribute("aria-hidden");
    }
  }

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
  setInterval(pollNavLive, 2500);
})();

(function () {
  var form = document.querySelector("[data-ask-paper]");
  if (!form) return;
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var data = new FormData(form);
    fetch(form.action, { method: "POST", body: data, headers: { Accept: "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        var box = document.getElementById("askPaperResult");
        if (!box) return;
        var cites = (payload.citations || [])
          .map(function (item) { return item.label + " p." + item.page; })
          .join("; ");
        box.textContent = (payload.answer || "") + (cites ? " Sources: " + cites : "");
      });
  });
})();

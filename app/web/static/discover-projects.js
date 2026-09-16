(function () {
  var form = document.getElementById("bulkProjectForm");
  if (!form) return;
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var select = document.getElementById("bulkProject");
    var boxes = document.querySelectorAll(".paper-select:checked");
    var ids = Array.prototype.map.call(boxes, function (box) {
      return box.value;
    }).filter(Boolean);
    var typed = (document.getElementById("bulkPaperIds").value || "").trim();
    if (typed) {
      typed.split(/[\s,]+/).forEach(function (value) {
        if (value && ids.indexOf(value) === -1) ids.push(value);
      });
    }
    if (!select || !ids.length) return;
    var data = new FormData();
    data.append("paper_ids", ids.join(","));
    fetch("/projects/" + select.value + "/papers", { method: "POST", body: data }).then(function () {
      window.location.href = "/projects/" + select.value + "/papers";
    });
  });
})();

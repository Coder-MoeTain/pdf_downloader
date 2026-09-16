(function () {
  var root = document.querySelector("[data-screening-root]");
  if (!root) return;
  var form = document.getElementById("screenForm");
  var decision = document.getElementById("screenDecision");
  var reason = document.getElementById("reason");
  form.querySelectorAll("[data-decision]").forEach(function (button) {
    button.addEventListener("click", function () {
      decision.value = button.getAttribute("data-decision");
    });
  });
  form.addEventListener("submit", function (event) {
    if (decision.value === "exclude" && !reason.value) {
      event.preventDefault();
      reason.focus();
      reason.reportValidity();
    }
  });
  document.addEventListener("keydown", function (event) {
    if (event.target && (event.target.tagName === "INPUT" || event.target.tagName === "TEXTAREA" || event.target.tagName === "SELECT")) return;
    var key = event.key.toLowerCase();
    if (key === "i") decision.value = "include";
    if (key === "m") decision.value = "maybe";
    if (key === "e") decision.value = "exclude";
    if (key === "i" || key === "m" || key === "e") {
      if (decision.value === "exclude" && !reason.value) {
        reason.focus();
        return;
      }
      form.requestSubmit();
    }
  });
})();

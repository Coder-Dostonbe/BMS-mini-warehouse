(function () {
  "use strict";

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var question = form.getAttribute("data-confirm");
    if (question && !window.confirm(question)) {
      event.preventDefault();
      return;
    }
    if (form.hasAttribute("data-once")) {
      if (form.getAttribute("data-sent") === "1") {
        event.preventDefault();
        return;
      }
      form.setAttribute("data-sent", "1");
      var buttons = form.querySelectorAll("button");
      // Disable after the submit is dispatched so the browser still sends the form.
      window.setTimeout(function () {
        for (var i = 0; i < buttons.length; i++) { buttons[i].disabled = true; }
      }, 0);
    }
  });

  // Back/forward cache restores the disabled state; undo it.
  window.addEventListener("pageshow", function () {
    var forms = document.querySelectorAll("form[data-once]");
    for (var i = 0; i < forms.length; i++) {
      forms[i].removeAttribute("data-sent");
      var buttons = forms[i].querySelectorAll("button");
      for (var j = 0; j < buttons.length; j++) { buttons[j].disabled = false; }
    }
  });

  var select = document.querySelector("select[data-quantity-input]");
  if (select) {
    var input = document.getElementById(select.getAttribute("data-quantity-input"));
    var hint = document.getElementById(select.getAttribute("data-quantity-hint"));
    var sync = function () {
      var option = select.options[select.selectedIndex];
      var unit = option ? option.getAttribute("data-unit") : null;
      if (input) { input.setAttribute("inputmode", unit === "pcs" ? "numeric" : "decimal"); }
      if (hint) { hint.textContent = option && option.getAttribute("data-hint") ? option.getAttribute("data-hint") : ""; }
    };
    select.addEventListener("change", sync);
    sync();
  }
})();

// Progressive enhancement for <form data-ajax="remove|reload">. JS off, or any
// fetch failure -> falls back to the normal POST + server redirect (the form
// still works exactly as before). Applied only where views.py opts in.
document.addEventListener("submit", function (e) {
  var form = e.target;
  var mode = form.getAttribute("data-ajax");
  if (!mode) return;
  e.preventDefault();
  fetch(form.action, {
    method: form.method || "POST",
    body: new FormData(form),
  }).then(function (resp) {
    if (!resp.ok) throw new Error("request failed");
    if (mode === "remove") {
      var card = form.closest(".card");
      if (card) card.remove();
    } else if (mode === "reload") {
      location.reload();
    }
  }).catch(function () {
    form.submit();
  });
});

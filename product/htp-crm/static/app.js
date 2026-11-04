// Progressive enhancement for <form data-ajax="remove|reload|swap">. JS off, or any
// fetch failure -> falls back to the normal POST + server redirect (the form
// still works exactly as before). Applied only where views.py opts in.
//
// swap: data-swap="id1 id2" — fetch follows the server's redirect, and each listed
// element is replaced by its fresh copy from that page (removed if it's gone). The
// server stays the only thing that renders HTML; the page just doesn't navigate,
// so a tick halfway down a đơn doesn't throw you back to the top.
document.addEventListener("submit", function (e) {
  var form = e.target;
  var mode = form.getAttribute("data-ajax");
  if (!mode || e.defaultPrevented) return; // e.g. onsubmit="return confirm()" said no
  e.preventDefault();
  if (form.dataset.busy) return; // double-tap on a slow connection
  form.dataset.busy = "1";
  var btn = form.querySelector("button");
  if (btn) btn.disabled = true;
  fetch(form.action, {
    method: form.method || "POST",
    body: new FormData(form),
  }).then(function (resp) {
    if (!resp.ok) throw new Error("request failed");
    if (mode === "remove") {
      var card = form.closest(".board-card, .card");
      if (card) card.remove();
    } else if (mode === "reload") {
      location.reload();
    } else if (mode === "swap") {
      return resp.text().then(function (html) {
        var fresh = new DOMParser().parseFromString(html, "text/html");
        (form.getAttribute("data-swap") || "").split(" ").forEach(function (id) {
          var old = id && document.getElementById(id);
          if (!old) return;
          var neu = fresh.getElementById(id);
          if (neu) old.replaceWith(document.importNode(neu, true));
          else old.remove();
        });
      });
    }
  }).catch(function () {
    form.submit();
  }).finally(function () {
    delete form.dataset.busy;
    if (btn && btn.isConnected) btn.disabled = false;
  });
});

// Money field grouping. Ported from htp-crm/views.py's fmtMoney, including the
// hard-won rule: do NOT re-insert grouping dots on every keystroke. Rewriting
// .value mid-keystroke makes phone keyboards (GBoard) re-commit the digit just
// typed, which multiplied "3000000" into "3333000000" on the CRM's price fields.
// Strip to digits while typing; group into 3.000.000 once, on blur.
document.addEventListener("input", function (e) {
  var inp = e.target;
  if (!inp.classList || !inp.classList.contains("money")) return;
  var d = inp.value.replace(/\D/g, "");
  if (d !== inp.value) inp.value = d;
});

document.addEventListener(
  "blur",
  function (e) {
    var inp = e.target;
    if (!inp.classList || !inp.classList.contains("money")) return;
    var v = inp.value.replace(/\D/g, "");
    inp.value = v ? v.replace(/\B(?=(\d{3})+(?!\d))/g, ".") : "";
  },
  true
);

// Xoá is destructive and the family taps fast on phones — make it deliberate.
document.addEventListener("submit", function (e) {
  var form = e.target;
  if (form.getAttribute("data-confirm") === null) return;
  if (!confirm(form.getAttribute("data-confirm"))) e.preventDefault();
});

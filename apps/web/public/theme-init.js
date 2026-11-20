// Apply a saved theme before first paint so there is no flash. "system" = no attribute.
// Kept as a separate file so the Content-Security-Policy can stay `script-src 'self'`.
(function () {
  try {
    var t = localStorage.getItem("c2b.theme");
    if (t === "light" || t === "dark") {
      document.documentElement.setAttribute("data-theme", t);
    }
  } catch (e) {
    /* storage can be blocked; the system theme is used */
  }
})();

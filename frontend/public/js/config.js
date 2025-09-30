window.BACKEND_BASE_URL = "http://10.8.0.2:8000";
window.DEBUG_BANNER = true;
if (typeof window !== "undefined") { window.FRONTEND_BASE_URL = window.FRONTEND_BASE_URL || window.location.origin; }
(function(){
  if (typeof window === "undefined" || !window.location) return;
  if (window.location.protocol !== "https:") return;
  if (window.ALLOW_INSECURE_BACKENDS) return;
  var baseKeys = ["BACKEND_BASE_URL"];
  baseKeys.forEach(function(name){
    var raw = window[name];
    if (typeof raw !== "string") return;
    if (!/^http:\/\//i.test(raw)) return;
    try {
      var parsed = new URL(raw, window.location.origin);
      if (!parsed.hostname) return;
      if (parsed.hostname !== window.location.hostname) return;
      parsed.protocol = "https:";
      var normalized = parsed.toString().replace(/\/+$/, "");
      window[name] = normalized;
    } catch (err) {
      console.warn("[config] Unable to normalize base URL for", name, err);
    }
  });
})();

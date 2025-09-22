// Backend base URL can be injected via .env -> generate-config.js
window.BACKEND_BASE_URL = "http://10.8.0.2:8000";

// Frontend base should be the actual origin of whoever is visiting, not localhost
// Use dynamic resolution as a safe default in case generate-config.js sets something else
window.FRONTEND_BASE_URL = window.location.origin;
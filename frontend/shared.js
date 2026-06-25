function resolveApiBaseUrl(raw) {
  const LEGACY_RENDER_HOST = "twiq-backend.onrender.com";
  const RENDER_API_HOST = "papertrade-absj.onrender.com";
  const base = (raw || `https://${RENDER_API_HOST}`).replace(/\/$/, "");
  return base.includes(LEGACY_RENDER_HOST)
    ? base.replace(LEGACY_RENDER_HOST, RENDER_API_HOST)
    : base;
}

const API_BASE_URL = resolveApiBaseUrl(window.TWIQ_API_BASE_URL);
const API_KEY = window.TWIQ_API_KEY || "";

const formatInr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

const formatNumber = new Intl.NumberFormat("en-IN", {
  maximumFractionDigits: 2,
});

function apiHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (API_KEY) headers["X-API-Key"] = API_KEY;
  return headers;
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: { ...apiHeaders(), ...(options.headers || {}) },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

function byId(id) {
  return document.getElementById(id);
}

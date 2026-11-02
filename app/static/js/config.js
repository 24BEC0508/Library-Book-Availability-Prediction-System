/**
 * Institutional Branding and Application Configuration.
 * Configurable so institution names, branding, and mode flags can be updated easily.
 */
const isLocalFile = typeof window !== "undefined" && window.location.protocol === "file:";

const CONFIG = {
  institutionName: "Apex University",
  libraryName: "University Library & Learning Commons",
  systemName: "Book Availability & Demand Predictor",
  tagline: "Authoritative academic circulation and forward-looking book availability intelligence.",
  timezone: "Asia/Kolkata",
  isDemoMode: false,
  // Relative '/api' automatically routes to the host (DigitalOcean Droplet, App Platform, or local server).
  // Can be overridden by setting window.CUSTOM_API_BASE.
  apiBase: (typeof window !== "undefined" && window.CUSTOM_API_BASE) || (isLocalFile ? "http://127.0.0.1:8000/api" : "/api"),
  localApiBase: "/api",
  defaultAdminKey: "library-admin-secret-key-2026",
  borrowingPolicy: "First-Come, First-Served (FCFS)",
};

window.LIBRARY_CONFIG = CONFIG;

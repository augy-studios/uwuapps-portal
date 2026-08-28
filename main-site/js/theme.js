// Theme system: 7 brand colour swatches + light/dark mode.
// Default is always light + classic (#ccffcc), regardless of OS preference.
// Once the user picks something, it is persisted.

const APP_KEY = "uwusuite";

export const COLOR_THEMES = [
  { id: "classic", label: "Classic", hex: "#ccffcc" },
  { id: "not-green-1", label: "Not green 1", hex: "#ffcccc" },
  { id: "not-green-2", label: "Not green 2", hex: "#ccccff" },
  { id: "not-green-3", label: "Not green 3", hex: "#ffffcc" },
  { id: "not-green-4", label: "Not green 4", hex: "#ffccff" },
  { id: "not-green-5", label: "Not green 5", hex: "#ccffff" },
  { id: "really-light-green", label: "Really really light green", hex: "#ffffff" },
];

const STORAGE_KEY_COLOR = `${APP_KEY}.colorTheme`;
const STORAGE_KEY_MODE = `${APP_KEY}.mode`;

// Pre-spec key, values classic / notgreen1..5 / ultralight.
const LEGACY_KEY_COLOR = "uwusuite-theme";
const LEGACY_COLOR_IDS = {
  classic: "classic",
  notgreen1: "not-green-1",
  notgreen2: "not-green-2",
  notgreen3: "not-green-3",
  notgreen4: "not-green-4",
  notgreen5: "not-green-5",
  ultralight: "really-light-green",
};

// Also runs in the pre-paint script so the migrated value lands before paint.
export function migrateLegacyTheme() {
  try {
    const legacy = localStorage.getItem(LEGACY_KEY_COLOR);
    if (!legacy) return;
    if (!localStorage.getItem(STORAGE_KEY_COLOR) && LEGACY_COLOR_IDS[legacy]) {
      localStorage.setItem(STORAGE_KEY_COLOR, LEGACY_COLOR_IDS[legacy]);
    }
    localStorage.removeItem(LEGACY_KEY_COLOR);
  } catch (_) {}
}

function hexToRgb(hex) {
  const n = parseInt(hex.replace("#", ""), 16);
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
}

export function getStoredColorTheme() {
  return localStorage.getItem(STORAGE_KEY_COLOR) || "classic";
}

export function getStoredMode() {
  return localStorage.getItem(STORAGE_KEY_MODE) || "light";
}

export function applyColorTheme(id) {
  const theme = COLOR_THEMES.find((t) => t.id === id) || COLOR_THEMES[0];
  document.documentElement.setAttribute("data-color-theme", theme.id);
  document.documentElement.style.setProperty("--brand", theme.hex);
  document.documentElement.style.setProperty("--brand-rgb", hexToRgb(theme.hex));
  localStorage.setItem(STORAGE_KEY_COLOR, theme.id);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme.hex);
  return theme;
}

export function applyMode(mode) {
  const resolved = mode === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-mode", resolved);
  localStorage.setItem(STORAGE_KEY_MODE, resolved);
  return resolved;
}

export function initTheme() {
  migrateLegacyTheme();
  applyColorTheme(getStoredColorTheme());
  applyMode(getStoredMode());
}

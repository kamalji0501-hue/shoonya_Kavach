/**
 * Strategy-pick Earth — ui-layouts / Cobe globe (vanilla).
 * Dark = card-globe-default; light = card-globe-white.
 * scale must stay ≤ 1 so the full sphere stays round (not a clipped arc).
 */
import createGlobe from "./vendor/cobe.js";

const PRESETS = {
  dark: {
    theta: 0.25,
    dark: 1,
    scale: 1,
    diffuse: 1.2,
    mapSamples: 40000,
    mapBrightness: 6,
    baseColor: [0.4, 0.6509, 1],
    markerColor: [1, 0, 0],
    glowColor: [0.2745, 0.5765, 0.898],
  },
  light: {
    theta: 0.25,
    dark: 0,
    scale: 1,
    diffuse: 1.2,
    mapSamples: 40000,
    mapBrightness: 6,
    baseColor: [1, 1, 1],
    markerColor: [0.2, 0.4, 0.9],
    glowColor: [0.85, 0.88, 0.95],
  },
};

let globe = null;
let phi = 0;
let resizeBound = null;
let currentTheme = "dark";
let lastSide = 0;

function themeKey() {
  const t = document.documentElement.getAttribute("data-theme");
  return t === "light" ? "light" : "dark";
}

/** Perfect square side length — never stretch. */
function canvasSide() {
  const vw = window.innerWidth || 800;
  const vh = window.innerHeight || 800;
  // Leave room for title + side cards; keep full circle on screen
  const side = Math.floor(Math.min(vw * 0.52, vh * 0.58, 560));
  return Math.max(260, side);
}

function destroy() {
  if (resizeBound) {
    window.removeEventListener("resize", resizeBound);
    resizeBound = null;
  }
  if (globe) {
    try { globe.destroy(); } catch (_) {}
    globe = null;
  }
  phi = 0;
  lastSide = 0;
}

function start(forceTheme) {
  const canvas = document.getElementById("stratPickGlobe");
  if (!canvas) return;
  currentTheme = forceTheme === "light" || forceTheme === "dark" ? forceTheme : themeKey();
  destroy();

  const preset = PRESETS[currentTheme] || PRESETS.dark;
  const side = canvasSide();
  lastSide = side;

  // Force square CSS before WebGL init (avoids oval stretch)
  canvas.width = side * 2;
  canvas.height = side * 2;
  canvas.style.width = side + "px";
  canvas.style.height = side + "px";
  canvas.style.maxWidth = "none";
  canvas.style.maxHeight = "none";
  canvas.style.aspectRatio = "1 / 1";

  const opts = {
    devicePixelRatio: 2,
    width: side * 2,
    height: side * 2,
    phi: 0,
    theta: preset.theta,
    dark: preset.dark,
    scale: preset.scale,
    diffuse: preset.diffuse,
    mapSamples: preset.mapSamples,
    mapBrightness: preset.mapBrightness,
    baseColor: preset.baseColor,
    markerColor: preset.markerColor,
    glowColor: preset.glowColor,
    opacity: 1,
    offset: [0, 0],
    markers: [],
    onRender: (state) => {
      state.phi = phi;
      phi += 0.006;
    },
  };

  try {
    globe = createGlobe(canvas, opts);
  } catch (err) {
    console.warn("strat-globe: createGlobe failed", err);
    return;
  }

  resizeBound = () => {
    if (!canvas.isConnected) return;
    const next = canvasSide();
    if (Math.abs(next - lastSide) < 12) return;
    const theme = currentTheme;
    destroy();
    start(theme);
  };
  window.addEventListener("resize", resizeBound);
}

function applyTheme(mode) {
  const t = mode === "light" ? "light" : "dark";
  const pick = document.getElementById("strategyPick");
  if (pick) pick.dataset.globeTheme = t;
  if (!pick || pick.classList.contains("hidden")) {
    currentTheme = t;
    return;
  }
  if (t === currentTheme && globe) return;
  start(t);
}

function stop() {
  destroy();
}

window.StratGlobe = { start, stop, applyTheme, PRESETS };

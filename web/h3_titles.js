import { app } from "../../scripts/app.js";

const NODES = ["H3PromptAssembler", "H3Studio"];

// Which prompt section each widget feeds, so the node reads like the output
// rather than like a list of variable names.
const TITLES = {
  llm_prompt: ["PASTE FULL PROMPT", "overrides the fields below when filled"],
  style_line: ["DETAILED_DESCRIPTION · STYLE", "goes above [Shot 1]"],
  summary: ["SUMMARY", "plot level; the task prefix is added for you"],
  shots: ["DETAILED_DESCRIPTION · SHOTS", "[Shot 1] … one block per shot"],
  overall_soundscape: ["OVERALL_SOUNDSCAPE", "continuous ambience"],
  non_diegetic_music: ["NON_DIEGETIC_MUSIC", "audience-only score, or N/A"],
  extra_definitions: ["EXTRA SUBJECT_DEFINITIONS", "appended verbatim"],
  extra_retention: ["EXTRA RETENTION_ANALYSIS", "appended verbatim"],
};

const ACCENT = { llm_prompt: "#e0b050" };
const tracked = new Set();

/**
 * The DOM element behind a multiline widget. The property name has moved
 * between frontend versions (inputEl, element, sometimes a wrapper around the
 * textarea), so all of them are checked rather than assuming one.
 */
function elementOf(w) {
  const cand = w.inputEl || w.element || w.domElement || null;
  if (!cand) return null;
  if (cand.tagName === "TEXTAREA" || cand.tagName === "INPUT") return cand;
  return cand.querySelector?.("textarea, input") || null;
}

function makeLabel(name, text, hint) {
  const el = document.createElement("div");
  el.className = "h3-section-title";
  el.textContent = text;
  el.title = hint;
  el.style.cssText =
    "position:absolute;pointer-events:none;z-index:60;" +
    "font:600 9px/1.35 sans-serif;letter-spacing:.06em;" +
    `color:${ACCENT[name] || "#7ac943"};` +
    "background:linear-gradient(180deg,rgba(26,26,26,.95),rgba(26,26,26,.75),rgba(26,26,26,0));" +
    "padding:2px 4px 5px;white-space:nowrap;overflow:hidden;" +
    "text-overflow:ellipsis;box-sizing:border-box";
  return el;
}

/**
 * Positions come from offsetLeft/offsetTop, and syncing runs on a rAF loop.
 *
 * The previous version read inline styles and hooked onDrawForeground. Neither
 * is dependable: the frontend does not always place DOM widgets with inline
 * top/left, and the canvas draw hook never fires under the Vue node renderer —
 * which is why the titles never appeared.
 */
function sync(node) {
  if (!node.widgets) return;
  const collapsed = node.flags?.collapsed;

  for (const w of node.widgets) {
    const spec = TITLES[w.name];
    if (!spec) continue;
    const el = elementOf(w);

    if (!el || !el.isConnected) {
      if (w._h3label) {
        w._h3label.remove();
        w._h3label = null;
      }
      continue;
    }

    if (!w._h3label) {
      w._h3label = makeLabel(w.name, spec[0], spec[1]);
      el.placeholder = spec[1];
      el.style.paddingTop = "16px";
      el.style.boxSizing = "border-box";
      if (ACCENT[w.name]) el.style.borderLeft = `2px solid ${ACCENT[w.name]}`;
    }

    const label = w._h3label;
    const host = el.offsetParent || el.parentElement;
    if (!host) continue;
    if (label.parentElement !== host) host.appendChild(label);

    const visible =
      !collapsed &&
      el.offsetParent !== null &&
      el.style.display !== "none" &&
      el.offsetWidth > 0;

    label.style.display = visible ? "block" : "none";
    if (!visible) continue;

    label.style.left = `${el.offsetLeft + 1}px`;
    label.style.top = `${el.offsetTop + 1}px`;
    label.style.width = `${Math.max(0, el.offsetWidth - 2)}px`;
  }
}

let ticking = false;
function tick() {
  for (const node of Array.from(tracked)) {
    if (!node.graph) {
      for (const w of node.widgets ?? []) {
        w._h3label?.remove();
        w._h3label = null;
      }
      tracked.delete(node);
      continue;
    }
    try {
      sync(node);
    } catch (_) {
      /* a transient layout state shouldn't kill the loop */
    }
  }
  if (tracked.size) requestAnimationFrame(tick);
  else ticking = false;
}

function track(node) {
  if (!node) return;
  tracked.add(node);
  if (!ticking) {
    ticking = true;
    requestAnimationFrame(tick);
  }
}

app.registerExtension({
  name: "bortevekk.h3.sectiontitles",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!NODES.includes(nodeData.name)) return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      track(this);
      return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      track(this);
      return r;
    };

    const onRemoved = nodeType.prototype.onRemoved;
    nodeType.prototype.onRemoved = function () {
      for (const w of this.widgets ?? []) {
        w._h3label?.remove();
        w._h3label = null;
      }
      tracked.delete(this);
      return onRemoved?.apply(this, arguments);
    };
  },

  // Nodes restored from a saved workflow do not always run onNodeCreated.
  async loadedGraphNode(node) {
    if (NODES.includes(node.type)) track(node);
  },

  // Last resort: pick up any instance the hooks above missed.
  async setup() {
    setInterval(() => {
      for (const n of app.graph?._nodes ?? []) {
        if (NODES.includes(n.type) && !tracked.has(n)) track(n);
      }
    }, 2000);
  },
});

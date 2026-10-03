import { app } from "../../scripts/app.js";

const NODE = "H3PromptAssembler";

// Fields a saved shot captures. llm_prompt is the usual one, but the manual
// fields come along so a shot saved before switching to paste mode still
// restores completely.
const CAPTURED = [
  "llm_prompt",
  "style_line",
  "summary",
  "shots",
  "overall_soundscape",
  "non_diegetic_music",
  "task",
  "video_1_role",
  "first_frame_picture",
];

function widget(node, name) {
  return node.widgets?.find((w) => w.name === name);
}

function toast(severity, summary, detail) {
  try {
    app.extensionManager?.toast?.add({ severity, summary, detail, life: 3500 });
  } catch (_) {
    console.log(`[H3] ${summary}: ${detail ?? ""}`);
  }
}

function readLibrary(node) {
  try {
    const v = JSON.parse(widget(node, "scene_library")?.value || "{}");
    return v && typeof v === "object" && !Array.isArray(v) ? v : {};
  } catch (_) {
    return {};
  }
}

function writeLibrary(node, lib) {
  const w = widget(node, "scene_library");
  if (w) {
    w.value = JSON.stringify(lib);
    if (w.inputEl) w.inputEl.value = w.value;
  }
  render(node);
  node.setDirtyCanvas(true, true);
}

/** Natural sort so shot 10 follows shot 9, not shot 1. */
function nat(a, b) {
  return String(a).localeCompare(String(b), undefined, {
    numeric: true,
    sensitivity: "base",
  });
}

function fields(node) {
  return {
    scene: (widget(node, "h3_scene")?.value ?? "").trim(),
    shot: (widget(node, "h3_shot")?.value ?? "").trim(),
  };
}

function setFields(node, scene, shot) {
  const s = widget(node, "h3_scene");
  const t = widget(node, "h3_shot");
  if (s) s.value = scene;
  if (t) t.value = shot;
}

/** Subjects a saved shot refers to, read from its @tokens. Shows at a glance
 *  which characters a shot needs before loading it. */
function subjectsOf(data) {
  const src = [data?.llm_prompt, data?.summary, data?.shots]
    .filter(Boolean)
    .join("\n");
  const found = [];
  for (const m of src.matchAll(/@([A-Za-z0-9_\-]+)!?/g)) {
    if (!found.includes(m[1])) found.push(m[1]);
  }
  return found;
}

function chipRow(names, { speakers = [] } = {}) {
  const wrap = document.createElement("div");
  wrap.style.cssText = "display:flex;flex-wrap:wrap;gap:3px;align-items:center";
  if (!names.length) {
    const none = document.createElement("span");
    none.textContent = "no @subjects";
    none.style.cssText = "color:#6b7075;font-size:9px;font-style:italic";
    wrap.appendChild(none);
    return wrap;
  }
  for (const n of names) {
    const c = document.createElement("span");
    c.textContent = "@" + n;
    const speaks = speakers.includes(n);
    c.title = speaks ? "speaks in this shot" : "";
    c.style.cssText =
      "font:9px sans-serif;padding:1px 5px;border-radius:8px;" +
      (speaks
        ? "background:#2f3b2a;color:#9ad86a;border:1px solid #3f5136"
        : "background:#2b2b2b;color:#9aa0a6;border:1px solid #3a3a3a");
    wrap.appendChild(c);
  }
  return wrap;
}

function speakersOf(data) {
  const src = [data?.llm_prompt, data?.summary, data?.shots].filter(Boolean).join("\n");
  const out = [];
  for (const m of src.matchAll(/@([A-Za-z0-9_\-]+)!/g))
    if (!out.includes(m[1])) out.push(m[1]);
  return out;
}

/** One-line gist of a saved shot, for the browser row. */
function preview(data) {
  const src =
    (data?.llm_prompt || "").trim() ||
    (data?.summary || "").trim() ||
    (data?.shots || "").trim();
  if (!src) return "empty";
  const flat = src
    .replace(/^(subject_definitions|summary|retention_analysis|detailed_description|overall_soundscape|non_diegetic_music)\s*:/gim, "")
    .replace(/\s+/g, " ")
    .trim();
  return flat.length > 90 ? flat.slice(0, 89) + "…" : flat;
}

function saveShot(node, scene, shot, { confirmOverwrite = true } = {}) {
  if (!scene || !shot) {
    toast("warn", "H3", "Set both a scene name and a shot number first.");
    return false;
  }
  const lib = readLibrary(node);
  lib[scene] = lib[scene] || {};
  const existed = !!lib[scene][shot];
  if (existed && confirmOverwrite && !window.confirm(`Overwrite ${scene} / shot ${shot}?`))
    return false;

  const data = {};
  for (const name of CAPTURED) {
    const w = widget(node, name);
    if (w) data[name] = w.value;
  }
  data._saved = new Date().toISOString().slice(0, 16).replace("T", " ");
  lib[scene][shot] = data;

  setFields(node, scene, shot);
  writeLibrary(node, lib);
  toast("success", "H3", `${existed ? "Updated" : "Saved"} ${scene} / shot ${shot}`);
  return true;
}

/** Next free integer shot key in a scene, so variants save without thinking. */
function nextShotKey(lib, scene) {
  const keys = Object.keys(lib?.[scene] || {});
  let n = 0;
  for (const k of keys) {
    const v = parseInt(k, 10);
    if (!Number.isNaN(v) && v > n) n = v;
  }
  return String(n + 1);
}

function loadShot(node, scene, shot) {
  const data = readLibrary(node)?.[scene]?.[shot];
  if (!data) {
    toast("warn", "H3", `Nothing saved at ${scene || "?"} / shot ${shot || "?"}`);
    return;
  }
  for (const name of CAPTURED) {
    const w = widget(node, name);
    if (w && data[name] !== undefined) {
      w.value = data[name];
      if (w.inputEl) w.inputEl.value = data[name]; // multiline widgets are DOM-backed
    }
  }
  setFields(node, scene, shot);
  render(node);
  node.setDirtyCanvas(true, true);
  toast("success", "H3", `Loaded ${scene} / shot ${shot}`);
}

function deleteShot(node, scene, shot) {
  const lib = readLibrary(node);
  if (!lib?.[scene]?.[shot]) return;
  if (!window.confirm(`Delete ${scene} / shot ${shot}?`)) return;
  delete lib[scene][shot];
  if (!Object.keys(lib[scene]).length) delete lib[scene];
  writeLibrary(node, lib);
  toast("success", "H3", `Deleted ${scene} / shot ${shot}`);
}

function renameScene(node, scene) {
  const next = (window.prompt("Rename scene:", scene) || "").trim();
  if (!next || next === scene) return;
  const lib = readLibrary(node);
  if (lib[next] && !window.confirm(`"${next}" exists. Merge shots into it?`)) return;
  lib[next] = Object.assign(lib[next] || {}, lib[scene]);
  delete lib[scene];
  if (fields(node).scene === scene) setFields(node, next, fields(node).shot);
  writeLibrary(node, lib);
}

/** Step through the saved shots of the current scene. */
function step(node, dir) {
  const { scene, shot } = fields(node);
  const keys = Object.keys(readLibrary(node)?.[scene] || {}).sort(nat);
  if (!keys.length) {
    toast("warn", "H3", `No saved shots in "${scene || "?"}"`);
    return;
  }
  const i = keys.indexOf(shot);
  loadShot(node, scene, keys[(i < 0 ? 0 : i + dir + keys.length) % keys.length]);
}

// --------------------------------------------------------------------------- //

const CSS = {
  btn:
    "padding:4px 8px;border-radius:5px;border:1px solid #4a4a4a;background:#2e2e2e;" +
    "color:#ddd;cursor:pointer;font-size:11px;white-space:nowrap",
  row:
    "display:flex;align-items:center;gap:6px;padding:4px 6px;border-radius:4px;" +
    "cursor:pointer;font-size:11px",
};

function render(node) {
  const root = node._h3scenes;
  if (!root) return;
  const lib = readLibrary(node);
  const { scene: curScene, shot: curShot } = fields(node);
  const filter = (node._h3filter || "").toLowerCase();
  root.innerHTML = "";

  // --- action bar ---
  const bar = document.createElement("div");
  bar.style.cssText = "display:flex;gap:5px;margin-bottom:5px;flex-wrap:wrap";

  const save = document.createElement("button");
  save.textContent = "💾 save";
  save.title = "Save over the current scene / shot";
  save.style.cssText = CSS.btn;
  save.onclick = () => {
    const f = fields(node);
    saveShot(node, f.scene, f.shot);
  };

  const saveNew = document.createElement("button");
  saveNew.textContent = "✚ save as new shot";
  saveNew.title = "Save into the next free shot number in this scene";
  saveNew.style.cssText = CSS.btn;
  saveNew.onclick = () => {
    const f = fields(node);
    if (!f.scene) {
      toast("warn", "H3", "Set a scene name first.");
      return;
    }
    saveShot(node, f.scene, nextShotKey(lib, f.scene), { confirmOverwrite: false });
  };

  const prev = document.createElement("button");
  prev.textContent = "◀";
  prev.title = "Previous shot in this scene";
  prev.style.cssText = CSS.btn;
  prev.onclick = () => step(node, -1);

  const next = document.createElement("button");
  next.textContent = "▶";
  next.title = "Next shot in this scene";
  next.style.cssText = CSS.btn;
  next.onclick = () => step(node, +1);

  bar.append(save, saveNew, prev, next);
  root.appendChild(bar);

  // what the fields currently hold, whether saved or not
  const live = {};
  for (const f of CAPTURED) live[f] = widget(node, f)?.value;
  const head = document.createElement("div");
  head.style.cssText =
    "display:flex;gap:6px;align-items:center;margin-bottom:5px;padding:4px 6px;" +
    "background:#232323;border:1px solid #3a3a3a;border-radius:5px";
  const hl = document.createElement("span");
  hl.textContent = "subjects in view:";
  hl.style.cssText = "font:600 9px sans-serif;color:#7a7f83;flex:none";
  head.append(hl, chipRow(subjectsOf(live), { speakers: speakersOf(live) }));
  root.appendChild(head);

  // --- filter ---
  const scenes = Object.keys(lib).sort(nat);
  const totalShots = scenes.reduce(
    (n, s) => n + Object.keys(lib[s] || {}).length,
    0
  );

  if (totalShots > 6) {
    const f = document.createElement("input");
    f.type = "text";
    f.placeholder = "filter scenes and shots…";
    f.value = node._h3filter || "";
    f.style.cssText =
      "width:100%;box-sizing:border-box;margin-bottom:5px;padding:4px 6px;" +
      "border-radius:5px;border:1px solid #4a4a4a;background:#232323;color:#ddd;" +
      "font-size:11px";
    f.oninput = () => {
      node._h3filter = f.value;
      render(node);
      f.focus();
      f.setSelectionRange(f.value.length, f.value.length);
    };
    root.appendChild(f);
  }

  // --- scrollable browser ---
  const list = document.createElement("div");
  list.style.cssText =
    "max-height:190px;overflow-y:auto;border:1px solid #3a3a3a;border-radius:6px;" +
    "padding:4px;background:#1e1e1e";

  if (!scenes.length) {
    const empty = document.createElement("div");
    empty.textContent =
      "No saved shots yet. Set a scene name and shot number, then press save.";
    empty.style.cssText = "color:#6b7075;font-size:11px;padding:10px;text-align:center";
    list.appendChild(empty);
  }

  node._h3collapsed = node._h3collapsed || {};
  let shown = 0;

  for (const scene of scenes) {
    const shots = Object.keys(lib[scene] || {}).sort(nat);
    const matching = filter
      ? shots.filter(
          (sh) =>
            scene.toLowerCase().includes(filter) ||
            sh.toLowerCase().includes(filter) ||
            preview(lib[scene][sh]).toLowerCase().includes(filter)
        )
      : shots;
    if (!matching.length) continue;
    shown += matching.length;

    const collapsed = !!node._h3collapsed[scene] && !filter;

    const head = document.createElement("div");
    head.style.cssText =
      CSS.row +
      ";background:#2a2a2a;font-weight:600;color:#cfd3d6;margin:2px 0";
    head.onclick = () => {
      node._h3collapsed[scene] = !node._h3collapsed[scene];
      render(node);
    };

    const caret = document.createElement("span");
    caret.textContent = collapsed ? "▸" : "▾";
    caret.style.cssText = "width:10px;color:#7a7f83";

    const nameEl = document.createElement("span");
    nameEl.textContent = scene;
    nameEl.style.cssText = "flex:1;overflow:hidden;text-overflow:ellipsis";

    const count = document.createElement("span");
    count.textContent = `${matching.length} shot${matching.length === 1 ? "" : "s"}`;
    count.style.cssText = "color:#7a7f83;font-weight:400";

    const ren = document.createElement("span");
    ren.textContent = "✎";
    ren.title = "Rename scene";
    ren.style.cssText = "color:#7a7f83;cursor:pointer;padding:0 3px";
    ren.onclick = (e) => {
      e.stopPropagation();
      renameScene(node, scene);
    };

    head.append(caret, nameEl, count, ren);
    list.appendChild(head);

    if (collapsed) continue;

    for (const sh of matching) {
      const data = lib[scene][sh];
      const active = scene === curScene && sh === curShot;

      const row = document.createElement("div");
      row.style.cssText =
        CSS.row +
        `;margin-left:12px;color:#b9bec2;` +
        (active ? "background:#2f3b2a;border-left:2px solid #7ac943" : "");
      row.title = data?._saved ? `saved ${data._saved}` : "";
      row.onclick = () => loadShot(node, scene, sh);
      row.onmouseenter = () => {
        if (!active) row.style.background = "#282828";
      };
      row.onmouseleave = () => {
        if (!active) row.style.background = "";
      };

      const num = document.createElement("span");
      num.textContent = sh;
      num.style.cssText =
        "min-width:20px;text-align:center;color:#7ac943;font-weight:600";

      const txt = document.createElement("span");
      txt.textContent = preview(data);
      txt.style.cssText =
        "flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#8b9094";

      const del = document.createElement("span");
      del.textContent = "✕";
      del.title = "Delete this shot";
      del.style.cssText = "color:#a06060;cursor:pointer;padding:0 3px";
      del.onclick = (e) => {
        e.stopPropagation();
        deleteShot(node, scene, sh);
      };

      row.append(num, txt, del);
      list.appendChild(row);

      const chips = document.createElement("div");
      chips.style.cssText = "margin:0 0 3px 34px";
      chips.appendChild(chipRow(subjectsOf(data), { speakers: speakersOf(data) }));
      list.appendChild(chips);
    }
  }

  if (scenes.length && !shown) {
    const none = document.createElement("div");
    none.textContent = "nothing matches that filter";
    none.style.cssText = "color:#6b7075;font-size:11px;padding:10px;text-align:center";
    list.appendChild(none);
  }

  root.appendChild(list);
}

app.registerExtension({
  name: "bortevekk.h3.assembler.scenelibrary",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE) return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);

      // serialize:false — a cursor into the library, not node data. The library
      // itself lives in scene_library, which does serialize and so travels with
      // the saved workflow.
      this.addWidget("text", "h3_scene", "kitchen", () => render(this), {
        serialize: false,
      });
      this.addWidget("text", "h3_shot", "1", () => render(this), {
        serialize: false,
      });

      const root = document.createElement("div");
      root.style.cssText = "width:100%;padding:2px 0";
      this._h3scenes = root;
      this.addDOMWidget("h3_scene_browser", "div", root, { serialize: false });

      // scene_library must exist as a widget to be saved with the workflow, but
      // nobody should be editing the raw JSON by hand
      const lib = widget(this, "scene_library");
      if (lib) {
        lib.hidden = true;
        lib.computeSize = () => [0, -4];
        if (lib.inputEl) lib.inputEl.style.display = "none";
      }

      if (this.size[0] < 340) this.size[0] = 340;
      setTimeout(() => render(this), 60);
      return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      const lib = widget(this, "scene_library");
      if (lib?.inputEl) lib.inputEl.style.display = "none";
      setTimeout(() => render(this), 60);
      return r;
    };
  },
});

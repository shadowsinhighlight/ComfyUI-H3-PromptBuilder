import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE = "H3SubjectAdvanced";
const THUMB_DEFAULT = 128;

function thumbSize(node) {
  const v = parseInt(widget(node, "preview_size")?.value, 10);
  return Number.isFinite(v) && v > 0 ? v : THUMB_DEFAULT;
}

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

function readList(node) {
  try {
    const v = JSON.parse(widget(node, "image_list")?.value || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch (_) {
    return [];
  }
}

function writeList(node, list) {
  const w = widget(node, "image_list");
  if (w) {
    w.value = JSON.stringify(list);
    if (w.inputEl) w.inputEl.value = w.value;
  }
  render(node);
  node.setDirtyCanvas(true, true);
}

/** Full-size viewer. Thumbnails are cover-cropped, so seeing the whole frame
 *  matters when checking a reference. */
function openLightbox(list, index) {
  document.querySelectorAll(".h3-lightbox").forEach((e) => e.remove());
  let i = index;

  const ov = document.createElement("div");
  ov.className = "h3-lightbox";
  ov.style.cssText =
    "position:fixed;inset:0;z-index:20000;background:rgba(0,0,0,.88);" +
    "display:flex;align-items:center;justify-content:center;flex-direction:column;gap:10px";

  const img = document.createElement("img");
  img.style.cssText =
    "max-width:92vw;max-height:84vh;object-fit:contain;border-radius:6px;" +
    "box-shadow:0 10px 40px rgba(0,0,0,.6)";

  const cap = document.createElement("div");
  cap.style.cssText = "color:#bbb;font:12px sans-serif;text-align:center";

  const show = () => {
    img.src = thumbUrl(list[i]);
    cap.textContent = `${i + 1} / ${list.length}  ·  ${list[i]}`;
  };

  const nav = (d, e) => {
    e?.stopPropagation();
    i = (i + d + list.length) % list.length;
    show();
  };

  const mkArrow = (txt, d) => {
    const b = document.createElement("div");
    b.textContent = txt;
    b.style.cssText =
      "position:fixed;top:50%;transform:translateY(-50%);font-size:34px;color:#888;" +
      "cursor:pointer;padding:14px;user-select:none";
    b.onclick = (e) => nav(d, e);
    return b;
  };
  const prevA = mkArrow("‹", -1); prevA.style.left = "10px";
  const nextA = mkArrow("›", +1); nextA.style.right = "10px";

  const close = () => {
    ov.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (e) => {
    if (e.key === "Escape") close();
    else if (e.key === "ArrowLeft") nav(-1);
    else if (e.key === "ArrowRight") nav(+1);
  };

  ov.onclick = close;
  img.onclick = (e) => e.stopPropagation();
  document.addEventListener("keydown", onKey);

  ov.append(img, cap);
  if (list.length > 1) ov.append(prevA, nextA);
  document.body.appendChild(ov);
  show();
}

function thumbUrl(rel) {
  const i = rel.lastIndexOf("/");
  const p = new URLSearchParams({
    filename: i >= 0 ? rel.slice(i + 1) : rel,
    subfolder: i >= 0 ? rel.slice(0, i) : "",
    type: "input",
  });
  return api.apiURL(`/view?${p}`);
}

/**
 * Count images on upstream subject nodes so each tile shows the <Picture N> it
 * will actually receive, not just its position within this character. Depth
 * capped in case a workflow ends up with a cyclic link.
 */
function upstreamCount(node, depth = 0) {
  if (depth > 12) return 0;
  const input = node.inputs?.find((i) => i.name === "subjects");
  if (!input || input.link == null) return 0;
  const link = app.graph.links?.[input.link];
  if (!link) return 0;
  const up = app.graph.getNodeById(link.origin_id);
  if (!up) return 0;
  const own = up.type === NODE ? readList(up).length : 0;
  return upstreamCount(up, depth + 1) + own;
}

async function upload(node, fileList) {
  const files = Array.from(fileList).filter((f) => f.type.startsWith("image/"));
  if (!files.length) {
    toast("warn", "H3", "No image files in that drop.");
    return;
  }
  const nick = (widget(node, "nickname")?.value ?? "").trim() || "unnamed";
  files.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));

  const body = new FormData();
  body.append("nickname", nick);
  for (const f of files) body.append("images", f, f.name);

  node._h3busy = `uploading ${files.length}…`;
  render(node);

  try {
    const res = await api.fetchApi("/h3/upload", { method: "POST", body });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    const list = readList(node).concat(data.saved || []);
    node._h3busy = null;
    writeList(node, list.slice(0, 9));
    if (list.length > 9) toast("warn", "H3", "Trimmed to 9 images (the H3 limit).");
    const skipped = data.skipped?.length ? ` (${data.skipped.length} skipped)` : "";
    toast("success", "H3", `Added ${data.saved.length} image(s)${skipped}`);
  } catch (e) {
    node._h3busy = null;
    render(node);
    toast("error", "H3 upload failed", String(e));
  }
}

function pickFiles(node) {
  const input = document.createElement("input");
  input.type = "file";
  input.multiple = true;
  input.accept = "image/*";
  input.style.display = "none";
  document.body.appendChild(input);
  input.addEventListener("change", async () => {
    if (input.files?.length) await upload(node, input.files);
    input.remove();
  });
  input.click();
}

const PRESET_FIELDS = [
  "nickname", "kind", "description", "video_role",
  "voice_mode", "retention", "retention_note",
];

async function fetchPresets() {
  try {
    const res = await api.fetchApi("/h3/subjects");
    return await res.json();
  } catch (_) {
    return {};
  }
}

async function savePreset(node) {
  const nick = (widget(node, "nickname")?.value ?? "").trim();
  const name = (window.prompt("Save subject as:", nick || "hero") || "").trim();
  if (!name) return;
  const data = { images: readList(node) };
  for (const f of PRESET_FIELDS) {
    const w = widget(node, f);
    if (w) data[f] = w.value;
  }
  try {
    const res = await api.fetchApi("/h3/subjects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, data }),
    });
    const j = await res.json();
    if (j.error) throw new Error(j.error);
    toast("success", "H3", `Saved subject "${name}"`);
    render(node);
  } catch (e) {
    toast("error", "H3", String(e));
  }
}

function applyPreset(node, name, data) {
  for (const f of PRESET_FIELDS) {
    const w = widget(node, f);
    if (w && data[f] !== undefined) {
      w.value = data[f];
      if (w.inputEl) w.inputEl.value = data[f];
    }
  }
  writeList(node, Array.isArray(data.images) ? data.images : []);
  if (data.missing?.length) {
    toast("warn", "H3",
      `"${name}" loaded, but ${data.missing.length} image file(s) are missing.`);
  } else {
    toast("success", "H3", `Loaded subject "${name}"`);
  }
}

/** Thumbnail picker. A native <select> can't show images, so this is a small
 *  popup anchored to the button. */
async function openPresetMenu(node, anchor) {
  const presets = await fetchPresets();
  const names = Object.keys(presets).sort((a, b) =>
    a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" })
  );

  document.querySelectorAll(".h3-preset-menu").forEach((m) => m.remove());
  const menu = document.createElement("div");
  menu.className = "h3-preset-menu";
  const r = anchor.getBoundingClientRect();
  menu.style.cssText =
    `position:fixed;left:${r.left}px;top:${r.bottom + 4}px;z-index:10000;` +
    "min-width:230px;max-height:320px;overflow-y:auto;background:#242424;" +
    "border:1px solid #4a4a4a;border-radius:6px;padding:4px;" +
    "box-shadow:0 6px 20px rgba(0,0,0,.5)";

  if (!names.length) {
    const e = document.createElement("div");
    e.textContent = "No saved subjects yet.";
    e.style.cssText = "color:#6b7075;font-size:11px;padding:10px;text-align:center";
    menu.appendChild(e);
  }

  for (const name of names) {
    const d = presets[name];
    const row = document.createElement("div");
    row.style.cssText =
      "display:flex;align-items:center;gap:8px;padding:4px;border-radius:4px;" +
      "cursor:pointer;font-size:11px;color:#ccc";
    row.onmouseenter = () => (row.style.background = "#303030");
    row.onmouseleave = () => (row.style.background = "");
    row.onclick = () => {
      applyPreset(node, name, d);
      menu.remove();
    };

    const thumb = document.createElement("div");
    thumb.style.cssText =
      "width:34px;height:34px;border-radius:4px;background:#1a1a1a;flex:none;" +
      "overflow:hidden;border:1px solid #3a3a3a";
    if (d.thumb) {
      const im = document.createElement("img");
      im.src = thumbUrl(d.thumb);
      im.style.cssText = "width:100%;height:100%;object-fit:cover";
      im.onerror = () => (thumb.textContent = "?");
      thumb.appendChild(im);
    }

    const label = document.createElement("div");
    label.style.cssText = "flex:1;overflow:hidden";
    const t = document.createElement("div");
    t.textContent = name;
    t.style.cssText = "font-weight:600;white-space:nowrap;text-overflow:ellipsis;overflow:hidden";
    const sub = document.createElement("div");
    const n = (d.images || []).length;
    sub.textContent =
      `${d.kind || "person"} · ${n} image${n === 1 ? "" : "s"}` +
      (d.missing?.length ? ` · ${d.missing.length} missing` : "");
    sub.style.cssText = `font-size:10px;color:${d.missing?.length ? "#e09090" : "#7a7f83"}`;
    label.append(t, sub);

    const del = document.createElement("span");
    del.textContent = "✕";
    del.style.cssText = "color:#a06060;padding:0 4px;cursor:pointer";
    del.onclick = async (e) => {
      e.stopPropagation();
      if (!window.confirm(`Delete saved subject "${name}"?`)) return;
      await api.fetchApi("/h3/subjects/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      menu.remove();
      openPresetMenu(node, anchor);
    };

    row.append(thumb, label, del);
    menu.appendChild(row);
  }

  document.body.appendChild(menu);
  setTimeout(() => {
    const close = (e) => {
      if (!menu.contains(e.target)) {
        menu.remove();
        document.removeEventListener("mousedown", close);
      }
    };
    document.addEventListener("mousedown", close);
  }, 0);
}

function render(node) {
  const root = node._h3gallery;
  if (!root) return;
  const list = readList(node);
  const offset = upstreamCount(node);
  root.innerHTML = "";

  const presetBar = document.createElement("div");
  presetBar.style.cssText = "display:flex;gap:5px;margin-bottom:5px";
  const loadBtn = document.createElement("button");
  loadBtn.textContent = "▾ saved subjects";
  loadBtn.style.cssText =
    "flex:1;padding:5px 8px;border-radius:5px;border:1px solid #4a4a4a;" +
    "background:#2e2e2e;color:#ddd;cursor:pointer;font-size:11px";
  loadBtn.onclick = () => openPresetMenu(node, loadBtn);
  const saveBtn = document.createElement("button");
  saveBtn.textContent = "💾";
  saveBtn.title = "Save this subject for reuse in other workflows";
  saveBtn.style.cssText =
    "padding:5px 9px;border-radius:5px;border:1px solid #4a4a4a;" +
    "background:#2e2e2e;color:#ddd;cursor:pointer;font-size:11px";
  saveBtn.onclick = () => savePreset(node);
  presetBar.append(loadBtn, saveBtn);
  root.appendChild(presetBar);

  const bar = document.createElement("div");
  bar.style.cssText =
    "display:flex;gap:6px;align-items:center;margin-bottom:6px;flex-wrap:wrap";

  const add = document.createElement("button");
  add.textContent = "＋ add images";
  add.style.cssText =
    "flex:1;min-width:96px;padding:5px 8px;border-radius:5px;border:1px solid #4a4a4a;" +
    "background:#2e2e2e;color:#ddd;cursor:pointer;font-size:11px";
  add.onclick = () => pickFiles(node);
  bar.appendChild(add);

  if (list.length) {
    const clear = document.createElement("button");
    clear.textContent = `🗑 remove all (${list.length})`;
    clear.style.cssText =
      "padding:5px 8px;border-radius:5px;border:1px solid #5a3a3a;background:#2e2020;" +
      "color:#e09090;cursor:pointer;font-size:11px";
    clear.onclick = () => {
      if (window.confirm(`Remove all ${list.length} images from @${(widget(node,"nickname")?.value||"").trim()}?`))
        writeList(node, []);
    };
    bar.appendChild(clear);
  }
  root.appendChild(bar);

  if (node._h3busy) {
    const b = document.createElement("div");
    b.textContent = node._h3busy;
    b.style.cssText = "color:#9aa0a6;font-size:11px;padding:4px";
    root.appendChild(b);
    return;
  }

  // Clamp to what the node can actually show. Without this a large
  // preview_size pushes tiles past the node's right edge and the tray runs off
  // the bottom, because the DOM widget does not reserve height for it.
  const avail = Math.max(60, (root.clientWidth || node.size[0] - 30) - 22);
  const size = Math.min(thumbSize(node), avail);
  const rowsShown = size >= 384 ? 1.6 : size >= 192 ? 2.4 : 3.2;
  const trayH = Math.round(size * rowsShown + 18);

  const grid = document.createElement("div");
  grid.style.cssText = `display:flex;flex-wrap:wrap;gap:6px;box-sizing:border-box;
    width:100%;max-height:${trayH}px;overflow-y:auto;overflow-x:hidden;
    border:1px dashed #4a4a4a;border-radius:6px;padding:6px;align-content:flex-start`;
  grid.dataset.h3drop = "1";

  if (!list.length) {
    const hint = document.createElement("div");
    hint.textContent = "drop images here";
    hint.style.cssText =
      `color:#6b7075;font-size:11px;display:flex;align-items:center;` +
      `justify-content:center;width:100%;height:${Math.min(size, 150)}px;` +
      `pointer-events:none`;
    grid.appendChild(hint);
  }

  list.forEach((rel, i) => {
    const tile = document.createElement("div");
    tile.draggable = true;
    tile.style.cssText =
      `position:relative;width:${size}px;height:${size}px;max-width:100%;` +
      `flex:0 0 auto;box-sizing:border-box;border-radius:5px;overflow:hidden;` +
      `background:#1c1c1c;border:1px solid #3a3a3a;cursor:pointer`;
    tile.title = "Click to view full size · drag to reorder";
    tile.onclick = (e) => {
      if (e.target === x) return;
      openLightbox(readList(node), i);
    };

    const img = document.createElement("img");
    img.src = thumbUrl(rel);
    img.title = rel;
    img.style.cssText = "width:100%;height:100%;object-fit:cover;display:block";
    img.draggable = false;
    // A workflow opened on another machine may reference files that aren't
    // there. Name the missing file on the tile rather than showing a broken
    // image, so it can be found and replaced.
    img.onerror = () => {
      tile.style.border = "1px solid #a05050";
      tile.style.background = "#2a1c1c";
      img.remove();
      const warnEl = document.createElement("div");
      warnEl.style.cssText =
        "padding:3px;font-size:8px;color:#e09090;height:100%;box-sizing:border-box;" +
        "display:flex;flex-direction:column;justify-content:center;text-align:center;" +
        "word-break:break-all;line-height:1.25";
      const f = rel.split("/").pop();
      warnEl.style.fontSize = `${Math.max(8, Math.min(13, Math.round(size / 11)))}px`;
      warnEl.innerHTML =
        `<div style="font-size:13px">⚠</div><div>missing</div>` +
        `<div style="color:#c08080">${f}</div>`;
      warnEl.title = `Missing: ${rel}\nDrop the file on this node to replace it, or ✕ to remove.`;
      tile.insertBefore(warnEl, tile.firstChild);
    };
    tile.appendChild(img);

    const badge = document.createElement("div");
    badge.textContent = offset + i + 1;
    const chrome = Math.max(9, Math.min(16, Math.round(size / 9)));
    badge.style.cssText =
      "position:absolute;left:0;top:0;background:rgba(0,0,0,.72);color:#7ac943;" +
      `font:bold ${chrome}px sans-serif;padding:1px 5px;border-bottom-right-radius:4px`;
    tile.appendChild(badge);

    const x = document.createElement("div");
    x.textContent = "✕";
    x.title = "Remove";
    const xs = Math.max(16, Math.min(28, Math.round(size / 5)));
    x.style.cssText =
      `position:absolute;right:0;top:0;width:${xs}px;height:${xs}px;` +
      `line-height:${xs}px;text-align:center;background:rgba(0,0,0,.72);` +
      `color:#e09090;font-size:${Math.round(xs * 0.55)}px;cursor:pointer;` +
      "border-bottom-left-radius:4px";
    x.onclick = (e) => {
      e.stopPropagation();
      const next = readList(node);
      next.splice(i, 1);
      writeList(node, next);
    };
    tile.appendChild(x);

    // drag to reorder — tray order decides <Picture N>
    tile.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/h3-index", String(i));
      e.dataTransfer.effectAllowed = "move";
    });
    tile.addEventListener("dragover", (e) => {
      if (e.dataTransfer.types.includes("text/h3-index")) {
        e.preventDefault();
        tile.style.outline = "2px solid #7ac943";
      }
    });
    tile.addEventListener("dragleave", () => (tile.style.outline = ""));
    tile.addEventListener("drop", (e) => {
      tile.style.outline = "";
      const from = parseInt(e.dataTransfer.getData("text/h3-index"), 10);
      if (Number.isNaN(from) || from === i) return;
      e.preventDefault();
      e.stopPropagation();
      const next = readList(node);
      next.splice(i, 0, next.splice(from, 1)[0]);
      writeList(node, next);
    });

    grid.appendChild(tile);
  });

  // file drops anywhere on the tray
  grid.addEventListener("dragover", (e) => {
    if (Array.from(e.dataTransfer.types).includes("Files")) {
      e.preventDefault();
      grid.style.borderColor = "#7ac943";
    }
  });
  grid.addEventListener("dragleave", () => (grid.style.borderColor = "#4a4a4a"));
  grid.addEventListener("drop", async (e) => {
    grid.style.borderColor = "#4a4a4a";
    if (!e.dataTransfer.files?.length) return;
    e.preventDefault();
    e.stopPropagation();
    await upload(node, e.dataTransfer.files);
  });

  root.appendChild(grid);
}

app.registerExtension({
  name: "bortevekk.h3.subjectadvanced.gallery",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE) return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);

      const root = document.createElement("div");
      root.style.cssText = "width:100%;padding:2px 0;box-sizing:border-box;overflow:hidden";
      this._h3gallery = root;
      this.addDOMWidget("h3_gallery", "div", root, { serialize: false });

      // image_list must exist as a widget to be saved with the workflow, but
      // nobody should be editing the raw JSON by hand
      const lw = widget(this, "image_list");
      if (lw) {
        lw.hidden = true;
        lw.computeSize = () => [0, -4];
        if (lw.inputEl) lw.inputEl.style.display = "none";
      }

      const ps = widget(this, "preview_size");
      if (ps) {
        const prev = ps.callback;
        ps.callback = (v) => {
          prev?.call(this, v);
          const want = thumbSize(this) + 40;
          if (this.size[0] < want) this.size[0] = Math.min(want, 1100);
          render(this);
        };
      }

      if (this.size[0] < 300) this.size[0] = 300;
      setTimeout(() => render(this), 60);
      return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      const lw = widget(this, "image_list");
      if (lw?.inputEl) lw.inputEl.style.display = "none";
      setTimeout(() => render(this), 60);
      return r;
    };

    // node-level drop, for when the pointer isn't over the tray
    nodeType.prototype.onDragOver = function (e) {
      return Array.from(e?.dataTransfer?.types ?? []).includes("Files");
    };
    nodeType.prototype.onDragDrop = async function (e) {
      if (!e?.dataTransfer?.files?.length) return false;
      await upload(this, e.dataTransfer.files);
      return true;
    };
  },
});

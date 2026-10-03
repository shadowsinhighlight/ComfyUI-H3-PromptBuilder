import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE = "H3Studio";
const KINDS = ["person", "environment", "object", "motion", "effect", "style"];
const VIDEO_ROLES = ["auto", "motion", "edit source", "continuation", "camera + timing", "audio source only"];
const PPS_MIN = 12;   // pixels per second, zoomed out
const PPS_MAX = 220;

const S = {
  btn: "padding:4px 8px;border-radius:5px;border:1px solid #4a4a4a;background:#2e2e2e;color:#ddd;cursor:pointer;font-size:11px;white-space:nowrap",
  inp: "width:100%;box-sizing:border-box;padding:3px 5px;border-radius:4px;border:1px solid #3f3f3f;background:#1c1c1c;color:#ddd;font-size:11px",
  lbl: "font:600 9px sans-serif;color:#7a7f83;letter-spacing:.04em;margin:4px 0 2px",
};

function widget(node, name) {
  return node.widgets?.find((w) => w.name === name);
}
function toast(sev, sum, det) {
  try { app.extensionManager?.toast?.add({ severity: sev, summary: sum, detail: det, life: 3500 }); }
  catch (_) { console.log(`[H3] ${sum}: ${det ?? ""}`); }
}
function readJSON(node, name, fallback) {
  try {
    const v = JSON.parse(widget(node, name)?.value || "");
    return v ?? fallback;
  } catch (_) { return fallback; }
}
function writeJSON(node, name, value) {
  const w = widget(node, name);
  if (w) {
    w.value = JSON.stringify(value);
    if (w.inputEl) w.inputEl.value = w.value;
  }
  node.setDirtyCanvas(true, true);
}
const subjects = (n) => readJSON(n, "subjects_json", []) || [];
const shots = (n) => readJSON(n, "shots_json", []) || [];
const thumbUrl = (rel) => {
  const i = rel.lastIndexOf("/");
  return api.apiURL(`/view?${new URLSearchParams({
    filename: i >= 0 ? rel.slice(i + 1) : rel,
    subfolder: i >= 0 ? rel.slice(0, i) : "",
    type: "input",
  })}`);
};
const fmt = (s) => {
  const m = Math.floor(Math.max(0, s) / 60);
  return `${String(m).padStart(2, "0")}:${(Math.max(0, s) - m * 60).toFixed(2).padStart(5, "0")}`;
};

// --------------------------------------------------------------------------- //
// subjects
// --------------------------------------------------------------------------- //

async function uploadTo(node, idx, files) {
  const imgs = Array.from(files).filter((f) => f.type.startsWith("image/"));
  if (!imgs.length) return;
  const list = subjects(node);
  const nick = list[idx]?.nickname || "unnamed";
  imgs.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  const body = new FormData();
  body.append("nickname", nick);
  for (const f of imgs) body.append("images", f, f.name);
  try {
    const res = await api.fetchApi("/h3/upload", { method: "POST", body });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    list[idx].images = (list[idx].images || []).concat(data.saved).slice(0, 9);
    writeJSON(node, "subjects_json", list);
    draw(node);
  } catch (e) {
    toast("error", "H3 upload failed", String(e));
  }
}

function subjectCard(node, spec, idx) {
  const list = subjects(node);
  const card = document.createElement("div");
  card.style.cssText =
    "border:1px solid #3a3a3a;border-radius:6px;padding:6px;margin-bottom:6px;background:#212121";

  const commit = () => { writeJSON(node, "subjects_json", list); };

  const top = document.createElement("div");
  top.style.cssText = "display:flex;gap:5px;align-items:center;margin-bottom:4px";

  const nick = document.createElement("input");
  nick.value = spec.nickname || "";
  nick.placeholder = "nickname";
  nick.style.cssText = S.inp + ";flex:1;font-weight:600;color:#7ac943";
  nick.oninput = () => { list[idx].nickname = nick.value.trim(); commit(); };

  const kind = document.createElement("select");
  kind.style.cssText = S.inp + ";width:auto";
  for (const k of KINDS) {
    const o = document.createElement("option");
    o.value = o.textContent = k;
    if ((spec.kind || "person") === k) o.selected = true;
    kind.appendChild(o);
  }
  kind.onchange = () => { list[idx].kind = kind.value; commit(); draw(node); };

  const up = document.createElement("button");
  up.textContent = "↑"; up.style.cssText = S.btn;
  up.title = "Move earlier — changes this subject's Picture numbers";
  up.onclick = () => {
    if (idx === 0) return;
    [list[idx - 1], list[idx]] = [list[idx], list[idx - 1]];
    commit(); draw(node);
  };

  const del = document.createElement("button");
  del.textContent = "✕"; del.style.cssText = S.btn + ";color:#e09090";
  del.onclick = () => {
    if (!window.confirm(`Remove subject @${spec.nickname}?`)) return;
    list.splice(idx, 1); commit(); draw(node);
  };

  top.append(nick, kind, up, del);
  card.appendChild(top);

  const desc = document.createElement("textarea");
  desc.value = spec.description || "";
  desc.placeholder = "fixed visual traits only — hair, build, garments";
  desc.rows = 2;
  desc.style.cssText = S.inp + ";resize:vertical;font-family:inherit";
  desc.oninput = () => { list[idx].description = desc.value; commit(); };
  card.appendChild(desc);

  // media assignment
  const media = document.createElement("div");
  media.style.cssText = "display:flex;gap:5px;align-items:center;margin-top:4px;flex-wrap:wrap";
  const mkSel = (label, key, opts) => {
    const s = document.createElement("select");
    s.style.cssText = S.inp + ";width:auto";
    for (const [v, t] of opts) {
      const o = document.createElement("option");
      o.value = v; o.textContent = t;
      if (String(spec[key] ?? "0") === String(v)) o.selected = true;
      s.appendChild(o);
    }
    s.onchange = () => { list[idx][key] = s.value; commit(); draw(node); };
    const wrap = document.createElement("span");
    wrap.style.cssText = "font:9px sans-serif;color:#7a7f83;display:flex;gap:3px;align-items:center";
    wrap.append(label + ":", s);
    return wrap;
  };
  media.append(
    mkSel("video", "video", [["0", "none"], ["1", "video_1"], ["2", "video_2"]]),
    mkSel("audio", "audio", [["0", "none"], ["1", "audio_1"]])
  );
  if (String(spec.video || "0") !== "0") {
    const r = document.createElement("select");
    r.style.cssText = S.inp + ";width:auto";
    for (const v of VIDEO_ROLES) {
      const o = document.createElement("option");
      o.value = o.textContent = v;
      if ((spec.video_role || "auto") === v) o.selected = true;
      r.appendChild(o);
    }
    r.onchange = () => { list[idx].video_role = r.value; commit(); };
    const w = document.createElement("span");
    w.style.cssText = "font:9px sans-serif;color:#7a7f83;display:flex;gap:3px;align-items:center";
    w.append("purpose:", r);
    media.appendChild(w);
  }
  card.appendChild(media);

  // gallery
  const size = parseInt(widget(node, "preview_size")?.value, 10) || 96;
  const tray = document.createElement("div");
  tray.style.cssText =
    `display:flex;flex-wrap:wrap;gap:4px;margin-top:5px;padding:4px;box-sizing:border-box;` +
    `border:1px dashed #454545;border-radius:5px;max-height:${size * 2 + 16}px;overflow-y:auto`;

  (spec.images || []).forEach((rel, i) => {
    const t = document.createElement("div");
    const eff = Math.min(size, 160);
    t.style.cssText =
      `position:relative;width:${eff}px;height:${eff}px;flex:0 0 auto;border-radius:4px;` +
      `overflow:hidden;background:#151515;border:1px solid #3a3a3a;cursor:pointer`;
    const im = document.createElement("img");
    im.src = thumbUrl(rel);
    im.style.cssText = "width:100%;height:100%;object-fit:cover";
    im.onerror = () => {
      t.style.borderColor = "#a05050";
      t.innerHTML = `<div style="font:8px sans-serif;color:#e09090;padding:3px;text-align:center">⚠ missing<br>${rel.split("/").pop()}</div>`;
    };
    t.appendChild(im);
    const x = document.createElement("div");
    x.textContent = "✕";
    x.style.cssText =
      "position:absolute;right:0;top:0;background:rgba(0,0,0,.7);color:#e09090;" +
      "font-size:9px;padding:1px 4px;cursor:pointer";
    x.onclick = (e) => { e.stopPropagation(); list[idx].images.splice(i, 1); commit(); draw(node); };
    t.appendChild(x);
    t.onclick = () => window.open(thumbUrl(rel), "_blank");
    tray.appendChild(t);
  });

  const add = document.createElement("div");
  add.textContent = "＋";
  add.title = "Add images (or drop them here)";
  const eff = Math.min(size, 160);
  add.style.cssText =
    `width:${eff}px;height:${eff}px;flex:0 0 auto;border:1px dashed #4a4a4a;border-radius:4px;` +
    "display:flex;align-items:center;justify-content:center;color:#6b7075;cursor:pointer;font-size:20px";
  add.onclick = () => {
    const inp = document.createElement("input");
    inp.type = "file"; inp.multiple = true; inp.accept = "image/*";
    inp.onchange = () => inp.files?.length && uploadTo(node, idx, inp.files);
    inp.click();
  };
  tray.appendChild(add);

  tray.ondragover = (e) => {
    if (Array.from(e.dataTransfer.types).includes("Files")) {
      e.preventDefault(); tray.style.borderColor = "#7ac943";
    }
  };
  tray.ondragleave = () => (tray.style.borderColor = "#454545");
  tray.ondrop = async (e) => {
    tray.style.borderColor = "#454545";
    if (!e.dataTransfer.files?.length) return;
    e.preventDefault(); e.stopPropagation();
    await uploadTo(node, idx, e.dataTransfer.files);
  };

  card.appendChild(tray);
  return card;
}

// --------------------------------------------------------------------------- //
// timeline
// --------------------------------------------------------------------------- //

function timeline(node) {
  const list = shots(node);
  const pps = node._h3pps || 60;
  const wrap = document.createElement("div");

  const bar = document.createElement("div");
  bar.style.cssText = "display:flex;gap:5px;align-items:center;margin-bottom:5px;flex-wrap:wrap";
  const addBtn = document.createElement("button");
  addBtn.textContent = "＋ shot";
  addBtn.style.cssText = S.btn;
  addBtn.onclick = () => {
    const end = list.reduce((m, s) => Math.max(m, (+s.start || 0) + (+s.dur || 0)), 0);
    list.push({ start: end, dur: 4, text: "", camera: "", sound: "" });
    writeJSON(node, "shots_json", list);
    node._h3sel = list.length - 1;
    draw(node);
  };
  const zoomOut = document.createElement("button");
  zoomOut.textContent = "−"; zoomOut.title = "Zoom out"; zoomOut.style.cssText = S.btn;
  zoomOut.onclick = () => { node._h3pps = Math.max(PPS_MIN, pps / 1.5); draw(node); };
  const zoomIn = document.createElement("button");
  zoomIn.textContent = "+"; zoomIn.title = "Zoom in"; zoomIn.style.cssText = S.btn;
  zoomIn.onclick = () => { node._h3pps = Math.min(PPS_MAX, pps * 1.5); draw(node); };
  const total = list.reduce((m, s) => Math.max(m, (+s.start || 0) + (+s.dur || 0)), 0);
  const tot = document.createElement("span");
  tot.textContent = `${list.length} shot(s) · ${fmt(total)}`;
  tot.style.cssText = "font:10px sans-serif;color:#7a7f83;margin-left:auto";
  bar.append(addBtn, zoomOut, zoomIn, tot);
  wrap.appendChild(bar);

  // ruler + track
  const scroll = document.createElement("div");
  scroll.style.cssText =
    "overflow-x:auto;overflow-y:hidden;border:1px solid #3a3a3a;border-radius:6px;background:#1a1a1a";
  const inner = document.createElement("div");
  const width = Math.max(240, (total + 4) * pps);
  inner.style.cssText = `position:relative;width:${width}px;height:74px`;

  const ruler = document.createElement("div");
  ruler.style.cssText = "position:absolute;left:0;top:0;right:0;height:16px;border-bottom:1px solid #333";
  const stepSec = pps > 120 ? 0.5 : pps > 45 ? 1 : pps > 22 ? 2 : 5;
  for (let t = 0; t <= total + 4; t += stepSec) {
    const tick = document.createElement("div");
    tick.style.cssText =
      `position:absolute;left:${t * pps}px;top:0;height:16px;border-left:1px solid #333;` +
      "padding-left:3px;font:8px sans-serif;color:#666;white-space:nowrap";
    tick.textContent = fmt(t);
    ruler.appendChild(tick);
  }
  inner.appendChild(ruler);

  list.forEach((sh, i) => {
    const start = +sh.start || 0, dur = Math.max(0.2, +sh.dur || 0);
    const b = document.createElement("div");
    const sel = node._h3sel === i;
    b.style.cssText =
      `position:absolute;left:${start * pps}px;top:22px;width:${dur * pps}px;height:44px;` +
      `border-radius:4px;box-sizing:border-box;cursor:grab;overflow:hidden;` +
      `background:${sel ? "#33502c" : "#2c3b46"};` +
      `border:1px solid ${sel ? "#7ac943" : "#44586a"}`;
    b.title = `Shot ${i + 1} · ${fmt(start)} → ${fmt(start + dur)}\nDrag to move, drag the right edge to resize`;

    const lab = document.createElement("div");
    lab.style.cssText = "padding:3px 5px;font:9px sans-serif;color:#cfd3d6;pointer-events:none";
    lab.innerHTML =
      `<b>Shot ${i + 1}</b> ${fmt(start)}<br>` +
      `<span style="color:#8b9094">${(sh.text || "empty").slice(0, 42)}</span>`;
    b.appendChild(lab);

    const grip = document.createElement("div");
    grip.style.cssText =
      "position:absolute;right:0;top:0;width:7px;height:100%;cursor:ew-resize;background:rgba(255,255,255,.10)";
    b.appendChild(grip);

    // drag to move / resize, snapped to a quarter second
    const startDrag = (e, mode) => {
      e.preventDefault(); e.stopPropagation();
      node._h3sel = i; draw(node);
      const x0 = e.clientX, s0 = start, d0 = dur;
      const move = (ev) => {
        const d = (ev.clientX - x0) / pps;
        const cur = shots(node);
        if (mode === "move") cur[i].start = Math.max(0, Math.round((s0 + d) * 4) / 4);
        else cur[i].dur = Math.max(0.25, Math.round((d0 + d) * 4) / 4);
        writeJSON(node, "shots_json", cur);
        draw(node);
      };
      const up = () => {
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    };
    b.onmousedown = (e) => { if (e.target !== grip) startDrag(e, "move"); };
    grip.onmousedown = (e) => startDrag(e, "resize");

    inner.appendChild(b);
  });

  scroll.appendChild(inner);
  wrap.appendChild(scroll);

  // editor for the selected shot
  const i = node._h3sel;
  if (i != null && list[i]) {
    const ed = document.createElement("div");
    ed.style.cssText =
      "margin-top:6px;border:1px solid #3a3a3a;border-radius:6px;padding:6px;background:#212121";
    const head = document.createElement("div");
    head.style.cssText = "display:flex;gap:5px;align-items:center;margin-bottom:4px";
    const h = document.createElement("span");
    h.textContent = `Shot ${i + 1}`;
    h.style.cssText = "font:600 11px sans-serif;color:#7ac943;flex:1";
    const rm = document.createElement("button");
    rm.textContent = "✕ delete shot";
    rm.style.cssText = S.btn + ";color:#e09090";
    rm.onclick = () => {
      const cur = shots(node); cur.splice(i, 1);
      writeJSON(node, "shots_json", cur);
      node._h3sel = null; draw(node);
    };
    head.append(h, rm);
    ed.appendChild(head);

    const field = (label, key, rows, ph) => {
      const l = document.createElement("div");
      l.textContent = label; l.style.cssText = S.lbl;
      const t = document.createElement("textarea");
      t.value = list[i][key] || ""; t.rows = rows; t.placeholder = ph;
      t.style.cssText = S.inp + ";resize:vertical;font-family:inherit";
      t.oninput = () => {
        const cur = shots(node);
        cur[i][key] = t.value;
        writeJSON(node, "shots_json", cur);
      };
      ed.append(l, t);
    };
    field("ACTION", "text", 3, "What is in frame and who does what. Use @nickname, @nickname! to speak.");
    field("CAMERA", "camera", 1, "pushes in slowly / holds static / tracks right");
    field("SOUND", "sound", 1, "Sounds tied to this moment.");
    wrap.appendChild(ed);
  }

  return wrap;
}

// --------------------------------------------------------------------------- //

function draw(node) {
  const root = node._h3studio;
  if (!root) return;
  root.innerHTML = "";
  const tab = node._h3tab || "subjects";

  const tabs = document.createElement("div");
  tabs.style.cssText = "display:flex;gap:4px;margin-bottom:6px";
  for (const [id, label] of [["subjects", "SUBJECTS"], ["timeline", "TIMELINE"]]) {
    const b = document.createElement("button");
    const on = tab === id;
    b.textContent = label + (id === "subjects" ? ` (${subjects(node).length})` : ` (${shots(node).length})`);
    b.style.cssText =
      S.btn + ";flex:1;font-weight:600;letter-spacing:.05em;" +
      (on ? "background:#33502c;border-color:#7ac943;color:#cfe8bf" : "");
    b.onclick = () => { node._h3tab = id; draw(node); };
    tabs.appendChild(b);
  }
  root.appendChild(tabs);

  if (tab === "subjects") {
    const list = subjects(node);
    list.forEach((s, i) => root.appendChild(subjectCard(node, s, i)));
    const add = document.createElement("button");
    add.textContent = "＋ add subject";
    add.style.cssText = S.btn + ";width:100%";
    add.onclick = () => {
      const cur = subjects(node);
      cur.push({ nickname: `subject${cur.length + 1}`, kind: "person",
                 description: "", images: [], video: "0", audio: "0",
                 video_role: "auto" });
      writeJSON(node, "subjects_json", cur);
      draw(node);
    };
    root.appendChild(add);
  } else {
    root.appendChild(timeline(node));
  }
}

app.registerExtension({
  name: "bortevekk.h3.studio",

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== NODE) return;

    const onCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const r = onCreated?.apply(this, arguments);
      const root = document.createElement("div");
      root.style.cssText = "width:100%;box-sizing:border-box;padding:2px 0;overflow:hidden";
      this._h3studio = root;
      this.addDOMWidget("h3_studio_panel", "div", root, { serialize: false });

      for (const n of ["subjects_json", "shots_json", "scene_library"]) {
        const w = widget(this, n);
        if (w) {
          w.hidden = true;
          w.computeSize = () => [0, -4];
          if (w.inputEl) w.inputEl.style.display = "none";
        }
      }
      const ps = widget(this, "preview_size");
      if (ps) {
        const prev = ps.callback;
        ps.callback = (v) => { prev?.call(this, v); draw(this); };
      }

      this._h3tab = "subjects";
      this._h3pps = 60;
      if (this.size[0] < 460) this.size[0] = 460;
      if (this.size[1] < 620) this.size[1] = 620;
      setTimeout(() => draw(this), 60);
      return r;
    };

    const onConfigure = nodeType.prototype.onConfigure;
    nodeType.prototype.onConfigure = function () {
      const r = onConfigure?.apply(this, arguments);
      for (const n of ["subjects_json", "shots_json", "scene_library"]) {
        const w = widget(this, n);
        if (w?.inputEl) w.inputEl.style.display = "none";
      }
      setTimeout(() => draw(this), 60);
      return r;
    };
  },
});

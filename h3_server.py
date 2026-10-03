"""
Upload endpoint for the H3 Subject Advanced image gallery.

Files land in ComfyUI/input/h3/<nickname>/ and the browser stores the returned
relative paths in the node's image_list widget, so the gallery contents travel
with the saved workflow.

The nickname arrives from the browser and becomes part of a path, so it is
sanitised here rather than trusted, and the resolved directory is verified to sit
inside the input directory before anything is written.
"""

import os
import re

try:
    import folder_paths
    from aiohttp import web
    from server import PromptServer
    _COMFY = True
except Exception:  # pragma: no cover
    folder_paths = web = PromptServer = None
    _COMFY = False

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff")
SUBROOT = "h3"


def sanitize_component(name, fallback="unnamed"):
    name = os.path.basename(str(name or "")).strip()
    name = re.sub(r"[^A-Za-z0-9_\-]", "_", name).strip("._")
    return name or fallback


def sanitize_filename(name):
    name = os.path.basename(str(name or "")).strip()
    stem, ext = os.path.splitext(name)
    if ext.lower() not in IMAGE_EXTS:
        return None
    stem = re.sub(r"[^A-Za-z0-9_\-. ]", "_", stem).strip("._ ")
    return (stem or "image") + ext.lower()


def unique_path(directory, filename):
    """Never overwrite: foo.png -> foo_1.png -> foo_2.png"""
    stem, ext = os.path.splitext(filename)
    candidate, n = filename, 1
    while os.path.exists(os.path.join(directory, candidate)):
        candidate = f"{stem}_{n}{ext}"
        n += 1
    return candidate


def target_dir(nickname):
    base = os.path.abspath(folder_paths.get_input_directory())
    rel = f"{SUBROOT}/{sanitize_component(nickname)}"
    path = os.path.abspath(os.path.join(base, *rel.split("/")))
    if not (path == base or path.startswith(base + os.sep)):
        raise ValueError("resolved path escapes the input directory")
    return path, rel


def _presets_path():
    try:
        base = folder_paths.get_user_directory()
    except Exception:
        base = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "h3_subject_presets.json")


def _read_presets():
    p = _presets_path()
    if not os.path.isfile(p):
        return {}
    try:
        import json
        with open(p, "r", encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def _write_presets(data):
    import json
    p = _presets_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)   # atomic; an interrupted write can't corrupt the library


if _COMFY:

    @PromptServer.instance.routes.post("/h3/upload")
    async def h3_upload(request):
        try:
            data = await request.post()
        except Exception as e:
            return web.json_response({"error": f"bad form data: {e}"}, status=400)

        try:
            directory, rel = target_dir(data.get("nickname", ""))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)

        os.makedirs(directory, exist_ok=True)
        saved, skipped = [], []

        for field in data.getall("images", []):
            raw_name = getattr(field, "filename", None)
            handle = getattr(field, "file", None)
            if not raw_name or handle is None:
                continue
            clean = sanitize_filename(raw_name)
            if clean is None:
                skipped.append(f"{raw_name} (not an image)")
                continue
            final = unique_path(directory, clean)
            try:
                handle.seek(0)
                with open(os.path.join(directory, final), "wb") as out:
                    while True:
                        chunk = handle.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)
                saved.append(f"{rel}/{final}")
            except Exception as e:
                skipped.append(f"{raw_name} ({e})")

        return web.json_response({"folder": rel, "saved": saved, "skipped": skipped})


    # ---- subject presets ------------------------------------------------- #
    # Stored on disk rather than in the workflow so a character can be reused
    # across projects, which is the whole point of saving one.

    @PromptServer.instance.routes.get("/h3/subjects")
    async def h3_subjects_list(request):
        presets = _read_presets()
        out = {}
        for name, data in presets.items():
            imgs = data.get("images") or []
            missing = [
                p for p in imgs
                if not os.path.isfile(os.path.join(
                    folder_paths.get_input_directory(), *p.split("/")))
            ]
            out[name] = {**data, "thumb": imgs[0] if imgs else None,
                         "missing": missing}
        return web.json_response(out)

    @PromptServer.instance.routes.post("/h3/subjects")
    async def h3_subjects_save(request):
        try:
            body = await request.json()
        except Exception as e:
            return web.json_response({"error": f"bad json: {e}"}, status=400)
        name = str(body.get("name") or "").strip()
        if not name:
            return web.json_response({"error": "name required"}, status=400)
        presets = _read_presets()
        presets[name] = body.get("data") or {}
        _write_presets(presets)
        return web.json_response({"ok": True, "count": len(presets)})

    @PromptServer.instance.routes.post("/h3/subjects/delete")
    async def h3_subjects_delete(request):
        try:
            body = await request.json()
        except Exception as e:
            return web.json_response({"error": f"bad json: {e}"}, status=400)
        presets = _read_presets()
        presets.pop(str(body.get("name") or ""), None)
        _write_presets(presets)
        return web.json_response({"ok": True, "count": len(presets)})

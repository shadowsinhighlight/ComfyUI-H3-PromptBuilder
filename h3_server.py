"""
Upload, saved-subject and subject-pack endpoints for the H3 Subject Advanced
image gallery.

Files land in ComfyUI/input/h3/<nickname>/ and the browser stores the returned
relative paths in the node's image_list widget, so the gallery contents travel
with the saved workflow.

The nickname arrives from the browser and becomes part of a path, so it is
sanitised here rather than trusted, and the resolved directory is verified to sit
inside the input directory before anything is written.
"""

import hashlib
import io
import json
import os
import re
import time
import zipfile

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


# ---- subject packs ------------------------------------------------------- #
# A pack is an ordinary .zip: manifest.json plus the images themselves, so a
# subject can be carried to a machine (or a fresh install) that has none of its
# files. The manifest keeps tray order, which is what decides <Picture N>.

PACK_FORMAT = "h3-subject-pack"
PACK_VERSION = 1
PACK_MAX_IMAGES = 9
PACK_MAX_IMAGE_BYTES = 200 * 1024 * 1024     # per image, uncompressed
PACK_MAX_TOTAL_BYTES = 1024 * 1024 * 1024    # whole pack, uncompressed
PACK_FIELDS = ("nickname", "kind", "description", "video_role",
               "voice_mode", "retention", "retention_note")


def _input_abs(rel):
    """Resolve an input-relative path, refusing anything outside input/."""
    base = os.path.abspath(folder_paths.get_input_directory())
    path = os.path.abspath(os.path.join(base, *str(rel).split("/")))
    if not path.startswith(base + os.sep):
        raise ValueError(f"path escapes the input directory: {rel}")
    return path


def build_pack(data):
    """Return (zip_bytes, missing_paths) for a subject's fields and images."""
    images = [p for p in (data.get("images") or []) if isinstance(p, str)]
    manifest = {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "fields": {k: data[k] for k in PACK_FIELDS if k in data},
        "images": [],
    }
    missing = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, rel in enumerate(images[:PACK_MAX_IMAGES], 1):
            try:
                src = _input_abs(rel)
            except ValueError:
                missing.append(rel)
                continue
            if not os.path.isfile(src):
                missing.append(rel)
                continue
            name = sanitize_filename(os.path.basename(src)) or f"image{i}.png"
            arc = f"images/{i:02d}_{name}"
            # images are already compressed; storing them is faster and no bigger
            zf.write(src, arc, compress_type=zipfile.ZIP_STORED)
            manifest["images"].append({"file": arc, "name": name, "source": rel})
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    return buf.getvalue(), missing


def _same_file(path, payload):
    try:
        if os.path.getsize(path) != len(payload):
            return False
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).digest() == hashlib.sha1(payload).digest()
    except OSError:
        return False


def import_pack(raw, nickname_override=None):
    """Unpack a pack into input/h3/<nickname>/. Archive paths are never used as
    write paths: each image is read by its manifest entry and written under a
    sanitised basename, so a crafted zip cannot place files elsewhere."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("not a zip file")
    with zf:
        try:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except KeyError:
            raise ValueError("manifest.json missing — not an H3 subject pack")
        except Exception as e:
            raise ValueError(f"manifest.json unreadable: {e}")
        if manifest.get("format") != PACK_FORMAT:
            raise ValueError("not an H3 subject pack")
        if int(manifest.get("version", 0)) > PACK_VERSION:
            raise ValueError("pack was made by a newer version of this node pack")

        fields = {k: v for k, v in (manifest.get("fields") or {}).items()
                  if k in PACK_FIELDS}
        if nickname_override:
            fields["nickname"] = nickname_override
        directory, rel_dir = target_dir(fields.get("nickname") or "unnamed")
        os.makedirs(directory, exist_ok=True)

        names = set(zf.namelist())
        saved, skipped, total = [], [], 0
        for entry in (manifest.get("images") or [])[:PACK_MAX_IMAGES]:
            arc = str(entry.get("file") or "")
            clean = sanitize_filename(entry.get("name") or os.path.basename(arc))
            if arc not in names or clean is None:
                skipped.append(f"{arc or '?'} (not found or not an image)")
                continue
            info = zf.getinfo(arc)
            total += info.file_size
            if info.file_size > PACK_MAX_IMAGE_BYTES or total > PACK_MAX_TOTAL_BYTES:
                skipped.append(f"{clean} (too large)")
                continue
            payload = zf.read(arc)
            # Re-importing the same pack reuses identical files instead of
            # piling up foo_1.png, foo_2.png ...
            existing = os.path.join(directory, clean)
            if os.path.isfile(existing) and _same_file(existing, payload):
                saved.append(f"{rel_dir}/{clean}")
                continue
            final = unique_path(directory, clean)
            with open(os.path.join(directory, final), "wb") as out:
                out.write(payload)
            saved.append(f"{rel_dir}/{final}")

    return {"fields": fields, "images": saved, "skipped": skipped, "folder": rel_dir}


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


    # ---- subject packs ---------------------------------------------------- #

    @PromptServer.instance.routes.post("/h3/pack/export")
    async def h3_pack_export(request):
        try:
            body = await request.json()
        except Exception as e:
            return web.json_response({"error": f"bad json: {e}"}, status=400)
        data = body.get("data") or {}
        if not data.get("images"):
            return web.json_response({"error": "this subject has no images"}, status=400)
        payload, missing = build_pack(data)
        if len(missing) == len(data.get("images") or []):
            return web.json_response(
                {"error": "none of the images exist on disk", "missing": missing},
                status=400)
        fname = sanitize_component(data.get("nickname") or "subject") + ".h3pack.zip"
        return web.Response(
            body=payload,
            content_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{fname}"',
                "X-H3-Filename": fname,
                "X-H3-Missing": str(len(missing)),
                "Access-Control-Expose-Headers": "X-H3-Filename, X-H3-Missing",
            },
        )

    @PromptServer.instance.routes.post("/h3/pack/import")
    async def h3_pack_import(request):
        try:
            data = await request.post()
        except Exception as e:
            return web.json_response({"error": f"bad form data: {e}"}, status=400)
        field = data.get("pack")
        handle = getattr(field, "file", None)
        if handle is None:
            return web.json_response({"error": "no pack file"}, status=400)
        handle.seek(0)
        raw = handle.read()
        nick = str(data.get("nickname") or "").strip() or None
        try:
            result = import_pack(raw, nickname_override=nick)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except Exception as e:
            return web.json_response({"error": f"import failed: {e}"}, status=500)
        return web.json_response(result)

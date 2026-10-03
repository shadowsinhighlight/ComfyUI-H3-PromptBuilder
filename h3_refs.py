"""
Reference collection for ComfyUI-H3-PromptBuilder.

Images, video and audio arrive on sockets from ComfyUI's own loaders rather than
being read off disk here — so any upstream node (LoadImage, LoadVideo, LoadAudio,
batch loaders, generated images) can feed a character.

One H3 Character Refs node per character, chained. Chain position determines
<Picture N> / <Video N> / <Audio N> numbering, and the router emits a ref_map the
assembler consumes, so the prompt's labels and the wiring cannot disagree.
"""

import os

try:
    import folder_paths
    _COMFY = True
except Exception:  # pragma: no cover
    folder_paths = None
    _COMFY = False

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:
    import torch
except Exception:  # pragma: no cover
    torch = None

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None

MAX_SLOTS = 9
MAX_VIDEOS = 3
MAX_AUDIOS = 3
MAX_VIDEO_AUDIOS = 3

SLOT_MODES = [
    "auto (pack in chain order)",
    "absolute (image_N = Picture N)",
]


class AnyType(str):
    """Socket type that accepts any connection.

    Used for the video and audio passthroughs. The MiniMax H3 loader's
    ref_video_* / ref_audio_* socket types vary between node packs and versions
    (VIDEO, IMAGE-as-frames, AUDIO), and a wrong guess here would make the
    sockets simply refuse to connect. Passing the object through untouched means
    whatever your loader produces is exactly what reaches the H3 node.
    """

    def __ne__(self, other):
        return False


ANY = AnyType("*")


def input_dir():
    if _COMFY:
        try:
            return folder_paths.get_input_directory()
        except Exception:
            pass
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "input")


def load_tensor(rel_path):
    """Same conversion ComfyUI's LoadImage performs, for a path under input/."""
    if Image is None or torch is None or np is None:
        raise RuntimeError("torch/PIL unavailable")
    from PIL import ImageOps
    full = os.path.join(input_dir(), rel_path)
    img = ImageOps.exif_transpose(Image.open(full)).convert("RGB")
    arr = np.array(img).astype(np.float32) / 255.0
    return torch.from_numpy(arr)[None, ]


def _stamp(img, number, nickname):
    """Draw the picture number (and owner) onto a thumbnail."""
    try:
        from PIL import ImageDraw, ImageFont
    except Exception:
        return img
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
        small = ImageFont.truetype("DejaVuSans.ttf", 12)
    except Exception:
        font = small = ImageFont.load_default()

    box = d.textbbox((0, 0), number, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.rectangle([0, 0, w + 14, h + 12], fill=(0, 0, 0))
    d.text((7, 3), number, fill=(122, 201, 67), font=font)

    tag = f"@{nickname}"
    tb = d.textbbox((0, 0), tag, font=small)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    d.rectangle([0, img.height - th - 9, tw + 10, img.height], fill=(0, 0, 0))
    d.text((5, img.height - th - 7), tag, fill=(200, 200, 200), font=small)
    return img


def resolve_chain(chain):
    """Assign reference numbers from a character chain.

    Shared by the router (which needs the media) and the assembler (which needs
    the labels). Keeping one implementation is the point: two copies of this
    logic would eventually disagree, and a prompt that disagrees with the wiring
    fails silently.

    Returns (placed, pmap, vmap, amap, notes) where placed maps
    slot -> (tensor, nickname).
    """
    chain = list(chain or [])
    placed, notes = {}, []

    # Absolute entries claim their exact slots first, so an auto-packed
    # character can never steal a slot someone pinned deliberately.
    for entry in chain:
        slots = entry.get("slots")
        if not slots:
            continue
        for slot, tensor in sorted(slots.items()):
            if slot > MAX_SLOTS:
                notes.append(f"! @{entry['nickname']}: image past slot {MAX_SLOTS}")
            elif slot in placed:
                notes.append(
                    f"! slot {slot} claimed by both @{placed[slot][1]} and "
                    f"@{entry['nickname']} — the second is dropped"
                )
            else:
                placed[slot] = (tensor, entry["nickname"])

    nxt = 1
    for entry in chain:
        if entry.get("slots"):
            continue
        for tensor in entry.get("images") or []:
            while nxt in placed and nxt <= MAX_SLOTS:
                nxt += 1
            if nxt > MAX_SLOTS:
                notes.append(f"! @{entry['nickname']}: image dropped — no free slot")
                continue
            placed[nxt] = (tensor, entry["nickname"])
            nxt += 1

    pmap = {}
    for entry in chain:
        nick = entry["nickname"]
        pmap[nick] = sorted(s for s, (_, n) in placed.items() if n == nick)

    # The prompt format has exactly one audio label, <Audio N>, so a standalone
    # audio file and a reference video's audio track draw from the same counter
    # even though they occupy different loader sockets. Per subject, the plain
    # audio is numbered before the video's track.
    vmap, amap, vamap = {}, {}, {}
    nv = na = nva = naudio = 0
    for entry in chain:
        nick = entry["nickname"]
        if entry.get("video") is not None:
            if nv < MAX_VIDEOS:
                nv += 1
                vmap[nick] = nv
            else:
                notes.append(f"! @{nick}: video dropped — max {MAX_VIDEOS}")
        if entry.get("audio") is not None:
            if na < MAX_AUDIOS:
                na += 1
                naudio += 1
                amap[nick] = naudio
            else:
                notes.append(f"! @{nick}: audio dropped — max {MAX_AUDIOS}")
        if entry.get("video_audio") is not None:
            if nva < MAX_VIDEO_AUDIOS:
                nva += 1
                naudio += 1
                vamap[nick] = naudio
            else:
                notes.append(
                    f"! @{nick}: video audio dropped — max {MAX_VIDEO_AUDIOS}")

    if naudio > 3:
        notes.append(
            f"! {naudio} audio references in total — the H3 guide allows 3. "
            f"<Audio 4> and beyond are unlikely to resolve."
        )

    used = sorted(placed)
    if used and used != list(range(1, len(used) + 1)):
        missing = [s for s in range(1, used[-1] + 1) if s not in placed]
        notes.append(
            f"! GAP: slots {missing} are empty. ref_image inputs must be filled "
            f"contiguously from 0 — close the gap or the tags will misalign."
        )

    return placed, pmap, vmap, amap, vamap, notes


# --------------------------------------------------------------------------- #
# H3 Ref Router
# --------------------------------------------------------------------------- #

class H3RefRouter:
    """Flattens the character chain into numbered reference slots for wiring into
    the H3 loader. Unfilled outputs return None — leave those unconnected rather
    than feeding a placeholder into the model."""

    CATEGORY = "MiniMax H3"
    FUNCTION = "route"
    RETURN_TYPES = (
        ("IMAGE",) * MAX_SLOTS
        + (ANY,) * MAX_VIDEOS
        + (ANY,) * MAX_AUDIOS
        + (ANY,) * MAX_VIDEO_AUDIOS
        + ("H3_SUBJECTS", "STRING")
    )
    RETURN_NAMES = (
        tuple(f"picture_{i}" for i in range(1, MAX_SLOTS + 1))
        + tuple(f"video_{i}" for i in range(1, MAX_VIDEOS + 1))
        + tuple(f"audio_{i}" for i in range(1, MAX_AUDIOS + 1))
        + tuple(f"video_audio_{i}" for i in range(1, MAX_VIDEO_AUDIOS + 1))
        + ("subjects", "report")
    )
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {"subjects": ("H3_SUBJECTS",)},
            "optional": {
                "preview": ("BOOLEAN", {"default": True,
                                        "label_on": "on", "label_off": "off"}),
            },
        }

    # -- preview ------------------------------------------------------------- #

    @staticmethod
    def _write_previews(placed):
        """Save small temp thumbnails, each stamped with the <Picture N> it was
        assigned. The number is drawn into the pixels rather than shown beside
        the image, because ComfyUI's preview grid reflows and a caption sitting
        next to the wrong tile is worse than none."""
        if not (_COMFY and Image is not None and np is not None):
            return []
        try:
            tmp = folder_paths.get_temp_directory()
            os.makedirs(tmp, exist_ok=True)
        except Exception:
            return []

        out = []
        for slot in sorted(placed):
            tensor = placed[slot][0]
            try:
                arr = tensor[0] if hasattr(tensor, "shape") and len(tensor.shape) == 4 else tensor
                arr = (arr.cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
                img = Image.fromarray(arr)
                img.thumbnail((256, 256), Image.LANCZOS)
                img = _stamp(img, str(slot), placed[slot][1])
                name = f"h3_ref_{slot:02d}.png"
                img.save(os.path.join(tmp, name), compress_level=4)
                out.append({"filename": name, "subfolder": "", "type": "temp"})
            except Exception:
                continue
        return out

    # -- main ---------------------------------------------------------------- #

    def route(self, subjects, preview=True):
        chain = list(subjects or [])
        placed, pmap, vmap, amap, vamap, notes = resolve_chain(chain)
        report = []

        videos = [e["video"] for e in chain if e.get("video") is not None][:MAX_VIDEOS]
        audios = [e["audio"] for e in chain if e.get("audio") is not None][:MAX_AUDIOS]
        vaudios = [e["video_audio"] for e in chain
                   if e.get("video_audio") is not None][:MAX_VIDEO_AUDIOS]

        for entry in chain:
            nick = entry["nickname"]
            assigned = pmap.get(nick, [])
            if not assigned:
                rng = "no images"
            elif len(assigned) == 1:
                rng = f"<Picture {assigned[0]}>"
            elif assigned == list(range(assigned[0], assigned[-1] + 1)):
                rng = f"<Picture {assigned[0]}>-<Picture {assigned[-1]}>"
            else:
                rng = ", ".join(f"<Picture {a}>" for a in assigned)
            mode = "absolute" if entry.get("slots") else "auto"
            extra = []
            if nick in vmap:
                extra.append(f"<Video {vmap[nick]}>")
            if nick in amap:
                extra.append(f"<Audio {amap[nick]}>")
            if nick in vamap:
                extra.append(f"<Audio {vamap[nick]}> (video track)")
            tail = ("  + " + ", ".join(extra)) if extra else ""
            report.append(f"@{nick}: {rng}{tail}  [{mode}]")
            for n in entry.get("notes", []):
                report.append(f"    {n}")
            for m in entry.get("missing", []):
                report.append(f"    ! MISSING FILE: {m}")

        if notes:
            report.append("")
            report.extend(notes)

        used = sorted(placed)
        report.append("")
        report.append("ComfyUI slot        ->  prompt label")
        report.append("-" * 46)
        for s in used:
            report.append(f"ref_image_{s - 1:<9}->  <Picture {s}>   (@{placed[s][1]})")
        for i in range(len(videos)):
            report.append(f"ref_video_{i:<9}->  <Video {i + 1}>")
        anum = sorted(amap.values())
        for i in range(len(audios)):
            n = anum[i] if i < len(anum) else i + 1
            report.append(f"ref_audio_{i:<9}->  <Audio {n}>")
        vanum = sorted(vamap.values())
        for i in range(len(vaudios)):
            n = vanum[i] if i < len(vanum) else i + 1
            report.append(f"ref_video_audio_{i:<3}->  <Audio {n}>")
        if not used and not videos and not audios and not vaudios:
            report.append("(nothing connected)")

        images = [placed[s][0] if s in placed else None
                  for s in range(1, MAX_SLOTS + 1)]
        vids = [videos[i] if i < len(videos) else None for i in range(MAX_VIDEOS)]
        auds = [audios[i] if i < len(audios) else None for i in range(MAX_AUDIOS)]
        vauds = [vaudios[i] if i < len(vaudios) else None
                 for i in range(MAX_VIDEO_AUDIOS)]

        result = tuple(images) + tuple(vids) + tuple(auds) + tuple(vauds) + \
            (chain, "\n".join(report))
        ui = {"images": self._write_previews(placed)} if preview else {"images": []}
        return {"ui": ui, "result": result}


NODE_CLASS_MAPPINGS = {"H3RefRouter": H3RefRouter}
NODE_DISPLAY_NAME_MAPPINGS = {"H3RefRouter": "H3 Ref Router"}

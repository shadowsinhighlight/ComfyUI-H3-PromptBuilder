"""
ComfyUI-H3-PromptBuilder
Structured prompt construction for MiniMax H3 Ref2VA.

Design notes
------------
Subjects are built one node per subject and chained through an H3_SUBJECTS link.
Numbering (<Subject 1>, <Subject 2>, ...) is derived from chain order at assembly
time, so inserting, removing, or reordering subject nodes renumbers everything
downstream without touching any text.

Body text is written with @nickname tokens instead of literal labels. The
assembler substitutes them, which means picture reassignment and subject
reordering never require rewriting prose.

No ComfyUI imports here on purpose — the module is importable standalone so the
assembly logic can be unit-tested outside the ComfyUI runtime.
"""

import json
import re
from collections import OrderedDict

# Imported relative-first, then absolute. ComfyUI loads a pack by filesystem
# path and may not register that name in sys.modules, which breaks relative
# imports; __init__.py also puts this folder on sys.path so the absolute form
# resolves. Whichever mechanism is available, one of the two works.
try:
    from .h3_refs import resolve_chain, load_tensor, ANY
except ImportError:
    from h3_refs import resolve_chain, load_tensor, ANY

MAX_PICTURES = 9
MAX_VIDEOS = 3
MAX_AUDIOS = 3
MAX_FILES = 12

NONE = "none"

KINDS = ["person", "environment", "object", "motion", "effect", "style"]

RETENTION_MARKERS = [
    "auto",
    "fully_preserved",
    "partially_preserved",
    "attribute_transfer",
    "weak_reference",
]

VOICE_MODES = ["timbre only", "reuse audio"]

VIDEO_ROLES = [
    "none",
    "motion",
    "edit source",
    "continuation",
    "camera + timing",
]

# Per-subject video purpose on H3 Subject Advanced. "auto" infers from kind:
# a kind=motion subject's video is a motion reference, anything else is
# ambiguous and gets flagged rather than guessed at.
PREVIEW_SIZES = ["64", "96", "128", "192", "256", "384", "512", "768", "1024"]

SUBJECT_VIDEO_ROLES = [
    "auto",
    "motion",
    "edit source",
    "continuation",
    "camera + timing",
    "audio source only",
]

BG_AUDIO_MODES = ["score", "ambience", "style reference"]

TASK_BASES = [
    "reference generation",
    "video editing",
    "video continuation",
]

# @nick  -> <Subject N>
# @nick! -> <Subject N> (SX)   (marks a vocal event)
LLM_MODES = ["merge", "override", "verbatim"]

# Section headers as an LLM typically emits them: lenient about case, and about
# space vs underscore, because a pasted prompt is hand-copied text rather than a
# machine format.
_SECTION_RE = re.compile(
    r"^[ \t]*(subject[ _]?definitions|summary|retention[ _]?analysis|"
    r"detailed[ _]?description|overall[ _]?soundscape|non[ _]?diegetic[ _]?music)"
    r"[ \t]*:[ \t]*",
    re.I | re.M,
)
_TASK_PREFIX_RE = re.compile(r"^\s*\[[^\]]*\]\s*")


def parse_prompt_sections(text):
    """Split a pasted six-section prompt into {section: body}."""
    out = {}
    matches = list(_SECTION_RE.finditer(text or ""))
    for i, m in enumerate(matches):
        key = re.sub(r"[ _]+", "_", m.group(1).strip().lower())
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        if body:
            out[key] = body
    return out


def strip_task_prefix(summary_text):
    """Drop a leading [reference generation + ...] so the node can regenerate it
    with the audio flags that match the actual wiring."""
    return _TASK_PREFIX_RE.sub("", summary_text or "").strip()


TOKEN_RE = re.compile(r"@([A-Za-z0-9_\-]+)(!?)")
SHOT_SPLIT_RE = re.compile(r"(?=\[Shot\s+\d+\])")
SHOT_HEAD_RE = re.compile(r"\[Shot\s+(\d+)\]")
TIMESTAMP_RE = re.compile(r"\b\d{2}:\d{2}\.\d{3}\b")
GUILLEMET_RE = re.compile(r"[‹›«»]")
PLACEHOLDER_RE = re.compile(r"\[\[[^\]]*\]\]")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _slug(name, fallback):
    s = re.sub(r"[^A-Za-z0-9_\-]", "", (name or "").strip())
    return s if s else fallback


def _parse_index(value):
    """'Picture 3' / 'Video 1' / 'Audio 2' -> 3 / 1 / 2 ; 'none' -> None"""
    if not value or value == NONE:
        return None
    m = re.search(r"(\d+)", value)
    return int(m.group(1)) if m else None


def _join_labels(kind, indices):
    labels = [f"<{kind} {i}>" for i in indices]
    if len(labels) == 1:
        return labels[0]
    if len(labels) == 2:
        return f"{labels[0]} and {labels[1]}"
    return ", ".join(labels[:-1]) + f", and {labels[-1]}"


def _split_shots(text):
    """Return [(shot_number, block_text), ...] preserving original text."""
    if not text.strip():
        return []
    parts = [p for p in SHOT_SPLIT_RE.split(text) if p.strip()]
    out = []
    for p in parts:
        m = SHOT_HEAD_RE.match(p.strip())
        out.append((int(m.group(1)) if m else None, p))
    return out


def _effective_video_role(s):
    """Resolve a subject's video purpose. 'auto' means: a motion subject's video
    is a motion reference; for anything else the purpose is genuinely ambiguous,
    so it stays unresolved and the linter asks rather than guessing."""
    role = (s.get("video_role") or "auto").lower()
    if role != "auto":
        return role
    return "motion" if s.get("kind") == "motion" else "unset"


def _is_timbre(mode):
    """True for the timbre-only voice modes. Accepts the older long labels so
    workflows saved before the widgets were shortened still resolve correctly."""
    m = (mode or "").lower()
    return m.startswith("timbre") or "style reference" in m


def _library_summary(raw):
    """One-line inventory of the saved scenes, so the library is visible in the
    lint output rather than hidden inside a collapsed widget."""
    try:
        lib = json.loads(raw or "{}")
    except Exception:
        return "! scene_library is not valid JSON — saving a shot will reset it."
    if not isinstance(lib, dict) or not lib:
        return ""
    parts = []
    for scene in sorted(lib):
        shots = lib[scene]
        if isinstance(shots, dict) and shots:
            keys = sorted(shots, key=lambda k: (len(k), k))
            parts.append(f"{scene}: {', '.join(keys)}")
    return ("saved scenes — " + " | ".join(parts)) if parts else ""


def _word_count(text):
    return len(re.findall(r"\b[\w'-]+\b", text))


# --------------------------------------------------------------------------- #
# Node 1 — H3 Subject
# --------------------------------------------------------------------------- #

class H3Subject:
    """One subject. Chain these into the assembler in the order you want them
    numbered. Tick the pictures that show this subject."""

    CATEGORY = "MiniMax H3"
    FUNCTION = "build"
    RETURN_TYPES = ("H3_SUBJECTS",)
    RETURN_NAMES = ("subjects",)

    @classmethod
    def INPUT_TYPES(cls):
        pics = {
            f"picture_{i}": ("BOOLEAN", {"default": False, "label_on": "on", "label_off": "off"})
            for i in range(1, MAX_PICTURES + 1)
        }
        required = OrderedDict()
        required["nickname"] = ("STRING", {
            "default": "hero",
            "multiline": False,
            "tooltip": "Write @hero in the summary/shots and it becomes <Subject N>. "
                       "Write @hero! where they speak to add the (SX) speaker ID.",
        })
        required["kind"] = (KINDS, {"default": "person"})
        required["description"] = ("STRING", {
            "default": "",
            "multiline": True,
            "tooltip": "FIXED visual traits only — hair, build, garments, marks. "
                       "No mood, no action, no location. For kind=motion, describe "
                       "the mechanics: stride, tempo, weight shift, arm swing.",
        })
        required.update(pics)
        required["motion_from_video"] = (
            [NONE] + [f"Video {i}" for i in range(1, MAX_VIDEOS + 1)],
            {"default": NONE},
        )
        required["voice_from_audio"] = (
            [NONE] + [f"Audio {i}" for i in range(1, MAX_AUDIOS + 1)],
            {"default": NONE},
        )
        required["voice_mode"] = (VOICE_MODES, {"default": VOICE_MODES[0]})
        required["retention"] = (RETENTION_MARKERS, {"default": "auto"})
        required["retention_note"] = ("STRING", {
            "default": "",
            "multiline": False,
            "tooltip": "Optional. Overrides the auto-generated retention sentence.",
        })

        return {
            "required": required,
            "optional": {
                "subjects": ("H3_SUBJECTS",),
                "character": ("H3_CHARACTER", {
                    "tooltip": "Optional. Wire from the matching H3 Character Refs "
                               "node to link them directly — the nickname then "
                               "follows the refs node automatically.",
                }),
            },
        }

    def build(self, subjects=None, character=None, **kw):
        chain = list(subjects) if subjects else []
        pictures = [i for i in range(1, MAX_PICTURES + 1) if kw.get(f"picture_{i}")]

        # A wired character handle is the pairing, so its nickname wins over the
        # widget — renaming the refs node then carries through with no retyping.
        nickname = kw.get("nickname")
        linked = False
        if character and character.get("nickname"):
            nickname = character["nickname"]
            linked = True

        entry = {
            "images": [],
            "slots": None,
            "video_audio": None,
            "advanced": False,
            "linked": linked,
            "nickname": _slug(nickname, f"subject{len(chain) + 1}"),
            "kind": kw.get("kind", "person"),
            "description": (kw.get("description") or "").strip(),
            "pictures": pictures,
            "video": _parse_index(kw.get("motion_from_video")),
            "audio": _parse_index(kw.get("voice_from_audio")),
            "voice_mode": kw.get("voice_mode", VOICE_MODES[0]),
            "retention": kw.get("retention", "auto"),
            "retention_note": (kw.get("retention_note") or "").strip(),
        }
        chain.append(entry)
        return (chain,)


# --------------------------------------------------------------------------- #
# H3 Subject Advanced — identity + media in one node
# --------------------------------------------------------------------------- #

class H3SubjectAdvanced:
    """A subject and its reference media together.

    Combining them removes the bookkeeping that caused most mistakes: there is
    no separate media node to keep in sync, no nickname to match twice, and no
    manual decision about which picture slot belongs to whom. Images upload into
    the node itself and number themselves from tray order and chain position.
    """

    CATEGORY = "MiniMax H3"
    FUNCTION = "build"
    RETURN_TYPES = ("H3_SUBJECTS",)
    RETURN_NAMES = ("subjects",)

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "nickname": ("STRING", {
                    "default": "hero",
                    "tooltip": "Write @hero in the summary/shots. @hero! marks a "
                               "vocal event.",
                }),
                "kind": (KINDS, {"default": "person"}),
                "description": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "tooltip": "FIXED visual traits only — hair, build, garments, "
                               "marks. No mood, no action, no location. For "
                               "kind=motion, describe the mechanics.",
                }),
            },
            "optional": {
                "subjects": ("H3_SUBJECTS",),
                "extra_images": ("IMAGE", {
                    "tooltip": "Optional. Images from elsewhere in the graph, "
                               "appended after the uploaded ones.",
                }),
                "video": (ANY, {}),
                "video_role": (SUBJECT_VIDEO_ROLES, {
                    "default": "auto",
                    "tooltip": "What the connected video is FOR. motion: only the "
                               "movement is transferred. edit source / continuation: "
                               "the target video derives from it. camera + timing: "
                               "only its framing and cut rhythm are followed.",
                }),
                "audio": (ANY, {}),
                "video_audio": (ANY, {
                    "tooltip": "The audio track that belongs with the connected "
                               "video. Goes to ref_video_audio_* on the loader and "
                               "is cited as its own <Audio N>.",
                }),
                "voice_mode": (VOICE_MODES, {"default": VOICE_MODES[0]}),
                "retention": (RETENTION_MARKERS, {"default": "auto"}),
                "retention_note": ("STRING", {"default": "", "multiline": False}),
                "image_list": ("STRING", {
                    "default": "[]",
                    "multiline": True,
                    "tooltip": "Uploaded image paths. Managed by the gallery.",
                }),
                "preview_size": (PREVIEW_SIZES, {
                    "default": "128",
                    "tooltip": "Gallery thumbnail size in pixels. Display only — it "
                               "has no effect on the images sent to the model.",
                }),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kw):
        # the gallery writes files outside the graph, so never cache
        return float("nan")

    def build(self, nickname, kind, description, subjects=None, extra_images=None,
              video=None, video_role="auto", audio=None, video_audio=None,
              voice_mode=VOICE_MODES[0],
              retention="auto", retention_note="", image_list="[]",
              preview_size="128"):
        # preview_size is a display setting consumed by the gallery; it is
        # declared here only so it serialises with the workflow.
        chain = list(subjects) if subjects else []
        nick = _slug(nickname, f"subject{len(chain) + 1}")

        notes, images = [], []
        try:
            paths = json.loads(image_list or "[]")
            paths = [p for p in paths if isinstance(p, str) and p.strip()]
        except Exception:
            paths = []
            notes.append("image_list is not valid JSON — no uploaded images used")

        missing = []
        for p in paths:
            if load_tensor is None:
                break
            try:
                images.append(load_tensor(p))
            except FileNotFoundError:
                missing.append(p)
            except Exception as e:
                notes.append(f"could not load {p} ({type(e).__name__}: {e})")
        if missing:
            # Named individually: on a machine without the files, knowing exactly
            # which ones are absent is the difference between a quick re-upload
            # and hunting through the workflow.
            notes.append(
                f"MISSING {len(missing)} image file(s) — re-add them on the node, "
                f"or copy them to ComfyUI/input/: " + ", ".join(missing)
            )

        if extra_images is not None:
            try:
                count = int(extra_images.shape[0])
            except Exception:
                count = 1
            for b in range(count):
                images.append(extra_images[b:b + 1] if count > 1 else extra_images)

        if len(images) > MAX_PICTURES:
            notes.append(f"{len(images)} images truncated to {MAX_PICTURES}")
            images = images[:MAX_PICTURES]

        chain.append({
            "nickname": nick,
            "kind": kind,
            "description": (description or "").strip(),
            "pictures": [],          # filled in by the assembler from the chain
            "images": images,
            "slots": None,
            "video": video,
            "video_role": video_role,
            "audio": audio,
            "video_audio": video_audio,
            "voice_mode": voice_mode,
            "retention": retention,
            "retention_note": (retention_note or "").strip(),
            "notes": notes,
            "missing": missing,
            "advanced": True,
        })
        return (chain,)


# --------------------------------------------------------------------------- #
# Node 2 — H3 Prompt Assembler
# --------------------------------------------------------------------------- #

class H3PromptAssembler:
    """Assembles the six-section H3 reference prompt, resolves @tokens, assigns
    speaker IDs, computes shot appearance lists, and lints the result."""

    CATEGORY = "MiniMax H3"
    FUNCTION = "assemble"
    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("prompt", "wiring_map", "warnings")
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "subjects": ("H3_SUBJECTS",),
                "task": (TASK_BASES, {"default": "reference generation"}),
                "video_1_role": (VIDEO_ROLES, {"default": NONE}),
                "first_frame_picture": (
                    [NONE] + [f"Picture {i}" for i in range(1, MAX_PICTURES + 1)],
                    {"default": NONE},
                ),
                "style_line": ("STRING", {
                    "default": "The target video is in a cinematic style, rendered in "
                               "full color, with soft directional lighting and a muted "
                               "palette.",
                    "multiline": True,
                }),
                "summary": ("STRING", {
                    "default": "@hero walks through a rain-slick street at night while "
                               "@cafe stays visible behind.",
                    "multiline": True,
                    "tooltip": "Plot level only. Use @nickname tokens. The task prefix "
                               "is added automatically — do not type it.",
                }),
                "shots": ("STRING", {
                    "default": "[Shot 1] A medium shot of ... @hero, the ..., steps "
                               "forward.\n[Shot 2] At 00:04.000, the shot cuts to ...",
                    "multiline": True,
                    "tooltip": "One [Shot N] per block. @nick for a mention, @nick! "
                               "where they speak. Shot 1 takes no timestamp.",
                }),
                "overall_soundscape": ("STRING", {"default": "", "multiline": True}),
                "non_diegetic_music": ("STRING", {"default": "N/A", "multiline": True}),
            },
            "optional": {
                "background_audio": (
                    [NONE] + [f"Audio {i}" for i in range(1, MAX_AUDIOS + 1)],
                    {"default": NONE},
                ),
                "background_audio_mode": (BG_AUDIO_MODES, {"default": BG_AUDIO_MODES[0]}),
                "extra_definitions": ("STRING", {"default": "", "multiline": True}),
                "extra_retention": ("STRING", {"default": "", "multiline": True}),
                "strict": ("BOOLEAN", {"default": True, "label_on": "lint", "label_off": "quiet"}),
                "llm_prompt": ("STRING", {
                    "default": "",
                    "multiline": True,
                    "tooltip": "Paste a whole prompt here. When non-empty it takes "
                               "precedence over the fields above. @nickname tokens "
                               "still resolve.",
                }),
                "llm_mode": (LLM_MODES, {"default": LLM_MODES[0]}),
                "scene_library": ("STRING", {
                    "default": "{}",
                    "multiline": True,
                    "tooltip": "Saved scenes and shots, stored with the workflow. "
                               "Managed by the buttons on this node.",
                }),
            },
        }

    # -- token resolution ---------------------------------------------------- #

    @staticmethod
    def _assign_speakers(shots_text, nick_to_num):
        """Speaker IDs follow order of FIRST VOCAL EVENT in the target video,
        which is not the same as subject order. Scan for @nick! in reading order."""
        speakers = OrderedDict()
        for m in TOKEN_RE.finditer(shots_text or ""):
            nick, bang = m.group(1), m.group(2)
            if bang and nick in nick_to_num and nick not in speakers:
                speakers[nick] = len(speakers) + 1
        return speakers

    @staticmethod
    def _substitute(text, nick_to_num, speakers, unknown):
        def repl(m):
            nick, bang = m.group(1), m.group(2)
            if nick not in nick_to_num:
                unknown.add(nick)
                return m.group(0)
            label = f"<Subject {nick_to_num[nick]}>"
            if bang and nick in speakers:
                label += f" (S{speakers[nick]})"
            return label
        return TOKEN_RE.sub(repl, text or "")

    # -- section builders ---------------------------------------------------- #

    @staticmethod
    def _definition_line(s, num, speakers):
        bits = []
        if s["pictures"]:
            bits.append(f"in {_join_labels('Picture', s['pictures'])}")
        # A video attached to a person is a motion reference, not another view of
        # them — citing it on the identity line tells the model to reproduce the
        # clip's performer. Only a subject that IS the movement cites its video.
        if s["video"] is not None and _effective_video_role(s) == "motion":
            bits.append(f"performed in <Video {s['video']}>")

        if s["kind"] == "person":
            head = f"<Subject {num}> is the person"
        elif s["kind"] == "motion":
            head = f"<Subject {num}> is the movement"
        else:
            head = f"<Subject {num}> is the {s['kind']}"

        src = (" " + " ".join(bits)) if bits else ""
        desc = f", {s['description']}" if s["description"] else ""
        return f"{head}{src}{desc}."

    @staticmethod
    def _audio_definition_lines(chain, nums, speakers):
        lines = []
        seen = set()
        for s, num in zip(chain, nums):
            a = s["audio"]
            if a is None or a in seen:
                continue
            seen.add(a)
            sid = speakers.get(s["nickname"])
            who = f"<Subject {num}>" + (f" (S{sid})" if sid else "")
            if _is_timbre(s["voice_mode"]):
                lines.append(
                    f"<Audio {a}> is the voice-timbre reference for {who}, providing "
                    f"vocal texture, pitch range, and delivery only."
                )
            else:
                lines.append(
                    f"<Audio {a}> is the vocal performance for {who}, reused as the "
                    f"audible voice in the target video."
                )
        return lines

    @staticmethod
    def _retention_line(s, num, appears):
        if s["retention_note"]:
            marker = s["retention"] if s["retention"] != "auto" else "fully_preserved"
            loc = f" (appears in {appears})" if appears else ""
            return f"<Subject {num}>{loc}: {marker} - {s['retention_note']}"

        marker = s["retention"]
        if marker == "auto":
            marker = "attribute_transfer" if s["kind"] == "motion" else "fully_preserved"

        if s["kind"] == "motion":
            note = (
                f"only the movement pattern from <Video {s['video']}> is transferred; "
                f"the performer's appearance, clothing, environment, framing, and camera "
                f"movement are not used."
                if s["video"] is not None else
                "only the movement pattern is transferred."
            )
        else:
            traits = s["description"].rstrip(".")
            noun = {
                "environment": "layout and finish",
                "object": "form and finish",
                "style": "visual treatment",
                "effect": "appearance",
            }.get(s["kind"], "appearance")
            tail = ("action and framing are newly generated."
                    if s["kind"] == "environment" else
                    "environment and action are newly generated.")
            note = (
                f"the defined {noun} is retained ({traits}); {tail}"
                if traits else
                "the defined visual characteristics are retained."
            )
        loc = f" (appears in {appears})" if appears else ""
        return f"<Subject {num}>{loc}: {marker} - {note}"

    @staticmethod
    def _audio_retention_lines(chain, nums, bg_audio, bg_mode):
        lines = []
        seen = set()
        for s, num in zip(chain, nums):
            a = s["audio"]
            if a is None or a in seen:
                continue
            seen.add(a)
            if _is_timbre(s["voice_mode"]):
                lines.append(
                    f"<Audio {a}>: reference - <Subject {num}> follows the vocal timbre "
                    f"and delivery of <Audio {a}> without copying the original signal; "
                    f"<Audio {a}> is never audible in the target video."
                )
            else:
                lines.append(
                    f"<Audio {a}>: fully_copy - <Audio {a}> is reused as the audible "
                    f"vocal track of <Subject {num}>."
                )
        if bg_audio is not None and bg_audio not in seen:
            if _is_timbre(bg_mode):
                lines.append(
                    f"<Audio {bg_audio}>: reference - the target soundtrack follows the "
                    f"character of <Audio {bg_audio}> without copying the original signal."
                )
            elif "ambience" in bg_mode:
                lines.append(
                    f"<Audio {bg_audio}>: partially_copy - the ambience layer of "
                    f"<Audio {bg_audio}> continues throughout the target video."
                )
            else:
                lines.append(
                    f"<Audio {bg_audio}>: fully_copy - <Audio {bg_audio}> is reused as "
                    f"the complete audience-only score."
                )
        return lines

    # -- main ---------------------------------------------------------------- #

    def assemble(self, subjects, task, video_1_role, first_frame_picture,
                 style_line, summary, shots, overall_soundscape, non_diegetic_music,
                 background_audio=NONE, background_audio_mode=BG_AUDIO_MODES[0],
                 extra_definitions="", extra_retention="", strict=True,
                 llm_prompt="", llm_mode=LLM_MODES[0], scene_library="{}"):

        chain = list(subjects or [])
        warn = []

        if not chain:
            return ("", "", "No subjects connected. Add at least one H3 Subject node.")

        # If any subject carries media, the chain itself is authoritative and
        # every number is derived from it — nothing is typed twice, so the prompt
        # and the wiring cannot disagree. With no media anywhere, the manual
        # widgets stay in charge exactly as before.
        has_media = any(
            e.get("images") or e.get("video") is not None
            or e.get("audio") is not None or e.get("video_audio") is not None
            for e in chain
        )
        if has_media and resolve_chain is not None:
            _, pics, vids, auds, vauds, ref_notes = resolve_chain(chain)
            warn.extend(n.lstrip("! ") for n in ref_notes)
            chain = [dict(s) for s in chain]
            for s in chain:
                nick = s["nickname"]
                if s.get("pictures") and not s.get("advanced"):
                    warn.append(
                        f"@{nick} has picture checkboxes ticked, but other subjects "
                        f"carry uploaded media — numbering comes from the chain and "
                        f"the checkboxes are ignored."
                    )
                s["pictures"] = list(pics.get(nick, []))
                if nick in vids:
                    s["video"] = vids[nick]
                if nick in auds:
                    s["audio"] = auds[nick]
                if nick in vauds:
                    s["video_audio_n"] = vauds[nick]
                for n in s.get("notes") or []:
                    warn.append(f"@{nick}: {n}")

        # nickname collisions would silently merge tokens
        nick_to_num, nums = {}, []
        for idx, s in enumerate(chain, start=1):
            nick = s["nickname"]
            if nick in nick_to_num:
                warn.append(f"duplicate nickname '@{nick}' — later subject wins; rename one.")
            nick_to_num[nick] = idx
            nums.append(idx)

        speakers = self._assign_speakers(shots, nick_to_num)
        unknown = set()

        # ---- pasted prompt takes precedence ---------------------------------- #
        paste = (llm_prompt or "").strip()
        parsed = parse_prompt_sections(paste) if paste else {}
        pasted_defs = pasted_rets = None

        if paste:
            if not parsed:
                # No section headers at all: guess from content rather than
                # silently dropping the paste on the floor.
                if "[Shot" in paste:
                    parsed = {"detailed_description": paste}
                    warn.append("pasted prompt has no section headers — read as "
                                "detailed_description because it contains [Shot n].")
                else:
                    parsed = {"summary": paste}
                    warn.append("pasted prompt has no section headers — read as "
                                "summary. Add 'summary:' / 'detailed_description:' "
                                "headers to split it properly.")

            if "summary" in parsed:
                summary = strip_task_prefix(parsed["summary"])
            if "detailed_description" in parsed:
                # the pasted block already carries its own style sentences
                style_line = ""
                shots = parsed["detailed_description"]
            if "overall_soundscape" in parsed:
                overall_soundscape = parsed["overall_soundscape"]
            if "non_diegetic_music" in parsed:
                non_diegetic_music = parsed["non_diegetic_music"]

            if llm_mode.startswith("override"):
                pasted_defs = parsed.get("subject_definitions")
                pasted_rets = parsed.get("retention_analysis")
            elif llm_mode.startswith("merge"):
                for k in ("subject_definitions", "retention_analysis"):
                    if k in parsed:
                        warn.append(
                            f"pasted {k} ignored in merge mode — generated from the "
                            f"Subject nodes instead. Switch to override to use it."
                        )

            # speaker IDs follow the pasted body, not the now-unused widget
            speakers = self._assign_speakers(shots, nick_to_num)

        if paste and llm_mode.startswith("verbatim"):
            out = self._substitute(paste, nick_to_num, speakers, unknown)
            notes = [f"@{n} has no matching subject node" for n in sorted(unknown)]
            notes.append("verbatim mode — sections are passed through unchanged.")
            return (out, "", "\n".join(f"! {n}" for n in notes))

        sum_txt = self._substitute(summary, nick_to_num, speakers, unknown).strip()
        shots_txt = self._substitute(shots, nick_to_num, speakers, unknown).strip()
        style_txt = (style_line or "").strip()

        # ---- shot appearance map (for retention "appears in ...") ---------- #
        shot_blocks = _split_shots(shots)
        appears = {}
        for s in chain:
            tok = re.compile(r"@" + re.escape(s["nickname"]) + r"(?![A-Za-z0-9_\-])")
            hits = [f"[Shot {n}]" for n, blk in shot_blocks if n and tok.search(blk)]
            appears[s["nickname"]] = ", ".join(hits)

        # ---- task prefix ---------------------------------------------------- #
        flags = []
        voice_modes = {s["voice_mode"] for s in chain if s["audio"] is not None}
        bg_idx = _parse_index(background_audio)
        has_video_audio = any(s_.get("video_audio_n") for s_ in chain)
        if has_video_audio or any(not _is_timbre(v) for v in voice_modes) or (
            bg_idx is not None and not _is_timbre(background_audio_mode)
        ):
            flags.append("audio reuse")
        elif voice_modes or bg_idx is not None:
            flags.append("audio reference")
        prefix = " + ".join([task] + flags)

        # ---- summary -------------------------------------------------------- #
        lead = ""
        roles = {_effective_video_role(s_): s_["video"]
                 for s_ in chain if s_["video"] is not None}
        if video_1_role == "edit source" or "edit source" in roles:
            v = roles.get("edit source", 1)
            lead = f"The target video is an edited version of <Video {v}>. "
            if task == "reference generation":
                task = "video editing"
        elif video_1_role == "continuation" or "continuation" in roles:
            v = roles.get("continuation", 1)
            lead = f"The target video continues from <Video {v}>. "
            if task == "reference generation":
                task = "video continuation"
        ff = _parse_index(first_frame_picture)
        if ff is not None:
            lead += f"The target video opens on the composition of <Picture {ff}>. "
        summary_out = f"[{prefix}] {lead}{sum_txt}"

        # ---- definitions ----------------------------------------------------- #
        defs = [self._definition_line(s, n, speakers) for s, n in zip(chain, nums)]
        defs += self._audio_definition_lines(chain, nums, speakers)
        for s_ in chain:
            if s_["video"] is None:
                continue
            role = _effective_video_role(s_)
            v = s_["video"]
            if role == "camera + timing":
                defs.append(
                    f"<Video {v}> is the temporal-structure reference for the target "
                    f"video, providing its camera movement and cut rhythm."
                )
            elif role == "edit source":
                defs.append(f"<Video {v}> is the source video being edited.")
            elif role == "continuation":
                defs.append(f"<Video {v}> is the video the target continues from.")
            elif role == "audio source only":
                defs.append(
                    f"<Video {v}> supplies its audio track only; none of its picture "
                    f"content appears in the target video."
                )
        for s_ in chain:
            n = s_.get("video_audio_n")
            if not n:
                continue
            v = s_.get("video")
            src = f" of <Video {v}>" if v else ""
            defs.append(
                f"<Audio {n}> is the audio track{src}, reused as the audible sound "
                f"of that footage in the target video."
            )
        if video_1_role == "camera + timing":
            defs.append(
                "<Video 1> is the temporal-structure reference for the target video, "
                "providing its camera movement and cut rhythm."
            )
        if extra_definitions.strip():
            defs.append(extra_definitions.strip())
        if pasted_defs:
            defs = [self._substitute(pasted_defs, nick_to_num, speakers, unknown)]

        # ---- retention ------------------------------------------------------- #
        rets = [
            self._retention_line(s, n, appears.get(s["nickname"], ""))
            for s, n in zip(chain, nums)
        ]
        rets += self._audio_retention_lines(chain, nums, bg_idx, background_audio_mode)
        for s_ in chain:
            if s_["video"] is None:
                continue
            role = _effective_video_role(s_)
            v = s_["video"]
            if role == "camera + timing":
                rets.append(
                    f"<Video {v}>: weak_reference - only the camera movement and "
                    f"pacing structure are followed."
                )
            elif role in ("edit source", "continuation"):
                rets.append(
                    f"<Video {v}>: fully_preserved - the target video keeps the "
                    f"structure of <Video {v}> except where changed below."
                )
            elif role == "audio source only":
                rets.append(
                    f"<Video {v}>: reference - only the audio track is used."
                )
        for s_ in chain:
            n = s_.get("video_audio_n")
            if n:
                rets.append(
                    f"<Audio {n}>: fully_copy - the accompanying audio track is "
                    f"reused unchanged."
                )
        if video_1_role in ("edit source", "continuation"):
            rets.append(
                f"<Video 1>: fully_preserved - the target video keeps the structure of "
                f"<Video 1> except where changed below."
            )
        elif video_1_role == "camera and timing structure":
            rets.append(
                "<Video 1>: weak_reference - only the camera movement and pacing "
                "structure are followed."
            )
        if extra_retention.strip():
            rets.append(extra_retention.strip())
        if pasted_rets:
            rets = [self._substitute(pasted_rets, nick_to_num, speakers, unknown)]

        # ---- assemble --------------------------------------------------------- #
        detailed = (style_txt + "\n" + shots_txt).strip()
        sections = [
            "subject_definitions:\n" + "\n".join(defs),
            "summary:\n" + summary_out,
            "retention_analysis:\n" + "\n".join(rets),
            "detailed_description:\n" + detailed,
            "overall_soundscape:\n" + (overall_soundscape.strip() or "N/A"),
            "non_diegetic_music:\n" + (non_diegetic_music.strip() or "N/A"),
        ]
        prompt = "\n\n".join(sections)

        # ---- wiring map -------------------------------------------------------- #
        pic_owner = {}
        for s, n in zip(chain, nums):
            for p in s["pictures"]:
                pic_owner.setdefault(p, []).append(f"@{s['nickname']} (<Subject {n}>)")
        wiring = ["ComfyUI slot  ->  prompt label  ->  content", "-" * 52]
        for p in sorted(pic_owner):
            wiring.append(
                f"ref_image_{p - 1:<4} ->  <Picture {p}>  ->  {', '.join(pic_owner[p])}"
            )
        vids = sorted({s["video"] for s in chain if s["video"] is not None})
        if video_1_role != NONE and 1 not in vids:
            vids = sorted(set(vids) | {1})
        for v in vids:
            wiring.append(f"ref_video_{v - 1:<4} ->  <Video {v}>   ->  {video_1_role if v == 1 else 'motion reference'}")
        auds = sorted({s["audio"] for s in chain if s["audio"] is not None} |
                      ({bg_idx} if bg_idx else set()))
        for a in auds:
            wiring.append(f"ref_audio_{a - 1:<4} ->  <Audio {a}>   ->  voice / soundtrack reference")
        wiring_map = "\n".join(wiring)

        # ---- lint -------------------------------------------------------------- #
        if strict:
            for nick in sorted(unknown):
                warn.append(f"@{nick} has no matching subject node — token left unresolved.")
            used_pics = sorted(pic_owner)
            if used_pics and used_pics != list(range(1, len(used_pics) + 1)):
                warn.append(
                    f"picture numbering has gaps ({used_pics}). Fill ref_image slots "
                    f"contiguously from 0 or the tags will misalign."
                )
            if len(used_pics) > MAX_PICTURES:
                warn.append(f"{len(used_pics)} pictures — max is {MAX_PICTURES}.")
            total = len(used_pics) + len(vids) + len(auds)
            if total > MAX_FILES:
                warn.append(f"{total} reference files — max is {MAX_FILES} per generation.")
            if len(vids) > MAX_VIDEOS:
                warn.append(f"{len(vids)} videos — max is {MAX_VIDEOS}.")
            if len(auds) > MAX_AUDIOS:
                warn.append(f"{len(auds)} audio files — max is {MAX_AUDIOS}.")

            for s in chain:
                if not s["pictures"] and s["video"] is None and s["audio"] is None:
                    warn.append(f"@{s['nickname']} has no reference assigned.")
                if not s["description"]:
                    warn.append(f"@{s['nickname']} has an empty description.")
                if not appears.get(s["nickname"]):
                    warn.append(f"@{s['nickname']} is defined but never appears in any shot.")
                if s["kind"] == "motion" and s["video"] is None:
                    warn.append(f"@{s['nickname']} is kind=motion but no video assigned.")
                if s["video"] is not None and _effective_video_role(s) == "unset":
                    warn.append(
                        f"@{s['nickname']} has a video but no purpose set — pick a "
                        f"video_role (motion / edit source / continuation / "
                        f"camera + timing / audio source only), or set kind=motion. "
                        f"Until then the video is loaded but never cited."
                    )

            for field, name in ((summary, "summary"), (shots, "shots"),
                                (style_line, "style_line")):
                if GUILLEMET_RE.search(field or ""):
                    warn.append(f"{name} contains ‹ › guillemets — H3 needs ASCII < >.")
                if PLACEHOLDER_RE.search(field or ""):
                    warn.append(f"{name} still contains [[...]] placeholders.")

            # timestamps: shot 1 none, later shots required
            for n, blk in shot_blocks:
                if n == 1 and TIMESTAMP_RE.search(blk):
                    warn.append("[Shot 1] carries a timestamp — it should not.")
                if n and n > 1 and not TIMESTAMP_RE.search(blk):
                    warn.append(f"[Shot {n}] has no MM:SS.mmm timestamp.")

            wc = _word_count(detailed)
            if wc < 350:
                warn.append(f"detailed_description is {wc} words — guide target is 350-500.")
            elif wc > 500:
                warn.append(f"detailed_description is {wc} words — guide target is 350-500.")

            # timbre-only audio must not be named in the sound sections
            for s in chain:
                if s["audio"] is not None and s["voice_mode"].startswith("timbre"):
                    lbl = f"<Audio {s['audio']}>"
                    for txt, name in ((overall_soundscape, "overall_soundscape"),
                                      (non_diegetic_music, "non_diegetic_music"),
                                      (shots, "shots")):
                        if lbl in (txt or ""):
                            warn.append(
                                f"{lbl} is timbre-only but is named in {name} — that is "
                                f"what makes it audible as a track. Remove it."
                            )
            if "<d>" in shots and "</d>" not in shots:
                warn.append("unclosed <d> tag in shots.")
            if re.search(r"<d>\s*(?!\[)", shots):
                warn.append("a <d> block is missing its [Language] tag.")

        lib_note = _library_summary(scene_library)
        warnings = "\n".join(f"! {w}" for w in warn) if warn else "clean — no issues found."
        if lib_note:
            warnings += f"\n\n{lib_note}"
        return (prompt, wiring_map, warnings)


NODE_CLASS_MAPPINGS = {
    "H3Subject": H3Subject,
    "H3SubjectAdvanced": H3SubjectAdvanced,
    "H3PromptAssembler": H3PromptAssembler,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3Subject": "H3 Subject",
    "H3SubjectAdvanced": "H3 Subject Advanced",
    "H3PromptAssembler": "H3 Prompt Assembler",
}

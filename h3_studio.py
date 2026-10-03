"""
H3 Studio — subjects, shots and prompt in one node.

The separate nodes stay the preferred route for anything complex: they make the
chain visible, and a graph you can read is easier to debug than a panel you have
to scroll. Studio exists for the other case — a single shot, quickly, without
wiring five nodes to get there.

Nothing is reimplemented here. Subjects are turned into ordinary chain entries
and handed to H3PromptAssembler, so Studio and the separate nodes produce
byte-identical prompts from the same inputs.
"""

import json

# Imported relative-first, then absolute — see the note in h3_nodes.py.
try:
    from .h3_refs import resolve_chain, load_tensor, ANY
    from .h3_nodes import (
        H3PromptAssembler, VOICE_MODES, LLM_MODES, TASK_BASES,
        PREVIEW_SIZES, MAX_PICTURES,
    )
except ImportError:
    from h3_refs import resolve_chain, load_tensor, ANY
    from h3_nodes import (
        H3PromptAssembler, VOICE_MODES, LLM_MODES, TASK_BASES,
        PREVIEW_SIZES, MAX_PICTURES,
    )

MAX_VIDEOS = 2
MAX_AUDIOS = 1
MAX_VIDEO_AUDIOS = 3


def _fmt_time(seconds):
    """MM:SS.mmm, the timestamp format the shot blocks use."""
    seconds = max(0.0, float(seconds))
    m = int(seconds // 60)
    s = seconds - m * 60
    return f"{m:02d}:{s:06.3f}"


def shots_to_text(shots):
    """Turn timeline bars into [Shot N] blocks.

    Shot 1 carries no timestamp — the guide reserves those for cuts — and every
    later shot gets one derived from its position on the timeline, which is the
    whole point of editing shots as bars rather than typing times by hand.
    """
    out = []
    for i, sh in enumerate(sorted(shots, key=lambda s: float(s.get("start", 0)))):
        parts = []
        head = f"[Shot {i + 1}]"
        if i > 0:
            head += f" At {_fmt_time(sh.get('start', 0))}, the shot cuts to"
        body = (sh.get("text") or "").strip()
        if i > 0 and body:
            body = body[0].lower() + body[1:]
        parts.append(f"{head} {body}".strip())
        cam = (sh.get("camera") or "").strip()
        if cam:
            parts.append(f"The camera {cam.rstrip('.')}.")
        snd = (sh.get("sound") or "").strip()
        if snd:
            parts.append(snd.rstrip(".") + ".")
        out.append(" ".join(p for p in parts if p))
    return "\n".join(out)


class H3Studio:
    """Subjects, a shot timeline and the prompt in a single node."""

    CATEGORY = "MiniMax H3"
    FUNCTION = "run"
    RETURN_TYPES = (
        ("STRING",)
        + ("IMAGE",) * MAX_PICTURES
        + (ANY,) * MAX_VIDEOS
        + (ANY,) * MAX_AUDIOS
        + (ANY,) * MAX_VIDEO_AUDIOS
        + ("STRING", "STRING")
    )
    RETURN_NAMES = (
        ("prompt",)
        + tuple(f"picture_{i}" for i in range(1, MAX_PICTURES + 1))
        + tuple(f"video_{i}" for i in range(1, MAX_VIDEOS + 1))
        + ("audio_1",)
        + tuple(f"video_audio_{i}" for i in range(1, MAX_VIDEO_AUDIOS + 1))
        + ("report", "warnings")
    )
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "task": (TASK_BASES, {"default": "reference generation"}),
                "style_line": ("STRING", {
                    "default": "The target video is cinematic, rendered in full "
                               "color, with soft directional lighting.",
                    "multiline": True,
                }),
                "summary": ("STRING", {"default": "", "multiline": True}),
                "overall_soundscape": ("STRING", {"default": "", "multiline": True}),
                "non_diegetic_music": ("STRING", {"default": "N/A", "multiline": True}),
            },
            "optional": {
                "video_1": (ANY, {}),
                "video_2": (ANY, {}),
                "audio_1": (ANY, {}),
                "video_audio_1": (ANY, {}),
                "video_audio_2": (ANY, {}),
                "video_audio_3": (ANY, {}),
                "extra_subjects": ("H3_SUBJECTS", {
                    "tooltip": "Optional. Subjects from separate H3 Subject nodes, "
                               "placed before the ones defined in this node.",
                }),
                "llm_prompt": ("STRING", {"default": "", "multiline": True}),
                "llm_mode": (LLM_MODES, {"default": LLM_MODES[0]}),
                "strict": ("BOOLEAN", {"default": True,
                                       "label_on": "lint", "label_off": "quiet"}),
                # managed by the Studio panel; declared so they save with the workflow
                "subjects_json": ("STRING", {"default": "[]", "multiline": True}),
                "shots_json": ("STRING", {"default": "[]", "multiline": True}),
                "scene_library": ("STRING", {"default": "{}", "multiline": True}),
                "preview_size": (PREVIEW_SIZES, {"default": "128"}),
            },
        }

    @classmethod
    def IS_CHANGED(cls, **kw):
        return float("nan")

    def run(self, task, style_line, summary, overall_soundscape, non_diegetic_music,
            video_1=None, video_2=None, audio_1=None,
            video_audio_1=None, video_audio_2=None, video_audio_3=None,
            extra_subjects=None,
            llm_prompt="", llm_mode=LLM_MODES[0], strict=True,
            subjects_json="[]", shots_json="[]", scene_library="{}",
            preview_size="128"):

        notes = []
        try:
            specs = json.loads(subjects_json or "[]")
            specs = specs if isinstance(specs, list) else []
        except Exception:
            specs, _ = [], notes.append("subjects_json is not valid JSON")

        try:
            shots = json.loads(shots_json or "[]")
            shots = shots if isinstance(shots, list) else []
        except Exception:
            shots, _ = [], notes.append("shots_json is not valid JSON")

        videos = [video_1, video_2]
        audios = [audio_1]
        vaudios = [video_audio_1, video_audio_2, video_audio_3]

        chain = list(extra_subjects or [])
        for spec in specs:
            images, missing = [], []
            for p in spec.get("images") or []:
                try:
                    images.append(load_tensor(p))
                except FileNotFoundError:
                    missing.append(p)
                except Exception as e:
                    notes.append(f"could not load {p} ({type(e).__name__}: {e})")
            if missing:
                notes.append(
                    f"@{spec.get('nickname', '?')}: MISSING {len(missing)} image "
                    f"file(s): " + ", ".join(missing)
                )

            vi = int(spec.get("video") or 0)
            ai = int(spec.get("audio") or 0)
            vai = int(spec.get("video_audio") or 0)
            chain.append({
                "nickname": (spec.get("nickname") or "subject").strip(),
                "kind": spec.get("kind") or "person",
                "description": (spec.get("description") or "").strip(),
                "pictures": [],
                "images": images[:MAX_PICTURES],
                "slots": None,
                "video": videos[vi - 1] if 1 <= vi <= MAX_VIDEOS else None,
                "video_role": spec.get("video_role") or "auto",
                "audio": audios[ai - 1] if 1 <= ai <= MAX_AUDIOS else None,
                "video_audio": (vaudios[vai - 1]
                                if 1 <= vai <= MAX_VIDEO_AUDIOS else None),
                "voice_mode": spec.get("voice_mode") or VOICE_MODES[0],
                "retention": spec.get("retention") or "auto",
                "retention_note": (spec.get("retention_note") or "").strip(),
                "notes": [],
                "missing": missing,
                "advanced": True,
            })

        shots_text = shots_to_text(shots)

        # Delegate to the assembler rather than rebuilding the six sections here,
        # so Studio and the separate nodes cannot drift apart.
        prompt, _wiring, warnings = H3PromptAssembler().assemble(
            subjects=chain, task=task, video_1_role="none",
            first_frame_picture="none", style_line=style_line, summary=summary,
            shots=shots_text, overall_soundscape=overall_soundscape,
            non_diegetic_music=non_diegetic_music, strict=strict,
            llm_prompt=llm_prompt, llm_mode=llm_mode, scene_library=scene_library,
        )

        placed, pmap, vmap, amap, vamap, rnotes = resolve_chain(chain)
        report = []
        for e in chain:
            nick = e["nickname"]
            got = pmap.get(nick, [])
            rng = (f"<Picture {got[0]}>-<Picture {got[-1]}>" if len(got) > 1
                   else (f"<Picture {got[0]}>" if got else "no images"))
            extra = []
            if nick in vmap:
                extra.append(f"<Video {vmap[nick]}>")
            if nick in amap:
                extra.append(f"<Audio {amap[nick]}>")
            if nick in vamap:
                extra.append(f"<Audio {vamap[nick]}> (video track)")
            report.append(f"@{nick}: {rng}" + ("  + " + ", ".join(extra) if extra else ""))
        report += rnotes
        report.append("")
        report.append(f"{len(shots)} shot(s), {_fmt_time(max([float(s.get('start', 0)) + float(s.get('dur', 0)) for s in shots] or [0]))} total")
        report.append("")
        for s_ in sorted(placed):
            report.append(f"ref_image_{s_ - 1:<3}->  <Picture {s_}>   (@{placed[s_][1]})")

        images_out = [placed[s_][0] if s_ in placed else None
                      for s_ in range(1, MAX_PICTURES + 1)]
        vids = [videos[i] if vmap and (i + 1) in vmap.values() else videos[i]
                for i in range(MAX_VIDEOS)]

        if notes:
            warnings = "\n".join(f"! {n}" for n in notes) + "\n" + warnings

        return tuple([prompt] + images_out + vids + [audios[0]] + vaudios +
                     ["\n".join(report), warnings])


NODE_CLASS_MAPPINGS = {"H3Studio": H3Studio}
NODE_DISPLAY_NAME_MAPPINGS = {"H3Studio": "H3 Studio"}

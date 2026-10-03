# ComfyUI-H3-PromptBuilder

Structured prompt construction for **MiniMax H3** reference-to-video generation
in ComfyUI.

H3's reference mode expects a six-section prompt in which every reference is
cited by number — `<Picture 6>`, `<Video 1>`, `<Audio 1>` — and those numbers
must match the order your images are wired into the loader. Maintaining that by
hand is where prompts silently break: add one image and every number below it
shifts, but the prose still says `<Picture 6>`, so the model reads the wrong
face and reports no error.

This pack removes the bookkeeping. Subjects carry their own reference media,
numbering is derived from what is actually connected, and a linter catches the
mistakes that otherwise fail quietly.

> Prompt-format conventions follow the MiniMax H3 prompt writing guide. This is
> an unofficial community pack and is not affiliated with MiniMax.

## Features

- **Drag-and-drop reference galleries** on each subject, with thumbnails badged
  by the `<Picture N>` they will actually receive
- **Automatic numbering** for pictures, video and audio, derived from tray order
  and chain position — nothing is typed twice
- **`@nickname` tokens** instead of literal labels, so `@hero` becomes
  `<Subject 1>` and renumbers itself when the graph changes
- **Speaker IDs** assigned by order of first vocal event, as the format requires
- **Paste a whole prompt** from an LLM and have it split into sections
- **Scene and shot library** saved with the workflow, with a scrollable browser
- **Saved subjects** reusable across workflows, picked from a thumbnail list
- **Linter** for numbering gaps, guillemets, missing timestamps, word count,
  unused subjects, missing files, and audio-reference leakage

## Nodes

| Node | Purpose |
|---|---|
| **H3 Subject Advanced** | A character or environment plus its reference media |
| **H3 Subject** | Minimal subject, for manual workflows or media-less subjects |
| **H3 Ref Router** | Flattens the chain into numbered slots for the H3 loader |
| **H3 Prompt Assembler** | Writes the six-section prompt |
| **H3 Studio** | All of the above in one node, with a shot timeline |

## Requirements

ComfyUI, plus a node pack that provides the MiniMax H3 loader. No Python
dependencies beyond what ComfyUI already ships.

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/shadowsinhighlight/ComfyUI-H3-PromptBuilder
```

Restart ComfyUI, then hard-refresh the browser so the frontend extensions load.

## Node reference

### H3 Subject

One node per subject. Chain them through the `subjects` socket — **chain order is
subject numbering**.

| Widget | Notes |
|---|---|
| `character` (input) | Optional. Wire from the matching H3 Character Refs node — the nickname then follows it automatically |
| `nickname` | Write `@hero` in the body text; becomes `<Subject N>`. Ignored when `character` is wired |
| `kind` | `person` / `environment` / `object` / `motion` / `effect` / `style` |
| `description` | **Fixed visual traits only.** No mood, no action, no location. For `kind=motion`, describe mechanics: stride, tempo, weight shift, arm swing |
| `picture_1` … `picture_9` | Tick every image showing this subject |
| `motion_from_video` | For `kind=motion`, which video carries the movement |
| `voice_from_audio` | Which audio is this subject's voice reference |
| `voice_mode` | Timbre-only (never audible) vs. reuse the signal |
| `retention` | Leave on `auto` unless overriding |
| `retention_note` | Optional custom retention sentence |

### H3 Prompt Assembler

Outputs `prompt`, `wiring_map`, and `warnings`.

| Widget | Notes |
|---|---|
| `task` | reference generation / video editing / video continuation |
| `video_1_role` | motion only / edit source / continuation / camera+timing structure |
| `first_frame_picture` | Adds the opening-composition clause |
| `style_line` | Goes above `[Shot 1]`, where the guide wants it |
| `summary` | Plot level. **Don't type the task prefix** — it's generated |
| `shots` | One `[Shot N]` block per line |
| `overall_soundscape` / `non_diegetic_music` | |
| `refs` (input) | Optional. Wire the H3 Character Refs chain here for automatic numbering |
| `background_audio` + mode | For score or ambience reuse |
| `extra_definitions` / `extra_retention` | Escape hatch for anything unusual |
| `strict` | Toggles the linter |

## Token syntax

| You write | You get |
|---|---|
| `@hero` | `<Subject 1>` |
| `@hero!` | `<Subject 1> (S2)` — the `!` marks a vocal event |

Speaker IDs are assigned by **order of first vocal event in the target video**,
not by subject order — so if `@friend!` appears before `@hero!`, she is `(S1)` and
he is `(S2)`. This is what the guide specifies and what people most often get
wrong by hand.

## What it generates for you

- Task prefix, including `+ audio reuse` / `+ audio reference` based on voice modes
- `<Audio N>` definition and retention lines from each subject's voice settings
- `(appears in [Shot 1], [Shot 3])` computed by scanning which shot blocks
  actually mention each subject
- `attribute_transfer` on motion subjects, `fully_preserved` on identity subjects
- `The target video is an edited version of <Video 1>.` lead-in for editing tasks
- A wiring map showing which `ref_image_N` slot takes which file

## H3 Subject Advanced

**The main node.** One per character, chained. It replaces the old
Character Refs + Subject pair — identity and media live together, so there is no
nickname to type twice and no decision about which picture slot belongs to whom.

| Widget / input | Notes |
|---|---|
| `nickname` | `@hero` in the body text |
| `kind` | `person` / `environment` / `object` / `motion` / `effect` / `style` |
| `description` | Fixed visual traits only |
| *(gallery)* | Drop images on the node or click **＋ add images** |
| `subjects` | Chain socket — chain position sets the numbering |
| `extra_images` | Optional IMAGE socket, appended after the uploaded ones |
| `video` / `audio` | Optional media sockets |
| `video_audio` | The audio track belonging with the connected video — goes to `ref_video_audio_*` and is cited as its own `<Audio N>` |
| `preview_size` | Gallery thumbnail size, 64–1024 px. Display only — it does not change the images sent to the model |
| `video_role` | **What the video is for**: motion / edit source / continuation / camera + timing / audio source only. `auto` infers motion for `kind=motion` and otherwise asks rather than guessing |
| `voice_mode`, `retention`, `retention_note` | As on the basic node |

**Saved subjects.** **💾** stores the current subject — nickname, kind,
description, roles and its image list — to disk, so it can be reused in other
workflows. **▾ saved subjects** opens a picker showing each one with a thumbnail
of its first image, its kind, image count, and a red note if any files are
missing. **✕** in the picker deletes a saved subject.

**Missing files.** If a workflow is opened where the images aren't present, each
absent tile turns red and names the file, the router `report` lists
`! MISSING FILE: …` per path, and the assembler `warnings` names them too. Drop
replacements on the node, or **✕** the tile.

**The gallery.** Thumbnails show the `<Picture N>` each image will actually get,
including the offset from upstream characters — so `@kitchen`'s first image reads
`4` when `@hero` has three. **✕** on a tile removes it, **🗑 remove all** clears
the character, and tiles **drag to reorder** since tray order decides numbering.

Thumbnail size is set by `preview_size` (64–1024 px). Picking a large size widens
the node to fit; on a node too narrow for the chosen size, tiles cap at the node
width rather than spilling past the edge. Badges and the ✕ scale with the tile.

Uploads go to `ComfyUI/input/h3/<nickname>/`. The ordered list lives in the
node's `image_list` widget, so it **saves with the workflow**.

Numbering is entirely automatic: images number in tray order, characters in chain
order, and `<Video N>` / `<Audio N>` follow the same rule. Change anything and
the prompt renumbers on the next run.

### Chaining

```
[Subject Advanced: hero] → [Subject Advanced: kitchen] → [Subject Advanced: gait]
                                                              |
                                        ┌─────────────────────┴──────────────┐
                                        ↓                                    ↓
                                 [H3 Ref Router]                   [H3 Prompt Assembler]
                                   → ref_image_0..8                        subjects
```

One chain feeds both. The router supplies the media to the MiniMax loader, the
assembler writes the prompt, and both derive numbering from the same shared
function so they cannot disagree.

### H3 Subject (basic)

Still available for a subject with no media of its own, and for the old fully
manual workflow. When **no** subject in the chain carries media, its
`picture_1`–`picture_9` checkboxes and video/audio dropdowns work as before. As
soon as any subject has uploaded media, the chain takes over and the checkboxes
are ignored — with a warning saying so.

### H3 Ref Router

Outputs `picture_1` … `picture_9` (into `ref_image_0` … `ref_image_8` — note the
off-by-one), `video_1` … `video_3`, `audio_1` … `audio_3`,
`video_audio_1` … `video_audio_3`, a `subjects` passthrough for the assembler,
and a `report`.

### Audio numbering

The prompt format has one audio label, `<Audio N>`, so standalone audio and a
reference video's audio track draw from the **same counter** even though they go
to different loader sockets. Per subject, the plain audio is numbered first. The
`report` prints the resulting map, e.g.

```
ref_audio_0        ->  <Audio 1>
ref_video_audio_0  ->  <Audio 2>
```

The guide allows three audio references in total; exceeding that is reported
rather than blocked.

The `subjects` output means the router can sit in the middle of the chain:

```
[Subject Advanced …] → [H3 Ref Router] → subjects → [H3 Prompt Assembler]
                            ↓
                     ref_image_0..8
```

Preview thumbnails are **stamped with their `<Picture N>` and owner**, drawn into
the image itself so a reflowing preview grid can never put a caption beside the
wrong tile.

**Unfilled outputs return `None`.** Leave them unconnected — a placeholder would
be read as a real reference.

### Video and audio socket types

The `video` and `audio` sockets accept any type. The MiniMax H3 loader's
`ref_video_*` / `ref_audio_*` socket types vary between node packs and versions,
and a wrong guess would leave them unable to connect at all. Whatever your loader
produces passes through untouched.

## H3 Studio

Everything in one node: subjects, a shot timeline, and the prompt. Useful for a
single scene without wiring five nodes; the separate nodes remain the better
route for anything complex, because a graph you can read is easier to debug than
a panel you have to scroll.

Two tabs:

**SUBJECTS** — add subjects inline, each with nickname, kind, description and its
own image tray (drag and drop, or ＋). `↑` reorders, which changes that subject's
Picture numbers. Video and audio are assigned by picking `video_1` / `video_2` /
`audio_1`, wired as sockets on the node.

**TIMELINE** — shots as draggable bars on a ruler. Drag a bar to move it, drag
its right edge to resize, click to edit its action, camera and sound. Positions
snap to a quarter second, and `+` / `−` zoom. **Shot timestamps are generated
from bar positions** — Shot 1 gets none (the format reserves timestamps for
cuts), and later shots get `At MM:SS.mmm, the shot cuts to …`.

Studio does not reimplement prompt building. It converts its panel state into
ordinary subject entries and hands them to the same assembler, so Studio and the
separate nodes produce identical prompts from identical inputs.

## Pasting a whole prompt

`llm_prompt` (bottom of the assembler) takes a complete prompt — the kind an LLM
hands you in one block. **When it's non-empty it takes precedence** over
`style_line`, `summary`, `shots`, `overall_soundscape` and `non_diegetic_music`.
Empty it and those fields go back to being live. Nothing is deleted either way.

`@nickname` tokens still resolve, and speaker IDs are assigned from the pasted
body rather than the widgets.

Section headers are matched leniently — `detailed_description:`,
`Detailed Description:` and `detailed description:` all work. Any leading
`[reference generation]` on the summary is stripped and regenerated so the audio
flags match your actual wiring.

`llm_mode`:

| Mode | Behaviour |
|---|---|
| `merge` (default) | Pasted body sections win; `subject_definitions` and `retention_analysis` are still generated from the Subject nodes and refs, keeping correct numbering |
| `override` | Pasted `subject_definitions` / `retention_analysis` are used too |
| `verbatim` | The paste is emitted unchanged apart from `@token` substitution |

A paste with no recognisable headers isn't dropped — it's read as
`detailed_description` if it contains `[Shot n]`, otherwise as `summary`, and
the lint output says which.

## Scene and shot library

A scrollable browser on the assembler lists every saved scene with its shots, so
nothing has to be remembered or retyped.

```
▾ kitchen                              3 shots  ✎
   1   @hero leans on the counter while @friend…  ✕
   2   @hero turns off the tap.                   ✕
   10  Wide on the doorway, @hero enters.         ✕
▸ bedroom                              1 shot   ✎
```

- **Click any row to load it.** The active shot is highlighted green.
- **Click a scene header** to collapse or expand it.
- **✕** deletes a shot, **✎** renames a scene (merging if the new name exists).
- Each row shows a one-line gist of the prompt; hover for the save timestamp.
- A filter box appears once there are more than six shots, matching scene names,
  shot numbers and prompt text.

Buttons:

| Button | Does |
|---|---|
| 💾 save | Saves over the current scene / shot |
| ✚ save as new shot | Saves into the next free number in the scene — for trying a variant without losing the original |
| ◀ ▶ | Step through the current scene's shots |

A saved shot captures `llm_prompt`, `style_line`, `summary`, `shots`,
`overall_soundscape`, `non_diegetic_music`, `task`, `video_1_role` and
`first_frame_picture`.

Everything lives in the `scene_library` widget as JSON, so it **saves with the
workflow** and travels when you duplicate the node. The raw JSON widget is
collapsed by the frontend.

Shot keys sort naturally, so shot 10 follows shot 9.

## Linter

Warns on: unresolved `@tokens`, picture-numbering gaps (the ref_image
misalignment trap), file-count limits (9 images / 3 videos / 3 audio / 12 total),
`‹ ›` guillemets instead of ASCII `< >`, leftover `[[placeholders]]`, a timestamp
on Shot 1 or a missing one on later shots, `detailed_description` outside
350–500 words, unclosed or unlabelled `<d>` blocks, subjects defined but never
used, and — the subtle one — a timbre-only `<Audio N>` named in a sound section,
which is what makes it leak in as an audible backing track.

## Related

Reference sheets (tiling several views into one slot) live in a separate pack,
[**ComfyUI-ReferenceSheet**](https://github.com/shadowsinhighlight/ComfyUI-ReferenceSheet). For resizing references to H3-friendly resolutions, see [**ComfyUI-H3-BulkResize**](https://github.com/shadowsinhighlight/ComfyUI-H3-BulkResize).

## Known limits

- `first_frame_picture` writes the *soft* composition clause. True pixel-locked
  frame-one conditioning requires the FL2VA checkpoint partition, not Ref2VA.
- No audio passthrough exists in H3 — every generation synthesizes its own track.
  If you need an exact recording in the output, mux it in post.


## Contributing

Issues and pull requests are welcome. When reporting a prompt that came out
wrong, include the `report` output from H3 Ref Router and the `warnings` output
from H3 Prompt Assembler — between them they usually show the cause.

## License

MIT — see [LICENSE](LICENSE).

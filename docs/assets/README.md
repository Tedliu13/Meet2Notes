# README demo

`meet2notes-demo.gif` is a looping animation of the real local application.
`meet2notes-demo.png` is its static first frame.

`meet2notes-demo-fast.gif` is the six-second variant, with the same 36 frames,
scene order and infinite loop. It is rebuilt from the original RGB captures with
a skin-tone-weighted palette for the live clip, separate palettes for the other
scenes, and Floyd–Steinberg dithering to avoid orange/gray patches on faces.
The original thirty-second animation remains unchanged.

The sequence shows live capture, speakers, AI notes, utilities, a real local
Meeting Assistant response, and the new transcript search with tags. The live
scene comes from the first four seconds of the supplied demo recording, where
the four-person call is visible. The recording ends at 2:42 on AI notes, so that
timestamp was not used for the live scene. The live scene shows an earlier app
version; the remaining screenshots show 0.7.0. Meeting content is example data.
AI output is captured as generated, without rewriting its answer for the demo.

Rebuild using Pillow and the original PNG captures:

```powershell
python scripts/build_readme_demo.py PATH_TO_CAPTURES --font C:/Windows/Fonts/segoeui.ttf
```

For the corrected six-second version, add
`--fast --output docs/assets/meet2notes-demo-fast.gif`.

See the script's help for capture filenames and the ffmpeg extraction command.
Supply a local TrueType font on other platforms. The script adds only an outer
frame, scene labels and transitions; it does not manufacture application UI.
Original screenshots and video frames are retained locally under
`temp/demo-0.7.0/`, which is excluded from Git.

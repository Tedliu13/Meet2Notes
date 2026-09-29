"""Build the README animation from real captures (requires Pillow).

Input directory: live-01.png ... live-16.png (four seconds at 4 fps),
02-speakers.png, 03-ai-notes.png, 04-utilities.png, 06-answer.png,
07-library.png. Capture screenshots through the running application.
Extract video frames with ffmpeg: -ss 0 -i demo.mp4 -t 4 -vf fps=4 live-%02d.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", type=Path)
    parser.add_argument("--output", type=Path, default=Path("docs/assets/meet2notes-demo.gif"))
    parser.add_argument("--font", type=Path, required=True, help="Path to a TrueType font")
    parser.add_argument("--fast", action="store_true", help="Use the exact six-second timing")
    args = parser.parse_args()
    font = ImageFont.truetype(str(args.font), 25)
    small = ImageFont.truetype(str(args.font), 16)
    labels = [
        "Live transcript",
        "Speakers",
        "AI notes",
        "Utilities",
        "Ask your meeting",
        "Find & organize",
    ]
    subtitles = [
        "Capture the conversation as it happens",
        "See who said what, with individual audio fragments",
        "Review summaries, highlights and decisions",
        "Take your audio, transcript and notes with you",
        "Reusable quick actions. A real answer from the local model.",
        "Search spoken words, filter by tag, jump to the moment",
    ]

    def compose(path: Path, scene: int) -> Image.Image:
        canvas = Image.new("RGB", (1360, 860), "#101e36")
        draw = ImageDraw.Draw(canvas)
        draw.text((40, 19), "Meet2Notes", font=font, fill="#ffffff")
        draw.text((250, 24), subtitles[scene], font=small, fill="#b9cce9")
        draw.rounded_rectangle((1225, 19, 1320, 49), 15, fill="#233b61")
        draw.text((1243, 23), f"0{scene + 1} / 06", font=small, fill="#d8e7ff")
        with Image.open(path) as screenshot:
            screen = ImageOps.contain(
                screenshot.convert("RGB"), (1280, 720), Image.Resampling.LANCZOS
            )
        canvas.paste(screen, (40 + (1280 - screen.width) // 2, 74 + (720 - screen.height) // 2))
        for index, label in enumerate(labels):
            x = 40 + index * 216
            color = "#5797ff" if index == scene else "#314661"
            draw.rounded_rectangle((x, 811, x + 197, 814), 1, fill=color)
            draw.text((x, 823), label, font=small, fill="#ffffff" if index == scene else "#829aba")
        return canvas

    scenes = [
        ([args.captures / f"live-{i:02d}.png" for i in range(1, 17)], 250),
        ([args.captures / "02-speakers.png"], 4000),
        ([args.captures / "03-ai-notes.png"], 4500),
        ([args.captures / "04-utilities.png"], 3500),
        ([args.captures / "06-answer.png"], 8000),
        ([args.captures / "07-library.png"], 4500),
    ]
    frames: list[Image.Image] = []
    durations: list[int] = []
    posters = []
    for index, (paths, duration) in enumerate(scenes):
        first = compose(paths[0], index)
        posters.append(first)
        if frames:
            previous = frames[-1]
            for blend in (0.25, 0.5, 0.75):
                frames.append(Image.blend(previous, first, blend))
                durations.append(80)
        for path in paths:
            frames.append(compose(path, index))
            durations.append(duration)

    # Share a palette only within the live clip to keep moving faces consistent.
    # Oversample the four-person call so the mostly white UI cannot displace skin
    # tones from the 256 available colors. Always quantize original RGB captures.
    live_samples = Image.new("RGB", (760, 353 * 4))
    for row, index in enumerate((0, 5, 10, 15)):
        live_samples.paste(frames[index].resize((340, 215)), (0, row * 353))
        live_samples.paste(frames[index].crop((740, 404, 1160, 757)), (340, row * 353))
    live_palette = live_samples.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    indexed = []
    for index, frame in enumerate(frames):
        palette = live_palette if index < 16 else frame.quantize(colors=256)
        indexed.append(frame.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG))
    if args.fast:
        timing = {250: 50, 80: 20, 4000: 800, 4500: 900, 3500: 700, 8000: 1600}
        durations = [timing[duration] for duration in durations]
        assert sum(durations) == 6000
    args.output.parent.mkdir(parents=True, exist_ok=True)
    indexed[0].save(
        args.output,
        save_all=True,
        append_images=indexed[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    posters[0].save(args.output.with_suffix(".png"))
    preview = Image.new("RGB", (680 * 2, 430 * 3))
    for index, poster in enumerate(posters):
        preview.paste(poster.resize((680, 430)), ((index % 2) * 680, (index // 2) * 430))
    preview.save(args.captures / "demo-contact-sheet.png")
    print(
        f"{args.output}: {len(frames)} frames, {sum(durations) / 1000:.1f}s, "
        f"{args.output.stat().st_size / 1024 / 1024:.2f} MiB"
    )


if __name__ == "__main__":
    main()

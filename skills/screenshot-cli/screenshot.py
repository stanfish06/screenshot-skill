# /// script
# dependencies = ["mss", "pillow"]
# requires-python = ">=3.12"
# ///
"""Screenshot CLI: capture a monitor or region, then optionally transform the image.

Processing order: capture -> crop -> roll -> rotate -> flip -> resize ->
filters/blur -> brightness/contrast/saturation/sharpness -> grayscale/invert/
autocontrast -> save.

Examples:
    uv run screenshot.py --list-monitors
    uv run screenshot.py --monitor 1 -o shot.png
    uv run screenshot.py --region 100,100,800,600 --format jpeg --quality 80
    uv run screenshot.py --resize 50% --grayscale --filter sharpen
    uv run screenshot.py --crop 0,0,400,300 --rotate 90 --flip-h -o out.webp
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import mss
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

FORMATS = {
    "png": ("PNG", ".png"),
    "jpeg": ("JPEG", ".jpg"),
    "webp": ("WEBP", ".webp"),
    "bmp": ("BMP", ".bmp"),
    "tiff": ("TIFF", ".tiff"),
}
SUFFIX_TO_FORMAT = {
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".webp": "webp",
    ".bmp": "bmp",
    ".tif": "tiff",
    ".tiff": "tiff",
}
FILTERS = {
    "blur": ImageFilter.BLUR,
    "sharpen": ImageFilter.SHARPEN,
    "smooth": ImageFilter.SMOOTH,
    "smooth-more": ImageFilter.SMOOTH_MORE,
    "detail": ImageFilter.DETAIL,
    "contour": ImageFilter.CONTOUR,
    "edge-enhance": ImageFilter.EDGE_ENHANCE,
    "edge-enhance-more": ImageFilter.EDGE_ENHANCE_MORE,
    "emboss": ImageFilter.EMBOSS,
    "find-edges": ImageFilter.FIND_EDGES,
}


def parse_box(value: str) -> tuple[int, int, int, int]:
    """Parse 'X,Y,W,H' into a box tuple."""
    parts = value.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(f"expected X,Y,W,H, got {value!r}")
    try:
        x, y, w, h = (int(p) for p in parts)
    except ValueError:
        raise argparse.ArgumentTypeError(f"non-integer value in {value!r}") from None
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError(f"width and height must be positive, got {value!r}")
    return x, y, w, h


def parse_offset(value: str) -> tuple[int, int]:
    """Parse 'DX,DY' into an offset tuple."""
    parts = value.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"expected DX,DY, got {value!r}")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError(f"non-integer value in {value!r}") from None


def parse_resize(value: str) -> tuple[str, float] | tuple[str, int | None, int | None]:
    """Parse 'WxH', 'Wx', 'xH', or 'N%' into a resize spec."""
    if value.endswith("%"):
        try:
            pct = float(value[:-1])
        except ValueError:
            raise argparse.ArgumentTypeError(f"bad percentage {value!r}") from None
        if pct <= 0:
            raise argparse.ArgumentTypeError("percentage must be positive")
        return ("percent", pct)
    if "x" not in value:
        raise argparse.ArgumentTypeError(f"expected WxH, Wx, xH, or N%, got {value!r}")
    ws, _, hs = value.partition("x")
    try:
        w = int(ws) if ws else None
        h = int(hs) if hs else None
    except ValueError:
        raise argparse.ArgumentTypeError(f"non-integer dimension in {value!r}") from None
    if w is None and h is None:
        raise argparse.ArgumentTypeError("resize needs at least one dimension")
    if (w is not None and w <= 0) or (h is not None and h <= 0):
        raise argparse.ArgumentTypeError("dimensions must be positive")
    return ("dims", w, h)


def resize_target(spec: tuple, size: tuple[int, int]) -> tuple[int, int]:
    """Compute the output size for a resize spec, keeping aspect if one dim is free."""
    w, h = size
    if spec[0] == "percent":
        pct = spec[1] / 100.0
        return max(1, round(w * pct)), max(1, round(h * pct))
    _, tw, th = spec
    if tw is not None and th is not None:
        return tw, th
    if tw is not None:
        return tw, max(1, round(h * tw / w))
    return max(1, round(w * th / h)), th


def build_parser() -> argparse.ArgumentParser:
    doc = __doc__ or ""
    parser = argparse.ArgumentParser(
        description=doc.split("\n\n")[0],
        epilog=doc[doc.index("Processing order") :],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    cap = parser.add_argument_group("capture")
    cap.add_argument(
        "-m", "--monitor", type=int, default=0,
        help="monitor index: 0 = all monitors combined, 1..N = individual (default: 0)",
    )
    cap.add_argument(
        "-r", "--region", type=parse_box, metavar="X,Y,W,H",
        help="capture region in pixels, relative to the selected monitor's origin",
    )
    cap.add_argument(
        "--list-monitors", action="store_true",
        help="print monitor geometry as JSON and exit",
    )

    out = parser.add_argument_group("output")
    out.add_argument(
        "-o", "--output", type=Path,
        help="output path (default: screenshot-<timestamp>.<ext> in the current directory)",
    )
    out.add_argument(
        "-f", "--format", choices=sorted(FORMATS),
        help="image format (default: inferred from output extension, else png)",
    )
    out.add_argument(
        "-q", "--quality", type=int, default=90, metavar="1-100",
        help="quality for jpeg/webp (default: 90; ignored for other formats)",
    )
    out.add_argument(
        "--lossless", action="store_true",
        help="lossless webp compression (webp only)",
    )

    geo = parser.add_argument_group("geometry")
    geo.add_argument(
        "--crop", type=parse_box, metavar="X,Y,W,H",
        help="crop the captured image (image coordinates, applied before other transforms)",
    )
    geo.add_argument(
        "--roll", type=parse_offset, metavar="DX,DY",
        help="cyclically shift the image by DX,DY pixels with wraparound",
    )
    geo.add_argument(
        "--rotate", type=float, metavar="DEG",
        help="rotate counterclockwise by DEG degrees (canvas expands to fit)",
    )
    geo.add_argument("--flip-h", action="store_true", help="mirror left-right")
    geo.add_argument("--flip-v", action="store_true", help="mirror top-bottom")
    geo.add_argument(
        "--resize", type=parse_resize, metavar="SPEC",
        help="resize to 'WxH', 'Wx' or 'xH' (keep aspect), or 'N%%' (Lanczos)",
    )

    enh = parser.add_argument_group("enhancement")
    enh.add_argument(
        "--filter", action="append", choices=sorted(FILTERS), default=[],
        metavar="NAME", dest="filters",
        help=f"apply a filter, repeatable; one of: {', '.join(sorted(FILTERS))}",
    )
    enh.add_argument(
        "--blur", type=float, metavar="RADIUS",
        help="gaussian blur with the given radius",
    )
    enh.add_argument("--brightness", type=float, metavar="F", help="brightness factor (1.0 = unchanged)")
    enh.add_argument("--contrast", type=float, metavar="F", help="contrast factor (1.0 = unchanged)")
    enh.add_argument("--saturation", type=float, metavar="F", help="color saturation factor (1.0 = unchanged)")
    enh.add_argument("--sharpness", type=float, metavar="F", help="sharpness factor (1.0 = unchanged)")
    enh.add_argument("--grayscale", action="store_true", help="convert to grayscale")
    enh.add_argument("--invert", action="store_true", help="invert colors")
    enh.add_argument("--autocontrast", action="store_true", help="stretch contrast to full range")

    return parser


def capture(monitor_index: int, region: tuple[int, int, int, int] | None) -> Image.Image:
    with mss.MSS() as sct:
        if not 0 <= monitor_index < len(sct.monitors):
            valid = f"0-{len(sct.monitors) - 1}"
            sys.exit(f"error: monitor {monitor_index} does not exist (valid: {valid})")
        mon = sct.monitors[monitor_index]
        if region is None:
            grab_area = mon
        else:
            x, y, w, h = region
            if x >= mon["width"] or y >= mon["height"]:
                sys.exit(f"error: region origin ({x},{y}) is outside the {mon['width']}x{mon['height']} monitor")
            clipped_w = min(w, mon["width"] - x)
            clipped_h = min(h, mon["height"] - y)
            if (clipped_w, clipped_h) != (w, h):
                print(f"warning: region clipped to {clipped_w}x{clipped_h} to fit the monitor", file=sys.stderr)
            grab_area = {
                "left": mon["left"] + x,
                "top": mon["top"] + y,
                "width": clipped_w,
                "height": clipped_h,
            }
        shot = sct.grab(grab_area)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def process(img: Image.Image, args: argparse.Namespace) -> Image.Image:
    if args.crop:
        x, y, w, h = args.crop
        if x + w > img.width or y + h > img.height:
            sys.exit(f"error: crop {x},{y},{w},{h} exceeds the {img.width}x{img.height} image")
        img = img.crop((x, y, x + w, y + h))
    if args.roll:
        img = ImageChops.offset(img, *args.roll)
    if args.rotate is not None:
        img = img.rotate(args.rotate, resample=Image.Resampling.BICUBIC, expand=True)
    if args.flip_h:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if args.flip_v:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if args.resize:
        img = img.resize(resize_target(args.resize, img.size), Image.Resampling.LANCZOS)
    for name in args.filters:
        img = img.filter(FILTERS[name])
    if args.blur is not None:
        img = img.filter(ImageFilter.GaussianBlur(args.blur))
    for factor, enhancer in (
        (args.brightness, ImageEnhance.Brightness),
        (args.contrast, ImageEnhance.Contrast),
        (args.saturation, ImageEnhance.Color),
        (args.sharpness, ImageEnhance.Sharpness),
    ):
        if factor is not None:
            img = enhancer(img).enhance(factor)
    if args.grayscale:
        img = img.convert("L")
    if args.invert:
        img = ImageOps.invert(img)
    if args.autocontrast:
        img = ImageOps.autocontrast(img)
    return img


def main() -> int:
    args = build_parser().parse_args()

    if args.list_monitors:
        with mss.MSS() as sct:
            monitors = [
                {"index": i, **{k: mon[k] for k in ("left", "top", "width", "height")}}
                | ({"note": "all monitors combined"} if i == 0 else {})
                for i, mon in enumerate(sct.monitors)
            ]
        print(json.dumps(monitors, indent=2))
        return 0

    if not 1 <= args.quality <= 100:
        sys.exit("error: --quality must be between 1 and 100")

    fmt = args.format
    if fmt is None and args.output is not None:
        suffix = args.output.suffix.lower()
        if suffix and suffix not in SUFFIX_TO_FORMAT:
            sys.exit(f"error: unrecognized extension {suffix!r}; pass --format explicitly")
        fmt = SUFFIX_TO_FORMAT.get(suffix)
    fmt = fmt or "png"
    pil_format, default_suffix = FORMATS[fmt]

    output = args.output
    if output is None:
        output = Path(f"screenshot-{datetime.now():%Y%m%d-%H%M%S}{default_suffix}")

    img = capture(args.monitor, args.region)
    img = process(img, args)

    save_kwargs: dict = {}
    if fmt == "png":
        save_kwargs["optimize"] = True
    elif fmt == "jpeg":
        save_kwargs.update(quality=args.quality, optimize=True)
    elif fmt == "webp":
        save_kwargs["lossless" if args.lossless else "quality"] = True if args.lossless else args.quality

    try:
        img.save(output, format=pil_format, **save_kwargs)
    except OSError as exc:
        sys.exit(f"error: could not save {output}: {exc}")

    size_kb = output.stat().st_size / 1024
    print(f"{output.resolve()} ({img.width}x{img.height}, {pil_format}, {size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# screenshot-skill

An [agent skill](https://agentskills.io) that gives coding agents (Claude
Code, Cursor, Codex, OpenCode, …) a reliable way to capture and post-process
macOS screenshots — without the `screencapture` Retina points-vs-pixels trap
or `sips`'s silent center-cropping.

The skill wraps `screenshot.py`, a self-contained [PEP 723](https://peps.python.org/pep-0723/)
script (mss + Pillow). [`uv`](https://docs.astral.sh/uv/) resolves its
dependencies on first run; there is nothing to install beyond `uv` itself.

## Install

With the [skills CLI](https://github.com/vercel-labs/skills):

```bash
# interactive (pick agents and scope)
npx skills add stanfish06/screenshot-skill

# non-interactive, global, Claude Code
npx skills add stanfish06/screenshot-skill -g -a claude-code -y
```

Or try it without installing:

```bash
npx skills use stanfish06/screenshot-skill@screenshot-cli | claude
```

## What the CLI does

```bash
S=<skill-dir>/screenshot.py
uv run $S --list-monitors                                  # monitor geometry as JSON
uv run $S -m 1 -o shot.png                                 # capture monitor 1
uv run $S -m 1 -r 100,100,800,600 -f jpeg -q 70 -o r.jpg   # region, jpeg quality 70
uv run $S --resize 50% --grayscale --filter sharpen        # post-processing
```

- **Capture**: whole desktop, single monitor, or pixel region (clipped with a
  warning if it overflows the monitor)
- **Output**: png / jpeg / webp / bmp / tiff, quality control, lossless webp,
  format inferred from the output extension
- **Geometry**: crop, roll (wraparound shift), rotate, flip, resize
  (`WxH`, `Wx`, `xH`, or `50%`)
- **Enhancement**: Pillow filters (sharpen, blur, contour, emboss, …),
  gaussian blur, and the full
  [ImageEnhance](https://pillow.readthedocs.io/en/stable/reference/ImageEnhance.html)
  module (brightness, contrast, saturation, sharpness), grayscale, invert,
  autocontrast

Processing order: crop → roll → rotate → flip → resize → filters → enhance.
Success prints `path (WxH, FORMAT, KB)`; bad input exits nonzero with a
one-line error, so agents can branch on the exit code. Run with `--help` for
the full reference.

## Requirements

- [`uv`](https://docs.astral.sh/uv/) and Python ≥ 3.12 (fetched by uv if absent)

## License

[MIT](LICENSE)

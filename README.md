# railshot

**Prepare model railway photos for digital control software** like Rocrail or
iTrain. Background removal, scale-accurate sizing, optional rail underlay —
Python CLI for batch processing.

Designed for **N-scale collections** (1:160) but works for any scale where
your scaling factor (px/mm) is consistent across the roster.

## What it does

- Pixel-accurate left/right cropping (buffer beam to buffer beam)
- Scale-accurate sizing across the entire collection
- Default 80 px output height (Rocrail wiki standard)
- Wheels aligned on a common ground line
- Optional digital rail underlay (consistent across the whole roster)
- Optional auto-rotation (level the underframe)
- Optional auto-perspective (straighten end faces)
- Optional pre-crop (studio ROI before background removal)
- Transparent PNG output

## Setup

```bash
pip install "rembg[cpu]" pillow numpy
pip install opencv-python      # only for --auto-perspective
pip install scipy              # optional, helps with --pre-crop auto
```

Or just install everything from `requirements.txt`:

```bash
pip install -r requirements.txt
```

On first run, `rembg` automatically downloads its ONNX model (~170 MB for
`isnet-general-use`, cached in `~/.u2net/`).

## Web UI (optional)

A minimal Streamlit-based web UI is included as `app.py`. Run it locally:

```bash
streamlit run app.py
```

Your browser opens automatically at `http://localhost:8501`. Upload a photo,
adjust the sliders in the sidebar, click **Process**, download the result.

The web UI is also designed to deploy directly to **Hugging Face Spaces**
(see `README_huggingface.md` and the deployment section at the bottom of
this file).

## Quickstart (CLI)

```powershell
python railshot.py coach.jpg -o coach.png `
    --mode scale --px-per-mm 2.0 --length-mm 165 --auto-rotate --rail
```

This produces a transparent PNG (~330×80 px when rail is enabled) with the
coach sitting bottom-aligned on a rail.

## Defining the scale

The most important parameter is **`--px-per-mm`**: how many pixels per
millimeter of model length. Set this **once** for your entire collection and
use the same value for every image. Otherwise vehicles won't be in the same
scale anymore.

### Method A: Define via height (recommended for locos with pantograph)

In this method, you use the tallest locomotive in your collection as the
anchor for the 80 px Rocrail height. This guarantees no loco gets too tall
and the pantograph space is used optimally.

1. **Measure the tallest loco** (rail head to roof with pantograph
   retracted). E.g. Re 460 ≈ 30 mm.
2. **Choose a target height:** say 60 px (leaves 20 px reserve at top for
   raised pantograph).
3. **Calculate:** `60 px / 30 mm = 2.0 px/mm`

This gives you for the rest of the collection:

| Vehicle              | Length (mm) | Output width (at 2.0 px/mm) |
|----------------------|-------------|------------------------------|
| Astoro power car     | 172         | 344 px                       |
| EW IV                | 165         | 330 px                       |
| RAe TEE control car  | 158         | 316 px                       |
| Re 460               | 116         | 232 px                       |
| Eem 923 (Tigerli)    |  58         | 116 px                       |

### Method B: Define via length

1. **Measure the main coach** (e.g. EW IV ≈ 165 mm).
2. **Choose a target width** (e.g. 250 px).
3. **Calculate:** `250 px / 165 mm = 1.515 px/mm`

| px-per-mm | EW IV (165 mm) | Re 460 (116 mm) | Tigerli (58 mm) |
|-----------|----------------|-----------------|------------------|
| 1.515     | 250 px         | 176 px          | 88 px            |
| 1.82      | 300 px         | 211 px          | 105 px           |
| 2.0       | 330 px         | 232 px          | 116 px           |

## Studio setup for clean results

1. **Clear background** around the model — no other vehicles, no dark walls,
   no monitors in the field of view.
2. **Frame-filling** — model should fill ~90% of the photo width, so rembg
   has enough pixels to recognize the subject.
3. **Frontal view** — camera parallel to the model. Then `--auto-perspective`
   isn't needed.
4. **Constant lighting** — avoids harsh shadows under the model.
5. **Plain underlay** — don't photograph a real rail underneath! The script
   adds a digital rail later (see next section).

## Digital rail underlay

Instead of photographing a real rail (which can confuse rembg), the script
overlays a **consistent digital rail** under each model. Advantages:

- Rail is **pixel-perfect identical** on all images
- rembg doesn't have to deal with rail details
- Rail is **cropped to model width** (not scaled) — sleeper spacing stays
  constant
- When models are placed adjacent in Rocrail, the rail forms a continuous line

### Usage

```powershell
python railshot.py coach.jpg -o coach.png `
    --mode scale --px-per-mm 2.0 --length-mm 165 --auto-rotate `
    --rail
```

The script expects a file named `rail.png` in the same folder as the script.
A default version is included in this repository (4 px high, 800 px wide).

### Customizing the rail

If the bundled rail doesn't suit your taste, create your own PNG with these
properties:

- **Height:** 3–8 px (subtle — too tall and it dominates the image)
- **Width:** any, at least as wide as your longest model
- **Format:** PNG with alpha channel
- **Content:** sleepers, rail head, ballast — your choice

Use it via `--rail-image my_rail.png`.

### Canvas behavior

- **Default (no flag):** the rail is **overlaid** on the bottom edge of the
  wheels. The wheels visually rest on the rail (this is the realistic look).
- **`--rail-extend`:** the canvas grows downwards by the rail height. The
  rail hangs **below** the wheels.

## Pre-crop: eliminate noise outside the studio

If your photo contains things outside the studio area (other locos, cables,
wall edges), a pre-crop **before** rembg helps a lot:

```powershell
python railshot.py coach.jpg -o coach.png `
    --mode scale --px-per-mm 2.0 --length-mm 165 `
    --pre-crop "170,80,1965,820"
```

Format: `"X1,Y1,X2,Y2"` in pixels of the original photo. Read coordinates in
Paint, IrfanView, GIMP, or any image viewer that shows the cursor position.

For a fixed studio setup, you measure the ROI **once** and reuse it for all
photos.

Auto variant (finds the brightest region):

```powershell
--pre-crop "auto"
--pre-crop "auto 180"   # custom brightness threshold
```

Works when the studio is clearly brighter than its surroundings.

## Auto-rotation and auto-perspective

```powershell
# Auto-level the bottom of the model (safe)
--auto-rotate

# Plus straighten end faces (experimental, requires opencv)
--auto-rotate --auto-perspective
```

Both corrections have built-in safety limits: corrections >5° rotation or
>30 px perspective are ignored, leaving the original unchanged. So
auto-correction can only help or do nothing — never break things.

The terminal output tells you what happened:

```
OK  ew4.jpg  ->  ew4.png  (330 x 80 px)  [rot -0.64°, bbox 1478x233 (aspect 6.34)]
```

## Batch processing with lengths.json

Create a `lengths.json` mapping filenames to model lengths:

```json
{
  "ew4_a_184-7": 165,
  "ew4_b_xxx": 165,
  "re460_001": 116,
  "tigerli": 58,
  "shimms_454": 103
}
```

Keys = filename **without** extension (or with — both work).
Values = model length in mm.

Run:

```powershell
python railshot.py ./photos -o ./out `
    --mode scale --px-per-mm 2.0 --lengths lengths.json `
    --pre-crop "170,80,1965,820" --auto-rotate --rail
```

Missing entries fall back to `--length-mm` if provided, otherwise raise an
error.

## PowerShell shortcuts for daily use

Add to your PowerShell profile (`notepad $PROFILE`):

```powershell
function rrp-one {
    param(
        [Parameter(Mandatory)][string]$In,
        [Parameter(Mandatory)][string]$Out,
        [Parameter(Mandatory)][int]$LengthMm
    )
    python railshot.py $In -o $Out `
        --mode scale --px-per-mm 2.0 --length-mm $LengthMm `
        --pre-crop "170,80,1965,820" --auto-rotate --rail
}

function rrp-batch {
    param(
        [string]$In = "./photos",
        [string]$Out = "./out"
    )
    python railshot.py $In -o $Out `
        --mode scale --px-per-mm 2.0 --lengths lengths.json `
        --pre-crop "170,80,1965,820" --auto-rotate --rail
}
```

Then just:

```powershell
rrp-one -In coach.jpg -Out coach.png -LengthMm 165
rrp-batch
```

## Debug mode

If something goes wrong, dump all intermediate steps:

```powershell
python railshot.py coach.jpg -o coach.png `
    [...other options...] `
    --debug-dir ./debug --verbose
```

Creates `./debug/coach/` with numbered PNGs:

| File                          | Content                          |
|-------------------------------|----------------------------------|
| `00_input.png`                | Original photo                   |
| `01_pre_crop.png`             | After pre-crop                   |
| `02_rembg.png`                | After background removal         |
| `03_edge_clean.png`           | Halo cleanup                     |
| `04_rotated_+0.64deg.png`     | After auto-rotation              |
| `05_perspective.png`          | After auto-perspective           |
| `06_cropped_NNNNxNNN.png`     | After bbox crop (with size!)     |
| `07_scaled_330x52.png`        | After scaling                    |
| `08_with_rail_330x52.png`     | After rail overlay               |
| `09_final_330x80.png`         | Final on canvas                  |

This shows exactly where in the pipeline things go wrong.

## Verifying the output

Some image viewers don't display transparency correctly (showing it as
black or white). To verify:

```powershell
python -c "from PIL import Image; img = Image.open('out.png'); print('Mode:', img.mode)"
```

Expected: `Mode: RGBA`. If `Mode: RGB`, the alpha channel is missing.

Visually: PyCharm shows transparency as a **gray checkerboard pattern**.
GIMP, Paint.NET, and modern browsers all do this correctly.

## Rocrail image specs

Per the Rocrail wiki:
- **Height:** 80 px (standard)
- **Max file size:** 50 KB
- **Format:** PNG with transparent background

For typical N-scale photos at 200–400 px width × 80 px height, output PNGs
land at 15–35 KB — well below the 50 KB limit.

**Important on train assembly:** in Rocrail you define each wagon with its
model length in mm. Rocrail then composes trains at runtime using these
lengths. Your individual wagon image doesn't need to contain a full train —
that's done by Rocrail dynamically.

## All options

| Flag                    | Default             | Purpose |
|-------------------------|---------------------|---------|
| `--mode`                | `scale`             | `height` or `scale` |
| `--canvas-height`       | `80`                | Output height in px (Rocrail standard) |
| `--max-width`           | —                   | Hard cap on width (height mode) |
| `--px-per-mm`           | —                   | **Scale: pixels per mm of model length** |
| `--length-mm`           | —                   | Length of current vehicle in mm |
| `--lengths`             | —                   | JSON with per-file lengths |
| `--pre-crop`            | —                   | ROI before rembg (`X1,Y1,X2,Y2` or `auto`) |
| `--pre-crop-padding`    | `20`                | Safety padding around ROI |
| `--auto-rotate`         | off                 | Level the bottom edge |
| `--min-rotation-deg`    | `0.2`               | Lower threshold (no rotation below) |
| `--max-rotation-deg`    | `5.0`               | Upper threshold (probably error) |
| `--auto-perspective`    | off                 | Straighten end faces |
| `--min-perspective-px`  | `1.5`               | Lower threshold |
| `--max-perspective-px`  | `30`                | Upper threshold |
| `--h-alpha-threshold`   | `128`               | Strict horizontal threshold |
| `--v-alpha-threshold`   | `32`                | Lenient vertical threshold |
| `--h-min-column-pixels` | `3`                 | Filter against cutout artefacts |
| `--edge-clean-threshold`| `64`                | Halo cleanup threshold |
| `--pad-left`            | `0`                 | Padding left (= 0 for Rocrail!) |
| `--pad-right`           | `0`                 | Padding right (= 0 for Rocrail!) |
| `--pad-top`             | `1`                 | Padding top |
| `--pad-bottom`          | `0`                 | Padding bottom |
| **`--rail`**            | off                 | **Place rail under model** |
| **`--rail-image`**      | `rail.png`          | **Path to rail template** |
| **`--rail-extend`**     | off                 | **Extend canvas instead of overlay** |
| `--align`               | `bottom`            | Vertical alignment in canvas |
| `--model`               | `isnet-general-use` | rembg model |
| `--debug-dir`           | —                   | Save intermediate steps as PNG |
| `-v` / `--verbose`      | off                 | More verbose error output |

## rembg model recommendations

- `isnet-general-use` — best all-rounder, slightly slower
- `u2net` — robust classic
- `u2netp` — fast and small, slightly less accurate

For shiny locos or fine pantographs, `isnet-general-use` typically gives
the cleanest cutout.

## Troubleshooting

### No transparent background (everything black)

Known rembg quirk. The script has a workaround built in that activates
when needed. If still black:

```bash
pip install "rembg[cpu]" --upgrade
```

### Model gets cut off left/right

```powershell
--h-alpha-threshold 96       # less strict (default 128)
--pad-left 1 --pad-right 1   # 1 px safety
```

### Pantograph/antenna missing at top

```powershell
--v-alpha-threshold 16    # more lenient (default 32)
--pad-top 3
```

### Halo around model

```powershell
--edge-clean-threshold 96    # stricter (default 64)
```

### Gaps between coaches in Rocrail

```powershell
--edge-clean-threshold 96 --h-alpha-threshold 160    # both stricter
```

### Model too small/large in output

Adjust `--px-per-mm`. **But:** if you change this mid-collection, **all**
models must be regenerated with the new value — otherwise the shared
scale breaks.

### Rail too prominent

Replace the bundled `rail.png` with a slimmer custom version. Make it
thinner (3 px instead of 4 px), use muted colors, less sleeper contrast.

### Rail file not found

```
FileNotFoundError: Rail file not found: ...
```

`rail.png` must be in the same folder as `railshot.py` — or pass an
explicit path with `--rail-image C:\path\to\my_rail.png`.

### Auto-perspective makes the image worse

Drop `--auto-perspective`. End-face detection is unreliable on rounded
transitions — better to shoot straight mechanically.

### Pre-crop cuts off the model

Re-measure ROI coordinates in Paint/GIMP. Buffer beams must be **inside**
the ROI. Tip: `--pre-crop-padding 30` gives extra safety.

## Recommended workflow for a complete collection

1. **Build a fixed studio setup** and don't change it.
2. **Measure the tallest loco** → determines `--px-per-mm`.
3. **Measure the pre-crop ROI** — once, reused for everything.
4. **Process a test image**, view in Rocrail, adjust scale if needed.
5. **Customize rail.png** if the default doesn't fit.
6. **Set up PowerShell shortcuts** with your fixed parameters.
7. **Maintain `lengths.json`** for all models.
8. **Run the batch**, copy results to your Rocrail image folder.

## What this tool does NOT do

- Detect multiple vehicles in one photo — please, one model per photo.
- Mirror/flip for the "other side" — shoot both sides or use mirror options
  in your control software.
- Color or brightness correction — best done at capture time (white balance,
  even lighting).
- Compose full trains — that's done by Rocrail/iTrain at runtime using your
  per-model length definitions.

## Deploy to Hugging Face Spaces

The web UI can be deployed to [Hugging Face Spaces](https://huggingface.co/spaces)
for free, giving you (or anyone) a public URL to use the tool without any
local installation.

**Steps:**

1. Create a free account at [huggingface.co](https://huggingface.co)
2. Click "New Space", choose **Streamlit** as SDK, name it `railshot` (or whatever you prefer), free CPU tier is sufficient
3. Clone your new Space repository:
   ```bash
   git clone https://huggingface.co/spaces/YOUR_USERNAME/railshot
   cd railshot
   ```
4. Copy these files from this repo into the cloned Space repo:
   - `app.py`
   - `railshot.py`
   - `rail.png`
   - `requirements.txt`
   - **Rename** `README_huggingface.md` to `README.md` (it has the YAML
     config header that HF needs)
5. Push to HF:
   ```bash
   git add .
   git commit -m "Initial deploy"
   git push
   ```
6. Wait ~3-5 minutes for the build, then your Space is live at
   `https://huggingface.co/spaces/YOUR_USERNAME/railshot`

**Note:** the first model download (~170 MB rembg model) happens on the
first user request and takes ~30 seconds. After that it's cached for the
lifetime of the Space.

## Acknowledgements

This tool stands on the shoulders of excellent open-source projects:

- **[rembg](https://github.com/danielgatis/rembg)** (MIT) — AI-based
  background removal using ONNX runtime
- **[Pillow](https://python-pillow.org/)** (HPND) — image manipulation
- **[NumPy](https://numpy.org/)** (BSD-3) — numerical operations
- **[Streamlit](https://streamlit.io/)** (Apache 2.0) — web UI framework
- **[OpenCV](https://opencv.org/)** (Apache 2.0, optional) — for
  perspective correction

## License

MIT — see [LICENSE](LICENSE).

The dependencies above retain their respective licenses and are not
redistributed as part of this repository — they are installed via pip.

## Contributing

Issues, ideas, and pull requests welcome. The tool was developed for
SBB-themed N-scale photography but should work for any scale and railway
network.

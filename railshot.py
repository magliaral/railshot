#!/usr/bin/env python3
"""
railshot — Prepare model railway photos for digital control software.

Default output: 80 px height (Rocrail wiki standard).

Features:
  - Background removal (rembg, AI-based)
  - Optional: auto-rotation (level the underframe)
  - Optional: auto-perspective (straighten end faces)
  - Crop to subject (buffer-accurate left/right)
  - Scale-accurate sizing (consistent across the whole roster)
  - Embed on Rocrail standard canvas (default 80 px high)
  - Output as PNG with transparency
  - Optional: digital rail underlay (consistent over the whole roster)

Defining the scale:

  --px-per-mm = Pixels per millimeter of model length.
                Set this ONCE per collection, then keep it constant.
                Example: 2.0 → a 165 mm long coach becomes 330 px wide.

  --length-mm = Length of the specific vehicle in mm (measure on the
                model with calipers, buffer beam to buffer beam).

How to find your px-per-mm value:
  Decide the desired width of your main coach (e.g. 250 px), divide by
  its measured model length (e.g. 165 mm):
      250 px / 165 mm = 1.515 px/mm
  Use this value for the entire collection.

Recommended workflow:
  1. Build a fixed studio setup (camera angle, distance, lighting).
  2. Process the first test photo without --auto-* flags. Check in Rocrail.
  3. If image is slightly tilted, try --auto-rotate.
  4. If end faces are skewed, try --auto-perspective (use cautiously!).
  5. Process the whole collection in batch with the same settings.

Examples (PowerShell with backtick `, otherwise one line):
  # Single photo: coach is 165 mm long, scale 1.515 px/mm
  python railshot.py coach.jpg -o coach.png `
      --mode scale --px-per-mm 1.515 --length-mm 165 --auto-rotate

  # Different locomotive at 116 mm in the same scale
  python railshot.py re460.jpg -o re460.png `
      --mode scale --px-per-mm 1.515 --length-mm 116 --auto-rotate
  # → Output is 176 px wide (= 116 × 1.515)

  # Batch with lengths JSON
  python railshot.py ./photos -o ./out `
      --mode scale --px-per-mm 1.515 --lengths lengths.json --auto-rotate

Note for N scale (1:160): model length is roughly prototype/160.
  EW IV (26,400 mm prototype) → ~165 mm model
  Re 460 (18,500 mm)          → ~116 mm model
  Eem 923 (9,200 mm)          →  ~58 mm model
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image
import numpy as np

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

# Rocrail wiki standard
ROCRAIL_DEFAULT_HEIGHT = 80


# ---------------------------------------------------------------------------
# rembg + alpha cleanup
# ---------------------------------------------------------------------------

def remove_background(img: Image.Image, session) -> Image.Image:
    """
    Remove background, return RGBA.

    Robust variant: on some systems rembg returns a black background with
    alpha=255 instead of a transparent one (see rembg issue #564). If that
    happens, we correct it afterwards using the mask that rembg computes
    internally via post_process.
    """
    from rembg import remove
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    out = remove(img, session=session)

    # Force RGBA mode
    if out.mode != "RGBA":
        out = out.convert("RGBA")

    # Sanity check: does the result actually have transparency?
    # If not, rembg fell into the "black background" mode
    arr = np.array(out)
    alpha = arr[..., 3]
    has_transparency = (alpha < 255).any()

    if not has_transparency:
        # rembg burned in a black background instead of making it transparent.
        # Workaround: ask rembg explicitly for a mask and build RGBA ourselves.
        try:
            mask = remove(img, session=session, only_mask=True)
            if mask.mode != "L":
                mask = mask.convert("L")
            mask_arr = np.array(mask)
            # Original image + new alpha mask
            orig_arr = np.array(img)
            if orig_arr.shape[2] == 3:
                # Append alpha channel
                rgba = np.dstack([orig_arr, mask_arr])
            else:
                rgba = orig_arr.copy()
                rgba[..., 3] = mask_arr
            out = Image.fromarray(rgba.astype(np.uint8), "RGBA")
        except Exception:
            # Last resort: mark black pixels as transparent
            arr = np.array(out)
            r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
            black_mask = (r < 5) & (g < 5) & (b < 5)
            arr[black_mask, 3] = 0
            out = Image.fromarray(arr, "RGBA")

    return out


def clean_alpha_edges(img: Image.Image, threshold: int) -> Image.Image:
    """Pixels with alpha below `threshold` become fully transparent."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    arr = np.array(img)
    mask = arr[..., 3] < threshold
    arr[mask, 3] = 0
    return Image.fromarray(arr, "RGBA")


# ---------------------------------------------------------------------------
# Geometrie-Korrektur
# ---------------------------------------------------------------------------

def _bottom_edge_points(alpha: np.ndarray, threshold: int = 128,
                        sample_step: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """
    Returns (xs, ys) of the bottommost opaque pixel per column.
    For each column: the y of the bottommost pixel with alpha >= threshold.
    Columns without subject are skipped.
    """
    h, w = alpha.shape
    mask = alpha >= threshold
    xs: list[int] = []
    ys: list[int] = []
    for x in range(0, w, sample_step):
        col = mask[:, x]
        if not col.any():
            continue
        # Last True index
        y = h - 1 - int(np.argmax(col[::-1]))
        xs.append(x)
        ys.append(y)
    return np.array(xs), np.array(ys)


def _trimmed_linregress(xs: np.ndarray, ys: np.ndarray,
                        trim_pct: float = 0.10) -> float | None:
    """
    Robust linear regression: trims the `trim_pct` most extreme residuals.
    Returns slope (radians from horizontal), or None if not enough points.
    """
    if len(xs) < 20:
        return None
    # Initial fit
    slope, intercept = np.polyfit(xs, ys, 1)
    residuals = np.abs(ys - (slope * xs + intercept))
    cutoff = np.quantile(residuals, 1.0 - trim_pct)
    keep = residuals <= cutoff
    if keep.sum() < 10:
        return None
    slope, _ = np.polyfit(xs[keep], ys[keep], 1)
    return float(np.arctan(slope))


def auto_rotate(img: Image.Image, max_correction_deg: float = 5.0,
                min_correction_deg: float = 0.2) -> tuple[Image.Image, float]:
    """
    Detect the tilt of the lower coach edge and correct it.

    - max_correction_deg: anything above this is treated as a detection
      error and no rotation is applied. Genuinely tilted studio photos
      are typically <2°.
    - min_correction_deg: anything below this is treated as noise, no
      rotation applied.

    Returns (rotated image, angle in degrees). Angle = 0 if nothing was done.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    alpha = np.array(img)[..., 3]
    xs, ys = _bottom_edge_points(alpha, threshold=128, sample_step=2)
    angle_rad = _trimmed_linregress(xs, ys, trim_pct=0.15)
    if angle_rad is None:
        return img, 0.0

    angle_deg = float(np.degrees(angle_rad))
    if abs(angle_deg) < min_correction_deg:
        return img, 0.0
    if abs(angle_deg) > max_correction_deg:
        # Probably a detection error (e.g. photo taken at a heavy angle)
        return img, 0.0

    # PIL rotates around the image center. We want the bottom line horizontal.
    # Since we rotate around the center, this is equivalent — the line ends
    # up horizontal relative to the new image frame.
    rotated = img.rotate(angle_deg, resample=Image.BICUBIC,
                         expand=True, fillcolor=(0, 0, 0, 0))
    return rotated, angle_deg


def _find_left_right_extreme_columns(alpha: np.ndarray,
                                     threshold: int = 128,
                                     min_column_pixels: int = 10
                                     ) -> tuple[int, int] | None:
    """Find the x-position of the first and last "real" column."""
    h, w = alpha.shape
    mask = alpha >= threshold
    col_counts = mask.sum(axis=0)
    valid = col_counts >= min_column_pixels
    if not valid.any():
        return None
    left = int(np.argmax(valid))
    right = int(w - np.argmax(valid[::-1]) - 1)
    return left, right


def _vertical_edge_skew(alpha: np.ndarray, side: str,
                        threshold: int = 128, band_width: int = 30
                        ) -> float | None:
    """
    Measures the skew of the end face (left or right).

    We take a narrow band along the outer edge of the coach and find,
    per row, the outermost opaque column. A linear regression through
    (y, x_outer) gives us the skew of the end face.

    Returns: slope dx/dy. 0 = perfectly vertical. Positive values = end
    face leans right with increasing y (bottom further out than roof).
    """
    h, w = alpha.shape
    mask = alpha >= threshold

    # First find horizontal bounding box
    bbox = _find_left_right_extreme_columns(alpha, threshold)
    if bbox is None:
        return None
    left, right = bbox

    if side == "left":
        # Per row: first opaque column
        x_outer = []
        ys = []
        for y in range(h):
            row = mask[y, left:left + band_width]
            if row.any():
                x_outer.append(left + int(np.argmax(row)))
                ys.append(y)
    elif side == "right":
        x_outer = []
        ys = []
        for y in range(h):
            row = mask[y, max(0, right - band_width):right + 1]
            if row.any():
                # Last True index in row
                x = right - int(np.argmax(row[::-1]))
                x_outer.append(x)
                ys.append(y)
    else:
        return None

    if len(ys) < 20:
        return None

    ys = np.array(ys, dtype=float)
    x_outer = np.array(x_outer, dtype=float)

    # Trim: bottom 15% (wheels/bogies extend sideways and would
    # distort the end face line)
    cutoff_y = np.quantile(ys, 0.85)
    keep = ys < cutoff_y
    if keep.sum() < 10:
        return None
    ys = ys[keep]
    x_outer = x_outer[keep]

    # Robust: trim outliers
    slope, intercept = np.polyfit(ys, x_outer, 1)
    residuals = np.abs(x_outer - (slope * ys + intercept))
    cutoff = np.quantile(residuals, 0.85)
    keep = residuals <= cutoff
    if keep.sum() < 10:
        return None
    slope, _ = np.polyfit(ys[keep], x_outer[keep], 1)
    return float(slope)  # dx/dy


def auto_perspective(img: Image.Image,
                     max_correction_px: float = 30.0,
                     min_correction_px: float = 1.5
                     ) -> tuple[Image.Image, tuple[float, float]]:
    """
    Correct horizontal trapezoid distortion of the end faces.

    Measures the skew of the left and right end faces (in dx/dy).
    Applies a homography that compensates for these skews.

    - max_correction_px: maximum correction in px over the full image
      height. Anything above is treated as a detection error.
    - min_correction_px: anything below is treated as noise, no correction.

    Requires opencv. If not installed: no correction, warning printed.

    Returns (corrected image, (left_slope, right_slope)).
    """
    try:
        import cv2
    except ImportError:
        print("  Note: --auto-perspective requires opencv-python "
              "(pip install opencv-python). Skipped.", file=sys.stderr)
        return img, (0.0, 0.0)

    if img.mode != "RGBA":
        img = img.convert("RGBA")
    arr = np.array(img)
    alpha = arr[..., 3]
    h, w = alpha.shape

    left_slope = _vertical_edge_skew(alpha, "left")
    right_slope = _vertical_edge_skew(alpha, "right")

    if left_slope is None or right_slope is None:
        return img, (0.0, 0.0)

    # Correction in pixels over the full height
    left_dx = left_slope * h
    right_dx = right_slope * h

    # Plausibility check
    if max(abs(left_dx), abs(right_dx)) > max_correction_px:
        return img, (0.0, 0.0)
    if max(abs(left_dx), abs(right_dx)) < min_correction_px:
        return img, (0.0, 0.0)

    # Subject bounding box
    bbox = _find_left_right_extreme_columns(alpha)
    if bbox is None:
        return img, (0.0, 0.0)
    left_x, right_x = bbox

    # Source points (the current trapezoid of the end faces):
    # We take the corner points top-left, top-right, bottom-left, bottom-right
    # along the end-face lines.
    src = np.array([
        [left_x + left_slope * 0,            0],   # top-left
        [right_x + right_slope * 0,          0],   # top-right
        [left_x + left_slope * (h - 1),  h - 1],   # bottom-left
        [right_x + right_slope * (h - 1), h - 1],  # bottom-right
    ], dtype=np.float32)

    # Target points: straight end faces at midpoint position left/right
    dst_left = (src[0, 0] + src[2, 0]) / 2.0
    dst_right = (src[1, 0] + src[3, 0]) / 2.0
    dst = np.array([
        [dst_left,  0],
        [dst_right, 0],
        [dst_left,  h - 1],
        [dst_right, h - 1],
    ], dtype=np.float32)

    M = cv2.getPerspectiveTransform(src, dst)
    bgra = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA)
    warped = cv2.warpPerspective(
        bgra, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    rgba = cv2.cvtColor(warped, cv2.COLOR_BGRA2RGBA)
    return Image.fromarray(rgba, "RGBA"), (left_slope, right_slope)


# ---------------------------------------------------------------------------
# Crop, scaling, canvas
# ---------------------------------------------------------------------------

def crop_to_subject(img: Image.Image,
                    h_alpha_threshold: int = 128,
                    v_alpha_threshold: int = 32,
                    h_min_column_pixels: int = 3,
                    pad_top: int = 1,
                    pad_bottom: int = 0,
                    pad_left: int = 0,
                    pad_right: int = 0) -> Image.Image:
    """Tightly crop to the subject (buffer-accurate left/right)."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    arr = np.array(img)
    alpha = arr[..., 3]
    h, w = alpha.shape

    v_mask = alpha > v_alpha_threshold
    v_rows = np.any(v_mask, axis=1)
    if not v_rows.any():
        return img
    top = int(np.argmax(v_rows))
    bottom = int(h - np.argmax(v_rows[::-1]))

    h_mask = alpha >= h_alpha_threshold
    col_counts = h_mask.sum(axis=0)
    valid_cols = col_counts >= h_min_column_pixels
    if not valid_cols.any():
        valid_cols = col_counts > 0
        if not valid_cols.any():
            return img
    left = int(np.argmax(valid_cols))
    right = int(w - np.argmax(valid_cols[::-1]))

    left = max(0, left - pad_left)
    right = min(w, right + pad_right)
    top = max(0, top - pad_top)
    bottom = min(h, bottom + pad_bottom)

    return img.crop((left, top, right, bottom))


def resize_scale_mode(img: Image.Image, length_mm: float,
                      px_per_mm: float) -> Image.Image:
    """
    Scale-accurate resize.
    New width = length_mm * px_per_mm
    Height follows proportionally (same scale horizontal and vertical).
    """
    new_w = max(1, round(length_mm * px_per_mm))
    w, h = img.size
    scale = new_w / w
    new_h = max(1, round(h * scale))
    return img.resize((new_w, new_h), Image.LANCZOS)


def add_rail(img: Image.Image, rail_path: Path,
             extend_canvas: bool = True) -> Image.Image:
    """
    Place a rail underneath the coach.

    The rail is cropped or tiled to coach width, not scaled — so that
    sleeper spacing stays constant across the whole roster.

    Args:
        img: the cut-out and scaled coach image (RGBA)
        rail_path: path to the rail template (PNG with alpha)
        extend_canvas: if True, the canvas is extended downwards by the
            rail height. If False, the rail is overlaid on the bottommost
            row(s) of the coach.

    Returns:
        Image with rail as RGBA.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    rail = Image.open(rail_path)
    if rail.mode != "RGBA":
        rail = rail.convert("RGBA")

    rail_w, rail_h = rail.size
    img_w, img_h = img.size

    # Fit rail to coach width — tile or crop
    if rail_w >= img_w:
        # Center crop
        x_off = (rail_w - img_w) // 2
        rail_strip = rail.crop((x_off, 0, x_off + img_w, rail_h))
    else:
        # Tile up to coach width
        rail_strip = Image.new("RGBA", (img_w, rail_h), (0, 0, 0, 0))
        x = 0
        while x < img_w:
            paste_w = min(rail_w, img_w - x)
            rail_strip.paste(rail.crop((0, 0, paste_w, rail_h)), (x, 0))
            x += rail_w

    if extend_canvas:
        # Extend canvas downwards by rail height
        new_h = img_h + rail_h
        canvas = Image.new("RGBA", (img_w, new_h), (0, 0, 0, 0))
        canvas.paste(img, (0, 0), img)
        canvas.paste(rail_strip, (0, img_h), rail_strip)
        return canvas
    else:
        # Overlay rail on the bottom rail_h pixels of the coach
        canvas = img.copy()
        # Paste with mask from rail alpha
        canvas.paste(rail_strip, (0, img_h - rail_h), rail_strip)
        return canvas


def fit_to_canvas(img: Image.Image, canvas_height: int,
                  align: str = "bottom"
                  ) -> tuple[Image.Image, bool]:
    """Embed image on canvas with fixed height. Coach at bottom, pantograph at top."""
    w, h = img.size
    overflow = False

    if h > canvas_height:
        overflow = True
        img = img.crop((0, h - canvas_height, w, h))
        h = canvas_height

    if h == canvas_height:
        return img, overflow

    canvas = Image.new("RGBA", (w, canvas_height), (0, 0, 0, 0))
    if align == "bottom":
        y = canvas_height - h
    elif align == "center":
        y = (canvas_height - h) // 2
    else:
        y = 0
    canvas.paste(img, (0, y), img)
    return canvas, overflow


# ---------------------------------------------------------------------------
# Hauptverarbeitung
# ---------------------------------------------------------------------------

def apply_pre_crop(img: Image.Image, spec: str,
                   padding: int = 20) -> Image.Image:
    """
    Crop the image to a ROI BEFORE rembg runs.

    spec can be:
      - "X1,Y1,X2,Y2" — manual coordinates (pixels in original)
      - "auto" — find the brightest connected region (= studio jig)
      - "auto N" — like auto, but with brightness threshold N (0-255)

    A safety padding is added all around.
    """
    w, h = img.size

    if spec.lower().startswith("auto"):
        # Heuristic: find largest bright connected region
        threshold = 200
        parts = spec.split()
        if len(parts) > 1:
            try:
                threshold = int(parts[1])
            except ValueError:
                pass
        return _auto_pre_crop(img, brightness_threshold=threshold,
                              padding=padding)

    # Manual coordinates
    try:
        coords = [int(x.strip()) for x in spec.split(",")]
        if len(coords) != 4:
            raise ValueError
    except ValueError:
        raise ValueError(
            f"--pre-crop must be 'X1,Y1,X2,Y2' or 'auto', "
            f"got: '{spec}'")

    x1, y1, x2, y2 = coords
    x1 = max(0, x1 - padding)
    y1 = max(0, y1 - padding)
    x2 = min(w, x2 + padding)
    y2 = min(h, y2 + padding)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"--pre-crop ROI is empty: ({x1},{y1})-({x2},{y2})")
    return img.crop((x1, y1, x2, y2))


def _auto_pre_crop(img: Image.Image, brightness_threshold: int = 200,
                   padding: int = 20) -> Image.Image:
    """
    Find the bbox of the studio jig (bright pixels) and crop to it.

    Strategy: the bbox of ALL bright pixels typically encompasses the
    jig area including the dark coach within (since the jig surrounds
    the coach top/bottom/left/right with bright material).

    If the studio jig is not clearly bright-dominant, falls back to
    the original image.
    """
    arr = np.array(img.convert("RGB"))
    brightness = arr.mean(axis=2)
    bright_mask = brightness >= brightness_threshold

    if not bright_mask.any():
        return img

    # Heuristic: only useful if a significant portion is bright (jig)
    bright_fraction = bright_mask.sum() / bright_mask.size
    if bright_fraction < 0.05:
        # Practically nothing bright, pre-crop wouldn't help
        return img

    # Bbox of all bright pixels — encompasses the jig
    rows = np.any(bright_mask, axis=1)
    cols = np.any(bright_mask, axis=0)
    if not rows.any() or not cols.any():
        return img
    y1 = int(np.argmax(rows))
    y2 = int(len(rows) - np.argmax(rows[::-1]))
    x1 = int(np.argmax(cols))
    x2 = int(len(cols) - np.argmax(cols[::-1]))

    w, h = img.size
    x1 = max(0, x1 - padding)
    y1 = max(0, y1 - padding)
    x2 = min(w, x2 + padding)
    y2 = min(h, y2 + padding)
    return img.crop((x1, y1, x2, y2))


def process_one(in_path: Path, out_path: Path, session, args,
                length_mm: float | None) -> dict:
    """Process a single image. Returns diagnostic info."""
    info = {"size": (0, 0), "overflow": False,
            "rotation_deg": 0.0,
            "perspective_slopes": (0.0, 0.0),
            "after_crop_size": (0, 0),
            "after_crop_aspect": 0.0}

    debug_dir: Path | None = None
    if args.debug_dir:
        debug_dir = Path(args.debug_dir) / in_path.stem
        debug_dir.mkdir(parents=True, exist_ok=True)

    def _save_debug(step: int, name: str, image: Image.Image) -> None:
        if debug_dir:
            image.save(debug_dir / f"{step:02d}_{name}.png")

    img = Image.open(in_path)
    _save_debug(0, "input", img.convert("RGBA"))

    # Pre-crop: cut to ROI BEFORE rembg, so rembg isn't distracted by
    # noise sources outside the studio
    if args.pre_crop:
        img = apply_pre_crop(img, args.pre_crop, args.pre_crop_padding)
        _save_debug(1, "pre_crop", img.convert("RGBA"))

    img = remove_background(img, session)
    _save_debug(2, "rembg", img)

    if args.edge_clean_threshold > 0:
        img = clean_alpha_edges(img, args.edge_clean_threshold)
        _save_debug(3, "edge_clean", img)

    if args.auto_rotate:
        img, angle = auto_rotate(img,
                                  max_correction_deg=args.max_rotation_deg,
                                  min_correction_deg=args.min_rotation_deg)
        info["rotation_deg"] = angle
        _save_debug(4, f"rotated_{angle:+.2f}deg", img)

    if args.auto_perspective:
        img, slopes = auto_perspective(
            img,
            max_correction_px=args.max_perspective_px,
            min_correction_px=args.min_perspective_px,
        )
        info["perspective_slopes"] = slopes
        _save_debug(5, "perspective", img)

    img = crop_to_subject(
        img,
        h_alpha_threshold=args.h_alpha_threshold,
        v_alpha_threshold=args.v_alpha_threshold,
        h_min_column_pixels=args.h_min_column_pixels,
        pad_top=args.pad_top,
        pad_bottom=args.pad_bottom,
        pad_left=args.pad_left,
        pad_right=args.pad_right,
    )
    info["after_crop_size"] = img.size
    info["after_crop_aspect"] = img.size[0] / img.size[1] if img.size[1] else 0
    _save_debug(6, f"cropped_{img.size[0]}x{img.size[1]}", img)

    if args.mode == "height":
        w, h = img.size
        scale = args.canvas_height / h
        new_w = max(1, round(w * scale))
        if args.max_width and new_w > args.max_width:
            scale = args.max_width / w
            new_w = args.max_width
        img = img.resize((new_w, max(1, round(h * scale))), Image.LANCZOS)
    else:
        if length_mm is None:
            raise ValueError(
                f"--mode scale needs --length-mm or an entry in --lengths "
                f"for file {in_path.name}")
        img = resize_scale_mode(img, length_mm, args.px_per_mm)
    _save_debug(7, f"scaled_{img.size[0]}x{img.size[1]}", img)

    # Place rail underneath the coach, if enabled
    if args.rail:
        rail_path = Path(args.rail_image) if args.rail_image else \
            Path(__file__).parent / "rail.png"
        if not rail_path.exists():
            raise FileNotFoundError(
                f"Rail file not found: {rail_path}\n"
                f"Expected: rail.png in the script folder, or pass a path "
                f"with --rail-image.")
        img = add_rail(img, rail_path, extend_canvas=args.rail_extend)
        _save_debug(8, f"with_rail_{img.size[0]}x{img.size[1]}", img)

    img, overflow = fit_to_canvas(img, args.canvas_height, align=args.align)
    info["overflow"] = overflow
    info["size"] = img.size
    _save_debug(9, f"final_{img.size[0]}x{img.size[1]}", img)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return info


# ---------------------------------------------------------------------------
# I/O Helpers
# ---------------------------------------------------------------------------

def gather_inputs(input_path: Path) -> list[Path]:
    if not input_path.exists():
        cwd = Path.cwd()
        hints: list[str] = []
        if str(input_path.parent) in (".", ""):
            stem = input_path.stem.lower()
            try:
                candidates = [p.name for p in cwd.iterdir()
                              if p.is_file() and stem in p.stem.lower()]
            except OSError:
                candidates = []
            if candidates:
                hints.append("Did you mean one of these files?")
                hints.extend(f"  - {c}" for c in candidates[:5])
        msg = f"Input '{input_path}' does not exist (looking in {cwd})."
        if hints:
            msg += "\n" + "\n".join(hints)
        raise FileNotFoundError(msg)

    if input_path.is_file():
        if input_path.suffix.lower() not in SUPPORTED_EXTS:
            raise ValueError(
                f"File format '{input_path.suffix}' is not supported. "
                f"Allowed: {', '.join(sorted(SUPPORTED_EXTS))}")
        return [input_path]

    if input_path.is_dir():
        files = sorted(p for p in input_path.iterdir()
                       if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS)
        if not files:
            raise FileNotFoundError(
                f"No supported image files found in folder '{input_path}' "
                f"({', '.join(sorted(SUPPORTED_EXTS))})")
        return files

    raise ValueError(f"'{input_path}' is neither a file nor a folder")


def load_lengths(lengths_file: Path | None) -> dict[str, float]:
    if lengths_file is None:
        return {}
    if not lengths_file.exists():
        raise FileNotFoundError(f"Lengths JSON not found: {lengths_file}")
    with open(lengths_file, "r", encoding="utf-8") as f:
        return {k: float(v) for k, v in json.load(f).items()}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=f"railshot — prepare model railway photos for digital "
                    f"control software (default: {ROCRAIL_DEFAULT_HEIGHT}px "
                    f"height, Rocrail standard)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("input", type=Path, help="Input file or folder")
    p.add_argument("-o", "--output", type=Path, required=True,
                   help="Output file (or folder for batch mode)")

    p.add_argument("--mode", choices=["height", "scale"], default="scale",
                   help="Scaling mode (default: scale)")
    p.add_argument("--canvas-height", type=int, default=ROCRAIL_DEFAULT_HEIGHT,
                   help="Output canvas height in px (default: 80, Rocrail "
                        "standard)")
    p.add_argument("--max-width", type=int, default=None,
                   help="Hard cap on width in height mode (px)")

    p.add_argument("--px-per-mm", type=float, default=None,
                   help="Scale: pixels per millimeter of model length. "
                        "Set ONCE per collection, then keep constant for "
                        "all images. Example: 1.515 means a 165 mm long "
                        "EW IV becomes 250 px wide.")
    p.add_argument("--length-mm", type=float, default=None,
                   help="Length of the current vehicle in mm "
                        "(measure on the model, buffer beam to buffer beam)")
    p.add_argument("--lengths", type=Path, default=None,
                   help="JSON {filename: length_mm} for batch processing")

    # Geometry correction
    p.add_argument("--auto-rotate", action="store_true",
                   help="Auto-level the lower coach edge")
    p.add_argument("--min-rotation-deg", type=float, default=0.2,
                   help="Below this angle no rotation (default: 0.2°)")
    p.add_argument("--max-rotation-deg", type=float, default=5.0,
                   help="Above this angle no rotation (probably a "
                        "detection error) (default: 5°)")

    p.add_argument("--auto-perspective", action="store_true",
                   help="Auto-straighten end faces "
                        "(experimental, requires opencv-python)")
    p.add_argument("--min-perspective-px", type=float, default=1.5,
                   help="Below this correction (px over full image height) "
                        "no correction (default: 1.5)")
    p.add_argument("--max-perspective-px", type=float, default=30.0,
                   help="Above this correction no application "
                        "(default: 30)")

    # Crop
    p.add_argument("--h-alpha-threshold", type=int, default=128,
                   help="Horizontal alpha threshold (default: 128, strict)")
    p.add_argument("--v-alpha-threshold", type=int, default=32,
                   help="Vertical alpha threshold (default: 32, lenient)")
    p.add_argument("--h-min-column-pixels", type=int, default=3,
                   help="Min opaque pixels per column to count (default: 3)")
    p.add_argument("--pad-left", type=int, default=0,
                   help="Padding left in px (= 0 for Rocrail!)")
    p.add_argument("--pad-right", type=int, default=0,
                   help="Padding right in px (= 0 for Rocrail!)")
    p.add_argument("--pad-top", type=int, default=1,
                   help="Padding top in px (default: 1)")
    p.add_argument("--pad-bottom", type=int, default=0,
                   help="Padding bottom in px (default: 0)")
    p.add_argument("--edge-clean-threshold", type=int, default=64,
                   help="Pixels with alpha below this become fully "
                        "transparent (default: 64)")

    # Rail
    p.add_argument("--rail", action="store_true",
                   help="Place a rail under the coach. Expects rail.png "
                        "in the script folder, or pass --rail-image.")
    p.add_argument("--rail-image", type=str, default=None,
                   help="Path to the rail template (PNG with alpha). "
                        "Default: rail.png in the script folder.")
    p.add_argument("--rail-extend", action=argparse.BooleanOptionalAction,
                   default=True,
                   help="Extend canvas downwards by rail height. Default: "
                        "on — the rail hangs UNDER the wheels, growing the "
                        "canvas. Use --no-rail-extend to overlay the rail "
                        "on the bottom edge of the wheels instead "
                        "(canvas height stays the same).")

    p.add_argument("--align", choices=["bottom", "center", "top"],
                   default="bottom",
                   help="Vertical alignment in canvas (default: bottom)")
    p.add_argument("--model", default="u2net",
                   help="rembg model (default: u2net)")

    p.add_argument("--pre-crop", type=str, default=None,
                   help="Crop to ROI BEFORE rembg. "
                        "Format: 'X1,Y1,X2,Y2' (pixels in original) or "
                        "'auto' (find bright region) or "
                        "'auto N' (with brightness threshold 0-255). "
                        "Eliminates noise sources outside the studio.")
    p.add_argument("--pre-crop-padding", type=int, default=20,
                   help="Safety padding around pre-crop ROI in px "
                        "(default: 20)")

    p.add_argument("--debug-dir", type=Path, default=None,
                   help="If set, all intermediate steps are saved as PNG "
                        "in this folder (one subfolder per input file). "
                        "Very helpful for debugging.")

    p.add_argument("-v", "--verbose", action="store_true",
                   help="More detailed error output")

    args = p.parse_args()

    if args.mode == "scale":
        if args.px_per_mm is None:
            p.error("--mode scale needs --px-per-mm")
        if args.length_mm is None and args.lengths is None:
            p.error("--mode scale needs --length-mm or --lengths")

    return args


def main() -> int:
    args = parse_args()

    try:
        inputs = gather_inputs(args.input)
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    is_batch = args.input.is_dir()
    if is_batch:
        args.output.mkdir(parents=True, exist_ok=True)
    else:
        if args.output.exists() and args.output.is_dir():
            args.output = args.output / (inputs[0].stem + ".png")

    try:
        lengths_map = load_lengths(args.lengths)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(f"Loading rembg model '{args.model}'...")
    print("(On first run, the model is downloaded, ~170 MB)")
    try:
        from rembg import new_session
        session = new_session(args.model)
    except ImportError as e:
        print(f"ERROR: rembg not fully installed.\n"
              f"  Fix: pip install \"rembg[cpu]\"\n"
              f"  Detail: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR loading rembg model: {e}", file=sys.stderr)
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    if args.auto_perspective:
        try:
            import cv2  # noqa: F401
        except ImportError:
            print("WARNING: --auto-perspective requires opencv-python.\n"
                  "  Install: pip install opencv-python\n"
                  "  Continuing without perspective correction.",
                  file=sys.stderr)

    print(f"Processing {len(inputs)} file(s) "
          f"(canvas: {args.canvas_height}px high)...")
    errors = 0
    overflows = 0
    rotated = 0
    perspectived = 0
    for in_path in inputs:
        if is_batch:
            out_path = args.output / (in_path.stem + ".png")
        else:
            out_path = args.output

        if args.mode == "scale":
            length_mm = (
                lengths_map.get(in_path.name)
                or lengths_map.get(in_path.stem)
                or args.length_mm
            )
        else:
            length_mm = None

        try:
            info = process_one(in_path, out_path, session, args, length_mm)
            extras = []
            if info["rotation_deg"] != 0:
                extras.append(f"rot {info['rotation_deg']:+.2f}°")
                rotated += 1
            ls, rs = info["perspective_slopes"]
            if ls != 0 or rs != 0:
                extras.append(f"persp L={ls*100:+.2f}%/h R={rs*100:+.2f}%/h")
                perspectived += 1
            if info["overflow"]:
                extras.append("TOO TALL")
                overflows += 1
            cs = info["after_crop_size"]
            extras.append(f"bbox {cs[0]}x{cs[1]} (aspect {info['after_crop_aspect']:.2f})")
            extra_str = "  [" + ", ".join(extras) + "]" if extras else ""
            print(f"  OK  {in_path.name}  ->  {out_path.name}  "
                  f"({info['size'][0]} x {info['size'][1]} px){extra_str}")
        except Exception as e:
            print(f"  ERROR  {in_path.name}: {e}", file=sys.stderr)
            if args.verbose:
                import traceback
                traceback.print_exc()
            errors += 1

    print(f"Done. {len(inputs) - errors}/{len(inputs)} successful.")
    if rotated:
        print(f"  -> {rotated} image(s) auto-rotated.")
    if perspectived:
        print(f"  -> {perspectived} image(s) perspective-corrected.")
    if overflows:
        print(f"  -> {overflows} image(s) too tall — clipped at top. "
              f"Increase --canvas-height if pantograph is affected.")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

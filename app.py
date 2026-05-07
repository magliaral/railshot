"""
railshot — Streamlit Web UI (multi-image, simplified)

Run locally:
    streamlit run app.py

Workflow:
  1. Upload one or more photos (use the × button to remove).
  2. Edit output name and length per image in the table.
  3. Set global settings in the sidebar.
  4. Click "Process all".
  5. Download individual PNGs or all results as ZIP.
"""

import io
import sys
import zipfile
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import streamlit as st
from PIL import Image

# Import railshot from the same folder
sys.path.insert(0, str(Path(__file__).parent))
from railshot import (
    process_one,
    ROCRAIL_DEFAULT_HEIGHT,
)


# ============================================================
# Page config
# ============================================================
st.set_page_config(
    page_title="railshot",
    page_icon="🚂",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# Cache rembg session
# ============================================================
@st.cache_resource(show_spinner="Loading rembg model (first run only)...")
def get_rembg_session(model_name: str):
    from rembg import new_session
    return new_session(model_name)


# ============================================================
# Session state for per-image edits and results
# ============================================================
# We store edits keyed by file_id so they survive across reruns even when
# the uploader returns the same files. Removed files (via × in the uploader)
# are cleaned up at the end of each render.
if "edits" not in st.session_state:
    # edits[file_id] = {"name": str, "length_mm": int}
    st.session_state.edits = {}

if "results" not in st.session_state:
    # results[file_id] = {"png": bytes, "info": dict, "error": str | None}
    st.session_state.results = {}


# ============================================================
# Header
# ============================================================
st.title("🚂 railshot")
st.caption(
    "Prepare model railway photos for digital control software "
    "(Rocrail, iTrain). Upload multiple images, set length per model, "
    "process them all in one go."
)


# ============================================================
# Sidebar: global settings
# ============================================================
with st.sidebar:
    st.header("⚙️ Global settings")
    st.caption("These apply to **all** images in the batch.")

    st.subheader("Scale")
    px_per_mm = st.number_input(
        "Pixels per mm",
        min_value=0.1, max_value=10.0, value=2.0, step=0.1,
        help="Scale factor. Set ONCE per collection — same value for all "
             "vehicles. E.g. 2.0 means a 165 mm coach becomes 330 px wide."
    )

    canvas_height = st.number_input(
        "Canvas height (px)",
        min_value=40, max_value=200, value=ROCRAIL_DEFAULT_HEIGHT, step=10,
        help="Output PNG height. Rocrail standard is 80 px."
    )

    st.subheader("Auto-corrections")
    auto_rotate = st.checkbox(
        "Auto-rotate", value=True,
        help="Auto-level the bottom edge. Safe — only applies if 0.2°-5°.",
    )
    auto_perspective = st.checkbox(
        "Auto-perspective", value=False,
        help="Straighten end faces. Experimental — requires opencv-python.",
    )

    st.subheader("Rail underlay")
    add_rail_flag = st.checkbox(
        "Add digital rail", value=True,
        help="Place a rail under the model.",
    )
    rail_extend = st.checkbox(
        "Extend canvas for rail", value=False,
        help="If on: canvas grows by rail height. If off (default): rail "
             "is overlaid on the wheels (more realistic).",
    )

    st.subheader("Pre-crop")
    pre_crop_mode = st.radio(
        "Mode",
        options=["off", "manual", "auto"],
        index=0,
        help="Crop the image BEFORE rembg, to remove distractions.",
    )
    pre_crop_value: Optional[str] = None
    if pre_crop_mode == "manual":
        pre_crop_value = st.text_input(
            "ROI 'X1,Y1,X2,Y2'",
            value="170,80,1965,820",
            help="Pixel coordinates in the original photo.",
        )
    elif pre_crop_mode == "auto":
        pre_crop_threshold = st.slider("Brightness threshold", 100, 250, 180)
        pre_crop_value = f"auto {pre_crop_threshold}"

    with st.expander("Advanced"):
        rembg_model = st.selectbox(
            "rembg model",
            options=["isnet-general-use", "u2net", "u2netp"],
            index=0,
        )
        h_alpha_threshold = st.slider("H-alpha threshold", 64, 200, 128, 8)
        v_alpha_threshold = st.slider("V-alpha threshold", 8, 128, 32, 4)
        edge_clean_threshold = st.slider("Edge cleanup", 0, 200, 64, 8)
        pad_top = st.slider("Padding top", 0, 10, 1, 1)


# ============================================================
# Main: file uploader (the source of truth)
# ============================================================
st.subheader("📷 Upload images")
uploaded_files = st.file_uploader(
    "Drop one or more photos. Use the × button to remove individual files.",
    type=["jpg", "jpeg", "png", "bmp", "tif", "tiff", "webp"],
    accept_multiple_files=True,
    label_visibility="collapsed",
)


# ============================================================
# Build the active list directly from the uploader
# ============================================================
# Each upload has a stable file_id we use as the key. Edits and results
# are looked up against this id. Files removed in the uploader simply
# disappear from this list.

active_keys = set()
items = []  # ordered list of dicts with everything needed for rendering

if uploaded_files:
    for f in uploaded_files:
        key = f.file_id
        active_keys.add(key)

        # Initialise edits on first sight of this file
        if key not in st.session_state.edits:
            st.session_state.edits[key] = {
                "name": Path(f.name).stem + ".png",
                "length_mm": 165,
            }

        items.append({
            "key": key,
            "raw_bytes": f.getvalue(),
            "raw_ext": Path(f.name).suffix,
            "edits": st.session_state.edits[key],
            "result": st.session_state.results.get(key),
        })

# Clean up edits/results for files that have been removed in the uploader
stale_keys = (
    set(st.session_state.edits.keys()) - active_keys
) | (
    set(st.session_state.results.keys()) - active_keys
)
for sk in stale_keys:
    st.session_state.edits.pop(sk, None)
    st.session_state.results.pop(sk, None)


# ============================================================
# Per-image table (no remove button — use the uploader's ×)
# ============================================================
if not items:
    st.info(
        "👆 Upload images above to get started. You can drag-and-drop "
        "multiple files at once."
    )
else:
    st.subheader(f"📋 Batch ({len(items)} image(s))")
    st.caption(
        "Edit output name and length per image. "
        "To remove an image, click the **×** next to its name in the "
        "upload zone above."
    )

    h_cols = st.columns([1, 3, 2])
    h_cols[0].markdown("**Preview**")
    h_cols[1].markdown("**Output name**")
    h_cols[2].markdown("**Length (mm)**")

    for it in items:
        cols = st.columns([1, 3, 2])
        # Preview
        try:
            preview = Image.open(io.BytesIO(it["raw_bytes"]))
            cols[0].image(preview, width=100)
        except Exception:
            cols[0].text("?")

        # Output name (editable)
        new_name = cols[1].text_input(
            "Output name",
            value=it["edits"]["name"],
            key=f"name_{it['key']}",
            label_visibility="collapsed",
        )
        if not new_name.lower().endswith(".png"):
            new_name = Path(new_name).stem + ".png"
        it["edits"]["name"] = new_name

        # Length (editable)
        it["edits"]["length_mm"] = cols[2].number_input(
            "Length",
            min_value=10, max_value=2000,
            value=int(it["edits"]["length_mm"]),
            step=1,
            key=f"len_{it['key']}",
            label_visibility="collapsed",
        )

    st.divider()

    # ============================================================
    # Process button
    # ============================================================
    if st.button(
        f"🚂 Process all {len(items)} image(s)",
        type="primary",
        use_container_width=True,
    ):
        # Reset results before re-running
        st.session_state.results = {}

        session = get_rembg_session(rembg_model)
        progress = st.progress(0.0, text="Starting...")
        total = len(items)

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            for i, it in enumerate(items):
                progress.progress(
                    i / total,
                    text=f"Processing {it['edits']['name']} ({i + 1}/{total})...",
                )

                tmp_input = tmp_dir / f"input_{i}{it['raw_ext']}"
                tmp_output = tmp_dir / f"output_{i}.png"
                tmp_input.write_bytes(it["raw_bytes"])

                args = SimpleNamespace(
                    mode="scale",
                    canvas_height=canvas_height,
                    max_width=None,
                    px_per_mm=px_per_mm,
                    length_mm=it["edits"]["length_mm"],
                    lengths=None,
                    pre_crop=pre_crop_value,
                    pre_crop_padding=20,
                    auto_rotate=auto_rotate,
                    min_rotation_deg=0.2,
                    max_rotation_deg=5.0,
                    auto_perspective=auto_perspective,
                    min_perspective_px=1.5,
                    max_perspective_px=30.0,
                    h_alpha_threshold=h_alpha_threshold,
                    v_alpha_threshold=v_alpha_threshold,
                    h_min_column_pixels=3,
                    pad_left=0,
                    pad_right=0,
                    pad_top=pad_top,
                    pad_bottom=0,
                    edge_clean_threshold=edge_clean_threshold,
                    rail=add_rail_flag,
                    rail_image=None,
                    rail_extend=rail_extend,
                    align="bottom",
                    model=rembg_model,
                    debug_dir=None,
                    verbose=False,
                )

                try:
                    info = process_one(
                        tmp_input, tmp_output, session, args,
                        it["edits"]["length_mm"],
                    )
                    st.session_state.results[it["key"]] = {
                        "png": tmp_output.read_bytes(),
                        "info": info,
                        "error": None,
                    }
                except Exception as e:
                    st.session_state.results[it["key"]] = {
                        "png": None,
                        "info": None,
                        "error": str(e),
                    }

            progress.progress(1.0, text="Done!")

        st.success(f"Processed {total} image(s).")
        # Re-render with results
        st.rerun()


# ============================================================
# Results section
# ============================================================
results_with_png = [
    (it, st.session_state.results.get(it["key"]))
    for it in items
    if st.session_state.results.get(it["key"]) is not None
]

if results_with_png:
    st.divider()
    st.subheader("✨ Results")

    success_items = [(it, r) for it, r in results_with_png if r["png"]]

    # ZIP download for all successful results
    if success_items:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for it, r in success_items:
                zf.writestr(it["edits"]["name"], r["png"])
        zip_buf.seek(0)

        st.download_button(
            label=f"📦 Download all {len(success_items)} result(s) as ZIP",
            data=zip_buf.getvalue(),
            file_name="railshot_results.zip",
            mime="application/zip",
            use_container_width=True,
        )
        st.divider()

    # Per-image result row
    for it, r in results_with_png:
        cols = st.columns([2, 4, 2])
        name = it["edits"]["name"]

        if r["error"]:
            cols[0].markdown(f"**❌ {name}**")
            cols[1].error(f"Failed: {r['error']}")
            continue

        result_img = Image.open(io.BytesIO(r["png"]))
        cols[0].image(result_img, caption=name)

        info = r["info"]
        bbox = info["after_crop_size"]
        aspect = info["after_crop_aspect"]
        rot = info["rotation_deg"]
        size = info["size"]

        details = (
            f"**{name}**\n\n"
            f"- Output: {size[0]}×{size[1]} px ({len(r['png'])/1024:.1f} KB)\n"
            f"- Bounding box: {bbox[0]}×{bbox[1]} (aspect {aspect:.2f})\n"
            f"- Length: {it['edits']['length_mm']} mm × {px_per_mm} = "
            f"{round(it['edits']['length_mm']*px_per_mm)} px\n"
        )
        if rot != 0:
            details += f"- Auto-rotated by {rot:+.2f}°\n"
        if info["overflow"]:
            details += "- ⚠️ Image is taller than canvas — clipped at top.\n"
        cols[1].markdown(details)

        cols[2].download_button(
            label="📥 Download",
            data=r["png"],
            file_name=name,
            mime="image/png",
            key=f"dl_{it['key']}",
            use_container_width=True,
        )


# ============================================================
# Footer
# ============================================================
with st.expander("ℹ️ About railshot"):
    st.markdown("""
    **railshot** prepares model railway photos for digital control software
    like Rocrail or iTrain. Built for **N-scale** (1:160) but works for any
    scale where the px-per-mm factor is consistent.

    **Multi-image workflow:**
    1. Drop multiple photos at once into the upload zone
    2. For each photo, edit the output name and the model length (mm)
    3. Set the global settings (scale, auto-rotate, rail, pre-crop) once
    4. Click "Process all"
    5. Download individual PNGs or all together as ZIP

    **Key concept:** the `Pixels per mm` value defines the scale for your
    *entire collection*. Set it once, then keep it constant — otherwise
    different vehicles won't be in the same scale anymore.

    Source: [github.com/magliaral/railshot](https://github.com/magliaral/railshot)
    | License: MIT
    """)

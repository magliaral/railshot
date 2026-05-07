"""
railshot — Streamlit Web UI

Run locally:
    streamlit run app.py

Or deploy to Hugging Face Spaces with the included README.md (see README).
"""

import io
import sys
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import streamlit as st
from PIL import Image

# Import railshot functions from the same folder
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
# Cache the rembg session — loading takes ~3 seconds
# ============================================================
@st.cache_resource(show_spinner="Loading rembg model (first run only)...")
def get_rembg_session(model_name: str):
    from rembg import new_session
    return new_session(model_name)


# ============================================================
# Header
# ============================================================
st.title("🚂 railshot")
st.caption(
    "Prepare model railway photos for digital control software "
    "(Rocrail, iTrain). Background removal, scale-accurate sizing, "
    "optional rail underlay."
)


# ============================================================
# Sidebar: Settings
# ============================================================
with st.sidebar:
    st.header("⚙️ Settings")

    st.subheader("Scale")
    px_per_mm = st.number_input(
        "Pixels per mm",
        min_value=0.1, max_value=10.0, value=2.0, step=0.1,
        help="Scale factor. Set ONCE per collection — same value for all "
             "vehicles. E.g. 2.0 means a 165 mm coach becomes 330 px wide."
    )
    length_mm = st.number_input(
        "Model length (mm)",
        min_value=10, max_value=2000, value=165, step=1,
        help="Length of THIS specific vehicle (buffer beam to buffer beam)."
    )

    canvas_height = st.number_input(
        "Canvas height (px)",
        min_value=40, max_value=200, value=ROCRAIL_DEFAULT_HEIGHT, step=10,
        help="Output PNG height. Rocrail standard is 80 px."
    )

    st.subheader("Auto-corrections")
    auto_rotate = st.checkbox(
        "Auto-rotate",
        value=True,
        help="Auto-level the bottom edge. Safe — only applies if 0.2°-5°."
    )
    auto_perspective = st.checkbox(
        "Auto-perspective",
        value=False,
        help="Straighten end faces. Experimental — requires opencv-python."
    )

    st.subheader("Rail underlay")
    add_rail_flag = st.checkbox(
        "Add digital rail",
        value=True,
        help="Place a rail under the model. Uses rail.png from script folder."
    )
    rail_extend = st.checkbox(
        "Extend canvas for rail",
        value=False,
        help="If on: canvas grows by rail height. If off (default): rail is "
             "overlaid on the bottom of the wheels (more realistic)."
    )

    st.subheader("Pre-crop")
    pre_crop_mode = st.radio(
        "Mode",
        options=["off", "manual", "auto"],
        index=0,
        help="Crop the image BEFORE rembg, to remove distractions outside "
             "the studio area."
    )
    pre_crop_value: Optional[str] = None
    if pre_crop_mode == "manual":
        pre_crop_value = st.text_input(
            "ROI 'X1,Y1,X2,Y2'",
            value="170,80,1965,820",
            help="Pixel coordinates in the original photo."
        )
    elif pre_crop_mode == "auto":
        pre_crop_threshold = st.slider(
            "Brightness threshold", 100, 250, 180,
            help="Higher = stricter. Default 180 works for most studios."
        )
        pre_crop_value = f"auto {pre_crop_threshold}"

    with st.expander("Advanced"):
        rembg_model = st.selectbox(
            "rembg model",
            options=["isnet-general-use", "u2net", "u2netp"],
            index=0,
            help="isnet-general-use: best quality. u2net: classic. "
                 "u2netp: fast but less accurate."
        )
        h_alpha_threshold = st.slider(
            "H-alpha threshold", 64, 200, 128, 8,
            help="Strictness for left/right cropping (default: 128)"
        )
        v_alpha_threshold = st.slider(
            "V-alpha threshold", 8, 128, 32, 4,
            help="Strictness for top/bottom cropping (default: 32, lenient)"
        )
        edge_clean_threshold = st.slider(
            "Edge cleanup threshold", 0, 200, 64, 8,
            help="Pixels with alpha below this become fully transparent."
        )
        pad_top = st.slider(
            "Padding top", 0, 10, 1, 1,
            help="Extra pixels above the model (for pantograph etc)"
        )

    st.divider()
    st.caption(
        "💡 Tip: set `Pixels per mm` once, then process your whole "
        "collection with the same value for consistent scaling."
    )


# ============================================================
# Main area: two columns
# ============================================================
col1, col2 = st.columns(2)

with col1:
    st.subheader("📷 Input")
    uploaded = st.file_uploader(
        "Choose a photo of a model railway vehicle",
        type=["jpg", "jpeg", "png", "bmp", "tif", "tiff", "webp"],
    )

    if uploaded:
        original = Image.open(uploaded)
        st.image(original, caption=f"Original ({original.size[0]}×{original.size[1]} px)",
                 use_container_width=True)

with col2:
    st.subheader("✨ Result")

    if not uploaded:
        st.info(
            "👈 Upload a photo on the left, adjust settings in the sidebar, "
            "then click **Process** below."
        )
    else:
        # Result placeholder
        result_placeholder = st.empty()
        info_placeholder = st.empty()
        download_placeholder = st.empty()


# ============================================================
# Process button
# ============================================================
if uploaded:
    st.divider()
    if st.button("🚂 Process image", type="primary", use_container_width=True):
        # Save uploaded file to temp location
        tmp_input = Path("/tmp/railshot_input" + Path(uploaded.name).suffix)
        tmp_output = Path("/tmp/railshot_output.png")
        tmp_input.write_bytes(uploaded.getvalue())

        # Build args namespace matching what process_one expects
        args = SimpleNamespace(
            mode="scale",
            canvas_height=canvas_height,
            max_width=None,
            px_per_mm=px_per_mm,
            length_mm=length_mm,
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
            with st.spinner("Removing background, cropping, scaling..."):
                session = get_rembg_session(rembg_model)
                info = process_one(tmp_input, tmp_output, session, args, length_mm)

            # Read result
            result_img = Image.open(tmp_output)
            png_bytes = tmp_output.read_bytes()

            # Show in col2
            with col2:
                # Show on a checkered background by default — Streamlit handles this
                st.image(result_img,
                         caption=f"Result ({result_img.size[0]}×{result_img.size[1]} px, "
                                 f"{len(png_bytes)/1024:.1f} KB)",
                         use_container_width=False)

                # Diagnostics
                bbox = info["after_crop_size"]
                aspect = info["after_crop_aspect"]
                rot = info["rotation_deg"]
                slopes = info["perspective_slopes"]

                cols = st.columns(3)
                cols[0].metric("Output size", f"{result_img.size[0]}×{result_img.size[1]} px")
                cols[1].metric("Bounding box", f"{bbox[0]}×{bbox[1]}")
                cols[2].metric("Aspect ratio", f"{aspect:.2f}")

                if rot != 0:
                    st.caption(f"🔄 Auto-rotated by {rot:+.2f}°")
                if slopes != (0, 0):
                    st.caption(f"📐 Perspective corrected: L={slopes[0]*100:+.2f}%/h R={slopes[1]*100:+.2f}%/h")
                if info["overflow"]:
                    st.warning("⚠️ Image is taller than canvas — clipped at top. "
                               "Increase canvas height if pantograph is affected.")

                # Download button
                output_name = Path(uploaded.name).stem + "_railshot.png"
                st.download_button(
                    label="📥 Download PNG",
                    data=png_bytes,
                    file_name=output_name,
                    mime="image/png",
                    use_container_width=True,
                )

        except Exception as e:
            with col2:
                st.error(f"❌ Processing failed: {e}")
                with st.expander("Show details"):
                    import traceback
                    st.code(traceback.format_exc())


# ============================================================
# Footer / info
# ============================================================
with st.expander("ℹ️ About railshot"):
    st.markdown("""
    **railshot** prepares model railway photos for digital control software
    like Rocrail or iTrain. Built for **N-scale** (1:160) but works for any
    scale where the px-per-mm factor is consistent.

    **Workflow:**
    1. Take photos of your models in a fixed studio setup
    2. Upload here, set scale once, process
    3. Download the transparent PNG and use it in Rocrail/iTrain

    **Key concept:** the `Pixels per mm` value defines the scale for your
    *entire collection*. Set it once, then keep it constant — otherwise
    different vehicles won't be in the same scale anymore.

    Source: [github.com/bt-unibe-ch/railshot](https://github.com/bt-unibe-ch/railshot)
    | License: MIT
    """)

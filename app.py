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
import json
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
# Translations — Home Assistant style
# ============================================================
TRANSLATIONS_DIR = Path(__file__).parent / "translations"


@st.cache_data
def _load_translations() -> dict[str, dict]:
    """Load all translation files from the translations/ folder."""
    out: dict[str, dict] = {}
    if TRANSLATIONS_DIR.exists():
        for f in sorted(TRANSLATIONS_DIR.glob("*.json")):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    out[f.stem] = json.load(fh)
            except Exception:
                pass  # silently skip broken translation files
    # Always have at least an English fallback so the app never crashes
    if "en" not in out:
        out["en"] = {}
    return out


TRANSLATIONS = _load_translations()
DEFAULT_LANG = "en"


def _detect_browser_language() -> Optional[str]:
    """
    Try to detect the user's preferred language from the browser's
    Accept-Language header. Returns the language code if it matches
    one we have translations for, otherwise None.

    The Accept-Language header looks like:
        "de-CH,de;q=0.9,en;q=0.8,fr;q=0.7"
    We parse it, sort by quality, and try to match against available
    translations (using just the primary tag, e.g. "de" not "de-CH").
    """
    try:
        # st.context.headers is available since Streamlit 1.36
        headers = st.context.headers
    except Exception:
        return None

    if not headers:
        return None

    accept_lang = headers.get("Accept-Language") or headers.get("accept-language")
    if not accept_lang:
        return None

    # Parse the header: split by comma, then by ";q=" for quality value
    candidates: list[tuple[float, str]] = []
    for entry in accept_lang.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ";q=" in entry:
            tag, q_str = entry.split(";q=", 1)
            try:
                q = float(q_str)
            except ValueError:
                q = 1.0
        else:
            tag = entry
            q = 1.0
        # Only keep the primary language tag ("de-CH" -> "de")
        primary = tag.split("-", 1)[0].strip().lower()
        candidates.append((q, primary))

    # Sort by descending quality
    candidates.sort(key=lambda x: x[0], reverse=True)

    # Return first one we have translations for
    available = set(TRANSLATIONS.keys())
    for _, lang in candidates:
        if lang in available:
            return lang
    return None


def _t(key: str, **fmt) -> str:
    """
    Translate a key into the currently selected language. Falls back to
    English, then to the key itself if missing entirely.

    Supports {placeholder} formatting via kwargs, e.g.
        _t("process.processing", name="foo.png", i=1, total=5)
    """
    lang = st.session_state.get("lang", DEFAULT_LANG)
    text = (
        TRANSLATIONS.get(lang, {}).get(key)
        or TRANSLATIONS.get(DEFAULT_LANG, {}).get(key)
        or key
    )
    if fmt:
        try:
            return text.format(**fmt)
        except Exception:
            return text
    return text


# ============================================================
# Page config
# ============================================================
# Use logo file if available, otherwise emoji fallback
_logo_path = Path(__file__).parent / "assets" / "favicon-180.png"
_page_icon = str(_logo_path) if _logo_path.exists() else "🚂"

st.set_page_config(
    page_title="railshot",
    page_icon=_page_icon,
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# Custom CSS — visual polish on top of the theme
# ============================================================
st.markdown("""
<style>
    /* Tighter top padding so the title sits closer to the top */
    .block-container { padding-top: 2rem; padding-bottom: 4rem; }

    /* Title with a subtle accent line on the left */
    h1 {
        border-left: 4px solid var(--primary-color, #E85D5D);
        padding-left: 0.75rem;
        margin-bottom: 0.25rem !important;
    }

    /* Subtle separator look for st.divider() */
    hr { opacity: 0.3; }

    /* Image previews in the batch table — soft shadow + rounded corners */
    [data-testid="stImage"] img {
        border-radius: 6px;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
    }

    /* Make the file uploader stand out a bit more */
    [data-testid="stFileUploaderDropzone"] {
        border: 1.5px dashed rgba(232, 93, 93, 0.4);
        transition: border-color 0.2s ease, background-color 0.2s ease;
    }
    [data-testid="stFileUploaderDropzone"]:hover {
        border-color: rgba(232, 93, 93, 0.8);
    }

    /* Primary action buttons get a tiny bit of breathing room */
    .stButton > button[kind="primary"] {
        font-weight: 600;
        letter-spacing: 0.01em;
    }

    /* Caption text slightly more muted */
    [data-testid="stCaptionContainer"] {
        opacity: 0.75;
    }

    /* Reduce the visual weight of metric labels */
    [data-testid="stMetricLabel"] {
        opacity: 0.7;
        font-size: 0.875rem;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================
# Cache rembg session
# ============================================================
@st.cache_resource(show_spinner="Loading rembg model (first run only)...")
def get_rembg_session(model_name: str):
    from rembg import new_session
    return new_session(model_name)


# ============================================================
# Security helpers
# ============================================================
# Maximum file size we accept per upload (bytes). Defends against memory
# exhaustion from malicious or accidental huge uploads. The Streamlit
# config also enforces this server-side, this is a second layer.
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

# Maximum image dimensions we'll process. Decoded pixels can be much larger
# than the file size suggests (PNG decompression bombs).
MAX_IMAGE_PIXELS = 8000 * 8000  # 64 megapixels


def _sanitize_filename(name: str, fallback: str = "output") -> str:
    """
    Make a user-supplied filename safe for use:
    - Strip directory components (prevents path traversal)
    - Replace dangerous characters with underscores
    - Ensure .png extension
    - Fall back to a safe default if input becomes empty
    """
    # Take only the last path component, strip any directory separators
    name = Path(name).name  # strips "../../etc/" etc.
    # Get just the stem and force .png extension
    stem = Path(name).stem
    # Allow only safe characters: letters, digits, dash, underscore, dot, space
    safe_stem = "".join(
        c if (c.isalnum() or c in "-_. ") else "_"
        for c in stem
    ).strip()
    if not safe_stem or safe_stem in (".", ".."):
        safe_stem = fallback
    return safe_stem + ".png"


def _validate_image_bytes(raw: bytes) -> Optional[str]:
    """
    Quick safety checks on uploaded image bytes.
    Returns an error message if invalid, None if OK.
    """
    if len(raw) > MAX_FILE_SIZE_BYTES:
        return f"File too large ({len(raw)/1024/1024:.1f} MB). " \
               f"Max: {MAX_FILE_SIZE_BYTES/1024/1024:.0f} MB."
    try:
        # Verify it's actually a parseable image and within size limits
        with Image.open(io.BytesIO(raw)) as img:
            img.verify()  # checks integrity without full decode
            w, h = img.size
            if w * h > MAX_IMAGE_PIXELS:
                return f"Image too big ({w}×{h} px). " \
                       f"Max: {MAX_IMAGE_PIXELS/1_000_000:.0f} megapixels."
    except Exception as e:
        return f"Not a valid image: {type(e).__name__}"
    return None


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
# Header — logo + tagline (theme-aware)
# ============================================================
# Streamlit fires a rerun when the user toggles theme in the Settings
# menu, so reading st.context.theme.type per-render is enough to update
# the logo. For users on "Use system setting", we additionally inject a
# CSS @media query so the logo follows the OS theme without a reload.
_assets = Path(__file__).parent / "assets"
_logo_light = _assets / "logo-light-small.png"
_logo_dark = _assets / "logo-dark-small.png"


def _img_to_data_uri(path: Path) -> str:
    """Encode a PNG file as a base64 data: URI for inline embedding."""
    import base64
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _detect_streamlit_theme() -> str:
    """Read Streamlit's currently-rendered theme. Returns 'light' or 'dark'."""
    try:
        t = st.context.theme.type
        return t if t in ("light", "dark") else "light"
    except Exception:
        return "light"


if _logo_light.exists() and _logo_dark.exists():
    light_uri = _img_to_data_uri(_logo_light)
    dark_uri = _img_to_data_uri(_logo_dark)
    current_theme = _detect_streamlit_theme()
    # Pick the right logo for THIS render based on Streamlit's theme.
    # If the user toggles theme in Settings, Streamlit triggers a rerun
    # so this re-evaluates with the new theme.
    primary_uri = dark_uri if current_theme == "dark" else light_uri
    fallback_uri = light_uri if current_theme == "dark" else dark_uri

    head_cols = st.columns([1, 6])
    with head_cols[0]:
        st.markdown(f"""
            <style>
            .railshot-logo {{
                width: 120px;
                height: 120px;
                background-size: contain;
                background-repeat: no-repeat;
                background-position: center;
                background-image: url('{primary_uri}');
            }}
            /* Follow OS-level dark mode preference if Streamlit is set
               to "Use system setting" (so the logo updates instantly even
               without a Streamlit rerun). */
            @media (prefers-color-scheme: {'light' if current_theme == 'dark' else 'dark'}) {{
                .railshot-logo {{
                    background-image: url('{fallback_uri}');
                }}
            }}
            </style>
            <div class="railshot-logo"></div>
        """, unsafe_allow_html=True)
    with head_cols[1]:
        st.title(_t("page_title"))
        st.caption(_t("tagline"))
elif _logo_light.exists() or _logo_dark.exists():
    fallback = _logo_light if _logo_light.exists() else _logo_dark
    head_cols = st.columns([1, 6])
    head_cols[0].image(str(fallback), width=120)
    with head_cols[1]:
        st.title(_t("page_title"))
        st.caption(_t("tagline"))
else:
    st.title("🚂 " + _t("page_title"))
    st.caption(_t("tagline"))


# ============================================================
# Sidebar: global settings
# ============================================================
with st.sidebar:
    # Auto-detect browser language on first visit
    if "lang" not in st.session_state:
        detected = _detect_browser_language()
        st.session_state.lang = detected if detected else DEFAULT_LANG

    # Language selector at the top of the sidebar
    available_langs = sorted(TRANSLATIONS.keys())
    lang_labels = {
        code: TRANSLATIONS.get(code, {}).get("language_name", code.upper())
        for code in available_langs
    }
    current_lang = st.session_state.lang
    selected_lang = st.selectbox(
        _t("sidebar.language"),
        options=available_langs,
        index=available_langs.index(current_lang)
            if current_lang in available_langs else 0,
        format_func=lambda code: lang_labels.get(code, code),
        key="lang_selector",
    )
    if selected_lang != current_lang:
        st.session_state.lang = selected_lang
        st.rerun()

    st.header(_t("sidebar.global_settings"))
    st.caption(_t("sidebar.global_settings_caption"))

    st.subheader(_t("sidebar.scale"))
    px_per_mm = st.number_input(
        _t("sidebar.px_per_mm"),
        min_value=0.1, max_value=10.0, value=2.0, step=0.1,
        help=_t("sidebar.px_per_mm_help"),
    )

    canvas_height = st.number_input(
        _t("sidebar.canvas_height"),
        min_value=40, max_value=200, value=ROCRAIL_DEFAULT_HEIGHT, step=10,
        help=_t("sidebar.canvas_height_help"),
    )

    st.subheader(_t("sidebar.auto_corrections"))
    auto_rotate = st.checkbox(
        _t("sidebar.auto_rotate"), value=True,
        help=_t("sidebar.auto_rotate_help"),
    )
    auto_perspective = st.checkbox(
        _t("sidebar.auto_perspective"), value=False,
        help=_t("sidebar.auto_perspective_help"),
    )

    st.subheader(_t("sidebar.rail"))
    add_rail_flag = st.checkbox(
        _t("sidebar.add_rail"), value=True,
        help=_t("sidebar.add_rail_help"),
    )
    rail_extend = st.checkbox(
        _t("sidebar.rail_extend"), value=True,
        help=_t("sidebar.rail_extend_help"),
    )

    st.subheader(_t("sidebar.precrop"))
    pre_crop_mode_keys = ["off", "manual", "auto"]
    pre_crop_mode = st.radio(
        _t("sidebar.precrop_mode"),
        options=pre_crop_mode_keys,
        index=0,
        format_func=lambda k: _t(f"sidebar.precrop_{k}"),
        help=_t("sidebar.precrop_help"),
    )
    pre_crop_value: Optional[str] = None
    if pre_crop_mode == "manual":
        pre_crop_value = st.text_input(
            _t("sidebar.precrop_roi"),
            value="170,80,1965,820",
            help=_t("sidebar.precrop_roi_help"),
        )
    elif pre_crop_mode == "auto":
        pre_crop_threshold = st.slider(
            _t("sidebar.precrop_threshold"), 100, 250, 180,
        )
        pre_crop_value = f"auto {pre_crop_threshold}"

    with st.expander(_t("sidebar.advanced")):
        rembg_model = st.selectbox(
            _t("sidebar.rembg_model"),
            options=["isnet-general-use", "u2net", "u2netp"],
            index=0,
        )
        h_alpha_threshold = st.slider(_t("sidebar.h_alpha"), 64, 200, 128, 8)
        v_alpha_threshold = st.slider(_t("sidebar.v_alpha"), 8, 128, 32, 4)
        edge_clean_threshold = st.slider(_t("sidebar.edge_clean"), 0, 200, 64, 8)
        pad_top = st.slider(_t("sidebar.pad_top"), 0, 10, 1, 1)


# ============================================================
# Main: file uploader (the source of truth)
# ============================================================
st.subheader(_t("main.upload_images"))
uploaded_files = st.file_uploader(
    _t("main.upload_help"),
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
upload_errors: list[str] = []

if uploaded_files:
    for f in uploaded_files:
        raw = f.getvalue()
        # Security: validate before accepting
        err = _validate_image_bytes(raw)
        if err:
            upload_errors.append(f"❌ **{f.name}**: {err}")
            continue

        key = f.file_id
        active_keys.add(key)

        # Initialise edits on first sight of this file
        if key not in st.session_state.edits:
            st.session_state.edits[key] = {
                "name": _sanitize_filename(f.name, fallback="image"),
                "length_mm": 165,
            }

        items.append({
            "key": key,
            "raw_bytes": raw,
            "raw_ext": Path(f.name).suffix.lower(),
            "edits": st.session_state.edits[key],
            "result": st.session_state.results.get(key),
        })

# Show validation errors prominently
for err_msg in upload_errors:
    st.error(err_msg)

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
    st.info(_t("main.empty_hint"))
else:
    st.subheader(_t("main.batch", count=len(items)))
    st.caption(_t("main.batch_caption"))

    h_cols = st.columns([1, 3, 2])
    h_cols[0].markdown(f"**{_t('main.preview')}**")
    h_cols[1].markdown(f"**{_t('main.output_name')}**")
    h_cols[2].markdown(f"**{_t('main.length_mm')}**")

    for it in items:
        cols = st.columns([1, 3, 2])
        # Preview
        try:
            preview = Image.open(io.BytesIO(it["raw_bytes"]))
            cols[0].image(preview, width=100)
        except Exception:
            cols[0].text("?")

        # Output name (editable, sanitised against path traversal etc.)
        new_name = cols[1].text_input(
            _t("main.output_name"),
            value=it["edits"]["name"],
            key=f"name_{it['key']}",
            label_visibility="collapsed",
        )
        new_name = _sanitize_filename(new_name, fallback="image")
        it["edits"]["name"] = new_name

        # Length (editable)
        it["edits"]["length_mm"] = cols[2].number_input(
            _t("main.length_mm"),
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
        _t("process.button", count=len(items)),
        type="primary",
        use_container_width=True,
    ):
        # Reset results before re-running
        st.session_state.results = {}

        session = get_rembg_session(rembg_model)
        progress = st.progress(0.0, text=_t("process.starting"))
        total = len(items)

        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            for i, it in enumerate(items):
                progress.progress(
                    i / total,
                    text=_t("process.processing",
                            name=it["edits"]["name"], i=i + 1, total=total),
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

            progress.progress(1.0, text=_t("process.done"))

        st.success(_t("process.success", count=total))
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
    st.subheader(_t("results.heading"))

    success_items = [(it, r) for it, r in results_with_png if r["png"]]

    # ZIP download for all successful results
    if success_items:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for it, r in success_items:
                zf.writestr(it["edits"]["name"], r["png"])
        zip_buf.seek(0)

        st.download_button(
            label=_t("results.zip_button", count=len(success_items)),
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
            cols[1].error(_t("results.failed", error=r["error"]))
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
            f"- {_t('results.output')}: {size[0]}×{size[1]} px "
            f"({len(r['png'])/1024:.1f} KB)\n"
            f"- {_t('results.bbox')}: {bbox[0]}×{bbox[1]} "
            f"({_t('results.aspect')} {aspect:.2f})\n"
            f"- {_t('results.length_calc')}: {it['edits']['length_mm']} mm × "
            f"{px_per_mm} = {round(it['edits']['length_mm']*px_per_mm)} px\n"
        )
        if rot != 0:
            details += f"- {_t('results.rotated', angle=f'{rot:+.2f}')}\n"
        if info["overflow"]:
            details += f"- {_t('results.overflow')}\n"
        cols[1].markdown(details)

        cols[2].download_button(
            label=_t("results.download"),
            data=r["png"],
            file_name=name,
            mime="image/png",
            key=f"dl_{it['key']}",
            use_container_width=True,
        )


# ============================================================
# Footer
# ============================================================
with st.expander(_t("footer.about")):
    st.markdown(_t("footer.about_text"))
"""
NEET Test Generator — Streamlit UI entry point.
This file contains only UI layout, inputs, and wiring.
All business logic lives in core/. All rendering lives in ui/.
"""

import json
import logging
import os
import sys

import requests
import streamlit as st
from dotenv import load_dotenv

from core.detector import (
    generate_question_distribution,
    parse_detection_result,
    SYSTEM_PROMPT as DETECTION_PROMPT,
    TOOLS as DETECTION_TOOLS,
)
from core.generator import generate_neet_test_multi_image_batched
from core.utils import encode_image_to_base64_url, images_to_content_dicts
from ui.renderer import render_test_view, render_detection_view

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────
from core.config import OPENAI_API_KEY, OPENAI_MODEL, MAX_OUTPUT_TOKENS, BATCH_SIZE as _BATCH_SIZE
API_KEY    = OPENAI_API_KEY
MODEL      = OPENAI_MODEL
MAX_TOKENS = MAX_OUTPUT_TOKENS
BATCH_SIZE = _BATCH_SIZE

# ── Page setup ────────────────────────────────────────────────────────────
st.set_page_config(page_title="NEET Test Generator", page_icon="📝", layout="wide")
st.title("📝 NEET Test Generator")

if "detection_result" not in st.session_state:
    st.session_state.detection_result = None
if "generator_result" not in st.session_state:
    st.session_state.generator_result = None

# ── Sidebar — inputs ──────────────────────────────────────────────────────
with st.sidebar:
    st.header("🖼️ Image Input")
    input_method = st.radio("Input Method", ["File Upload", "URL"], horizontal=True)

    image_data_list = []
    image_urls = []

    if input_method == "File Upload":
        uploaded_files = st.file_uploader(
            "Upload Images (up to 5)",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
        )
        if uploaded_files:
            for i, f in enumerate(uploaded_files[:5]):
                image_data_list.append(f.getvalue())
                st.image(f, caption=f"Image {i+1}", width="stretch")
    else:
        st.caption("Enter up to 5 image URLs (one per line)")
        url_input = st.text_area(
            "Image URLs",
            placeholder="https://example.com/image1.png",
            height=100,
        )
        if url_input:
            for i, url in enumerate([u.strip() for u in url_input.strip().splitlines() if u.strip()][:5]):
                image_urls.append(url)
                try:
                    r = requests.get(url, timeout=10)
                    if r.status_code == 200:
                        st.image(r.content, caption=f"Image {i+1}", width="stretch")
                        image_data_list.append(r.content)
                except Exception as e:
                    st.error(f"Failed to load image {i+1}: {e}")

    st.divider()
    st.header("⚙️ Settings")

    subject = st.text_input("Subject", value="biology")

    difficulty = st.selectbox("Difficulty", ["easy", "medium", "hard"], index=2)

    question_type = st.selectbox(
        "Question Type",
        ["combination", "mcq", "assertion_reason", "match_the_column"],
        format_func=lambda x: {
            "combination":      "🔀 Combination (Mixed)",
            "mcq":              "🔵 MCQ Only",
            "assertion_reason": "🟣 Assertion-Reason Only",
            "match_the_column": "🟠 Match the Column Only",
        }.get(x, x),
    )

    TYPE_DESCRIPTIONS = {
        ("mcq",              "easy"):   "Direct factual MCQs from single statements",
        ("mcq",              "medium"): "Comprehension-based MCQs combining 2-3 sentences",
        ("mcq",              "hard"):   "Complex analytical MCQs synthesizing multiple concepts",
        ("assertion_reason", "easy"):   "Simple A-R with obvious relationships",
        ("assertion_reason", "medium"): "A-R requiring analysis of cause-effect",
        ("assertion_reason", "hard"):   "Complex A-R with non-obvious relationships",
        ("match_the_column", "easy"):   "Simple matching with 3-4 pairs",
        ("match_the_column", "medium"): "Intermediate matching with 4-5 pairs",
        ("match_the_column", "hard"):   "Complex matching with 5+ pairs",
        ("combination",      "easy"):   "Mixed question types - Easy level",
        ("combination",      "medium"): "Mixed types including mandatory A-R and Match",
        ("combination",      "hard"):   "Mixed types with maximum complexity",
    }
    desc = TYPE_DESCRIPTIONS.get((question_type, difficulty))
    if desc:
        st.caption(f"📝 {desc}")

    question_count = st.slider("Question Count", min_value=1, max_value=20, value=5)

    num_batches = (question_count + BATCH_SIZE - 1) // BATCH_SIZE
    if question_count > BATCH_SIZE:
        st.caption(f"📦 Will generate in {num_batches} batch(es)")

# ── Main — buttons ────────────────────────────────────────────────────────
st.subheader("Upload images in the sidebar, then click a button below")

has_images = bool(image_data_list or image_urls)
if not has_images:
    st.warning("⚠️ Please upload images or provide URLs in the sidebar first")

col1, col2 = st.columns(2)
generate_btn = col1.button("🚀 Generate Test",      type="primary",   disabled=not has_images, width="stretch")
count_btn    = col2.button("📊 Get Question Count", type="secondary",  disabled=not has_images, width="stretch")
st.divider()

# ── Generate Test ─────────────────────────────────────────────────────────
if generate_btn:
    if not API_KEY:
        st.error("OPENAI_API_KEY not set in environment.")
    else:
        image_contents = images_to_content_dicts(image_data_list, image_urls)
        effective_max_tokens = 4000 if question_type == "assertion_reason" else MAX_TOKENS
        effective_batch_size = 10 if (question_type == "assertion_reason" and question_count > 15) else BATCH_SIZE

        logger.info(f"Generate: subject={subject} type={question_type} diff={difficulty} count={question_count} images={len(image_contents)}")
        with st.spinner(f"Generating {question_count} questions across {len(image_contents)} image(s)..."):
            try:
                result = generate_neet_test_multi_image_batched(
                    image_contents=image_contents,
                    subject=subject,
                    difficulty=difficulty,
                    question_count=question_count,
                    question_type=question_type,
                    batch_size=effective_batch_size,
                    model=MODEL,
                    max_tokens=effective_max_tokens,
                    api_key=API_KEY,
                )
                st.session_state.generator_result = result
                st.session_state.detection_result = None
            except Exception as e:
                import traceback
                logger.error(traceback.format_exc())
                st.error(f"{type(e).__name__}: {e}")
                st.code(traceback.format_exc())

# ── Question Count ────────────────────────────────────────────────────────
if count_btn:
    if not API_KEY:
        st.error("OPENAI_API_KEY not set in environment.")
    else:
        img_urls = [encode_image_to_base64_url(d) for d in image_data_list] or image_urls
        with st.spinner("Analysing content..."):
            try:
                raw = generate_question_distribution(
                    image_urls=img_urls,
                    system_prompt=DETECTION_PROMPT,
                    tools=DETECTION_TOOLS,
                    model=MODEL,
                    max_output_tokens=MAX_TOKENS,
                    api_key=API_KEY,
                )
                st.session_state.detection_result = parse_detection_result(raw)
                st.session_state.generator_result = None
            except Exception as e:
                import traceback
                st.error(f"{type(e).__name__}: {e}")
                st.code(traceback.format_exc())

# ── Results ───────────────────────────────────────────────────────────────
if st.session_state.generator_result:
    render_test_view(st.session_state.generator_result)
    with st.expander("📋 Raw JSON"):
        st.code(json.dumps(st.session_state.generator_result, indent=2, ensure_ascii=False), language="json")

if st.session_state.detection_result:
    st.subheader("📊 Question Count Analysis")
    render_detection_view(st.session_state.detection_result)
    with st.expander("📋 Raw Response"):
        st.code(str(st.session_state.detection_result), language="python")

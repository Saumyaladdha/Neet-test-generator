"""
Streamlit UI for Testing NEET Question Generator
"""

import logging
import sys
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging to show in terminal
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

import streamlit as st
import json
import base64
import requests

# Import the modules
from number_of_question_detection import (
    generate_question_distribution,
    SYSTEM_PROMPT as DETECTION_PROMPT,
    TOOLS as DETECTION_TOOLS
)
from test_generator import (
    generate_neet_test,
    generate_neet_test_from_url,
    generate_neet_test_batched,
    generate_neet_test_from_url_batched,
    generate_neet_test_multi_image_batched,
)

# Page config
st.set_page_config(
    page_title="NEET Test Generator",
    page_icon="📝",
    layout="wide"
)

st.title("📝 NEET Test Generator")

# Initialize session state
if "detection_result" not in st.session_state:
    st.session_state.detection_result = None

if "generator_result" not in st.session_state:
    st.session_state.generator_result = None


# Load API key from environment variable
api_key = os.getenv("OPENAI_API_KEY")
model = "gpt-5-mini"
max_tokens = 10000
batch_size = 3

with st.sidebar:
    st.header("🖼️ Image Input")

    input_method = st.radio(
        "Input Method",
        ["File Upload", "URL"],
        horizontal=True
    )

    image_data_list = []
    image_urls = []

    if input_method == "File Upload":
        uploaded_files = st.file_uploader(
            "Upload Images (2-5)",
            type=["png", "jpg", "jpeg", "webp"],
            accept_multiple_files=True,
            help="Upload 2-5 textbook page images"
        )
        if uploaded_files:
            for i, uploaded_file in enumerate(uploaded_files[:5]):  # Max 5 images
                image_data_list.append(uploaded_file.getvalue())
                st.image(uploaded_file, caption=f"Image {i+1}", width="stretch")
    else:
        st.caption("Enter up to 5 image URLs (one per line)")
        url_input = st.text_area(
            "Image URLs",
            placeholder="https://example.com/image1.png\nhttps://example.com/image2.png",
            height=100
        )
        if url_input:
            urls = [u.strip() for u in url_input.strip().split("\n") if u.strip()]
            for i, url in enumerate(urls[:5]):  # Max 5 URLs
                image_urls.append(url)
                try:
                    response = requests.get(url, timeout=10)
                    if response.status_code == 200:
                        st.image(response.content, caption=f"Image {i+1}", width="stretch")
                        image_data_list.append(response.content)
                except Exception as e:
                    st.error(f"Failed to load image {i+1}: {e}")

    st.divider()
    st.header("⚙️ Settings")

    subject = st.text_input("Subject", value="biology")

    difficulty = st.selectbox(
        "Difficulty",
        ["easy", "medium", "hard"],
        index=2
    )

    question_type = st.selectbox(
        "Question Type",
        ["combination", "mcq", "assertion_reason", "match_the_column"],
        index=0,
        format_func=lambda x: {
            "combination": "🔀 Combination (Mixed)",
            "mcq": "🔵 MCQ Only",
            "assertion_reason": "🟣 Assertion-Reason Only",
            "match_the_column": "🟠 Match the Column Only"
        }.get(x, x),
        help="Select question type. Each type uses a specialized prompt optimized for that format."
    )

    # Show description based on selected type and difficulty
    type_descriptions = {
        ("mcq", "easy"): "Direct factual MCQs from single statements",
        ("mcq", "medium"): "Comprehension-based MCQs combining 2-3 sentences",
        ("mcq", "hard"): "Complex analytical MCQs synthesizing multiple concepts",
        ("assertion_reason", "easy"): "Simple A-R with obvious relationships",
        ("assertion_reason", "medium"): "A-R requiring analysis of cause-effect",
        ("assertion_reason", "hard"): "Complex A-R with non-obvious relationships",
        ("match_the_column", "easy"): "Simple matching with 3-4 pairs",
        ("match_the_column", "medium"): "Intermediate matching with 4-5 pairs",
        ("match_the_column", "hard"): "Complex matching with 5+ pairs",
        ("combination", "easy"): "Mixed question types - Easy level",
        ("combination", "medium"): "Mixed types including mandatory A-R and Match",
        ("combination", "hard"): "Mixed types with maximum complexity",
    }
    desc_key = (question_type, difficulty)
    if desc_key in type_descriptions:
        st.caption(f"📝 {type_descriptions[desc_key]}")

    question_count = st.slider(
        "Question Count",
        min_value=1,
        max_value=20,
        value=5
    )

    if question_count > batch_size:
        num_batches = (question_count + batch_size - 1) // batch_size
        st.caption(f"📦 Will generate in {num_batches} batch(es)")


# --- Helper Functions ---
def encode_image_to_base64_url(image_bytes: bytes, media_type: str = "image/png") -> str:
    """Convert image bytes to base64 data URL."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{media_type};base64,{b64}"


def format_detection_result(result) -> str:
    """Format detection result for display."""
    output = []
    args = None

    # Try to extract function call arguments from various response formats
    if hasattr(result, 'output'):
        for item in result.output:
            item_type = getattr(item, 'type', None)
            # Handle 'function_call' type (Responses API)
            if item_type == 'function_call':
                args_raw = getattr(item, 'arguments', None)
                if args_raw:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                break
            # Handle 'tool_calls' or other formats
            elif item_type == 'tool_use' or item_type == 'tool_call':
                args_raw = getattr(item, 'input', None) or getattr(item, 'arguments', None)
                if args_raw:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                break

    # If still no args, try to find in choices (Chat Completions format)
    if not args and hasattr(result, 'choices'):
        for choice in result.choices:
            msg = getattr(choice, 'message', None)
            if msg and hasattr(msg, 'tool_calls') and msg.tool_calls:
                tc = msg.tool_calls[0]
                func = getattr(tc, 'function', None)
                if func:
                    args_raw = getattr(func, 'arguments', None)
                    if args_raw:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                break

    if args:
        output.append(f"### 🟢 Easy Questions: {args.get('easy_count', 'N/A')}")
        output.append(f"**Reasoning:** {args.get('easy_reasoning', 'N/A')}")
        output.append("")
        output.append(f"### 🟡 Medium Questions: {args.get('medium_count', 'N/A')}")
        output.append(f"**Reasoning:** {args.get('medium_reasoning', 'N/A')}")
        output.append("")
        output.append(f"### 🔴 Hard Questions: {args.get('hard_count', 'N/A')}")
        output.append(f"**Reasoning:** {args.get('hard_reasoning', 'N/A')}")
        return "\n".join(output)

    # Fallback: show raw result structure for debugging
    return f"Could not parse response. Raw output types: {[type(item).__name__ + ':' + str(getattr(item, 'type', 'no-type')) for item in getattr(result, 'output', [])]}\n\n{str(result)[:500]}"


def render_latex_text(text: str):
    """Render text that may contain LaTeX. Handles both inline LaTeX and tabular environments."""
    import re

    # Check if text contains LaTeX tabular environment
    if '\\begin{tabular}' in text or '\\begin{tabular}' in text.replace('\\\\', '\\'):
        # Split into parts: before table, table, after table
        # First, normalize the escaped backslashes
        normalized = text.replace('\\\\', '\\')

        # Find the "Match the following:" prefix if present
        prefix_match = re.match(r'^(.*?)(\\begin\{tabular\})', normalized, re.DOTALL)
        if prefix_match:
            prefix = prefix_match.group(1).strip()
            if prefix:
                st.markdown(f"**{prefix}**")

        # Extract and render the tabular part
        tabular_match = re.search(r'\\begin\{tabular\}.*?\\end\{tabular\}', normalized, re.DOTALL)
        if tabular_match:
            table_latex = tabular_match.group(0)
            st.latex(table_latex)
        return

    # Check if text contains inline LaTeX ($...$)
    if '$' in text:
        # For text with inline LaTeX, use st.markdown with latex rendering
        # Streamlit markdown supports $...$ for inline math
        formatted = text.replace('\\n\\n', '\n\n').replace('\\n', '\n')
        st.markdown(formatted)
    else:
        # Plain text
        formatted = text.replace('\\n\\n', '\n\n').replace('\\n', '\n')
        st.markdown(f"**{formatted}**")


def render_test_view(result: dict):
    """Render questions in a proper test paper view using Streamlit components."""

    if "parse_error" in result:
        st.error(f"**Parse Error:** {result.get('parse_error')}")
        st.code(result.get('raw_response', ''), language="text")
        return

    # Test Header
    if "test_metadata" in result:
        meta = result["test_metadata"]

        # Header styled like exam paper
        st.markdown("""
        <div style="text-align: center; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 10px; margin-bottom: 20px;">
            <h2 style="color: white; margin: 0;">📋 NEET Practice Test</h2>
        </div>
        """, unsafe_allow_html=True)

        # Metadata in columns
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("Subject", meta.get('subject', 'N/A').title())
        with col2:
            st.metric("Topic", meta.get('topic', 'N/A')[:20] + "..." if len(str(meta.get('topic', ''))) > 20 else meta.get('topic', 'N/A'))
        with col3:
            st.metric("Difficulty", meta.get('difficulty', 'N/A').title())
        with col4:
            q_type = meta.get('question_type', 'combination')
            q_type_display = {
                "mcq": "MCQ",
                "assertion_reason": "A-R",
                "match_the_column": "Match",
                "combination": "Mixed"
            }.get(q_type, q_type)
            st.metric("Type", q_type_display)
        with col5:
            st.metric("Questions", meta.get('total_questions', 'N/A'))

        if meta.get('content_limitation_note'):
            st.info(f"📌 {meta['content_limitation_note']}")

        if meta.get('batch_info'):
            batch_info = meta['batch_info']
            st.caption(f"🔄 Generated in {batch_info['batches_used']} batch(es): {batch_info['questions_per_batch']}")

        st.markdown("---")

    # Questions
    if "questions" in result:
        questions = result["questions"]

        for idx, q in enumerate(questions):
            q_id = q.get('question_id', idx + 1)
            q_type = q.get('question_type', 'MCQ')
            q_text = q.get('question_text', '')
            correct_answer = q.get('correct_answer', '').lower()

            # Question type badge
            type_colors = {
                'MCQ': '🔵',
                'ASSERTION_REASON': '🟣',
                'MATCH_THE_COLUMN': '🟠'
            }
            type_badge = type_colors.get(q_type, '⚪')

            # Question container
            with st.container():
                st.markdown(f"""
                <div style="background: #f8f9fa; padding: 15px; border-radius: 10px; border-left: 4px solid #667eea; margin-bottom: 10px;">
                    <span style="background: #667eea; color: white; padding: 2px 10px; border-radius: 15px; font-size: 12px; margin-right: 10px;">
                        Q{q_id}
                    </span>
                    <span style="background: #e9ecef; padding: 2px 8px; border-radius: 10px; font-size: 11px;">
                        {type_badge} {q_type.replace('_', ' ')}
                    </span>
                </div>
                """, unsafe_allow_html=True)

                # Question text - handle LaTeX, tables, and multiline
                if q_type == 'MATCH_THE_COLUMN' and ('\\begin{tabular}' in q_text or 'tabular' in q_text):
                    # Render LaTeX table
                    render_latex_text(q_text)
                elif q_type == 'MATCH_THE_COLUMN' and '|' in q_text:
                    # Fallback: Render as markdown table
                    st.markdown(f"**{q_text.split('|')[0].strip()}**")
                    table_part = '|' + '|'.join(q_text.split('|')[1:])
                    st.markdown(table_part)
                elif '$' in q_text or '\\' in q_text:
                    # Contains LaTeX formulas
                    render_latex_text(q_text)
                elif q_type == 'ASSERTION_REASON' and '\\n' in q_text:
                    parts = q_text.replace('\\n\\n', '\n\n').replace('\\n', '\n')
                    st.markdown(f"**{parts}**")
                else:
                    st.markdown(f"**{q_text}**")

                st.write("")

                # Options
                if "options" in q:
                    options = q["options"]

                    for key in ['a', 'b', 'c', 'd']:
                        if key in options:
                            val = options[key]
                            is_correct = key == correct_answer
                            label = key.upper()

                            # Check if option contains LaTeX
                            has_latex = '$' in val or '\\' in val

                            if is_correct:
                                st.markdown(f"""
                                <div style="background: #d4edda; padding: 10px 15px; border-radius: 8px; margin: 5px 0; border: 1px solid #28a745;">
                                    <strong style="color: #155724;">({label})</strong> ✓
                                    <span style="margin-left: 10px; color: #155724;">{val}</span>
                                </div>
                                """, unsafe_allow_html=True)
                            else:
                                st.markdown(f"""
                                <div style="background: #ffffff; padding: 10px 15px; border-radius: 8px; margin: 5px 0; border: 1px solid #dee2e6;">
                                    <strong>({label})</strong>
                                    <span style="margin-left: 10px;">{val}</span>
                                </div>
                                """, unsafe_allow_html=True)

                # Explanation in expander
                if "explanation" in q:
                    with st.expander("💡 View Explanation"):
                        for key, exp in q["explanation"].items():
                            if key.lower() == correct_answer:
                                st.success(f"**({key.upper()})** {exp}")
                            else:
                                st.write(f"**({key.upper()})** {exp}")

                st.markdown("---")

        # Answer Key at the bottom
        if questions:
            st.markdown("### 📝 Answer Key")
            answer_cols = st.columns(min(len(questions), 10))
            for idx, q in enumerate(questions):
                col_idx = idx % 10
                with answer_cols[col_idx]:
                    correct = q.get('correct_answer', '?').upper()
                    st.markdown(f"""
                    <div style="text-align: center; padding: 5px; background: #667eea; color: white; border-radius: 5px; margin: 2px;">
                        <small>Q{q.get('question_id', idx+1)}</small><br>
                        <strong>{correct}</strong>
                    </div>
                    """, unsafe_allow_html=True)
        else:
            st.warning("No questions were generated. Try again or check the image content.")


# --- Main Content ---
st.subheader("Upload images in sidebar, then click a button below")

# Check if images are provided
has_images = len(image_data_list) > 0 or len(image_urls) > 0

if not has_images:
    st.warning("⚠️ Please upload images or provide URLs in the sidebar first")

# Two buttons side by side
col1, col2 = st.columns(2)

with col1:
    generate_btn = st.button(
        "🚀 Generate Test",
        type="primary",
        disabled=not has_images,
        width="stretch"
    )

with col2:
    count_btn = st.button(
        "📊 Get Question Count",
        type="secondary",
        disabled=not has_images,
        width="stretch"
    )

st.divider()

# --- Generate Test ---
if generate_btn:
    logger.info("=== Generate Test button clicked ===")
    if not api_key:
        logger.warning("No API key provided")
        st.error("Please enter your OpenAI API key in the sidebar")
    elif not image_data_list and not image_urls:
        logger.warning("No images provided")
        st.error("Please upload images or provide URLs first")
    else:
        logger.info(f"Starting generation: subject={subject}, difficulty={difficulty}, type={question_type}, count={question_count}, batch_size={batch_size}")
        with st.spinner(f"Generating questions in batches of {batch_size}..."):
            try:
                # Build image content dicts for ALL images
                all_img_contents = []
                if image_data_list:
                    for img_data in image_data_list:
                        b64 = base64.b64encode(img_data).decode("utf-8")
                        all_img_contents.append({
                            "type": "input_image",
                            "image_url": f"data:image/png;base64,{b64}"
                        })
                elif image_urls:
                    for url in image_urls:
                        all_img_contents.append({
                            "type": "input_image",
                            "image_url": url
                        })

                if all_img_contents:
                    logger.info(f"Using {len(all_img_contents)} image(s) for generation")
                    result = generate_neet_test_multi_image_batched(
                        image_contents=all_img_contents,
                        subject=subject,
                        difficulty=difficulty,
                        question_count=question_count,
                        question_type=question_type,
                        batch_size=batch_size,
                        model=model,
                        max_tokens=max_tokens,
                        api_key=api_key
                    )
                else:
                    st.error("No image data available")
                    result = None

                if result:
                    logger.info(f"Generation complete! Got {len(result.get('questions', []))} questions")
                    st.session_state.generator_result = result
                    st.session_state.detection_result = None
            except Exception as e:
                import traceback
                logger.error(f"Generation failed: {type(e).__name__}: {e}")
                logger.error(traceback.format_exc())
                st.error(f"Error: {type(e).__name__}: {e}")
                st.code(traceback.format_exc())

# --- Get Question Count ---
if count_btn:
    if not api_key:
        st.error("Please enter your OpenAI API key in the sidebar")
    else:
        with st.spinner("Analyzing content..."):
            try:
                # Prepare all image URLs
                img_urls = []
                for img_data in image_data_list:
                    img_urls.append(encode_image_to_base64_url(img_data))

                if not img_urls and image_urls:
                    img_urls = image_urls

                result = generate_question_distribution(
                    image_urls=img_urls,
                    system_prompt=DETECTION_PROMPT,
                    tools=DETECTION_TOOLS,
                    model=model,
                    max_output_tokens=max_tokens,
                    api_key=api_key
                )
                st.session_state.detection_result = result
                st.session_state.generator_result = None
            except Exception as e:
                import traceback
                st.error(f"Error: {type(e).__name__}: {e}")
                st.code(traceback.format_exc())

# --- Results Display ---
if st.session_state.generator_result:
    render_test_view(st.session_state.generator_result)

    with st.expander("📋 Raw JSON"):
        st.code(json.dumps(st.session_state.generator_result, indent=2, ensure_ascii=False), language="json")

if st.session_state.detection_result:
    st.subheader("📊 Question Count Analysis")
    formatted = format_detection_result(st.session_state.detection_result)
    st.markdown(formatted)

    with st.expander("📋 Raw Response"):
        st.code(str(st.session_state.detection_result), language="python")

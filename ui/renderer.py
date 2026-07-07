"""
Streamlit rendering components for the NEET Test Generator UI.
All functions here use Streamlit — no business logic lives here.
"""

import re
import streamlit as st


def render_latex_text(text: str):
    """Render text that may contain LaTeX, including tabular environments."""
    if '\\begin{tabular}' in text or '\\begin{tabular}' in text.replace('\\\\', '\\'):
        normalized = text.replace('\\\\', '\\')
        prefix_match = re.match(r'^(.*?)(\\begin\{tabular\})', normalized, re.DOTALL)
        if prefix_match:
            prefix = prefix_match.group(1).strip()
            if prefix:
                st.markdown(f"**{prefix}**")
        tabular_match = re.search(r'\\begin\{tabular\}.*?\\end\{tabular\}', normalized, re.DOTALL)
        if tabular_match:
            st.latex(tabular_match.group(0))
        return

    formatted = text.replace('\\n\\n', '\n\n').replace('\\n', '\n')
    if '$' in text:
        st.markdown(formatted)
    else:
        st.markdown(f"**{formatted}**")


def render_test_view(result: dict):
    """Render a full test paper from a generation result dict."""
    if "parse_error" in result:
        st.error(f"**Parse Error:** {result.get('parse_error')}")
        st.code(result.get('raw_response', ''), language="text")
        return

    if "test_metadata" in result:
        meta = result["test_metadata"]
        st.markdown("""
        <div style="text-align:center;padding:20px;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);border-radius:10px;margin-bottom:20px;">
            <h2 style="color:white;margin:0;">📋 NEET Practice Test</h2>
        </div>
        """, unsafe_allow_html=True)

        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.metric("Subject", meta.get('subject', 'N/A').title())
        with col2:
            topic = str(meta.get('topic', 'N/A'))
            st.metric("Topic", topic[:20] + "..." if len(topic) > 20 else topic)
        with col3:
            st.metric("Difficulty", meta.get('difficulty', 'N/A').title())
        with col4:
            q_type_display = {
                "mcq": "MCQ", "assertion_reason": "A-R",
                "match_the_column": "Match", "combination": "Mixed"
            }.get(meta.get('question_type', ''), meta.get('question_type', 'N/A'))
            st.metric("Type", q_type_display)
        with col5:
            st.metric("Questions", meta.get('total_questions', 'N/A'))

        if meta.get('content_limitation_note'):
            st.info(f"📌 {meta['content_limitation_note']}")
        if meta.get('batch_info'):
            bi = meta['batch_info']
            st.caption(f"🔄 Generated in {bi['batches_used']} batch(es): {bi['questions_per_batch']}")
        st.markdown("---")

    questions = result.get("questions", [])
    if not questions:
        st.warning("No questions were generated. Try again or check the image content.")
        return

    type_badge = {'MCQ': '🔵', 'ASSERTION_REASON': '🟣', 'MATCH_THE_COLUMN': '🟠'}

    for idx, q in enumerate(questions):
        q_id = q.get('question_id', idx + 1)
        q_type = q.get('question_type', 'MCQ')
        q_text = q.get('question_text', '')
        correct_answer = q.get('correct_answer', '').lower()

        with st.container():
            st.markdown(f"""
            <div style="background:#f8f9fa;padding:15px;border-radius:10px;border-left:4px solid #667eea;margin-bottom:10px;">
                <span style="background:#667eea;color:white;padding:2px 10px;border-radius:15px;font-size:12px;margin-right:10px;">Q{q_id}</span>
                <span style="background:#e9ecef;padding:2px 8px;border-radius:10px;font-size:11px;">
                    {type_badge.get(q_type, '⚪')} {q_type.replace('_', ' ')}
                </span>
            </div>
            """, unsafe_allow_html=True)

            if q_type == 'MATCH_THE_COLUMN' and ('\\begin{tabular}' in q_text or 'tabular' in q_text):
                render_latex_text(q_text)
            elif q_type == 'MATCH_THE_COLUMN' and '|' in q_text:
                st.markdown(f"**{q_text.split('|')[0].strip()}**")
                st.markdown('|' + '|'.join(q_text.split('|')[1:]))
            elif '$' in q_text or '\\' in q_text:
                render_latex_text(q_text)
            elif q_type == 'ASSERTION_REASON' and '\\n' in q_text:
                st.markdown(f"**{q_text.replace('\\n\\n', chr(10)+chr(10)).replace('\\n', chr(10))}**")
            else:
                st.markdown(f"**{q_text}**")

            st.write("")

            for key in ['a', 'b', 'c', 'd']:
                val = q.get('options', {}).get(key)
                if val is None:
                    continue
                if key == correct_answer:
                    st.markdown(f"""
                    <div style="background:#d4edda;padding:10px 15px;border-radius:8px;margin:5px 0;border:1px solid #28a745;">
                        <strong style="color:#155724;">({key.upper()})</strong> ✓
                        <span style="margin-left:10px;color:#155724;">{val}</span>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div style="background:#ffffff;padding:10px 15px;border-radius:8px;margin:5px 0;border:1px solid #dee2e6;">
                        <strong>({key.upper()})</strong>
                        <span style="margin-left:10px;">{val}</span>
                    </div>
                    """, unsafe_allow_html=True)

            if "explanation" in q:
                with st.expander("💡 View Explanation"):
                    for key, exp in q["explanation"].items():
                        if key.lower() == correct_answer:
                            st.success(f"**({key.upper()})** {exp}")
                        else:
                            st.write(f"**({key.upper()})** {exp}")

            st.markdown("---")

    st.markdown("### 📝 Answer Key")
    answer_cols = st.columns(min(len(questions), 10))
    for idx, q in enumerate(questions):
        with answer_cols[idx % 10]:
            correct = q.get('correct_answer', '?').upper()
            st.markdown(f"""
            <div style="text-align:center;padding:5px;background:#667eea;color:white;border-radius:5px;margin:2px;">
                <small>Q{q.get('question_id', idx+1)}</small><br>
                <strong>{correct}</strong>
            </div>
            """, unsafe_allow_html=True)


def render_detection_view(parsed: dict):
    """Render the question count analysis result."""
    if "error" in parsed:
        st.error(f"Could not parse response: {parsed['error']}")
        st.code(str(parsed.get('raw_preview', '')), language="text")
        return

    st.markdown(f"### 🟢 Easy Questions: {parsed['easy_count']}")
    st.markdown(f"**Reasoning:** {parsed['easy_reasoning']}")
    st.markdown(f"### 🟡 Medium Questions: {parsed['medium_count']}")
    st.markdown(f"**Reasoning:** {parsed['medium_reasoning']}")
    st.markdown(f"### 🔴 Hard Questions: {parsed['hard_count']}")
    st.markdown(f"**Reasoning:** {parsed['hard_reasoning']}")

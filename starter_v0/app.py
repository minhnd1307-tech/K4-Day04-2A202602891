from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from chat import (
    now_iso,
    safe_slug,
    trim_history,
    run_model_tool_loop,
    write_transcript,
)
from env_loader import load_lab_env
from providers import make_provider
from tools import load_tool_declarations, to_openai_tools
from versioning import artifact_version_dict, build_artifact_version

ROOT = Path(__file__).resolve().parent
ARTIFACTS_DIR = ROOT / "artifacts"
TRANSCRIPTS_DIR = ROOT / "transcripts"
load_lab_env(ROOT)

st.set_page_config(
    page_title="IT Helpdesk Agent — Live Chat",
    page_icon="🛠️",
    layout="wide",
)

st.title("🛠️ IT Helpdesk Agent — Live Chat & Diagnostics")
st.caption("Northstar Labs Mock IT Service Desk | Day 04 Lab — Evidence-Based Tool Calling")

# Sidebar Configuration
with st.sidebar:
    st.header("⚙️ Agent Settings")
    provider_name = st.selectbox(
        "Model Provider",
        options=["openai", "openrouter", "anthropic", "gemini"],
        index=0,
    )
    custom_model = st.text_input(
        "Model override (optional)",
        value="",
        placeholder="e.g. gpt-4o-mini",
        help="Để trống để dùng model mặc định của provider.",
    )
    version_label = st.text_input(
        "Version label",
        value="v3",
        help="Nhãn phiên bản để log transcript (v0, v1, v2, v3...)",
    )
    
    st.divider()
    st.subheader("📄 Artifacts & Hashing")
    prompt_path = ARTIFACTS_DIR / "system_prompt.md"
    tools_path = ARTIFACTS_DIR / "tools.yaml"

    if prompt_path.exists() and tools_path.exists():
        art_ver = build_artifact_version(version_label, prompt_path, tools_path)
        st.success(f"**Artifact Version:** `{art_ver.artifact_version}`")
        st.text(f"Prompt hash: {art_ver.prompt_hash[:12]}")
        st.text(f"Tools hash:  {art_ver.tools_hash[:12]}")
    else:
        st.error("Missing system_prompt.md or tools.yaml!")
        st.stop()

    st.divider()
    history_window = st.slider("History Window (turns)", min_value=1, max_value=10, value=5)
    max_tool_rounds = st.slider("Max Tool Rounds", min_value=1, max_value=8, value=4)

    if st.button("🗑️ Reset Chat & New Session", use_container_width=True):
        st.session_state.clear()
        st.rerun()

# Initialize Session State
if "messages" not in st.session_state:
    st.session_state.messages = []  # list of {"role": "user"|"assistant", "content": str, "tool_events": list, "rounds": list}

if "transcript_id" not in st.session_state:
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    transcript_id = "_".join([
        safe_slug(version_label),
        safe_slug(provider_name),
        timestamp,
    ])
    st.session_state.transcript_id = transcript_id
    st.session_state.transcript_path = TRANSCRIPTS_DIR / f"{transcript_id}.transcript.json"
    st.session_state.transcript_data = {
        "transcript_id": transcript_id,
        **artifact_version_dict(art_ver),
        "provider": provider_name,
        "model": custom_model or None,
        "system_prompt": str(prompt_path),
        "tools": str(tools_path),
        "history_window": history_window,
        "max_tool_rounds": max_tool_rounds,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "turns": [],
    }

# Display existing messages
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        # If assistant has tool calls in this turn, display them in an expander
        if msg["role"] == "assistant" and msg.get("rounds"):
            for r in msg["rounds"]:
                calls = r.get("tool_calls", [])
                results = r.get("tool_results", [])
                if calls:
                    with st.expander(f"🔍 Round {r.get('round', 1)}: {len(calls)} Tool Call(s)", expanded=False):
                        for call, res in zip(calls, results or [{}] * len(calls)):
                            c_name = call.get("name")
                            c_args = call.get("args", {})
                            c_res = res.get("result", {})
                            is_err = "error" in c_res
                            st.markdown(f"**Tool:** `{'⚠️ ' if is_err else '✅ '}{c_name}`")
                            st.caption("Arguments:")
                            st.json(c_args, expanded=False)
                            st.caption("Result:")
                            st.json(c_res, expanded=False)
        st.markdown(msg["content"])

# Chat input
if user_prompt := st.chat_input("Nhập câu hỏi hoặc sự cố IT cần hỗ trợ..."):
    # Display user message
    st.chat_message("user").markdown(user_prompt)
    st.session_state.messages.append({"role": "user", "content": user_prompt})

    # Prepare model call
    system_prompt_text = prompt_path.read_text(encoding="utf-8")
    tool_declarations = load_tool_declarations(tools_path)
    openai_tools = to_openai_tools(tool_declarations)

    try:
        provider = make_provider(provider_name)
    except Exception as exc:
        st.error(f"Failed to initialize provider {provider_name}: {exc}")
        st.stop()

    # Reconstruct history for model
    dialog_history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.messages[:-1]
    ]

    working_messages = [
        {"role": "system", "content": system_prompt_text},
        *trim_history(dialog_history, history_window),
        {"role": "user", "content": user_prompt},
    ]

    turn_index = len(st.session_state.transcript_data["turns"]) + 1
    turn_record: dict[str, Any] = {
        "turn_index": turn_index,
        "started_at": now_iso(),
        "user": user_prompt,
        "status": "started",
        "assistant_text": None,
        "rounds": [],
        "tool_events": [],
    }

    with st.chat_message("assistant"):
        status_placeholder = st.empty()
        with status_placeholder.container():
            st.info("Đang xử lý yêu cầu và kiểm tra tool...")

        try:
            loop_result = run_model_tool_loop(
                provider=provider,
                messages=working_messages,
                tools=openai_tools,
                model=custom_model or None,
                max_tool_rounds=max_tool_rounds,
            )
            status_placeholder.empty()

            rounds = loop_result.get("rounds", [])
            assistant_reply = loop_result.get("assistant_text", "")

            # Show tool details
            for r in rounds:
                calls = r.get("tool_calls", [])
                results = r.get("tool_results", [])
                if calls:
                    with st.expander(f"🔍 Round {r.get('round', 1)}: {len(calls)} Tool Call(s)", expanded=True):
                        for call, res in zip(calls, results or [{}] * len(calls)):
                            c_name = call.get("name")
                            c_args = call.get("args", {})
                            c_res = res.get("result", {})
                            is_err = "error" in c_res
                            st.markdown(f"**Tool:** `{'⚠️ ' if is_err else '✅ '}{c_name}`")
                            st.caption("Arguments:")
                            st.json(c_args, expanded=False)
                            st.caption("Result:")
                            st.json(c_res, expanded=False)

            st.markdown(assistant_reply)

            # Record turn
            turn_record.update(loop_result)
            turn_record["ended_at"] = now_iso()
            st.session_state.transcript_data["turns"].append(turn_record)

            # Save transcript to disk
            write_transcript(st.session_state.transcript_path, st.session_state.transcript_data)

            # Append to session state messages
            st.session_state.messages.append({
                "role": "assistant",
                "content": assistant_reply,
                "rounds": rounds,
                "tool_events": loop_result.get("tool_events", []),
            })

        except Exception as exc:
            status_placeholder.empty()
            err_msg = f"{type(exc).__name__}: {str(exc)}"
            st.error(f"Lỗi thực thi: {err_msg}")
            turn_record.update({"status": "provider_error", "error": err_msg, "ended_at": now_iso()})
            st.session_state.transcript_data["turns"].append(turn_record)
            write_transcript(st.session_state.transcript_path, st.session_state.transcript_data)

with st.sidebar:
    st.divider()
    st.caption(f"📁 Transcript: `{st.session_state.transcript_path.name}`")
    if st.session_state.transcript_path.exists():
        transcript_bytes = st.session_state.transcript_path.read_bytes()
        st.download_button(
            "⬇️ Tải file Transcript JSON",
            data=transcript_bytes,
            file_name=st.session_state.transcript_path.name,
            mime="application/json",
            use_container_width=True,
        )

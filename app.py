import os
import time
import json
import sqlite3
import pandas as pd
import streamlit as st
from google import genai
from google.genai import types

from setup_sandbox import init_sandbox, DB_PATH, INBOX_DIR
from tools import (
    list_inbox_files,
    read_inbox_file,
    query_internal_db,
    register_new_vendor,
    enter_invoice_into_erp,
    verify_invoice_record,
    ask_human,
)

# Map tool names to actual Python functions
TOOL_MAP = {
    "list_inbox_files": list_inbox_files,
    "read_inbox_file": read_inbox_file,
    "query_internal_db": query_internal_db,
    "register_new_vendor": register_new_vendor,
    "enter_invoice_into_erp": enter_invoice_into_erp,
    "verify_invoice_record": verify_invoice_record,
    "ask_human": ask_human,
}

SYSTEM_PROMPT = """You are an Autonomous AI Back-Office Task Worker for a company.
Your goal is to complete user requests accurately by interacting with the company inbox (`inbox/`) and internal ERP SQLite database.

OPERATING PRINCIPLES:
1. AUTONOMY: Break complex goals into logical steps. Discover files using `list_inbox_files` and inspect them with `read_inbox_file`.
2. DATE AWARENESS: When asked for the "latest" invoice from a vendor, inspect all relevant files for that vendor and compare their issue dates before choosing which one to process.
3. ERROR RECOVERY (SELF-HEALING): If a tool returns an error (such as `VENDOR_NOT_REGISTERED`), analyze the error message, find the missing vendor details (email, tax_id) from the invoice text, call `register_new_vendor`, and then retry `enter_invoice_into_erp`.
4. HUMAN-IN-THE-LOOP: Never guess when financial data is ambiguous (e.g., two conflicting dollar amounts in an email) or when a security policy blocks an action (e.g., invoices over $5,000). Call `ask_human` to get clarification or approval. Only set `human_approved=True` if the user explicitly approves in this conversation.
5. MANDATORY VERIFICATION: Whenever you insert or modify data in the ERP database, you MUST call `verify_invoice_record` (or `query_internal_db`) BEFORE giving your final answer to verify that the requested outcome was actually achieved.
6. FINAL REPORT: End with a concise summary that includes:
   - What actions were taken (including any error recovery or approvals).
   - The exact extracted details (Invoice ID, Vendor, Amount, Due Date).
   - Verification evidence (the exact database record verified).
"""

# Ensure sandbox exists on startup
if not os.path.exists(DB_PATH):
    init_sandbox()

st.set_page_config(page_title="Autonomous AI Task Worker", page_icon="🤖", layout="wide")

# Initialize Streamlit Session State
if "trace" not in st.session_state:
    st.session_state.trace = []          # Visual log of steps for the UI
if "contents" not in st.session_state:
    st.session_state.contents = []       # Raw Gemini conversation memory
if "waiting_for_human" not in st.session_state:
    st.session_state.waiting_for_human = False
if "pending_tool_call_id" not in st.session_state:
    st.session_state.pending_tool_call_id = None
if "pending_question" not in st.session_state:
    st.session_state.pending_question = ""
if "agent_finished" not in st.session_state:
    st.session_state.agent_finished = False


def run_agent_loop(api_key: str, max_steps: int = 12):
    """Executes the ReAct (Reason + Act + Observe) loop until completion or human pause."""
    client = genai.Client(api_key=api_key)
    
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=list(TOOL_MAP.values()),
        temperature=0.1,
        # Disable automatic function calling so we can control the loop, log steps, and pause on ask_human
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    steps = 0
    while steps < max_steps:
        steps += 1

        # 1. REASON: Ask Gemini what to do next based on working memory
        # Automatic retry with exponential backoff for 503 (Server Busy) and 429 (Rate Limit)
        response = None
        for attempt in range(4):
            try:
                response = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=st.session_state.contents,
                    config=config,
                )
                break
            except Exception as e:
                err_str = str(e)
                if ("503" in err_str or "429" in err_str) and attempt < 3:
                    wait_time = 15 * (attempt + 1)
                    st.toast(f"Rate limit or server busy. Pausing for {wait_time}s before retrying...", icon="⏳")
                    time.sleep(wait_time)
                else:
                    raise e

        candidate = response.candidates[0]
        model_content = candidate.content
        st.session_state.contents.append(model_content)

        # Extract any text reasoning ("Thought" or final answer)
        thought_texts = [part.text for part in model_content.parts if part.text]
        function_calls = [part.function_call for part in model_content.parts if part.function_call]

        # If there are no tool calls, the agent has finished its task!
        if not function_calls:
            final_text = "\n".join(thought_texts) if thought_texts else "Task completed."
            st.session_state.trace.append({"type": "final", "content": final_text})
            st.session_state.agent_finished = True
            return

        # Log intermediate thought if the model emitted one alongside a tool call
        if thought_texts:
            st.session_state.trace.append({"type": "thought", "content": "\n".join(thought_texts)})

        # 2. ACT & 3. OBSERVE: Execute each requested tool call
        tool_response_parts = []
        for fn_call in function_calls:
            fn_name = fn_call.name
            fn_args = dict(fn_call.args) if fn_call.args else {}

            st.session_state.trace.append({
                "type": "action",
                "tool": fn_name,
                "args": fn_args
            })

            # Special handling if the AI decides it needs human clarification/approval
            if fn_name == "ask_human":
                question = fn_args.get("question", "Could you please clarify or approve this action?")
                st.session_state.waiting_for_human = True
                st.session_state.pending_question = question
                st.session_state.pending_tool_call_name = fn_name
                st.session_state.trace.append({
                    "type": "human_prompt",
                    "question": question
                })
                return  # Pause execution loop and wait for user UI input!

            # Execute the actual Python function
            tool_fn = TOOL_MAP.get(fn_name)
            if not tool_fn:
                result = {"status": "error", "message": f"Unknown tool '{fn_name}'"}
            else:
                try:
                    result = tool_fn(**fn_args)
                except Exception as e:
                    result = {"status": "error", "message": f"Tool execution exception: {str(e)}"}

            st.session_state.trace.append({
                "type": "observation",
                "tool": fn_name,
                "result": result
            })

            tool_response_parts.append(
                types.Part.from_function_response(
                    name=fn_name,
                    response={"result": result}
                )
            )

            
        # Append all tool observations back into the agent's working memory
        st.session_state.contents.append(
            types.Content(role="user", parts=tool_response_parts)
        )
        time.sleep(2)  # Pacing pause to stay within free-tier RPM limits

    st.session_state.trace.append({
        "type": "error",
        "content": "Maximum step limit (12) reached. Stopping execution for safety."
    })


# ==========================================
# STREAMLIT SIDEBAR & CONFIGURATION
# ==========================================
with st.sidebar:
    st.title("⚙️ Worker Controls")
    api_key = st.text_input(
        "Google Gemini API Key",
        value=os.environ.get("GEMINI_API_KEY", ""),
        type="password",
        help="Get a free API key from https://aistudio.google.com/app/apikey"
    )

    st.divider()
    st.subheader("🔄 Environment Reset")
    if st.button("Reset Database & Inbox Sandbox", use_container_width=True):
        init_sandbox()
        st.session_state.trace = []
        st.session_state.contents = []
        st.session_state.waiting_for_human = False
        st.session_state.agent_finished = False
        st.success("Sandbox reset to clean state!")
        st.rerun()

    st.divider()
    st.markdown("""
    **Evaluation Criteria Demonstrated:**
    - **Autonomy:** Multi-step discovery & execution
    - **Reliability:** Auto-recovers from foreign-key DB errors
    - **Safety/HITL:** Pauses on >$5k policy or ambiguous data
    - **Verification:** Queries DB row proof before completing
    """)


# ==========================================
# MAIN LAYOUT: LEFT (AGENT) | RIGHT (ERP DB)
# ==========================================
st.title("🤖 Autonomous AI Back-Office Task Worker")
st.caption("A ReAct (Reason + Act + Observe + Verify) Agent Prototype interacting with a local File Inbox & SQLite ERP System.")

col_agent, col_env = st.columns([1.15, 1], gap="large")

with col_agent:
    st.subheader("1. Assign a Natural Language Task")

    # Preset Demo Scenarios
    st.markdown("**Quick Demo Scenarios (Click to populate):**")
    p1, p2 = st.columns(2)
    p3, p4 = st.columns(2)

    default_task = st.session_state.get(
        "task_input",
        "Find the latest invoice from Acme Corp, extract the amount and due date, enter it into our internal system, and tell me once it is done."
    )

    if p1.button("🟢 Demo 1: Standard (Acme Corp)", use_container_width=True):
        st.session_state.task_input = "Find the latest invoice from Acme Corp, extract the amount and due date, enter it into our internal system, and tell me once it is done."
        st.rerun()
    if p2.button("🟡 Demo 2: Error Recovery (Globex)", use_container_width=True):
        st.session_state.task_input = "Find the invoice from Globex Inc in the inbox, enter it into our ERP system, and verify it was recorded."
        st.rerun()
    if p3.button("🟠 Demo 3: High-Value Policy (Stark)", use_container_width=True):
        st.session_state.task_input = "Process the latest Stark Industries invoice into the internal system and confirm completion."
        st.rerun()
    if p4.button("🔵 Demo 4: Ambiguous Email (Wayne)", use_container_width=True):
        st.session_state.task_input = "Read the email from Wayne Enterprises, enter their invoice into the ERP system, and confirm when done."
        st.rerun()

    task_prompt = st.text_area("Natural Language Goal:", value=default_task, height=85)

    if st.button("🚀 Dispatch Autonomous Worker", type="primary", use_container_width=True):
        if not api_key:
            st.error("Please enter your Google Gemini API Key in the left sidebar first!")
        else:
            # Reset conversation state for new task
            st.session_state.trace = [{"type": "goal", "content": task_prompt}]
            st.session_state.contents = [
                types.Content(role="user", parts=[types.Part.from_text(text=task_prompt)])
            ]
            st.session_state.waiting_for_human = False
            st.session_state.agent_finished = False
            with st.spinner("Agent is reasoning and executing tools..."):
                run_agent_loop(api_key)
            st.rerun()

    # Render Execution Trace
    st.subheader("2. Live Execution Trace (ReAct Loop)")
    if not st.session_state.trace:
        st.info("Select a demo scenario above and click **Dispatch Autonomous Worker** to watch the agent work.")

    for step in st.session_state.trace:
        step_type = step["type"]
        if step_type == "goal":
            st.chat_message("user").write(f"**Goal:** {step['content']}")
        elif step_type == "thought":
            with st.chat_message("assistant"):
                st.markdown(f"🧠 **Thought:** {step['content']}")
        elif step_type == "action":
            with st.status(f"🛠️ **Action:** Calling `{step['tool']}()`", state="complete", expanded=False):
                st.json(step["args"])
        elif step_type == "observation":
            res = step["result"]
            status_flag = res.get("status", "")
            if status_flag in ("error", "blocked_by_policy", "unverified"):
                with st.status(f"⚠️ **Observation (`{step['tool']}`):** `{status_flag.upper()}` — Agent will adapt", state="error", expanded=True):
                    st.json(res)
            elif status_flag == "verified":
                with st.status(f"✅ **Verification Proof (`{step['tool']}`):** Outcome Verified in DB!", state="complete", expanded=True):
                    st.json(res)
            else:
                with st.status(f"👁️ **Observation (`{step['tool']}`):** Success", state="complete", expanded=False):
                    st.json(res)
        elif step_type == "human_prompt":
            st.warning(f"⏸️ **Agent Paused — Human Input Required:**\n\n{step['question']}")
        elif step_type == "human_reply":
            st.chat_message("user").write(f"👤 **Human Response:** {step['content']}")
        elif step_type == "final":
            st.success("🎯 **Task Completed & Verified!**")
            with st.chat_message("assistant"):
                st.markdown(step["content"])
        elif step_type == "error":
            st.error(step["content"])

    # Human-in-the-loop Input Box (Only appears when the agent calls `ask_human`)
    if st.session_state.waiting_for_human:
        with st.form("human_approval_form"):
            human_answer = st.text_input(
                "Your Response / Approval:",
                placeholder="e.g., 'Yes, I approve entering this $12,500 invoice' or 'Use $1,200.00'"
            )
            submitted = st.form_submit_button("Send Response & Resume Agent")
            if submitted and human_answer.strip():
                st.session_state.trace.append({"type": "human_reply", "content": human_answer})
                st.session_state.waiting_for_human = False

                # Send the human's answer back to Gemini as the output of ask_human()
                st.session_state.contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=st.session_state.pending_tool_call_name,
                                response={"result": {"status": "human_responded", "human_input": human_answer}}
                            )
                        ]
                    )
                )
                with st.spinner("Resuming autonomous execution..."):
                    run_agent_loop(api_key)
                st.rerun()


# ==========================================
# RIGHT COLUMN: LIVE ERP DATABASE & INBOX
# ==========================================
with col_env:
    st.subheader("3. Live Company Environment")
    tab_db, tab_inbox, tab_audit = st.tabs([
        "🗄️ ERP Database Tables",
        "📬 Inbox Files (5)",
        "📜 System Audit Log"
    ])

    conn = sqlite3.connect(DB_PATH)

    with tab_db:
        st.markdown("#### `invoices` Table (Internal System)")
        df_invoices = pd.read_sql_query("SELECT * FROM invoices", conn)
        st.dataframe(df_invoices, use_container_width=True, hide_index=True)

        st.markdown("#### `vendors` Table (Approved Vendors)")
        df_vendors = pd.read_sql_query("SELECT * FROM vendors", conn)
        st.dataframe(df_vendors, use_container_width=True, hide_index=True)

    with tab_inbox:
        st.caption("Unstructured documents & emails sitting in the local `inbox/` directory:")
        if os.path.exists(INBOX_DIR):
            for fname in sorted(os.listdir(INBOX_DIR)):
                with st.expander(f"📄 {fname}"):
                    with open(os.path.join(INBOX_DIR, fname), "r", encoding="utf-8") as f:
                        st.code(f.read(), language="text")

    with tab_audit:
        st.caption("Real-time database write & verification events:")
        df_audit = pd.read_sql_query("SELECT * FROM audit_logs ORDER BY id DESC", conn)
        st.dataframe(df_audit, use_container_width=True, hide_index=True)

    conn.close()
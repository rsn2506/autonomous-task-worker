# 🤖 Autonomous AI Back-Office Task Worker

An autonomous, self-healing AI agent prototype built around a custom **ReAct (Reason + Act + Observe + Verify)** execution loop. Given a high-level natural language goal, the worker autonomously explores an unstructured document inbox (`inbox/`), extracts relevant financial entities, interacts with a relational ERP database (`company_erp.db`), recovers from database constraint errors, pauses for human approval when safety thresholds or ambiguities arise, and verifies state changes before concluding.

🔗 **Demo Video:** [https://drive.google.com/drive/folders/1-IXQkcvXbKu4Rc27LYlo6fUYuDciFSMa?usp=drive_link]

---

## 1. Setup and Run Instructions

### Prerequisites
* Python 3.9+
* A free [Google Gemini API Key](https://aistudio.google.com/app/apikey)

### Installation
```bash
# 1. Clone the repository
git clone [https://github.com/](https://github.com/)<your-username>/autonomous-task-worker.git
cd autonomous-task-worker

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Initialize the simulated ERP database and sample inbox files
python setup_sandbox.py

# 5. Launch the interactive Streamlit interface
streamlit run app.py
```
Once the app opens at `http://localhost:8501`, enter your `GEMINI_API_KEY` in the sidebar (or set `export GEMINI_API_KEY="your_key"` in your environment) and click **Dispatch Autonomous Worker**.

---

## 2. Architecture Overview

Rather than relying on heavy, opaque orchestration frameworks (such as LangChain or AutoGen), this prototype implements a transparent **State-Space ReAct Loop** directly in Python using native LLM tool calling (`gemini-2.5-flash`).

```text
User Goal ──► [ Working Memory (Conversation State) ]
                        │
                        ▼
              ┌───────────────────┐
              │ 1. REASON (LLM)   │ ◄── Evaluates goal + past observations
              └─────────┬─────────┘
                        │ Emits Thought + Tool Call (or Final Summary)
                        ▼
              ┌───────────────────┐
              │ 2. ACT (Executor) │ ◄── Executes Python tool on OS / SQLite DB
              └─────────┬─────────┘     (Intercepts `ask_human` to pause loop)
                        │
                        ▼
              ┌───────────────────┐
              │ 3. OBSERVE        │ ◄── Captures return payload or structured error
              └─────────┬─────────┘     Appends back into Working Memory
                        │
                        └──────► Loops until outcome is verified via `verify_invoice_record`
```

### Core Components
* **`setup_sandbox.py` (Environment Generator):** Initializes a local SQLite ERP database (`vendors`, `invoices`, `audit_logs` tables) and populates `inbox/` with unstructured `.txt` invoices and emails containing realistic edge cases.
* **`tools.py` (Tool & Safety Layer):** Exposes 7 atomic tools (`list_inbox_files`, `read_inbox_file`, `query_internal_db`, `register_new_vendor`, `enter_invoice_into_erp`, `verify_invoice_record`, `ask_human`). Enforces relational integrity and business policy guardrails at the code level.
* **`app.py` (ReAct Orchestrator & Live Inspection UI):** Manages working memory (`st.session_state.contents`), executes the step-bounded `while` loop, handles mid-flight suspension for Human-in-the-Loop (HITL) prompts, and renders side-by-side visibility into the live database tables and execution trace.

---

## 3. Important Technical & Design Decisions

1. **Custom Orchestration Loop (`automatic_function_calling=False`):**
   By disabling the SDK's black-box auto-execution and writing an explicit `while steps < max_steps` loop, the system achieves deterministic step limits (`max_steps=12`), full observability of intermediate thoughts/observations, and the ability to cleanly suspend execution mid-task when `ask_human()` is invoked.
2. **Errors as Structured Observations (Self-Healing Execution):**
   Instead of letting Python exceptions crash the program, tools catch operational failures (such as a missing foreign key `VENDOR_NOT_REGISTERED` when inserting an invoice for a new vendor) and return structured diagnostic JSON to the model. The agent reads the error, autonomously extracts the vendor's tax ID and email from the source document, calls `register_new_vendor()`, and retries `enter_invoice_into_erp()`.
3. **Hard Policy Guardrails in Code, Not Just Prompts:**
   Prompt instructions alone cannot guarantee safety for high-stakes actions. The $5,000 autonomous spending limit is enforced directly inside `enter_invoice_into_erp()` in `tools.py`. Even if the LLM attempts to insert a $12,500 invoice without asking, the tool rejects the call with `APPROVAL_REQUIRED` unless `human_approved=True` has been explicitly unlocked via user interaction.
4. **Mandatory Post-Action Verification:**
   Write confirmation messages can be misleading. The agent is instructed and equipped with `verify_invoice_record(invoice_id)` to perform an independent `SELECT` read against the database after insertion, returning the exact persisted row as verifiable evidence in the final report.

---

## 4. Built-in Evaluation Scenarios

The prototype ships with 4 one-click scenarios demonstrating the evaluation criteria:
* **Demo 1 — Multi-File Discovery & Date Reasoning (Acme Corp):** Inbox contains two Acme Corp documents (August and October). The agent lists files, inspects both, compares issue dates, extracts the October invoice (`INV-2026-089`), inserts it, and verifies the database row.
* **Demo 2 — Autonomous Error Recovery (Globex Inc):** `Globex Inc` is not yet registered in the `vendors` table. When `enter_invoice_into_erp` fails with `VENDOR_NOT_REGISTERED`, the agent autonomously calls `register_new_vendor` using metadata from the invoice and retries insertion.
* **Demo 3 — Policy Guardrail & Human Approval (Stark Industries):** Invoice total is $12,500.00 (exceeding the $5,000 policy threshold). The tool blocks insertion, the agent pauses execution via `ask_human`, waits for manager approval in the UI, and resumes to complete and verify the task.
* **Demo 4 — Ambiguity Detection (Wayne Enterprises):** An email mentions two possible totals ($1,200.00 vs. $1,650.00 with overtime surcharge). Rather than guessing financial data, the agent pauses and asks the user which amount to record.

---

## 5. Assumptions Made
* **Local Sandbox Scope:** Unstructured inputs are represented as plain-text documents/emails in a local directory (`inbox/`), and the company's internal ERP system is represented as a relational SQLite database (`company_erp.db`).
* **Single-User Session:** Human-in-the-loop approvals given in the active UI session are treated as authorized manager sign-off.
* **Deterministic Schema:** While tasks vary in natural language, the internal ERP schema (`vendors`, `invoices`, `audit_logs`) represents the fixed target system of record.

---

## 6. Known Limitations
* **No OCR / Vision Pipeline Yet:** The current inbox reader processes `.txt` and `.json` files rather than scanned PDF images or complex multi-page tables.
* **In-Memory Conversation State:** Working memory is stored in `st.session_state`; refreshing the browser tab mid-task resets the active agent trace (though database changes persist in SQLite).
* **Boolean Approval Flag:** While `enter_invoice_into_erp` checks `human_approved=True`, a production system would bind this to a cryptographic token signed by the human's auth session rather than a boolean parameter passed by the LLM.

---

## 7. What I Would Build Next (With Additional Time)
1. **Multimodal PDF & Browser Tooling:** Add Gemini's native PDF/image vision input to parse scanned PDF invoices directly, plus a Playwright browser tool to submit data into legacy web forms that lack APIs.
2. **Cryptographic Approval Tokens:** Replace the `human_approved: bool` argument with a server-side session token generated only when the user clicks "Approve" in the UI, making it impossible for a prompt injection inside an email to bypass the $5,000 gate.
3. **Transactional Rollbacks & Dry-Run Mode:** Wrap multi-step database mutations in savepoints so the user can preview a dry-run diff before committing changes.
4. **Persistent Task Queue:** Move the agent execution loop to a background worker (e.g., Celery/Redis) so long-running tasks survive browser refreshes and can notify users via Slack or email when human input is needed.

---

## 8. Models, APIs, Frameworks, and Libraries Used
* **Model:** `gemini-2.5-flash` via the official `google-genai` Python SDK (chosen for fast latency and reliable native function calling).
* **Database:** `sqlite3` (Python standard library) for persistent relational state and audit logging.
* **UI / Frontend:** `streamlit` and `pandas` for real-time trace inspection and database table visualization.
* **No External Agent Frameworks:** The ReAct orchestration loop, tool dispatch, and state management were written from scratch in pure Python.
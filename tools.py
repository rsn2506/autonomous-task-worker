import os
import sqlite3
from setup_sandbox import DB_PATH, INBOX_DIR

def log_audit(action: str, details: str):
    """Helper to record every write action in the database audit log."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO audit_logs (action, details) VALUES (?, ?)", (action, details))
    conn.commit()
    conn.close()

def list_inbox_files() -> dict:
    """Lists all document and email filenames available in the company inbox folder."""
    try:
        files = os.listdir(INBOX_DIR)
        return {"status": "success", "files": sorted(files)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def read_inbox_file(filename: str) -> dict:
    """Reads the full text content of a specific file inside the inbox folder.
    
    Args:
        filename: The exact name of the file to read (e.g., 'invoice_acme_oct.txt').
    """
    filepath = os.path.join(INBOX_DIR, filename)
    if not os.path.exists(filepath):
        return {
            "status": "error",
            "message": f"File '{filename}' does not exist in inbox. Call list_inbox_files() first."
        }
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    return {"status": "success", "filename": filename, "content": content}

def query_internal_db(sql_query: str) -> dict:
    """Executes a read-only SELECT query on the company SQLite database (tables: invoices, vendors, audit_logs).
    
    Args:
        sql_query: A valid SQLite SELECT query string.
    """
    if not sql_query.strip().upper().startswith("SELECT"):
        return {
            "status": "error",
            "message": "Only SELECT queries are permitted in query_internal_db. Use dedicated tools for inserts."
        }
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql_query)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return {"status": "success", "row_count": len(rows), "results": rows}
    except Exception as e:
        return {"status": "error", "message": f"SQL Error: {str(e)}"}

def register_new_vendor(name: str, email: str, tax_id: str) -> dict:
    """Registers a new vendor into the internal ERP database 'vendors' table.
    
    Args:
        name: Full legal name of the vendor (e.g., 'Globex Inc').
        email: Billing contact email of the vendor.
        tax_id: Official Tax ID string found on the vendor's invoice.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO vendors (name, email, tax_id) VALUES (?, ?, ?)",
            (name, email, tax_id)
        )
        conn.commit()
        conn.close()
        log_audit("REGISTER_VENDOR", f"Registered vendor '{name}' ({email}, Tax ID: {tax_id})")
        return {"status": "success", "message": f"Vendor '{name}' registered in ERP."}
    except sqlite3.IntegrityError:
        return {"status": "error", "message": f"Vendor '{name}' already exists in the database."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def enter_invoice_into_erp(
    invoice_id: str,
    vendor_name: str,
    amount: float,
    due_date: str,
    human_approved: bool = False
) -> dict:
    """Enters an extracted invoice into the internal ERP 'invoices' table.
    
    Args:
        invoice_id: Unique invoice identifier (e.g., 'INV-2026-089').
        vendor_name: Exact vendor name. Must already be registered in the 'vendors' table.
        amount: Numeric total dollar amount (e.g., 1850.50).
        due_date: Due date in YYYY-MM-DD format.
        human_approved: Set to True ONLY AFTER receiving explicit confirmation from the user via ask_human.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Check 1: Is the vendor registered in the ERP system?
        cursor.execute("SELECT name FROM vendors WHERE name = ?", (vendor_name,))
        if not cursor.fetchone():
            conn.close()
            return {
                "status": "error",
                "error_code": "VENDOR_NOT_REGISTERED",
                "message": (
                    f"Database Foreign Key Failure: Vendor '{vendor_name}' is not in the 'vendors' table. "
                    f"Please register the vendor first using register_new_vendor() before inserting this invoice."
                )
            }

        # Check 2: Is the invoice already entered?
        cursor.execute("SELECT invoice_id FROM invoices WHERE invoice_id = ?", (invoice_id,))
        if cursor.fetchone():
            conn.close()
            return {
                "status": "error",
                "error_code": "DUPLICATE_INVOICE",
                "message": f"Invoice '{invoice_id}' already exists in the ERP database."
            }

        # Check 3: Company Policy Safety Gate (Invoices > $5,000 require human approval)
        if amount > 5000.0 and not human_approved:
            conn.close()
            return {
                "status": "blocked_by_policy",
                "error_code": "APPROVAL_REQUIRED",
                "message": (
                    f"SECURITY POLICY TRIGGERED: Invoice '{invoice_id}' for ${amount:,.2f} exceeds the $5,000.00 "
                    f"autonomous limit. You MUST call ask_human() to request manager approval before calling "
                    f"this tool again with human_approved=True."
                )
            }

        # Insert the invoice
        cursor.execute(
            "INSERT INTO invoices (invoice_id, vendor_name, amount, due_date, status) VALUES (?, ?, ?, ?, 'PENDING_PAYMENT')",
            (invoice_id, vendor_name, amount, due_date)
        )
        conn.commit()
        conn.close()

        log_audit("INSERT_INVOICE", f"Entered invoice {invoice_id} from {vendor_name} for ${amount:,.2f} due {due_date}")
        return {
            "status": "success",
            "message": f"Invoice '{invoice_id}' inserted into ERP. Remember to verify the record before concluding."
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

def verify_invoice_record(invoice_id: str) -> dict:
    """Verifies whether a specific invoice ID actually exists in the ERP database and returns its exact row as proof.
    
    Args:
        invoice_id: The invoice ID to check in the database.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        record = dict(row)
        log_audit("VERIFY_INVOICE", f"Verified existence of invoice {invoice_id} in ERP")
        return {
            "status": "verified",
            "verified": True,
            "database_record_proof": record
        }
    else:
        return {
            "status": "unverified",
            "verified": False,
            "message": f"Verification Failed: Invoice '{invoice_id}' was NOT found in the invoices table."
        }

def ask_human(question: str) -> dict:
    """Pauses autonomous execution and asks the human user for clarification or manager approval.
    
    Args:
        question: Clear, specific question explaining why you cannot safely proceed without human input.
    """
    # Note: In our agent loop (app.py), intercepting `ask_human` pauses the loop and displays an input box!
    return {"status": "waiting_for_human", "question": question}
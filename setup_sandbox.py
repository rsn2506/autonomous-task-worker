import os
import sqlite3

DB_PATH = "company_erp.db"
INBOX_DIR = "inbox"

def init_sandbox():
    # 1. Create inbox directory
    os.makedirs(INBOX_DIR, exist_ok=True)

    # 2. Reset and create SQLite Database
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Create Vendors table
    cursor.execute("""
        CREATE TABLE vendors (
            vendor_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            email TEXT NOT NULL,
            tax_id TEXT NOT NULL
        )
    """)

    # Create Invoices table
    cursor.execute("""
        CREATE TABLE invoices (
            invoice_id TEXT PRIMARY KEY,
            vendor_name TEXT NOT NULL,
            amount REAL NOT NULL,
            due_date TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (vendor_name) REFERENCES vendors (name)
        )
    """)

    # Create Audit Log table
    cursor.execute("""
        CREATE TABLE audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            action TEXT NOT NULL,
            details TEXT NOT NULL
        )
    """)

    # Pre-populate existing vendors (Notice: Globex Inc is intentionally missing!)
    vendors = [
        ("Acme Corp", "billing@acmecorp.com", "TAX-ACME-991"),
        ("Stark Industries", "accounts@stark.com", "TAX-STARK-001"),
        ("Wayne Enterprises", "finance@wayne.com", "TAX-WAYNE-882"),
    ]
    cursor.executemany("INSERT INTO vendors (name, email, tax_id) VALUES (?, ?, ?)", vendors)

    # Pre-populate one old invoice
    cursor.execute("""
        INSERT INTO invoices (invoice_id, vendor_name, amount, due_date, status)
        VALUES ('INV-2026-001', 'Acme Corp', 450.00, '2026-08-15', 'PAID')
    """)

    conn.commit()
    conn.close()

    # 3. Create Sample Files in inbox/
    files = {
        "email_acme_aug.txt": """From: billing@acmecorp.com
Date: August 1, 2026
Subject: Invoice INV-2026-001

Hi team,
Please find attached details for your August server hosting:
Invoice ID: INV-2026-001
Vendor: Acme Corp
Amount Due: $450.00
Due Date: 2026-08-15
""",
        "invoice_acme_oct.txt": """========================================
INVOICE: INV-2026-089
DATE ISSUED: October 10, 2026
VENDOR: Acme Corp (billing@acmecorp.com)
========================================
Description: Q4 Cloud GPU Cluster Rental
Total Amount: $1,850.50
Payment Due Date: 2026-11-10
Status: UNPAID
========================================
""",
        "invoice_globex_oct.txt": """--- GLOBEX INC OFFICIAL INVOICE ---
Invoice Number: GLX-9942
Issue Date: October 12, 2026
Vendor Name: Globex Inc
Vendor Contact: ar@globex.io
Vendor Tax ID: TAX-GLX-7741

Services Rendered: Automated QA Testing Suite
Amount: $2,300.00
Due Date: 2026-11-15
""",
        "invoice_stark_heavy.txt": """STARK INDUSTRIES - ENTERPRISE BILLING
Invoice ID: STK-2026-500
Date: October 14, 2026
Vendor: Stark Industries
Item: Custom Arc Reactor Cooling Unit
Total Amount: $12,500.00
Due Date: 2026-11-01
Note: High-value hardware order.
""",
        "email_wayne_dispute.txt": """From: finance@wayne.com
Date: October 15, 2026
Subject: Updated Invoice WYN-404

Hello,
Our original invoice WYN-404 for security consulting listed $1,200.00, 
but with the weekend overtime surcharge, the revised total is $1,650.00.
However, our account manager mentioned we might waive the surcharge if you prefer.
Due Date: 2026-11-20.
Please enter whichever amount your finance lead approves!
"""
    }

    for filename, content in files.items():
        with open(os.path.join(INBOX_DIR, filename), "w", encoding="utf-8") as f:
            f.write(content)

    print("Sandbox initialized! 'company_erp.db' and 'inbox/' files created.")

if __name__ == "__main__":
    init_sandbox()
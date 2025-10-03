# app.py
import streamlit as st
import pandas as pd
import pdfplumber
import sqlite3
from datetime import datetime
import os
import re

# ---------- CONFIG ----------
DB_FILE = "orders.db"
SNAPSHOT_FILE = "orders_snapshot.csv"

# ---------- DB setup ----------
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()
c.execute('''
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_order_no TEXT,
    invoice_no TEXT,
    order_date TEXT,
    invoice_date TEXT,
    customer TEXT,
    address TEXT,
    product TEXT,
    hsn TEXT,
    qty TEXT,
    gross_amount TEXT,
    total_amount TEXT,
    added_at TEXT
)
''')
conn.commit()

st.title("📦 FineFaser Order Tracker (Cloud-Safe)")

# ---------- Helpers ----------
def clean_amt(s):
    return s.replace("Rs.","").replace("Rs","").replace(",","").strip() if s else s

def extract_from_text(text):
    """Parse Meesho label fields from extracted text."""
    res = {}

    # CUSTOMER block
    if "Customer Address" in text:
        after = text.split("Customer Address",1)[1]
        lines = [l.strip() for l in after.splitlines() if l.strip()]
        if lines:
            res['customer'] = lines[0]
            # address until "If undelivered"
            addr_lines = []
            for line in lines:
                if line.lower().startswith("if undelivered"):
                    break
                addr_lines.append(line)
            res['address'] = " ".join(addr_lines).strip()

    # Purchase / Invoice / Dates
    m_po = re.search(r"Purchase Order No\.\s*([\d_]+)", text)
    if m_po: res['purchase_order_no'] = m_po.group(1)
    m_inv = re.search(r"Invoice No\.\s*([^\s\n]+)", text)
    if m_inv: res['invoice_no'] = m_inv.group(1)
    m_od = re.search(r"Order Date\s*([0-9./-]+)", text)
    if m_od: res['order_date'] = m_od.group(1)
    m_id = re.search(r"Invoice Date\s*([0-9./-]+)", text)
    if m_id: res['invoice_date'] = m_id.group(1)

    # Product / HSN / Qty / Gross Amount
    if "Description" in text:
        rest = text.split("Description",1)[1]
        lines = [l.strip() for l in rest.splitlines() if l.strip()]
        for line in lines:
            m = re.search(r"(\d{5,6})\s+(\d+)\s+Rs\.?\s*([0-9\.,]+)", line)
            if m:
                res['hsn'] = m.group(1)
                res['qty'] = m.group(2)
                res['gross_amount'] = clean_amt(m.group(3))
                res['product'] = line[:m.start()].strip()
                break

    # Total Amount (last Rs value)
    all_rs = re.findall(r"Rs\.?\s*([0-9\.,]+)", text)
    if all_rs:
        res['total_amount'] = clean_amt(all_rs[-1])

    return res

# ---------- File Upload ----------
uploaded_file = st.file_uploader("Upload Meesho Label (PDF)", type=["pdf"])

if uploaded_file:
    with pdfplumber.open(uploaded_file) as pdf:
        text = ""
        for page in pdf.pages:
            ptext = page.extract_text()
            if ptext:
                text += "\n" + ptext

    fields = extract_from_text(text)
    fields['added_at'] = datetime.utcnow().isoformat()

    # Normalize keys
    for k in ["purchase_order_no","invoice_no","order_date","invoice_date","customer","address","product","hsn","qty","gross_amount","total_amount"]:
        fields.setdefault(k,"NA")

    st.subheader("Extracted Order Data")
    st.json(fields)

    # Insert into DB
    c.execute('''
        INSERT INTO orders (purchase_order_no, invoice_no, order_date, invoice_date,
                            customer, address, product, hsn, qty, gross_amount, total_amount, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        fields["purchase_order_no"], fields["invoice_no"], fields["order_date"], fields["invoice_date"],
        fields["customer"], fields["address"], fields["product"], fields["hsn"], fields["qty"],
        fields["gross_amount"], fields["total_amount"], fields["added_at"]
    ))
    conn.commit()
    st.success("✅ Order saved to database")

    # Snapshot CSV
    df = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
    df.to_csv(SNAPSHOT_FILE, index=False)

    # Download button
    st.download_button("⬇️ Download All Orders (CSV)",
                       data=df.to_csv(index=False),
                       file_name=SNAPSHOT_FILE,
                       mime="text/csv")

# ---------- Display Orders ----------
st.subheader("📊 All Saved Orders")
df_all = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
st.dataframe(df_all)






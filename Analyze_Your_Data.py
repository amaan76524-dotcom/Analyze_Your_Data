# app.py
import streamlit as st
import pandas as pd
import pdfplumber
import sqlite3
import re
from datetime import datetime

DB_FILE = "orders.db"
SNAPSHOT_FILE = "orders_snapshot.csv"

EXPECTED_COLUMNS = [
    "purchase_order_no","order_date",
    "customer_address","bill_to_address",
    "product","hsn","qty","gross_amount","total_amount","added_at"
]

# ---------- DB init ----------
def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT)')
    conn.commit()
    c.execute("PRAGMA table_info(orders)")
    existing = [row[1] for row in c.fetchall()]
    for col in EXPECTED_COLUMNS:
        if col not in existing:
            c.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT")
    conn.commit()
    return conn, c

conn, c = init_db()

st.title("📦 FineFaser Order Tracker (Dual Address Version)")

# ---------- Helpers ----------
def clean_amt(s):
    return s.replace("Rs.","").replace("Rs","").replace(",","").strip() if s else s

def extract_block(lines, start_kw, end_kws):
    """Extract text block between start keyword and any of the end keywords."""
    content = []
    capture = False
    for line in lines:
        if start_kw.lower() in line.lower():
            capture = True
            continue
        if capture:
            if any(end_kw.lower() in line.lower() for end_kw in end_kws):
                break
            content.append(line)
    return content

def extract_from_text(text):
    res = {k: "NA" for k in EXPECTED_COLUMNS}
    lines = [re.sub(r'\s+', ' ', l).strip() for l in text.splitlines() if l.strip()]

    # ---------- CUSTOMER ADDRESS ----------
    cust_block = extract_block(lines, "Customer Address", ["Pickup", "Product Details", "If undelivered"])
    if cust_block:
        res["customer_address"] = " ".join(cust_block).strip()

    # ---------- BILL TO / SHIP TO ----------
    bill_block = extract_block(lines, "BILL TO / SHIP TO", ["Purchase Order No", "Description", "Product Details"])
    if bill_block:
        res["bill_to_address"] = " ".join(bill_block).strip()

    # ---------- PURCHASE ORDER NUMBER ----------
    for line in lines:
        if "Purchase Order No" in line:
            m = re.search(r"\b(\d{12,20})\b", line)
            if m:
                res["purchase_order_no"] = m.group(1)

    # ---------- ORDER DATE ----------
    for line in lines:
        if "Order Date" in line:
            m = re.search(r"\d{1,2}[./-]\d{1,2}[./-]\d{4}", line)
            if m:
                res["order_date"] = m.group(0)
                break

    # ---------- PRODUCT + HSN + QTY + AMOUNT ----------
    desc_section = extract_block(lines, "Description", ["Tax is not payable", "Total", "This is a computer"])
    for line in desc_section:
        m = re.search(r"(.+?)\s+(\d{5,6})\s+(\d+)\s+Rs\.?\s*([\d\.,]+)", line)
        if m:
            res["product"] = m.group(1).strip()
            res["hsn"] = m.group(2)
            res["qty"] = m.group(3)
            res["gross_amount"] = clean_amt(m.group(4))
            break

    # ---------- TOTAL AMOUNT ----------
    all_rs = re.findall(r"Rs\.?\s*([\d\.,]+)", text)
    if all_rs:
        res["total_amount"] = clean_amt(all_rs[-1])

    return res

# ---------- Upload + Extract ----------
uploaded_file = st.file_uploader("Upload Meesho Label (PDF)", type=["pdf"])

if uploaded_file:
    with pdfplumber.open(uploaded_file) as pdf:
        text = ""
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += "\n" + t

    fields = extract_from_text(text)
    fields["added_at"] = datetime.utcnow().isoformat()

    st.subheader("✅ Extracted Order Data")
    st.json(fields)

    # ---------- Save ----------
    try:
        c.execute(f'''
            INSERT INTO orders ({",".join(EXPECTED_COLUMNS)})
            VALUES ({",".join("?"*len(EXPECTED_COLUMNS))})
        ''', tuple(fields[col] for col in EXPECTED_COLUMNS))
        conn.commit()
        st.success("Order saved to database")
    except Exception as e:
        st.error(f"DB insert failed: {e}")

    # ---------- CSV Download ----------
    df = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
    df.to_csv(SNAPSHOT_FILE, index=False)
    st.download_button("⬇️ Download Orders (CSV)",
                       data=df.to_csv(index=False),
                       file_name=SNAPSHOT_FILE,
                       mime="text/csv")

# ---------- Display ----------
st.subheader("📊 All Saved Orders")
df_all = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
st.dataframe(df_all)

















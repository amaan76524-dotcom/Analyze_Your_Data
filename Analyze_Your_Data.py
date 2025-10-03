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
    "purchase_order_no","order_date","customer","address",
    "product","hsn","qty","gross_amount","total_amount","added_at"
]

# ---------- DB init ----------
def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT)')
    conn.commit()
    # add missing columns if needed
    c.execute("PRAGMA table_info(orders)")
    existing = [row[1] for row in c.fetchall()]
    for col in EXPECTED_COLUMNS:
        if col not in existing:
            c.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT")
    conn.commit()
    return conn, c

conn, c = init_db()

st.title("📦 FineFaser Order Tracker (Minimal)")

# ---------- Helpers ----------
def clean_amt(s):
    return s.replace("Rs.","").replace("Rs","").replace(",","").strip() if s else s

def extract_from_text(text):
    res = {k: "NA" for k in EXPECTED_COLUMNS}

    # Normalize lines
    lines = [re.sub(r'\s+', ' ', l).strip() for l in text.splitlines() if l.strip()]

    for line in lines:
        # Purchase Order No
        if line.startswith("Purchase Order No"):
            m = re.search(r"(\d{12,20})", line)
            if m: res['purchase_order_no'] = m.group(1)

        # Order Date
        elif line.startswith("Order Date"):
            m = re.search(r"(\d{1,2}[./-]\d{1,2}[./-]\d{4})", line)
            if m: res['order_date'] = m.group(1)

        # Customer & Address block
        elif line.startswith("Customer Address"):
            idx = lines.index(line) + 1
            cust_lines = []
            while idx < len(lines) and not any(x in lines[idx] for x in ["If undelivered","Sold by","Description"]):
                cust_lines.append(lines[idx])
                idx += 1
            if cust_lines:
                res['customer'] = cust_lines[0]
                res['address'] = " ".join(cust_lines)

        # Product + HSN + Qty + Gross
        elif re.search(r"\d{5,6}\s+\d+\s+Rs", line):
            m = re.search(r"(.+?)\s+(\d{5,6})\s+(\d+)\s+Rs\.?\s*([\d\.,]+)", line)
            if m:
                res['product'] = m.group(1).strip()
                res['hsn'] = m.group(2)
                res['qty'] = m.group(3)
                res['gross_amount'] = clean_amt(m.group(4))

    # Total Amount (last Rs value in doc)
    all_rs = re.findall(r"Rs\.?\s*([\d\.,]+)", text)
    if all_rs:
        res['total_amount'] = clean_amt(all_rs[-1])

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
    fields['added_at'] = datetime.utcnow().isoformat()

    st.subheader("✅ Extracted Order Data")
    st.json(fields)

    # Save to DB
    try:
        c.execute(f'''
            INSERT INTO orders ({",".join(EXPECTED_COLUMNS)})
            VALUES ({",".join("?"*len(EXPECTED_COLUMNS))})
        ''', tuple(fields[col] for col in EXPECTED_COLUMNS))
        conn.commit()
        st.success("Order saved to database")
    except Exception as e:
        st.error(f"DB insert failed: {e}")

    # Export CSV
    df = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
    df.to_csv(SNAPSHOT_FILE, index=False)

    st.download_button("⬇️ Download Orders (CSV)", data=df.to_csv(index=False),
                       file_name=SNAPSHOT_FILE, mime="text/csv")

# ---------- Display All Orders ----------
st.subheader("📊 All Saved Orders")
df_all = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
st.dataframe(df_all)















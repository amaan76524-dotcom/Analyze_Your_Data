# app.py
import streamlit as st
import pandas as pd
import pdfplumber
import sqlite3
from datetime import datetime
import re

# ---------- CONFIG ----------
DB_FILE = "orders.db"
SNAPSHOT_FILE = "orders_snapshot.csv"

# ---------- DB init + schema-migration ----------
EXPECTED_COLUMNS = [
    "purchase_order_no","invoice_no","order_date","invoice_date","customer","address",
    "product","hsn","sku","size","color","qty","gross_amount","total_amount",
    "payment_type","courier","added_at"
]

def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT)')
    conn.commit()
    # schema migration
    c.execute("PRAGMA table_info(orders)")
    existing = [row[1] for row in c.fetchall()]
    for col in EXPECTED_COLUMNS:
        if col not in existing:
            c.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT")
    conn.commit()
    return conn, c

conn, c = init_db()

st.title("📦 FineFaser Order Tracker")

# ---------- Helpers ----------
def clean_amt(s):
    return s.replace("Rs.","").replace("Rs","").replace(",","").strip() if s else s

def extract_payment_type(text):
    if re.search(r'\bCOD\b', text, flags=re.I):
        return "COD"
    if re.search(r'\bPrepaid\b', text, flags=re.I):
        return "Prepaid"
    return "NA"

def extract_courier(text):
    if re.search(r'\bDelhivery\b', text, flags=re.I):
        return "Delhivery"
    if re.search(r'\bXpress\s*Bees\b', text, flags=re.I):
        return "Xpress Bees"
    return "NA"

def extract_from_text(text):
    res = {}

    # Payment type and courier
    res['payment_type'] = extract_payment_type(text)
    res['courier'] = extract_courier(text)

    # Customer & Address
    if "Customer Address" in text:
        after = text.split("Customer Address",1)[1]
        lines = [l.strip() for l in after.splitlines() if l.strip()]
        clean_lines = [l for l in lines if not (
            l.upper().startswith("COD") or
            l.lower().startswith("prepaid") or
            l.lower().startswith("delhivery") or
            l.lower().startswith("xpress") or
            l.lower().startswith("pickup")
        )]
        if clean_lines:
            res['customer'] = clean_lines[0]
            addr_lines = []
            for line in clean_lines:
                if line.lower().startswith("if undelivered"):
                    break
                addr_lines.append(line)
            res['address'] = " ".join(addr_lines).strip()

    # Purchase Order, Invoice, Dates
    m_po = re.search(r"Purchase Order No\.\s*([0-9]+)", text)
    if m_po: res['purchase_order_no'] = m_po.group(1)

    m_inv = re.search(r"Invoice No\.\s*([^\s\n]+)", text)
    if m_inv: res['invoice_no'] = m_inv.group(1)

    m_od = re.search(r"Order Date\s*([0-9./-]+)", text)
    if m_od: res['order_date'] = m_od.group(1)

    m_id = re.search(r"Invoice Date\s*([0-9./-]+)", text)
    if m_id: res['invoice_date'] = m_id.group(1)

    # SKU / Size / Color
    if "SKU Size Qty Color Order No." in text:
        block = text.split("SKU Size Qty Color Order No.")[1].split("\n")[1].strip()
        parts = block.split()
        if len(parts) >= 4:
            res['sku'] = parts[0]
            if parts[1].lower() == "free" and parts[2].lower() == "size":
                res['size'] = "Free Size"
                res['qty'] = parts[3]
                res['color'] = parts[4] if len(parts) > 4 else "NA"
            else:
                res['size'] = parts[1]
                res['qty'] = parts[2]
                res['color'] = parts[3]

    # Product / HSN / Gross Amount
    if "Description" in text:
        rest = text.split("Description",1)[1]
        lines = [l.strip() for l in rest.splitlines() if l.strip()]
        for line in lines:
            m = re.search(r"(.+?)\s+(\d{5,6})\s+(\d+)\s+Rs\.?\s*([0-9\.,]+)", line)
            if m:
                res['product'] = m.group(1).strip()
                res['hsn'] = m.group(2)
                if 'qty' not in res:
                    res['qty'] = m.group(3)
                res['gross_amount'] = clean_amt(m.group(4))
                break

    # Total Amount
    all_rs = re.findall(r"Rs\.?\s*([0-9\.,]+)", text)
    if all_rs:
        res['total_amount'] = clean_amt(all_rs[-1])

    # Normalize
    for k in EXPECTED_COLUMNS:
        res.setdefault(k, "NA")

    return res

# ---------- Upload + Extract ----------
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

    st.subheader("Extracted Order Data")
    st.json(fields)

    try:
        c.execute('''
            INSERT INTO orders (purchase_order_no, invoice_no, order_date, invoice_date,
                                customer, address, product, hsn, sku, size, color, qty,
                                gross_amount, total_amount, payment_type, courier, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            fields["purchase_order_no"], fields["invoice_no"], fields["order_date"], fields["invoice_date"],
            fields["customer"], fields["address"], fields["product"], fields["hsn"], fields["sku"],
            fields["size"], fields["color"], fields["qty"], fields["gross_amount"],
            fields["total_amount"], fields["payment_type"], fields["courier"], fields["added_at"]
        ))
        conn.commit()
        st.success("✅ Order saved to database")
    except Exception as e:
        st.error(f"DB insert failed: {e}")

    df = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
    df.to_csv(SNAPSHOT_FILE, index=False)

    st.download_button("⬇️ Download All Orders (CSV)", data=df.to_csv(index=False),
                       file_name=SNAPSHOT_FILE, mime="text/csv")

# ---------- Display Orders ----------
st.subheader("📊 All Saved Orders")
df_all = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
st.dataframe(df_all)










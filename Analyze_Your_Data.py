# app.py
import streamlit as st
import pandas as pd
import pdfplumber
import sqlite3
from datetime import datetime
import re
import os

# ---------- CONFIG ----------
DB_FILE = "orders.db"
SNAPSHOT_FILE = "orders_snapshot.csv"

# ---------- DB init + schema-migration ----------
EXPECTED_COLUMNS = [
    "purchase_order_no","invoice_no","order_date","invoice_date","customer","address",
    "product","hsn","sku","size","color","qty","gross_amount","total_amount","added_at"
]

def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    # ensure table exists (minimal)
    c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT)')
    conn.commit()

    # fetch existing columns
    c.execute("PRAGMA table_info(orders)")
    existing = [row[1] for row in c.fetchall()]

    # add missing columns (safe ALTER TABLE)
    for col in EXPECTED_COLUMNS:
        if col not in existing:
            c.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT")
    conn.commit()
    return conn, c

conn, c = init_db()

st.title("📦 FineFaser Order Tracker — Improved Parser")

# ---------- Helpers ----------
def clean_amt(s):
    return s.replace("Rs.","").replace("Rs","").replace(",","").strip() if s else s

def find_block(text, start_tokens, end_tokens):
    tl = text.lower()
    start_idx = None
    for tok in start_tokens:
        i = tl.find(tok.lower())
        if i != -1:
            start_idx = i
            break
    if start_idx is None:
        return None
    end_idx = len(text)
    for tok in end_tokens:
        j = tl.find(tok.lower(), start_idx)
        if j != -1:
            end_idx = min(end_idx, j)
    return text[start_idx:end_idx]

def clean_line_prefixes(line):
    # remove common noisy prefixes and phrases
    line = re.sub(r'^(Prepaid:|COD:)\s*', '', line, flags=re.I)
    line = line.replace('Do not collect cash', '')
    line = re.sub(r'\bDestination Code\b', '', line, flags=re.I)
    line = re.sub(r'\bReturn Code\b', '', line, flags=re.I)
    return line.strip()

def extract_customer_block(text):
    start_tokens = ["Customer Address", "Bill To / Ship To", "BILL TO / SHIP TO", "BILL TO", "SHIP TO", "BILL TO / SHIP TO", "BILL TO / SHIP TO"]
    end_tokens = ["If undelivered", "Sold by", "Sold by :", "GSTIN", "Purchase Order No", "Description", "Product Details", "Product Details", "Invoice No", "Invoice Date"]
    block = find_block(text, start_tokens, end_tokens)
    if not block:
        return {"customer":"NA","address":"NA"}
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    cleaned = []
    for line in lines:
        nl = clean_line_prefixes(line)
        # skip lines that are only service labels
        if re.match(r'^(delhivery|pickup|destination code|return code|if undelivered)', nl, flags=re.I):
            continue
        if nl == "":
            continue
        cleaned.append(nl)

    if not cleaned:
        return {"customer":"NA","address":"NA"}

    # remove trailing noise tokens in each line
    cleaned = [re.sub(r'\b(Pickup|Destination Code|Return Code)\b.*$', '', l, flags=re.I).strip() for l in cleaned]

    # find PIN line index (6-digit Indian PIN)
    pin_idx = None
    for idx, l in enumerate(cleaned):
        if re.search(r'\b\d{6}\b', l):
            pin_idx = idx
            break

    # If first cleaned line has leading 'name + house no' join, try split name by first digit
    customer = None
    address = None
    if pin_idx is not None:
        # Attempt to infer name: look for short line before or at pin line
        for i in range(0, pin_idx+1):
            cand = cleaned[i]
            # if line contains a name then digit (like "vaishnavi 19,..."), extract leading alphabetic part
            m = re.match(r'^([A-Za-z\.\-\s&]+)\s+\d', cand)
            if m:
                customer = m.group(1).strip()
                break
            # else prefer short lines (<=8 words) as name
            if len(cand.split()) <= 8 and re.search(r'[A-Za-z]', cand):
                customer = cand
                break
        if not customer:
            # fallback: take first token before comma if exists
            first = cleaned[0]
            customer = first.split(',')[0].strip()
        # address as lines up to and including pin line
        addr_lines = cleaned[:pin_idx+1]
        address = " ".join(addr_lines).strip()
    else:
        # no pin found; take first non-noise line as name and rest as address
        customer = cleaned[0]
        address = " ".join(cleaned[1:]) if len(cleaned) > 1 else cleaned[0]

    # final sanitization
    customer = re.sub(r'^(?:Prepaid:|COD:)\s*', '', customer, flags=re.I).strip()
    address = address.replace(customer, '').strip() if customer and customer in address else address
    address = re.sub(r'\s{2,}', ' ', address).strip()

    if not customer: customer = "NA"
    if not address: address = "NA"
    return {"customer": customer, "address": address}

def extract_from_text(text):
    res = {}

    # customer/address block
    cb = extract_customer_block(text)
    res.update(cb)

    # Purchase Order No (prefer explicit label, else long numeric token >=12 digits)
    m_po = re.search(r"Purchase Order No\.?\s*[:\-]?\s*([0-9_]+)", text, flags=re.I)
    if m_po:
        res['purchase_order_no'] = m_po.group(1)
    else:
        long_nums = re.findall(r"\b\d{12,24}\b", text)
        if long_nums:
            # pick the first long numeric that is not a 6-digit PIN
            chosen = None
            for n in long_nums:
                if len(n) >= 12 and len(n) != 6:
                    chosen = n
                    break
            if chosen:
                res['purchase_order_no'] = chosen

    # Invoice No (only explicit label)
    m_inv = re.search(r"Invoice No\.?\s*[:\-]?\s*([^\s,\n]+)", text, flags=re.I)
    if m_inv:
        res['invoice_no'] = m_inv.group(1)

    # Order / Invoice Dates (dd.mm.yyyy or dd/mm/yyyy or yyyy-mm-dd)
    date_re = r"\b(?:[0-3]?\d[./-][0-1]?\d[./-]\d{4}|\d{4}[./-][0-1]?\d[./-][0-3]?\d)\b"
    m_od = re.search(r"Order Date\s*[:\-]?\s*(" + date_re + ")", text, flags=re.I)
    if m_od:
        res['order_date'] = m_od.group(1)
    m_id = re.search(r"Invoice Date\s*[:\-]?\s*(" + date_re + ")", text, flags=re.I)
    if m_id:
        res['invoice_date'] = m_id.group(1)
    # fallback: any date present
    if 'order_date' not in res or res.get('order_date') is None:
        m_any = re.search(date_re, text)
        if m_any and 'order_date' not in res:
            res['order_date'] = m_any.group(0)

    # SKU / Size / Color (explicit block)
    sku_block = None
    m_sku_label = re.search(r"SKU\s+Size\s+Qty\s+Color\s+Order No\.", text, flags=re.I)
    if m_sku_label:
        # get next non-empty line after the label
        tail = text[m_sku_label.end():]
        lines = [l.strip() for l in tail.splitlines() if l.strip()]
        if lines:
            sku_block = lines[0]
    if sku_block:
        parts = sku_block.split()
        # heuristic: Free Size case has 'Free Size' as two tokens
        try:
            if len(parts) >= 5 and parts[1].lower() == 'free' and parts[2].lower() == 'size':
                res['sku'] = parts[0]
                res['size'] = "Free Size"
                res['qty'] = parts[3]
                res['color'] = parts[4] if len(parts) > 4 else "NA"
            elif len(parts) >= 4:
                res['sku'] = parts[0]
                res['size'] = parts[1]
                res['qty'] = parts[2]
                res['color'] = parts[3]
        except Exception:
            pass

    # Product / HSN / Qty / Gross Amount (Description table)
    if "Description" in text:
        rest = text.split("Description",1)[1]
        lines = [l.strip() for l in rest.splitlines() if l.strip()]
        for line in lines:
            # pattern: <product text> <HSN> <Qty> Rs. <gross>
            m = re.search(r"(.+?)\s+(\d{5,6})\s+(\d+)\s+Rs\.?\s*([0-9\.,]+)", line)
            if m:
                prod = m.group(1).strip()
                res['product'] = prod
                res['hsn'] = m.group(2)
                # don't overwrite qty if found in SKU block
                if 'qty' not in res or res.get('qty') in (None,"NA"):
                    res['qty'] = m.group(3)
                res['gross_amount'] = clean_amt(m.group(4))
                break
        # fallback simpler pattern (HSN and amount in separate columns)
        if 'product' not in res:
            for line in lines:
                m2 = re.search(r"(\d{5,6})\s+1\s+Rs\.?\s*([0-9\.,]+)", line)
                if m2:
                    res.setdefault('hsn', m2.group(1))
                    res.setdefault('gross_amount', clean_amt(m2.group(2)))
                    break

    # Total: last Rs value on document
    all_rs = re.findall(r"Rs\.?\s*([0-9\.,]+)", text)
    if all_rs:
        res['total_amount'] = clean_amt(all_rs[-1])

    # normalize keys
    for k in EXPECTED_COLUMNS:
        if k not in res:
            res[k] = "NA"
    return res

# ---------- UI ----------
uploaded_file = st.file_uploader("Upload Meesho Label (PDF)", type=["pdf"])
show_raw = st.checkbox("Show raw extracted text", value=False)

if uploaded_file:
    with pdfplumber.open(uploaded_file) as pdf:
        extracted_text = ""
        for page in pdf.pages:
            t = page.extract_text() or ""
            extracted_text += "\n" + t

    if show_raw:
        st.subheader("Raw extracted text")
        st.text_area("Raw text", value=extracted_text, height=300)

    fields = extract_from_text(extracted_text)
    fields['added_at'] = datetime.utcnow().isoformat()

    st.subheader("Parsed fields")
    st.json(fields)

    # Insert into DB (safe: schema auto-migrated on startup)
    try:
        c.execute('''
            INSERT INTO orders (purchase_order_no, invoice_no, order_date, invoice_date,
                                customer, address, product, hsn, sku, size, color, qty,
                                gross_amount, total_amount, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            fields["purchase_order_no"], fields["invoice_no"], fields["order_date"], fields["invoice_date"],
            fields["customer"], fields["address"], fields["product"], fields["hsn"], fields["sku"],
            fields["size"], fields["color"], fields["qty"], fields["gross_amount"],
            fields["total_amount"], fields["added_at"]
        ))
        conn.commit()
        st.success("✅ Order saved to database")
    except Exception as e:
        st.error(f"DB insert failed: {e}")

    # Snapshot CSV
    df = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
    df.to_csv(SNAPSHOT_FILE, index=False)

    st.download_button("⬇️ Download All Orders (CSV)", data=df.to_csv(index=False), file_name=SNAPSHOT_FILE, mime="text/csv")

st.subheader("📊 All Saved Orders")
df_all = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
st.dataframe(df_all)









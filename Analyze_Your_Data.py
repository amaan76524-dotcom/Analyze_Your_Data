# app.py
import streamlit as st
import pandas as pd
import pdfplumber
import sqlite3
import os
import re
import tempfile
from datetime import datetime

# OCR libs
try:
    import pytesseract
    from pdf2image import convert_from_path
    from PIL import Image
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

# ---------- CONFIG ----------
DB_FILE = "orders.db"
SNAPSHOT_FILE = "orders_snapshot.csv"

# ---------- DB setup ----------
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
c = conn.cursor()
c.execute('''
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT,
    invoice_no TEXT,
    purchase_order_no TEXT,
    order_date TEXT,
    invoice_date TEXT,
    customer TEXT,
    address TEXT,
    product TEXT,
    hsn TEXT,
    sku TEXT,
    qty TEXT,
    gross_amount TEXT,
    total_amount TEXT,
    added_at TEXT
)
''')
conn.commit()

st.title("📦 FineFaser — Improved Order Extractor")

st.markdown("Upload a Meesho label (PDF). App will try text-extraction first and fall back to OCR if needed.")

# ---------- Helpers ----------
def clean_amt(s):
    return s.replace("Rs.","").replace("Rs","").replace(",","").strip() if s else s

def extract_from_text(text):
    """Robust heuristics to parse the common fields from text."""
    res = {}
    # CUSTOMER block
    if "Customer Address" in text:
        after = text.split("Customer Address",1)[1]
        lines = [l.strip() for l in after.splitlines() if l.strip()]
        # pick first likely name (skip COD lines)
        name = None
        for line in lines:
            if line.upper().startswith("COD"): continue
            if line.lower().startswith("delhivery"): continue
            if line.lower().startswith("pickup"): continue
            if re.search(r"[A-Za-z]", line) and len(line.split()) <= 6:
                name = line
                break
        res['customer'] = name or (lines[0] if lines else "NA")
        # address: lines until 'If undelivered' or 'Pickup'
        addr_lines = []
        for line in lines:
            low = line.lower()
            if low.startswith("if undelivered") or low.startswith("pickup"):
                break
            addr_lines.append(line)
        # remove leading COD if present
        if addr_lines and addr_lines[0].upper().startswith("COD"):
            addr_lines = addr_lines[1:]
        # avoid repeating customer in address:
        if addr_lines and res['customer'] and addr_lines[0].strip() == res['customer'].strip():
            addr_lines = addr_lines[1:]
        res['address'] = " ".join(addr_lines).strip() if addr_lines else "NA"

    # PURCHASE / INVOICE / DATES: often in a header line then numeric line
    parts = text.splitlines()
    for i, line in enumerate(parts):
        if re.search(r"Purchase Order No", line, re.I) and re.search(r"Invoice No", line, re.I):
            if i + 1 < len(parts):
                vals = parts[i+1].split()
                # Expect: <purchase_order> <invoice_no> <order_date> <invoice_date>
                if len(vals) >= 4:
                    res['purchase_order_no'] = vals[0]
                    res['invoice_no'] = vals[1]
                    res['order_date'] = vals[2]
                    res['invoice_date'] = vals[3]
            break
    if 'purchase_order_no' not in res:
        m = re.search(r"\b(\d{15,18})\b", text)
        if m: res['purchase_order_no'] = m.group(1)

    # PRODUCT area: find "Description" then find the HSN / qty / Rs pattern
    if "Description" in text:
        rest = text.split("Description", 1)[1]
        lines = [l.strip() for l in rest.splitlines() if l.strip()]
        pattern = re.compile(r"(\d{5,6})\s+(\d+)\s+Rs\.?\s*([0-9\.,]+)")
        matched = False
        for idx, line in enumerate(lines):
            m = pattern.search(line)
            if m:
                hsn, qty, gross = m.groups()
                res['hsn'] = hsn
                res['qty'] = qty
                res['gross_amount'] = clean_amt(gross)
                # product often on same or previous line
                prod_name = line[:m.start()].strip()
                if len(prod_name) < 3 and idx > 0:
                    prod_name = lines[idx - 1].strip()
                res['product'] = prod_name
                matched = True
                break
        if not matched:
            # fallback minimal product detection
            for line in lines:
                if re.search(r"HSN", line, re.I) and re.search(r"Qty", line, re.I): continue
                if re.search(r"[A-Za-z]", line):
                    res['product'] = line
                    break
            mqty = re.search(r"\bQty[:\s]*([0-9]+)\b", text)
            if mqty and 'qty' not in res: res['qty'] = mqty.group(1)
            mg = re.search(r"Gross Amount[:\s]*Rs\.?\s*([0-9\.,]+)", text)
            if mg and 'gross_amount' not in res: res['gross_amount'] = clean_amt(mg.group(1))

    # TOTAL: last Rs.<num>
    all_rs = re.findall(r"Rs\.?\s*([0-9\.,]+)", text)
    if all_rs:
        res['total_amount'] = clean_amt(all_rs[-1])

    # SKU (optional)
    msku = re.search(r"SKU\s+Size\s+Qty\s+Color\s+Order No\.\s*(.+)", text, re.I)
    if msku:
        res['sku'] = msku.group(1).strip()

    return res

def pdf_to_text_bytesio(uploaded_file, poppler_path=None, force_ocr=False):
    """Try pdfplumber first; if empty or force_ocr -> use pdf2image + pytesseract."""
    # pdfplumber on BytesIO
    text = ""
    try:
        with pdfplumber.open(uploaded_file) as pdf:
            for page in pdf.pages:
                ptext = page.extract_text()
                if ptext:
                    text += "\n" + ptext
    except Exception:
        text = ""

    # If text is short or user forces OCR, use OCR
    if force_ocr or (len(text.strip()) < 200):
        if not OCR_AVAILABLE:
            return text, False, "OCR libs not installed"
        # write bytes to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(uploaded_file.getbuffer())
            tmp_path = tmp.name
        try:
            images = convert_from_path(tmp_path, dpi=300, poppler_path=poppler_path) if poppler_path else convert_from_path(tmp_path, dpi=300)
            ocr_text = ""
            for im in images:
                ocr_text += pytesseract.image_to_string(im, lang='eng') + "\n"
        finally:
            try:
                os.remove(tmp_path)
            except:
                pass
        return ocr_text, True, "ocr_used"
    return text, False, "plumber"

# ---------- UI ----------
uploaded_file = st.file_uploader("Upload Meesho Label (PDF)", type=["pdf"])
force_ocr = st.checkbox("Force OCR (useful for scanned labels)", value=False)
show_raw = st.checkbox("Show raw extracted text (debug)", value=False)

# Allow user to provide poppler / tesseract paths via Streamlit secrets (useful on Windows)
poppler_path = st.secrets.get("POPPLER_PATH") if "POPPLER_PATH" in st.secrets else None
tesseract_cmd = st.secrets.get("TESSERACT_CMD") if "TESSERACT_CMD" in st.secrets else None
if tesseract_cmd and OCR_AVAILABLE:
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

if uploaded_file:
    with st.spinner("Extracting..."):
        text, used_ocr, msg = pdf_to_text_bytesio(uploaded_file, poppler_path=poppler_path, force_ocr=force_ocr)
    if show_raw:
        st.subheader("Raw extracted text")
        st.text_area("Raw text", value=text, height=300)

    fields = extract_from_text(text)
    fields['added_at'] = datetime.utcnow().isoformat()
    # quick normalization for missing keys
    for k in ["purchase_order_no","invoice_no","order_date","invoice_date","customer","address","product","qty","gross_amount","total_amount","hsn","sku"]:
        fields.setdefault(k,"NA")

    st.subheader("Parsed fields")
    st.json(fields)

    # Insert to DB
    try:
        c.execute('''
            INSERT INTO orders (order_no, invoice_no, purchase_order_no, order_date, invoice_date, customer, address, product, hsn, sku, qty, gross_amount, total_amount, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            fields.get("order_no","NA"),
            fields.get("invoice_no","NA"),
            fields.get("purchase_order_no","NA"),
            fields.get("order_date","NA"),
            fields.get("invoice_date","NA"),
            fields.get("customer","NA"),
            fields.get("address","NA"),
            fields.get("product","NA"),
            fields.get("hsn","NA"),
            fields.get("sku","NA"),
            fields.get("qty","NA"),
            fields.get("gross_amount","NA"),
            fields.get("total_amount","NA"),
            fields.get("added_at")
        ))
        conn.commit()
        st.success("✅ Order saved to local DB")
    except Exception as e:
        st.error(f"DB insert failed: {e}")

    # snapshot to CSV
    df = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
    df.to_csv(SNAPSHOT_FILE, index=False)
    st.info(f"Snapshot saved to {SNAPSHOT_FILE}")

    # Download button
    st.download_button("⬇️ Download snapshot CSV", data=df.to_csv(index=False), file_name=SNAPSHOT_FILE, mime="text/csv")

st.subheader("All saved orders (local DB)")
df_all = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
st.dataframe(df_all)


# app.py
import streamlit as st
import pandas as pd
import pdfplumber
import sqlite3
import os
import re
from datetime import datetime
from github import Github

# Files
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
    order_date TEXT,
    customer TEXT,
    address TEXT,
    product TEXT,
    qty TEXT,
    gross_amount TEXT,
    total_amount TEXT,
    added_at TEXT
)
''')
conn.commit()

st.title("📦 FineFaser Order Tracker")

# ---------- helpers ----------
def extract_text_from_pdf(uploaded_file):
    text = ""
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + "\n"
    return text

def re_search(pattern, text, default="NA", flags=re.I):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else default

def extract_order_fields(text):
    # robust-ish regex extractions with fallbacks
    order_no = re_search(r"Order\s*No\.?\s*[:\-]?\s*([^\n\r]+)", text)
    invoice_no = re_search(r"Invoice\s*No\.?\s*[:\-]?\s*([^\n\r]+)", text)
    order_date = re_search(r"Order\s*Date[:\-]?\s*([^\n\r]+)", text)

    # customer / address block
    customer = "NA"
    address = "NA"
    if "Customer Address" in text:
        try:
            block = text.split("Customer Address",1)[1].split("If undelivered",1)[0]
            lines = [l.strip() for l in block.splitlines() if l.strip()]
            if lines:
                customer = lines[0]
                address = " ".join(lines)
        except:
            pass

    # product: try to find line after 'Description'
    product = "NA"
    qty = "NA"
    if "Description" in text:
        try:
            rest = text.split("Description",1)[1]
            lines = [l.strip() for l in rest.splitlines() if l.strip()]
            if lines:
                product = lines[0]
        except:
            pass
    # qty common pattern
    qty = re_search(r"\bQty[:\s]*([0-9]+)", text, default="NA")

    # amounts
    gross_amount = re_search(r"Gross\s*Amount[:\s]*Rs\.?\s*([0-9\.,]+)", text, default="NA")
    # fallback: find first Rs.<num> after product line
    if gross_amount == "NA":
        m = re.search(r"Rs\.?\s*([0-9\.,]+)", text)
        if m: gross_amount = m.group(1)

    total_amount = re_search(r"Total(?:\s+Rs\.?)?\s*[:\s]*Rs?\.?\s*([0-9\.,]+)", text, default="NA")
    # fallback to last Rs.<num>
    if total_amount == "NA":
        all_rs = re.findall(r"Rs\.?\s*([0-9\.,]+)", text)
        if all_rs:
            total_amount = all_rs[-1]

    return {
        "order_no": order_no,
        "invoice_no": invoice_no,
        "order_date": order_date,
        "customer": customer,
        "address": address,
        "product": product,
        "qty": qty,
        "gross_amount": gross_amount,
        "total_amount": total_amount
    }

# ---------- file upload & extract ----------
uploaded_file = st.file_uploader("Upload Meesho Label (PDF)", type=["pdf"])
if uploaded_file:
    with st.spinner("Extracting text..."):
        text = extract_text_from_pdf(uploaded_file)

    fields = extract_order_fields(text)
    fields["added_at"] = datetime.utcnow().isoformat()

    # show preview to user
    st.subheader("Extracted fields")
    st.json(fields)

    # insert into sqlite
    try:
        c.execute('''
            INSERT INTO orders (order_no, invoice_no, order_date, customer, address, product, qty, gross_amount, total_amount, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            fields["order_no"], fields["invoice_no"], fields["order_date"], fields["customer"],
            fields["address"], fields["product"], fields["qty"], fields["gross_amount"], fields["total_amount"], fields["added_at"]
        ))
        conn.commit()
        st.success("✅ Order saved to local DB (orders.db)")
    except Exception as e:
        st.error(f"DB insert failed: {e}")

    # export snapshot CSV
    df = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
    df.to_csv(SNAPSHOT_FILE, index=False)
    st.info(f"Snapshot exported to {SNAPSHOT_FILE}")

    # try to push snapshot to GitHub if secrets are configured
    github_token = st.secrets.get("GITHUB_TOKEN") if "GITHUB_TOKEN" in st.secrets else None
    github_repo = st.secrets.get("GITHUB_REPO") if "GITHUB_REPO" in st.secrets else None
    github_path = st.secrets.get("GITHUB_FILE_PATH") if "GITHUB_FILE_PATH" in st.secrets else SNAPSHOT_FILE

    if github_token and github_repo:
        try:
            g = Github(github_token)
            repo = g.get_repo(github_repo)  # format: "username/repo"
            content = open(SNAPSHOT_FILE, "r", encoding="utf-8").read()
            try:
                existing = repo.get_contents(github_path)
                repo.update_file(existing.path,
                                 f"Update orders snapshot {datetime.utcnow().isoformat()}",
                                 content,
                                 existing.sha)
                st.success("✅ Snapshot updated on GitHub.")
            except Exception:
                # file missing -> create
                repo.create_file(github_path,
                                 f"Add orders snapshot {datetime.utcnow().isoformat()}",
                                 content)
                st.success("✅ Snapshot created on GitHub.")
        except Exception as e:
            st.error(f"Failed to push to GitHub: {e}")
    else:
        st.info("GitHub token/repo not configured. Snapshot not pushed to GitHub.")

# ---------- show all orders ----------
st.subheader("📊 All Orders (local snapshot)")
df_all = pd.read_sql_query("SELECT * FROM orders ORDER BY id DESC", conn)
st.dataframe(df_all)

st.download_button("⬇️ Download snapshot CSV", data=df_all.to_csv(index=False), file_name=SNAPSHOT_FILE, mime="text/csv")

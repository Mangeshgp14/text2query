# app.py
"""
Text2Query – Llama3.1 + MySQL + NL→SQL
Works with ollama local model: llama3.1
Requirements:
    pip install streamlit mysql-connector-python requests pandas sqlparse
Run:
    ollama pull llama3.1
    ollama serve
    streamlit run app.py
"""

import streamlit as st
import mysql.connector
from mysql.connector import Error as MySQLError
import pandas as pd
import requests
import sqlparse
import re

# ---------- CONFIG ----------
# LLM_MODEL = "llama3.1:8b"  # Try "llama3.1:70b" or "llama3.1:8b" for better performance
LLM_MODEL = "mannix/defog-llama3-sqlcoder-8b"
OLLAMA_URL = "http://localhost:11434/api/generate"
USE_LLM = True
DEFAULT_LIMIT = 100

# ---------- PAGE SETUP ----------
st.set_page_config(page_title="Text2Query", page_icon="🔍", layout="wide")

# ---------- SESSION STATE ----------
state_defaults = {
    "connection_status": None,
    "error_message": "",
    "db_credentials": None,
    "tables": [],
    "selected_table": None,
    "generated_sql": "",
    "last_df": None,
    "last_raw_llm": "",
    "sql_preview": "",
    "last_error": "",
    "last_question": ""
}
for k,v in state_defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------- STYLE ----------
def load_css(fname="styles.css"):
    try:
        with open(fname) as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)
    except:
        pass

load_css("styles.css")


# ---------- HELPERS ----------
def mysql_error_to_message(err: MySQLError, host, port, db):
    try:
        errno = err.errno
    except:
        errno = None
    msg = str(err)
    if errno == 1045:
        return "Access denied (1045). Invalid username/password."
    if errno == 1049:
        return f"Unknown database '{db}' (1049)."
    if errno == 2003:
        return f"Cannot connect to MySQL at {host}:{port} (2003)."
    if errno == 2005:
        return f"Unknown MySQL host '{host}' (2005)."
    return msg


def attempt_connect(host, port, user, pwd, db):
    try:
        conn = mysql.connector.connect(
            host=host,
            port=int(port),
            user=user,
            password=pwd,
            database=db,
            connection_timeout=5
        )
        if not conn.is_connected():
            return False, "Connected but session invalid.", []
        cur = conn.cursor()
        cur.execute("SHOW TABLES;")
        tables = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
        return True, "", tables
    except mysql.connector.Error as err:
        return False, mysql_error_to_message(err, host, port, db), []
    except Exception as ex:
        return False, f"Unexpected error: {ex}", []


def get_columns(creds, table):
    try:
        conn = mysql.connector.connect(
            host=creds["host"],
            port=int(creds["port"]),
            user=creds["username"],
            password=creds["password"],
            database=creds["database"]
        )
        cur = conn.cursor()
        cur.execute(f"""
            SELECT COLUMN_NAME, COLUMN_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
        """, (creds["database"], table))
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [(r[0],r[1]) for r in rows]
    except:
        return []


def get_sample_data(creds, table, limit=3):
    """Get sample rows to provide context to AI"""
    try:
        conn = mysql.connector.connect(
            host=creds["host"],
            port=int(creds["port"]),
            user=creds["username"],
            password=creds["password"],
            database=creds["database"]
        )
        df = pd.read_sql_query(f"SELECT * FROM `{table}` LIMIT {limit};", conn)
        conn.close()
        # Format as simple text representation
        if len(df) > 0:
            sample = df.head(limit).to_string(index=False, max_rows=limit)
            return sample
        return ""
    except:
        return ""


def pretty_sql(sql):
    try:
        return sqlparse.format(sql, reindent=True, keyword_case='upper')
    except:
        return sql


def run_sql(creds, sql):
    try:
        conn = mysql.connector.connect(
            host=creds["host"],
            port=int(creds["port"]),
            user=creds["username"],
            password=creds["password"],
            database=creds["database"],
            connection_timeout=8
        )
        df = pd.read_sql_query(sql, conn)
        conn.close()
        return df, ""
    except Exception as ex:
        return None, str(ex)


# ---------- ENHANCED LLM PROMPT ----------
def build_prompt(table, columns, question, sample_data="", previous_error=""):
    """Enhanced prompt with examples, types, and sample data"""
    
    # Format columns with types
    cols_detail = ", ".join([f"{c} ({t})" for c, t in columns]) if columns else "(unknown)"
    
    # Base prompt with examples
    prompt = f"""You are an expert MySQL query generator. Convert natural language questions into valid MySQL SELECT statements.

IMPORTANT RULES:
1. Output ONLY the SQL query
2. No explanations, no markdown code blocks, no commentary
3. Use LIMIT {DEFAULT_LIMIT} unless the user specifies a different limit
4. Always use proper table and column aliases when needed
5. Ensure the query is syntactically correct for MySQL
6. Use backticks for table/column names if they contain special characters

DATABASE INFORMATION:
Table: {table}
Columns: {cols_detail}
"""

    # Add sample data if available
    if sample_data:
        prompt += f"""
Sample Data (first 3 rows):
{sample_data}
"""

    # Add examples for better learning
    prompt += """
EXAMPLES OF GOOD QUERIES:

Question: "Show me all records"
SQL: SELECT * FROM `table_name` LIMIT 100;

Question: "Top 5 highest prices"
SQL: SELECT * FROM `table_name` ORDER BY price DESC LIMIT 5;

Question: "Count by category"
SQL: SELECT category, COUNT(*) as count FROM `table_name` GROUP BY category LIMIT 100;

Question: "Average price by category"
SQL: SELECT category, AVG(price) as avg_price FROM `table_name` GROUP BY category LIMIT 100;

Question: "Records where status is active"
SQL: SELECT * FROM `table_name` WHERE status = 'active' LIMIT 100;

Question: "Total sales by month"
SQL: SELECT DATE_FORMAT(date_column, '%Y-%m') as month, SUM(sales) as total FROM `table_name` GROUP BY month ORDER BY month DESC LIMIT 100;
"""

    # Add error feedback if retrying
    if previous_error:
        prompt += f"""
PREVIOUS ATTEMPT FAILED WITH ERROR:
{previous_error}

Please fix the query to avoid this error.
"""

    # Add the actual question
    prompt += f"""
USER QUESTION: {question}

Generate the MySQL query:
"""

    return prompt.strip()


def extract_sql(raw: str):
    """
    Extract SQL from LLM response
    """
    candidate = ""
    
    # Remove markdown code blocks if present
    raw = re.sub(r'```sql\s*', '', raw)
    raw = re.sub(r'```\s*', '', raw)
    
    # Direct SELECT match until semicolon
    m = re.search(r"(SELECT[\s\S]*?;)", raw, flags=re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
    else:
        # Fallback: find SELECT and take everything after it
        m = re.search(r"(SELECT[\s\S]*)", raw, flags=re.IGNORECASE)
        if m:
            candidate = m.group(1).strip()

    if candidate and not candidate.endswith(";"):
        candidate += ";"

    return candidate.strip() if candidate else ""


def call_llm(table, cols, question, creds, retry_error=""):
    """Call LLM with enhanced prompt including sample data"""
    
    # Get sample data for context
    sample_data = get_sample_data(creds, table, limit=3)
    
    # Build enhanced prompt
    prompt = build_prompt(table, cols, question, sample_data, retry_error)
    
    try:
        payload = {"model": LLM_MODEL, "prompt": prompt, "stream": False}
        r = requests.post(OLLAMA_URL, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        raw = ""
        for k in ("response","text","output","content"):
            if k in data:
                raw = str(data[k]).strip()
                break
        if not raw:
            raw = str(data)
    except Exception as e:
        return f"[LLM ERROR: {e}]", ""

    sql = extract_sql(raw)
    return raw, sql


def safe_sql_check(sql: str):
    if not sql.strip():
        return False, "SQL empty."
    s = sql.lower().strip()
    if not s.startswith("select"):
        return False, "Only SELECT allowed."
    forbidden = ["drop","delete","update","insert","alter","truncate","merge","exec","call"]
    for w in forbidden:
        if w in s:
            return False, f"Forbidden keyword '{w}'."
    return True, ""


# ---------- HEADER ----------
st.markdown(
    """
<div style="display:flex; gap:12px; align-items:center; justify-content: center;">
  
  <div>
    <div class="app-title">Text 2 Query</div>
    <div class="app-subtitle">Helping Product Manager query databases in simple english using GEN AI 🤖</div>
  </div>
</div>
""",
    unsafe_allow_html=True
)

# ---------- LEFT PANEL: CONNECTION ----------
with st.container():
    st.markdown("### Step 1) Connect Your Database 🔒")

    col1,col2 = st.columns(2)
    with col1:
        host = st.text_input("Host", value="localhost")
    with col2:
        port = st.text_input("Port", value="3306")

    database = st.text_input("Database", value="")
    username = st.text_input("Username", value="root")
    password = st.text_input("Password", type="password")

    c1,c2 = st.columns(2)
    connect_btn = c1.button("Connect", use_container_width=True)

    if connect_btn:
        ok,msg,tables = attempt_connect(host, port, username, password, database)
        if ok:
            st.session_state.connection_status = "success"
            st.session_state.error_message = ""
            st.session_state.db_credentials = {
                "host": host,
                "port": port,
                "database": database,
                "username": username,
                "password": password
            }
            st.session_state.tables = tables
            st.session_state.selected_table = None
        else:
            st.session_state.connection_status = "error"
            st.session_state.error_message = msg
            st.session_state.tables = []
            st.session_state.selected_table = None

    if st.session_state.connection_status == "success":
        st.success("✓ Connected")
    elif st.session_state.connection_status == "error":
        st.error(st.session_state.error_message)

    st.markdown("### Step 2) Select a Table to Query 🗃️")
    if st.session_state.connection_status=="success":
        if st.session_state.tables:
            sel = st.selectbox("Choose table from this dropdown list", ["--"]+st.session_state.tables)
            if sel!="--":
                st.session_state.selected_table = sel
        else:
            st.info("No tables found.")
    else:
        st.markdown("<div class='small-muted'>Connect to list tables</div>", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)

# ---------- TABLE PREVIEW (TOP 5) ----------
if st.session_state.selected_table:
    st.markdown(f"### 🔎 Preview `{st.session_state.selected_table}` (Top 5)")
    creds = st.session_state.db_credentials
    try:
        conn = mysql.connector.connect(
            host=creds["host"],
            port=int(creds["port"]),
            user=creds["username"],
            password=creds["password"],
            database=creds["database"]
        )
        preview = pd.read_sql_query(
            f"SELECT * FROM `{st.session_state.selected_table}` LIMIT 5;",
            conn
        )
        conn.close()
        
        # Convert all columns to string to force left alignment
        preview_display = preview.astype(str)
        
        st.dataframe(
            preview_display,
            use_container_width=True
        )
    except Exception as e:
        st.error(f"Preview error: {e}")

st.markdown("</div>", unsafe_allow_html=True)

# ---------- RIGHT PANEL: PROMPT ----------
st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
st.markdown("### Step 3) Ask a Question in plain English ❓")

if not st.session_state.selected_table:
    st.info("Select a table first.")
    user_q = st.text_area("", height=80, disabled=True)
    gen_btn = st.button("Generate SQL Query", disabled=True)
    retry_btn = False
else:
    user_q = st.text_area("", placeholder="e.g., Show me top 10 records ordered by price", height=100)
    col_gen1, col_gen2 = st.columns([3, 1])
    with col_gen1:
        gen_btn = st.button("Generate SQL Query", use_container_width=True)
    with col_gen2:
        retry_btn = st.button("🔄 Retry", use_container_width=True, disabled=not st.session_state.last_error)

# ---------- TOP-N ROWS DIRECT BYPASS ----------
def detect_top_n(q: str):
    q = q.lower().strip()
    m = re.search(r"(top|first)\s+(\d+)", q)
    if m:
        return int(m.group(2))
    return None

# ---------- GENERATE SQL ----------
if gen_btn:
    if not user_q.strip():
        st.error("Enter a question.")
    else:
        st.session_state.last_question = user_q
        st.session_state.last_error = ""
        
        # Pattern detection for simple queries
        n = detect_top_n(user_q)
        if n and st.session_state.selected_table:
            sql = f"SELECT * FROM `{st.session_state.selected_table}` LIMIT {n};"
            st.session_state.generated_sql = pretty_sql(sql)
            st.session_state["sql_preview"] = pretty_sql(sql)
            st.session_state.last_raw_llm = "Bypass LLM – top N detected."
            st.success(f"Direct SQL generated (LIMIT {n}).")
        else:
            # Enhanced LLM path
            creds = st.session_state.db_credentials
            cols = get_columns(creds, st.session_state.selected_table)
            if USE_LLM:
                with st.spinner("🤖 Generating SQL Query using AI..."):
                    raw, sql = call_llm(
                        st.session_state.selected_table, 
                        cols, 
                        user_q,
                        creds
                    )
                    st.session_state.last_raw_llm = raw

                    if sql and sql.strip():
                        st.session_state.generated_sql = pretty_sql(sql)
                        st.session_state["sql_preview"] = pretty_sql(sql)
                        st.success("✅ SQL generated. Review before running.")
                    else:
                        stub = f"SELECT * FROM `{st.session_state.selected_table}` LIMIT 100;"
                        st.session_state.generated_sql = pretty_sql(stub)
                        st.session_state["sql_preview"] = pretty_sql(stub)
                        st.warning("LLM returned no SQL. Using fallback query.")
            else:
                stub = f"SELECT * FROM `{st.session_state.selected_table}` LIMIT 100;"
                st.session_state.generated_sql = pretty_sql(stub)
                st.session_state["sql_preview"] = pretty_sql(stub)
                st.info("LLM disabled. Using default.")

# ---------- RETRY WITH ERROR FEEDBACK ----------
if retry_btn and st.session_state.last_error and st.session_state.last_question:
    creds = st.session_state.db_credentials
    cols = get_columns(creds, st.session_state.selected_table)
    
    with st.spinner("🔄 Retrying with error feedback..."):
        raw, sql = call_llm(
            st.session_state.selected_table,
            cols,
            st.session_state.last_question,
            creds,
            retry_error=st.session_state.last_error
        )
        st.session_state.last_raw_llm = raw
        
        if sql and sql.strip():
            st.session_state.generated_sql = pretty_sql(sql)
            st.session_state["sql_preview"] = pretty_sql(sql)
            st.success("✅ New SQL generated based on error feedback.")
        else:
            st.warning("Could not generate improved SQL.")

# ---------- SQL PREVIEW ----------
st.markdown("### Step 4) Review / Edit Generated SQL 📝")
sql_editor = st.text_area("", key="sql_preview", height=160)

# ---------- RUN SQL ----------
run_btn = st.button("Run Query", use_container_width=True)

if run_btn:
    sql = sql_editor.strip()
    ok,reason = safe_sql_check(sql)
    if not ok:
        st.error(f"Blocked: {reason}")
        st.session_state.last_error = reason
    else:
        creds = st.session_state.db_credentials
        with st.spinner("Executing..."):
            df, err = run_sql(creds, sql)
            if err:
                st.error(f"Execution error: {err}")
                st.session_state.last_error = err
            else:
                st.success(f"Query returned {len(df)} rows")
                st.session_state.last_error = ""
                
                # Convert all columns to string to force left alignment
                df_display = df.astype(str)
                
                st.dataframe(
                    df_display,
                    use_container_width=True
                )

# ---------- FOOTER ----------
st.markdown(
    "<div class='footer-text'>Credentials not stored. Have Fun !</div>",
    unsafe_allow_html=True
)
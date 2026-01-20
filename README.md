# Text2Query 🔍➡️🧾
**App Video Demo:**
[Link](https://drive.google.com/file/d/1B3TOtfvEzrNRn8ktblGnt_6t2ovLcKvD/view?usp=sharing)

---

**Summary:** Help non-technical Product Managers turn plain-English questions into SQL queries and actionable answers, so that PMs can validate hypotheses and track KPIs without waiting on data teams.

---

## Context
Product Managers use SQL every day to:
1. Track KPIs  
2. Validate hypotheses from experiments or user research  
3. Extract and analyze data for product decisions  

Many PMs are comfortable with analytics thinking but not with SQL syntax. That gap creates delays and context loss when relying on others.

---

## Problem Statement ❗
Non-technical PMs spend hours writing and debugging SQL or waiting for data teams to deliver extracts. This slows decision cycles and reduces the number of rapid experiments a PM can run.

---

## Solution ✨
A product that connects to a database and enables PMs to query it by asking questions in plain English and get:
- a ready-to-run SQL query, and
- a short plain-English explanation of the result and next steps.

---

## Target Users 🎯
- **Primary:** Product Managers with low/no SQL expertise  
- **Secondary:** PMs running quick hypothesis validation or KPI checks

---

## Product Flow (MVP) 🔁

- **Data Connection:** System establishes a secure connection to the data source.  
- **Scope Selection:** User selects relevant schemas and tables.
- **Ask Question:** User enters a plain-English question.  
- **SQL Generation:** LLM synthesizes natural language into a SQL query.  
- **Validation:** User previews the code for safety and accuracy.  
- **Execution:** The query is executed against the database.  
- **Insight Delivery:** System displays results, SQL, and a short plain-English interpretation.  

---

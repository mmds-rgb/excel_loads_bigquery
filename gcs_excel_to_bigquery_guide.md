# GCS Excel to BigQuery Ingestion Guide

A lightweight, serverless, console-deployable data pipeline for automated spreadsheet processing.

---

## 📌 1. Solution Overview

This architecture automatically loads weekly Excel spreadsheets (`.xlsx` / `.xls`) from **Google Cloud Storage (GCS)** directly into **Google BigQuery**. It is designed specifically for teams with beginner-to-intermediate GCP experience, eliminating Docker containers, command-line deployments, and complex infrastructure orchestration.

```
┌─────────────────┐      ┌─────────────────────────┐      ┌───────────────────────┐
│ 1. File Upload  │ ───> │ 2. Cloud Function (Gen2)│ ───> │ 3. BigQuery Ingestion │
│ (GCS Landing)   │      │    (Transform & Clean)  │      │    (Append Rows)      │
└─────────────────┘      └─────────────────────────┘      └───────────────────────┘
                                      │
                                      ▼
                         ┌─────────────────────────┐
                         │ 4. Auto Archival        │
                         │    (GCS /archived/)     │
                         └─────────────────────────┘
```

---

## ⚙️ 2. Core Architecture Components

| Component | Resource Name | Purpose & Key Features |
| :--- | :--- | :--- |
| **Landing Bucket** | `brand-campaign-landing` | Incoming drop zone for raw weekly Excel spreadsheets. |
| **Cloud Function** | `excel-to-bigquery-loader` | Serverless Python 3.11 runtime (Gen 2) triggered automatically by storage events. |
| **BigQuery Dataset** | `IntactLoadTesting` | Analytical data warehouse destination dataset. |
| **BigQuery Table** | `brand_campaign_weekly_performance` | Auto-created destination table with schema autodetection & lineage columns. |
| **Archive Location** | `brand-campaign-landing/archived/` | Long-term audit folder preventing duplicate processing and keeping landing root clean. |

---

## 🚀 3. Step-by-Step GCP Console Setup

### Step 1: Create the Cloud Storage Bucket
1. In the GCP Console, navigate to **Cloud Storage** ➔ **Buckets**.
2. Click **+ CREATE**.
3. Enter a unique name: `brand-campaign-landing`.
4. Select Region: **`northamerica-northeast1 (Montréal)`** and standard storage class.
5. Click **CREATE**.

### Step 2: Create the BigQuery Dataset
1. Navigate to **BigQuery** in the GCP Console.
2. In the Explorer panel, click the three dots next to your Project ID ➔ **Create dataset**.
3. Set **Dataset ID** to: `IntactLoadTesting`.
4. Select **Data location**: **`northamerica-northeast1 (Montréal)`**.
5. Click **CREATE DATASET**. *(The destination table will be auto-created upon first file load)*.

### Step 3: Configure & Deploy via the Cloud Run "Create service" Wizard

After clicking the **Python** tile under *"Write a function"*, configure the fields on the **Create service** screen:

| Screen Field | Recommended Value | Notes |
| :--- | :--- | :--- |
| **Service name** | `excel-to-bigquery-loader` | Name of your Cloud Run function. |
| **Region** | **`northamerica-northeast1 (Montréal)`** | Must match your GCS bucket and BigQuery dataset region. |
| **Runtime** | `Python 3.11` / `Python 3.12` | Leave as Python. |
| **Trigger (optional)** | Click **+ Add trigger** | Select **Cloud Storage** ➔ Event: `google.cloud.storage.object.v1.finalized` ➔ Bucket: `brand-campaign-landing` ➔ Click **Save trigger**. |
| **Authentication** | **Require authentication** | Recommended: allows authorized GCP storage events to call the service. |
| **Billing & Scaling** | **Request-based** & **Auto scaling (Min: 0)** | Zero idle cost when no files are being processed. |

#### Deployment & Code Entry:
1. Click the blue **CREATE** button at the bottom left.
2. In the **Code Editor** that opens:
   * Set **Entry point** to: `gcs_excel_to_bigquery`
   * Paste `requirements.txt` and `main.py` (provided below) into the corresponding tabs.
3. Click **DEPLOY**.

---

## 🔄 4. Processing Logic & Data Transformations (`main.py` Deep Dive)

The `main.py` function acts as an intelligent, self-healing ingestion engine designed to handle messy real-world spreadsheets without manual intervention:

1. **Stage 1: Event Filtering & Safety Guardrails**
   * **Loop Prevention:** Ignores files uploaded inside `archived/`, preventing infinite trigger loops.
   * **File Filter:** Only processes valid spreadsheet extensions (`.xlsx` and `.xls`).
   * **Dead Event Protection:** Checks `blob.exists()` and catches `NotFound` exceptions so retried events for already-archived files exit cleanly (HTTP 200) without crashing.

2. **Stage 2: In-Memory Spreadsheet Parsing**
   * Downloads file bytes directly into memory via `io.BytesIO` without writing to local disk, maximizing speed and security.
   * Parses tabular data using `openpyxl` and `pandas`. Empty files are safely skipped with a log warning.

3. **Stage 3: Column Sanitization & Naming Compliance (Regex)**
   * Converts all column headers to lowercase `snake_case`.
   * **Regex Cleaning:** Automatically strips out characters prohibited by BigQuery (parentheses, slashes, currency symbols, spaces, hyphens). For example, `Cost (CAD)` becomes `cost_cad` and `CTR %` becomes `ctr`.
   * Prefixes column names that begin with a number with an underscore (`_`) to guarantee valid BigQuery identifiers.

4. **Stage 4: Audit Metadata & Date Standardization**
   * **Audit Metadata:** Injects `_ingested_at` (UTC timestamp) for ingestion tracking and `_source_file` for end-to-end data lineage.
   * **Date Normalization:** Scans columns containing `"date"` or `"period"` and standardizes values into ISO format (`YYYY-MM-DD`).

5. **Stage 5: Dynamic Schema Alignment & Evolution**
   * **Type Matching:** Automatically inspects the existing BigQuery table schema. If a column is defined as `STRING` in BigQuery, it dynamically converts numeric IDs, floats, or codes into clean strings to prevent `ArrowTypeError` mismatches.
   * **Schema Evolution:** Enables `ALLOW_FIELD_ADDITION` so that whenever future agency files introduce new marketing columns, BigQuery automatically adds them to the table.

6. **Stage 6: BigQuery Ingestion & Automated Archiving**
   * Appends rows directly to the table using high-throughput BigQuery load jobs (`WRITE_APPEND`).
   * Copies the original file to `gs://<bucket>/archived/<YYYYMMDD_HHMMSS>_<filename>` and deletes it from the landing root to maintain an audit trail and keep the bucket clean.

---

## 💻 5. Production Source Code

### `requirements.txt`
```text
functions-framework>=3.5.0
google-cloud-storage>=2.14.0
google-cloud-bigquery>=3.17.0
pandas>=2.2.0
pandas-gbq>=0.26.1
openpyxl>=3.1.2
pyarrow>=15.0.0
```

### `main.py`
```python
import io
import os
import re
import logging
from datetime import datetime, timezone
import functions_framework
from google.api_core.exceptions import NotFound
import pandas as pd
from google.cloud import storage, bigquery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gcs_excel_to_bigquery")

BIGQUERY_DATASET = os.getenv("BIGQUERY_DATASET", "IntactLoadTesting")
BIGQUERY_TABLE = os.getenv("BIGQUERY_TABLE", "brand_campaign_weekly_performance")

@functions_framework.cloud_event
def hello_gcs(cloud_event):
    """
    Triggered on GCS file upload. Parses Excel and appends to BigQuery.
    Named hello_gcs to match Cloud Run's default entry point.
    """
    event_data = cloud_event.data
    bucket_name = event_data["bucket"]
    file_name = event_data["name"]

    logger.info(f"⚡ New file event: gs://{bucket_name}/{file_name}")

    # Skip archived files or non-Excel extensions
    is_archive = file_name.startswith("archived/")
    is_excel = file_name.lower().endswith((".xlsx", ".xls"))
    if is_archive or not is_excel:
        logger.info(f"⏩ Skipping non-target file: {file_name}")
        return

    storage_client = storage.Client()
    bigquery_client = bigquery.Client()

    # 1. Download file bytes (gracefully handle already-processed/deleted files)
    source_bucket = storage_client.bucket(bucket_name)
    blob = source_bucket.blob(file_name)
    if not blob.exists():
        logger.info(f"⏩ File gs://{bucket_name}/{file_name} no longer exists. Skipping.")
        return

    try:
        file_bytes = blob.download_as_bytes()
    except NotFound:
        return

    # 2. Parse Excel spreadsheet
    df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")
    if df.empty:
        logger.warning(f"⚠️ {file_name} is empty. Skipping BigQuery load.")
        return

    # 3. Clean columns (letters, numbers, underscores only; strip parentheses/symbols)
    clean_columns = []
    for col in df.columns:
        c = str(col).strip().lower()
        c = re.sub(r"[^a-z0-9_]+", "_", c).strip("_")
        if c and c[0].isdigit():
            c = f"_{c}"
        if not c:
            c = f"column_{len(clean_columns)}"
        clean_columns.append(c)
    df.columns = clean_columns

    df["_ingested_at"] = datetime.now(timezone.utc)
    df["_source_file"] = file_name

    # 4. Standardize date formats
    for col in df.columns:
        if "date" in col or "period" in col:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")

    # 5. Schema Alignment: Match DataFrame types to existing BigQuery table schema
    table_id = f"{bigquery_client.project}.{BIGQUERY_DATASET}.{BIGQUERY_TABLE}"
    try:
        existing_table = bigquery_client.get_table(table_id)
        schema_map = {field.name: field.field_type for field in existing_table.schema}
        logger.info(f"📋 Aligning types with existing table schema ({len(schema_map)} columns)...")

        for col in df.columns:
            if col in schema_map:
                expected_type = schema_map[col]
                if expected_type in ("STRING", "BYTES"):
                    df[col] = df[col].apply(lambda x: None if pd.isna(x) else (str(int(x)) if isinstance(x, float) and x.is_integer() else str(x).strip()))
                elif expected_type in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                elif expected_type in ("INTEGER", "INT64"):
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
                elif expected_type in ("DATE", "DATETIME", "TIMESTAMP"):
                    df[col] = pd.to_datetime(df[col], errors="coerce")
    except NotFound:
        pass

    # 6. Append to BigQuery with automatic schema update/evolution
    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        schema_update_options=[bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION],
        autodetect=True
    )
    
    logger.info(f"📤 Loading {len(df)} rows to BigQuery: {table_id}")
    load_job = bigquery_client.load_table_from_dataframe(df, table_id, job_config=job_config)
    load_job.result()
    logger.info(f"✅ Successfully inserted {len(df)} rows into {table_id}!")

    # 7. Archive original file to /archived/ with timestamp prefix
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_name = os.path.basename(file_name)
    archive_name = f"archived/{timestamp}_{base_name}"
    source_bucket.copy_blob(blob, source_bucket, archive_name)
    blob.delete()
    logger.info(f"🗄️ Moved file to gs://{bucket_name}/{archive_name}")

# Alias so both 'hello_gcs' and 'gcs_excel_to_bigquery' work
gcs_excel_to_bigquery = hello_gcs
```

---

## 🧪 6. Testing & Verification

1. **Drop a Test File:** Open Cloud Storage ➔ go to `brand-campaign-landing` ➔ drag and drop any `.xlsx` file.
2. **Check Function Logs:** Open Cloud Functions ➔ click `excel-to-bigquery-loader` ➔ click the **LOGS** tab.
3. **Query in BigQuery:**
   ```sql
   SELECT * 
   FROM `IntactLoadTesting.brand_campaign_weekly_performance` 
   ORDER BY _ingested_at DESC 
   LIMIT 100;
   ```

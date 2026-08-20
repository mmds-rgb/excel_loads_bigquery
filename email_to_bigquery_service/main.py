import io
import os
import re
import logging
from datetime import datetime, timezone
import functions_framework
from google.api_core.exceptions import NotFound
import pandas as pd
from google.cloud import storage, bigquery

# Configure logging for Cloud Run logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gcs_excel_to_bigquery")

BIGQUERY_DATASET = os.getenv("BIGQUERY_DATASET", "IntactLoadTesting")
BIGQUERY_TABLE = os.getenv("BIGQUERY_TABLE", "brand_campaign_weekly_performance")


def clean_string_val(val):
    """Formats values as clean strings without trailing .0 on integer floats."""
    if pd.isna(val):
        return None
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    return str(val).strip()


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
        logger.info(f"⏩ File gs://{bucket_name}/{file_name} was not found. Skipping.")
        return

    # 2. Parse Excel spreadsheet
    df = pd.read_excel(io.BytesIO(file_bytes), engine="openpyxl")
    if df.empty:
        logger.warning(f"⚠️ {file_name} is empty. Skipping BigQuery load.")
        return

    # 3. Clean columns for BigQuery (letters, numbers, underscores only; strip parentheses/symbols)
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
        logger.info(f"📋 Found existing BigQuery table schema with {len(schema_map)} columns. Aligning types...")

        for col in df.columns:
            if col in schema_map:
                expected_type = schema_map[col]
                if expected_type in ("STRING", "BYTES"):
                    df[col] = df[col].apply(clean_string_val)
                elif expected_type in ("FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                elif expected_type in ("INTEGER", "INT64"):
                    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
                elif expected_type in ("DATE", "DATETIME", "TIMESTAMP"):
                    df[col] = pd.to_datetime(df[col], errors="coerce")
    except NotFound:
        logger.info(f"🆕 Table {table_id} does not exist yet. It will be auto-created.")

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


# Alias so both 'hello_gcs' and 'gcs_excel_to_bigquery' work seamlessly
gcs_excel_to_bigquery = hello_gcs

#!/bin/bash
# Script to deploy the BQ DQ rules and alert infrastructure

PROJECT_ID="mmds-477-20260514190152"
LOCATION="us-central1" # Update this to your dataset's location if different (e.g. US, EU, etc.)
DATASET_ID="dataform"
TABLE_ID="test_daily_loads"

echo "=== 1. Initializing BigQuery Alerts Table ==="
# Run the first section of the SQL script to create the alerts table
bq query --use_legacy_sql=false --project_id="${PROJECT_ID}" \
  "CREATE TABLE IF NOT EXISTS \`${PROJECT_ID}.${DATASET_ID}.dq_alerts\` (
    alert_time TIMESTAMP OPTIONS(description='Time when the alert check ran'),
    table_name STRING OPTIONS(description='Table that was checked'),
    rule_name STRING OPTIONS(description='Name of the DQ rule'),
    status STRING OPTIONS(description='Status (e.g. FAIL)'),
    details STRING OPTIONS(description='Detailed alert message')
  );"

echo "=== 2. Deploying Dataplex Auto DQ Scan ==="
# Deploy Dataplex Auto DQ scan using the YAML config file
gcloud dataplex datascans create data-quality test-daily-loads-dq \
  --project="${PROJECT_ID}" \
  --location="${LOCATION}" \
  --data-source-resource="//bigquery.googleapis.com/projects/${PROJECT_ID}/datasets/${DATASET_ID}/tables/${TABLE_ID}" \
  --data-quality-spec-file="dataplex_dq_spec.yaml" \
  --schedule="0 1 * * *" \
  --use-user-credential \
  --description="Daily DQ checks for missing load dates and volume anomalies on test_daily_loads table"

echo "=== 3. Creating BigQuery Scheduled Queries for Alerts (Alternative) ==="
# You can set up scheduled queries in BigQuery console or via gcloud:
# For example, to schedule a daily check:
# gcloud transfer jobs create ... (or use BigQuery scheduled queries)
echo "Deployment scripts and configurations generated successfully!"

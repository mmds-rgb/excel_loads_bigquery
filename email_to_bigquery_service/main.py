import os
import logging
from flask import Flask, request, jsonify
from config import Config
from services.outlook_service import OutlookService
from services.gcs_service import GCSService
from services.bigquery_service import BigQueryService

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("email_to_bigquery")

app = Flask(__name__)

gcs_service = GCSService()
bq_service = BigQueryService()

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for Cloud Run container probes."""
    return jsonify({"status": "healthy", "service": "email-to-bigquery-ingestion"}), 200

@app.route("/trigger-sync", methods=["POST"])
def trigger_sync():
    """
    Scheduled endpoint invoked by Cloud Scheduler.
    2. Uploads raw files to GCS landing bucket.
    3. Parses Excel content and appends records to BigQuery.
    4. Moves raw files to GCS archive bucket.
    """
    logger.info("Starting scheduled Outlook email ingestion pipeline...")
    
    try:
        outlook_service = OutlookService()
        files = outlook_service.fetch_weekly_attachments()

        if not files:
            logger.info("No new campaign emails or attachments found.")
            return jsonify({"status": "completed", "message": "No new attachments to process", "files_processed": 0}), 200

        total_rows_inserted = 0
        processed_files = []

        for item in files:
            filename = item["filename"]
            content = item["content"]

            # 1. Save raw file to GCS landing bucket
            gcs_uri = gcs_service.upload_bytes(Config.GCS_LANDING_BUCKET, filename, content)

            # 2. Parse Excel & append to BigQuery
            rows_added = bq_service.process_and_load_excel(content, filename)
            total_rows_inserted += rows_added

            # 3. Archive processed file in GCS
            gcs_service.archive_blob(Config.GCS_LANDING_BUCKET, filename, Config.GCS_ARCHIVE_BUCKET)

            processed_files.append({"filename": filename, "gcs_uri": gcs_uri, "rows_loaded": rows_added})

        return jsonify({
            "status": "success",
            "files_processed": len(processed_files),
            "total_rows_loaded": total_rows_inserted,
            "details": processed_files
        }), 200

    except Exception as e:
        logger.error(f"Error during ingestion pipeline execution: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "error_message": str(e)}), 500


@app.route("/process-gcs-event", methods=["POST"])
def process_gcs_event():
    """
    Eventarc / GCS Event Notification endpoint.
    Triggered when an Excel file is uploaded to the landing bucket (e.g., via Power Automate).
    """
    event = request.get_json()
    if not event:
        return jsonify({"error": "Invalid request, no event JSON payload received"}), 400

    bucket_name = event.get("bucket")
    file_name = event.get("name")

    if not file_name or not file_name.lower().endswith((".xlsx", ".xls")):
        logger.info(f"Ignoring non-Excel file event: {file_name}")
        return jsonify({"status": "skipped", "reason": "Not an Excel file"}), 200

    logger.info(f"Processing GCS Event Notification for blob: gs://{bucket_name}/{file_name}")

    try:
        # Download blob content from GCS
        bucket = gcs_service.client.bucket(bucket_name)
        blob = bucket.blob(file_name)
        content_bytes = blob.download_as_bytes()

        # Parse Excel & append to BigQuery
        rows_added = bq_service.process_and_load_excel(content_bytes, file_name)

        # Archive processed file
        gcs_service.archive_blob(bucket_name, file_name, Config.GCS_ARCHIVE_BUCKET)

        return jsonify({
            "status": "success",
            "filename": file_name,
            "rows_loaded": rows_added
        }), 200

    except Exception as e:
        logger.error(f"Failed to process GCS event for {file_name}: {str(e)}", exc_info=True)
        return jsonify({"status": "error", "error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

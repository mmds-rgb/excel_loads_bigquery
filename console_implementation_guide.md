# Knowledge Catalog Data Quality Console Implementation Guide

This guide walks you through the step-by-step process of implementing the Knowledge Catalog Data Quality rules from [dataplex_dq_spec.yaml](file:///home/mmds/intact_marketing/dataplex_dq_spec.yaml) using the Google Cloud Platform (GCP) Console.

> [!IMPORTANT]
> This guide uses placeholder names (`primary-394719` for Project ID, `IntactLoadTesting` for Dataset, and `test_daily_loads` for the table). Please replace these with your actual GCP Project, Dataset, and Table names when implementing.

---

## 🛠️ Step 1: Navigate to Knowledge Catalog Data Quality

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Select your project (e.g. **`primary-394719`**).
3. In the left navigation menu, search for and select **Knowledge Catalog** (formerly Dataplex).
4. In the Knowledge Catalog navigation pane on the left, click **Data quality**.

---

## ➕ Step 2: Create a Data Quality Scan

1. On the **Data quality** landing page, click **+ CREATE DATA QUALITY SCAN** at the top.
2. In the **Define scan** section, enter the following details:
   - **Display name**: `test-daily-loads-dq`
   - **Scan ID**: `test-daily-loads-dq` (automatically populated or editable)
   - **Description**: `Daily DQ checks for missing load dates and volume anomalies on test_daily_loads table`
3. In the **Select table** section:
   - Click **Browse** to open the BigQuery table selector.
   - Choose your project (e.g. **`primary-394719`**), dataset (e.g. **`IntactLoadTesting`**), and table (e.g. **`test_daily_loads`**).
   - Click **Select**.
4. In the **Schedule** section:
   - Select **Repeat** (to run on a schedule).
   - Set the frequency to **Daily**.
   - Set the time (e.g. `01:00` UTC) to run after your daily ingestion completes.
5. Click **CONTINUE**.

---

## 📏 Step 3: Define Data Quality Rules

You will add three rules corresponding to the specifications in the YAML configuration.

### Rule 1: Dynamic Missing Date Check (Custom SQL Assertion)
This rule identifies if any expected `load_date` is missing from the last 30 days.

1. Click **+ ADD RULE**.
2. Select **Custom SQL assertion** as the rule type.
3. In the rule editor pane, configure the following:
   - **Rule name**: `all-data-loaded-dynamic`
   - **Description**: `Fails if any load_date is missing from the last 30 days`
   - **Dimension**: Select **Completeness**.
   - **SQL statement**: Paste the following query:
     ```sql
     SELECT expected_date 
     FROM UNNEST(
       GENERATE_DATE_ARRAY(
         DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY), 
         DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
       )
     ) AS expected_date 
     LEFT JOIN ( 
       SELECT DISTINCT load_date 
       FROM ${data()} 
     ) AS actual_table 
       ON expected_date = actual_table.load_date 
     WHERE actual_table.load_date IS NULL
     ```
     > [!NOTE]
     > Dataplex dynamically replaces `${data()}` with the target table during execution, applying any scanning optimizations automatically.
4. **How it works:**
   * **`UNNEST(GENERATE_DATE_ARRAY(...))`**: Generates a list of all calendar dates from the last 30 days (excluding today).
   * **`LEFT JOIN`**: Joins the list of expected dates against the distinct `load_date`s that actually exist in the table.
   * **`WHERE actual_table.load_date IS NULL`**: Filters the results to keep only dates that are present in the expected calendar array but *completely missing* in your table.
   * **Pass/Fail Logic**: In a Custom SQL assertion check, the rule fails if the query returns **any rows**. If all daily loads are present, the query returns 0 rows (passes). If June 1st is missing, it returns `2026-06-01` as a row (fails).
5. Click **ADD**.

### Rule 2: Yesterday's Load Presence (SQL Aggregate Check Rule)
This rule ensures that yesterday's load date exists in the table.

1. Click **+ ADD RULE**.
2. Select **SQL aggregate check rule** as the rule type.
3. In the rule editor pane, configure:
   - **Rule name**: `check-yesterday-load-exists`
   - **Description**: `Ensures yesterday's load_date is present in the table`
   - **Dimension**: Select **Completeness**.
   - **SQL expression**: Enter the following expression:
     ```sql
     COUNTIF(load_date = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)) > 0
     ```
4. **How it works:**
   * **`COUNTIF(load_date = ...)`**: Counts how many rows in the table have a `load_date` equal to yesterday.
   * **`> 0`**: Returns `true` if at least 1 row exists, and `false` if no rows match (meaning yesterday's load is missing).
   * **Pass/Fail Logic**: In a SQL Aggregate check, the expression must evaluate to `true` for the check to pass.
5. Click **ADD**.

### Rule 3: Volume Anomaly Detection (SQL Aggregate Check Rule)
This rule checks if yesterday's row count is a statistical anomaly (outside 3 standard deviations of the 30-day average).

1. Click **+ ADD RULE**.
2. Select **SQL aggregate check rule** as the rule type.
3. In the rule editor pane, configure:
   - **Rule name**: `check-yesterday-volume-anomaly`
   - **Description**: `Fails if yesterday's row count is a statistical anomaly (outside 3 stddevs of 30-day average)`
   - **Dimension**: Choose or enter **Volume** (or select another appropriate category, e.g. **Custom** or **Validity**).
   - **SQL expression**: Paste the following query:
     ```sql
     (
       SELECT
         yesterday.row_count BETWEEN (avg_vol - 3 * stddev_vol) AND (avg_vol + 3 * stddev_vol)
       FROM (
         SELECT
           AVG(daily_vol) AS avg_vol,
           STDDEV(daily_vol) AS stddev_vol
         FROM (
           SELECT load_date, COUNT(1) AS daily_vol
           FROM ${data()}
           WHERE load_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
             AND load_date < DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
           GROUP BY load_date
         )
       ) historical
       CROSS JOIN (
         SELECT COUNT(1) AS row_count
         FROM ${data()}
         WHERE load_date = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
       ) yesterday
     )
     ```
4. **How it works:**
   * **`historical`**: Subquery that groups your table by `load_date` to calculate the transaction counts for each day over the last 30 days. It then computes the average (`avg_vol`) and standard deviation (`stddev_vol`) of these daily counts.
   * **`yesterday`**: Subquery that counts the total transaction rows for yesterday.
   * **`BETWEEN`**: Validates whether yesterday's volume is inside the standard range `[Average - 3 * StdDev, Average + 3 * StdDev]`. If yesterday has abnormally low or high volume (e.g. 0 rows or 500 rows, when the average is ~35), the expression returns `false` (fails).
5. Click **ADD**.

Click **CONTINUE** after all rules have been successfully added.

---

## ⚙️ Step 4: Configure Additional Settings

1. **Export Settings**:
   - Under **Export settings**, toggle **Publish results to BigQuery** to **ON**.
   - Under **BigQuery dataset**, choose dataset `IntactLoadTesting` (or another dataset dedicated to reporting).
   - Specify a table suffix if desired, or let Dataplex auto-create the table.
2. **Set up Email Alerts** (Optional & Recommended):
   - Scroll down to the **Notification report** section.
   - Click **+ Add email ID** and enter your email address (up to 5 addresses are supported).
   - Under **Triggers**, toggle **Quality score (<=)** to **ON** and enter `99` (or `100` to alert if any single rule fails, as any failure will drop the score below 100).
   - Toggle **Job failures** to **ON** to get alerted if the scan execution itself fails.
3. Click **CREATE** to save and provision the data quality scan.

---

## 🚀 Step 5: Run the Scan & View Results

1. Once created, click on your scan named **`test-daily-loads-dq`** from the Data quality scan list.
2. Click **RUN NOW** to trigger the scan immediately.
3. Watch the **Job history** tab below. The status will update from **Running** to **Succeeded** (or **Failed** if quality checks failed).
4. Review the **Results** dashboard tab for rule-by-rule status, dimension breakdowns, and query links to inspect failing rows.

---

## 🧪 Step 6: Testing & Verification Data Setup

To test the data quality scan, you can populate the target table with mock transaction data containing a planned gap (e.g. missing June 1, 2026).

### 1. Populate Target Table
Run this SQL query in the BigQuery Console to create and populate the table with 30-40 records per day between May 1st and June 15th, omitting June 1st:

```sql
-- Create the transaction table (Replace project.dataset.table with your own)
CREATE OR REPLACE TABLE `primary-394719.IntactLoadTesting.test_daily_loads` (
  transaction_id STRING,
  load_date DATE,
  amount NUMERIC,
  customer_id STRING
);

-- Populate it with a gap on June 1, 2026 (Replace project.dataset.table with your own)
INSERT INTO `primary-394719.IntactLoadTesting.test_daily_loads` (
  transaction_id,
  load_date,
  amount,
  customer_id
)
SELECT
  GENERATE_UUID() AS transaction_id,
  day AS load_date,
  CAST(ROUND(RAND() * 500, 2) AS NUMERIC) AS amount,
  CONCAT('CUST-', CAST(CAST(FLOOR(RAND() * 1000) AS INT64) AS STRING)) AS customer_id
FROM
  UNNEST(GENERATE_DATE_ARRAY('2026-05-01', '2026-06-15')) AS day
CROSS JOIN
  UNNEST(GENERATE_ARRAY(1, CAST(30 + FLOOR(RAND() * 11) AS INT64)))
WHERE
  day != '2026-06-01';
```

### 2. Verify Data Ingestion & Gaps
Run this query in BigQuery to check that the dates loaded correctly and that the target gap (June 1st) has exactly 0 records:

```sql
SELECT 
  MIN(load_date) AS min_date,
  MAX(load_date) AS max_date,
  COUNT(DISTINCT load_date) AS total_active_days,
  (SELECT COUNT(1) FROM `primary-394719.IntactLoadTesting.test_daily_loads` WHERE load_date = '2026-06-01') AS missing_day_count -- Replace table path
FROM `primary-394719.IntactLoadTesting.test_daily_loads`; -- Replace table path
```

**Expected output:**
* **min_date**: `2026-05-01`
* **max_date**: `2026-06-15`
* **total_active_days**: `45`
* **missing_day_count**: `0` (indicating June 1st has no data, which will trigger a failure in Rule 1)

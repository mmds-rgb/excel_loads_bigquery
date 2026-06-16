-- SQL script to set up BQ DQ alert table and check queries

-- 1. Create the DQ alert destination table if it doesn't exist
CREATE TABLE IF NOT EXISTS `mmds-477-20260514190152.dataform.dq_alerts` (
  alert_time TIMESTAMP OPTIONS(description="Time when the alert check ran"),
  table_name STRING OPTIONS(description="Table that was checked"),
  rule_name STRING OPTIONS(description="Name of the DQ rule"),
  status STRING OPTIONS(description="Status (e.g. FAIL)"),
  details STRING OPTIONS(description="Detailed alert message")
);

-- 2. DQ Rule: Missing Data Check (Yesterday)
-- Run this daily. It will insert a row only if yesterday's load is missing.
INSERT INTO `mmds-477-20260514190152.dataform.dq_alerts` (
  alert_time,
  table_name,
  rule_name,
  status,
  details
)
SELECT
  CURRENT_TIMESTAMP() AS alert_time,
  'test_daily_loads' AS table_name,
  'missing_yesterday_load' AS rule_name,
  'FAIL' AS status,
  'No rows found for load_date = ' || CAST(DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY) AS STRING) AS details
WHERE NOT EXISTS (
  SELECT 1
  FROM `mmds-477-20260514190152.dataform.test_daily_loads`
  WHERE load_date = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
);

-- 3. DQ Rule: Volume Anomaly Detection
-- Fails and alerts if yesterday's row count is outside 3 standard deviations of 30-day average.
INSERT INTO `mmds-477-20260514190152.dataform.dq_alerts` (
  alert_time,
  table_name,
  rule_name,
  status,
  details
)
WITH stats AS (
  SELECT
    AVG(rows_ingested) AS avg_vol,
    STDDEV(rows_ingested) AS stddev_vol
  FROM `mmds-477-20260514190152.dataform.test_daily_loads`
  WHERE load_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
    AND load_date < DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
),
yesterday AS (
  SELECT COALESCE(SUM(rows_ingested), 0) AS yesterday_vol
  FROM `mmds-477-20260514190152.dataform.test_daily_loads`
  WHERE load_date = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
)
SELECT
  CURRENT_TIMESTAMP() AS alert_time,
  'test_daily_loads' AS table_name,
  'volume_anomaly' AS rule_name,
  'FAIL' AS status,
  FORMAT('Yesterday volume (%d) is outside historical 30-day range [%.2f, %.2f]',
    yesterday_vol,
    avg_vol - 3 * COALESCE(stddev_vol, 0),
    avg_vol + 3 * COALESCE(stddev_vol, 0)
  ) AS details
FROM yesterday, stats
WHERE yesterday_vol NOT BETWEEN (avg_vol - 3 * COALESCE(stddev_vol, 0)) AND (avg_vol + 3 * COALESCE(stddev_vol, 0));

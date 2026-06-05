-- Accelerate 2.0 AMER roster query
-- Source of truth for project roster, AONR, dates, AE, SFDC/Kantata links.
--
-- SUBSTITUTION: {{LEAD_FILTER}} is replaced with the consultant's full name
-- from HUBBLE_LEAD_FILTER before execution. No other modifications allowed.
--
-- To run: hubble-analyst reads this template, substitutes {{LEAD_FILTER}},
-- and executes via run_hubble_query MCP. The query must never be modified
-- beyond the filter substitution unless the user explicitly requests it.
--
-- SCHEMA NOTE: Only primary_contact_email is available from Hubble
-- (email_user_primary in mavenlink.custom_fld_project_proserv). There is no
-- email_user_secondary column. Secondary contacts can only be discovered via
-- handover threads or Gmail scan — never from Hubble.

WITH custom_fields AS (
  SELECT
    p1.project_id,
    p1.email_user_primary AS primary_contact_email,
    p3."project csat link" AS csat_link,
    p3.sfdc_opportunity_owner AS account_executive
  FROM mavenlink.custom_fld_project_proserv p1
  LEFT JOIN mavenlink.custom_fld_project_proserv_three p3
    ON p1.project_id = p3.project_id
    AND p3.day = date_to_day(CURRENT_DATE)
  WHERE p1.day = date_to_day(CURRENT_DATE)
),

accel_type AS (
  SELECT project_id, accelerate_type
  FROM mavenlink.custom_fld_project_proserv_four
  WHERE day = date_to_day(CURRENT_DATE)
),

opp_merchant AS (
  SELECT
    opportunity_id,
    array_agg(merchant_id ORDER BY ts_merchant_created) AS stripe_account_ids
  FROM communia_sales.dim_opportunity_merchant
  WHERE is_real_merchant_id = true
    AND is_current = true
  GROUP BY opportunity_id
)

SELECT
  p.project_id,
  p.project_name,
  p.project_lead_user_name,
  cf.account_executive,
  p.project_geography,
  p.segment AS account_segment,
  p.project_status,
  at.accelerate_type,
  p.overall_project_health,
  p.incremental_onr AS sfdc_aonr,
  format_datetime(p.ds_deployment_start, 'yyyy-MM-dd') AS kantata_start_date,
  format_datetime(p.ds_deployment_end, 'yyyy-MM-dd') AS kantata_end_date,
  concat('https://stripe.lightning.force.com/lightning/r/Opportunity/',
    p.opportunity_id, '/view') AS sfdc_opp_link,
  concat('https://app.mavenlink.com/workspaces/',
    CAST(p.project_id AS varchar)) AS kantata_workspace_link,
  cf.primary_contact_email,
  cf.csat_link,
  om.stripe_account_ids,
  COALESCE(date_diff('day', p.ds_health_report_date, CURRENT_DATE), 999)
    AS days_since_last_health_report,
  p.overall_health_description AS last_health_report_text

FROM communia_sales.agg_mavenlink_projects p
LEFT JOIN custom_fields cf
  ON p.project_id = cf.project_id
LEFT JOIN accel_type at
  ON p.project_id = at.project_id
LEFT JOIN opp_merchant om
  ON p.opportunity_id = om.opportunity_id
WHERE
  p.department = 'ISD'
  AND p.ds_deployment_start >= DATE '2025-02-01'
  AND p.project_status = 'In Progress'
  AND p.project_geography = 'AMER'
  AND at.accelerate_type = 'Accelerate 2.0'
  AND p.project_lead_user_name ILIKE '%{{LEAD_FILTER}}%'
ORDER BY days_since_last_health_report DESC

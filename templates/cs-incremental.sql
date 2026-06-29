-- Accelerate Case Studio — Incremental Pull
-- Pulls new messages since last scan across all open Accelerate Core cases
-- assigned to the consultant.
--
-- Parameters:
--   {{consultant_username}} — Stripe LDAP username (e.g. 'sebastiangtz')
--   {{since_timestamp}}     — ISO timestamp of last scan (space separator, e.g. '2026-06-06 12:00:00')
--
-- Source: mongo.sfdcemailmessages joined to analytics.userops_cases
-- Notes:
--   - message_date returns as Unix epoch integer (convert in Python)
--   - cc_address is semicolon+space separated
--   - is_incoming=false means sent via accelerate@ handle (definitive outbound)
--   - is_incoming=true requires from_address check for direction (see plan)
--   - Status values are lowercase ('closed', 'resolved')

SELECT
    em.sfdc_id              AS message_id,
    em.parent_id            AS case_id,
    em.message_date         AS message_date,
    em.from_address         AS from_address,
    em.to_address           AS to_address,
    em.cc_address           AS cc_address,
    em.from_name            AS from_name,
    em.subject              AS subject,
    em.text_body            AS text_body,
    em.html_body            AS html_body,
    em.is_incoming          AS is_incoming,
    em.has_attachment       AS has_attachment,
    em.thread_identifier    AS thread_identifier,
    uc.subject              AS case_subject,
    uc.status               AS case_status
FROM mongo.sfdcemailmessages em
INNER JOIN analytics.userops_cases uc
    ON em.parent_id = uc.case_id
WHERE
    uc.assignee_email = '{{consultant_username}}@stripe.com'
    AND uc.latest_queue = 'Accelerate Core'
    AND uc.status NOT IN ('closed', 'resolved')
    AND em.message_date > TIMESTAMP '{{since_timestamp}}'
    AND (em.is_bounced IS NULL OR em.is_bounced = false)
    AND (em.is_internal_email IS NULL OR em.is_internal_email = false)
    AND (em.text_body IS NOT NULL OR em.html_body IS NOT NULL)
ORDER BY em.parent_id, em.message_date ASC

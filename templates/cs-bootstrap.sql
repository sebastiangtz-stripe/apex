-- Accelerate Case Studio — Bootstrap Pull (full case history)
-- Pulls the complete conversation history for a single case.
--
-- Parameters:
--   {{case_sfdc_id}} — The SFDC Case ID (e.g. '500VN00000qmFyAYAU')
--
-- Source: mongo.sfdcemailmessages joined to analytics.userops_cases
-- Notes:
--   - message_date returns as Unix epoch integer (convert in Python)
--   - cc_address is semicolon+space separated
--   - No status filter (bootstrap pulls everything regardless of case state)

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
    em.parent_id = '{{case_sfdc_id}}'
    AND (em.is_bounced IS NULL OR em.is_bounced = false)
    AND (em.is_internal_email IS NULL OR em.is_internal_email = false)
    AND (em.text_body IS NOT NULL OR em.html_body IS NOT NULL)
ORDER BY em.message_date ASC

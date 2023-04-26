--
-- If application tables are imported into snowflake, these SnowSQL queries can
-- be used to demonstrate how to join together the various new tables created
-- for LC2 (new learner credit).
--

-- list subsidies created for testing:
  select title, uuid as subsidy_uuid, ledger_id
    from prod.enterprise_subsidy.subsidy_subsidy
   where uuid in (
            '<replace with LC2 Test Subsidy A>', -- LC2 Test Subsidy A
            '<replace with LC2 Test Subsidy B>', -- LC2 Test Subsidy B
            '<replace with LC2 Test Subsidy C>', -- LC2 Test Subsidy C
            '<replace with LC2 Test Subsidy D>', -- LC2 Test Subsidy D
         )
order by title
;

-- list subsidy access policies created for testing:
  select subsidy.title as subsidy_title,
         subsidy.uuid as subsidy_uuid,
         policy.uuid as policy_uuid,
         policy.*
    from prod.enterprise_access.subsidy_access_policy_subsidyaccesspolicy as policy
    join prod.enterprise_subsidy.subsidy_subsidy as subsidy
      on policy.subsidy_uuid = subsidy.uuid
   where subsidy.uuid in (
            '<replace with LC2 Test Subsidy A>', -- LP2 Test Subsidy A
            '<replace with LC2 Test Subsidy B>', -- LP2 Test Subsidy B
            '<replace with LC2 Test Subsidy C>', -- LP2 Test Subsidy C
            '<replace with LC2 Test Subsidy D>', -- LP2 Test Subsidy D
         )
order by subsidy.title
;

-- list transactions created for testing:
  select subsidy.title as subsidy_title,
         subsidy.uuid as subsidy_uuid,
         transaction.*
    from prod.enterprise_subsidy.openedx_ledger_transaction as transaction
    join prod.enterprise_subsidy.subsidy_subsidy as subsidy
      on transaction.ledger_id = subsidy.ledger_id
   where subsidy.uuid in (
            '<replace with LC2 Test Subsidy A>', -- LP2 Test Subsidy A
            '<replace with LC2 Test Subsidy B>', -- LP2 Test Subsidy B
            '<replace with LC2 Test Subsidy C>', -- LP2 Test Subsidy C
            '<replace with LC2 Test Subsidy D>', -- LP2 Test Subsidy D
         )
order by subsidy.title, transaction.created
;

-- list reversals created for testing:
  select subsidy.title as subsidy_title,
         subsidy.uuid as subsidy_uuid,
         transaction.uuid as transaction_uuid,
         reversal.uuid as reversal_uuid,
         reversal.quantity
    from prod.enterprise_subsidy.openedx_ledger_reversal as reversal
    join prod.enterprise_subsidy.openedx_ledger_transaction as transaction
      on reversal.transaction_id = transaction.uuid
    join prod.enterprise_subsidy.subsidy_subsidy as subsidy
      on transaction.ledger_id = subsidy.ledger_id
   where subsidy.uuid in (
            '<replace with LC2 Test Subsidy A>', -- LP2 Test Subsidy A
            '<replace with LC2 Test Subsidy B>', -- LP2 Test Subsidy B
            '<replace with LC2 Test Subsidy C>', -- LP2 Test Subsidy C
            '<replace with LC2 Test Subsidy D>', -- LP2 Test Subsidy D
         )
order by subsidy.title, reversal.created
;

-- list OCM enrollments created for testing:
  select subsidy.title as subsidy_title,
         subsidy.uuid as subsidy_uuid,
         transaction.uuid as transaction_uuid,
         sce.id as enrollment_id,
         sce.is_active as enrollment_is_active,
         sce.mode as enrollment_mode
    from prod.lms.student_courseenrollment as sce
    join prod.enterprise_subsidy.openedx_ledger_transaction as transaction
      on transaction.lms_user_id = sce.user_id and transaction.content_key = sce.course_id
    join prod.enterprise_subsidy.subsidy_subsidy as subsidy
      on transaction.ledger_id = subsidy.ledger_id
   where subsidy.uuid in (
            '<replace with LC2 Test Subsidy A>', -- LP2 Test Subsidy A
            '<replace with LC2 Test Subsidy B>', -- LP2 Test Subsidy B
            '<replace with LC2 Test Subsidy C>', -- LP2 Test Subsidy C
            '<replace with LC2 Test Subsidy D>', -- LP2 Test Subsidy D
         )
order by subsidy.title, transaction.created
;

-- calculate balance of subsidy A (expect it to be $851 = $1000 - $49 - $49 + $49):
with all_quantities as (
  select transaction.quantity
    from prod.enterprise_subsidy.openedx_ledger_transaction as transaction
    join prod.enterprise_subsidy.subsidy_subsidy as subsidy
      on transaction.ledger_id = subsidy.ledger_id
   where subsidy.uuid = '<replace with LC2 Test Subsidy A>'

  union all
 
  select reversal.quantity
    from prod.enterprise_subsidy.openedx_ledger_reversal as reversal
    join prod.enterprise_subsidy.openedx_ledger_transaction as transaction
      on reversal.transaction_id = transaction.uuid
    join prod.enterprise_subsidy.subsidy_subsidy as subsidy
      on transaction.ledger_id = subsidy.ledger_id
   where subsidy.uuid = '<replace with LC2 Test Subsidy A>'
)
select concat('$', sum(quantity) / 100)
  from all_quantities 
;

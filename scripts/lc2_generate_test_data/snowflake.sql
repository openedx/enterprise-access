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

-- List OCM enrollments created for testing.  Link the following chain of models:
--
-- * Subsidy ->
-- * Transaction ->
-- * LearnerCreditEnterpriseCourseEnrollment ->
-- * EnterpriseCourseEnrollment ->
-- * CourseEnrollment
--
  select subsidy.title as subsidy_title,
         subsidy.uuid as subsidy_uuid,
         transaction.uuid as transaction_uuid,
         lcece.id as fulfillment_identifier,
         lcece.is_revoked as fulfillment_revoked,
         ece.id as enterprise_course_enrollment_id,
         sce.id as lms_enrollment_id,
         sce.is_active as lms_enrollment_is_active,
         sce.mode as lms_enrollment_mode
    from prod.enterprise_subsidy.openedx_ledger_transaction as transaction
    join prod.enterprise_subsidy.subsidy_subsidy as subsidy
      on transaction.ledger_id = subsidy.ledger_id
    join prod.lms.enterprise_learnercreditenterprisecourseenrollment lcece
      on lcece.transaction_id = transaction.uuid
    join prod.lms.enterprise_enterprisecourseenrollment ece
      on lcece.enterprise_course_enrollment_id = ece.id
    join prod.lms.student_courseenrollment as sce
      on transaction.lms_user_id = sce.user_id and transaction.content_key = sce.course_id
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
  select iff(transaction.state in ('committed'), transaction.quantity, 0) as committed_quantity,
         iff(transaction.state in ('created', 'pending', 'committed'), transaction.quantity, 0) as pending_quantity
    from prod.enterprise_subsidy.openedx_ledger_transaction as transaction
    join prod.enterprise_subsidy.subsidy_subsidy as subsidy
      on transaction.ledger_id = subsidy.ledger_id
   where subsidy.uuid = '<replace with LC2 Test Subsidy A>'

  union all

  select iff(reversal.state in ('committed'), reversal.quantity, 0) as committed_quantity,
         iff(reversal.state in ('created', 'pending', 'committed'), reversal.quantity, 0) as pending_quantity
    from prod.enterprise_subsidy.openedx_ledger_reversal as reversal
    join prod.enterprise_subsidy.openedx_ledger_transaction as transaction
      on reversal.transaction_id = transaction.uuid
    join prod.enterprise_subsidy.subsidy_subsidy as subsidy
      on transaction.ledger_id = subsidy.ledger_id
   where subsidy.uuid = '<replace with LC2 Test Subsidy A>'
)
select concat('$', sum(pending_quantity) / 100) as pending_balance,
       concat('$', sum(committed_quantity) / 100) as final_balance
  from all_quantities 
;

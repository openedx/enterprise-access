New Learner Credit: Generate Test Data
--------------------------------------

This directory contains a postman collection which can be used to generate a
set of test policies, transactions, enterprise fulfillments, and LMS
enrollments.  NOT currently idempotent, and also does not create the subsidies
or reversals (due to the create endpoint not existing at the time of writing).

Before running the postman collection, create four new subsidies (via Django
Admin) against the same test enterprise customer, and fill in the following
collection variables:

* JWT payload
* JWT signature
* lms_user_id
* subsidy_A_uuid
* subsidy_B_uuid
* subsidy_C_uuid
* subsidy_D_uuid
* enterprise_customer_uuid
* enterprise_customer_catalog_uuid

After running the postman collection, create 4 transaction reversals (via
Django Admin) against the following transactions found via collection
variables:

* transaction_A.2_uuid
* transaction_B.2_uuid
* transaction_C.2_uuid
* transaction_D.2_uuid

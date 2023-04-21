0008 Additional Metadata for Policy Redemption
************************************************************

Status
======

Proposed

Context
=======

This document intends to outline modifications to existing API endpoint(s) such that MFEs can pass additional metadata into the redemption flow. This extention was devised to support required GEAG meatadata such as DOB and terms acceptance dates.

Decision
=======

The GEAG system requires additional metadata about a learner in order to process an allocation. This additional metadata (name, dob, etc) are collected during the enrollment flow on the edX side before enrollment. This additional metadata is not persisted anywhere on the edX side. Because of the lack of persistence and the placement of this information collection on the edX side of the process we decided to create a faicility for the frontend to pass along additional metadata into the redemption flow.


POST access policy redeem transaction
======================================
**/api/v1/policy/<policy_uuid>/redeem/**

This is the existing API endpoint to eedeem subsidy value by making a request to enterprise-subsidy service.

Inputs
------

- ``enterprise_customer_uuid`` (POST data, required): The uuid of the enterprise customer.
- ``lms_user_id`` (POST data, required): The user for whom the transaction is written and for which a enrollment should occur.
- ``content_key`` (POST data, required): The content for which a enrollment is created.
- ``metadata`` (POST data, optional): The new metadata dict
::

  [
    {
      "content_key": "course-v1:ImperialX+dacc003+3T2019",
      "enterprise_customer_uuid": "65653029-35ec-4907-855f-85b148cdfcf7",
      "lms_user_id": 12345,
      "metadata": {
        "geag_first_name": "Mona",
        "geag_last_name": "Lisa",
        "geag_date_of_birth": "1503-01-01",
        "geag_terms_accepted_at": "2021-05-21T17:32:28Z"
    }
  ]

Consequences
************

The subsidy service will need to accept metadata in a similar way so that the policy service can pass the metadata along.


.. _0003 Initial API Specification: 0003-initial-api-specification.rst
.. _0006 API Specification for Enterprise Micro-frontends (MFEs): 0006-api-specification-for-enterprise-mfes.rst
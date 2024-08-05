0023 Constraint on ``spend_limit`` based on ``total_deposits``
********************************************

Status
======
**Accepted** (June 2024)

Context
=======
When a customer provisions a learner-credit-based policy, ``enterprise_subsidy``'s ``Subsidy`` model populates the field 
``starting_balance`` with their initial deposit. Depending on which tool was used to provision the customer's subsidy,
the ``starting_balance`` would initally be set as the sum of all policies ``spend_limit`` or a user defined ``starting_balance``.
A pattern began to emerge where the the sum of all policies associated to a subsidy's ``spend_limit`` field found on the 
``SubsidyAccessPolicy`` model would be greater then the ``total_deposits`` value. The ``total_deposits`` value represents the sum of
all committed deposits and adjustments. (`source <https://github.com/openedx/openedx-ledger/blob/bd498864afdd517391323ee99e91bfb75d5a63e9/openedx_ledger/models.py#L189-L208>`_).
This includes the ``starting_balance`` and any ``adjustments`` on the subsidy.

Having the sum of the ``spend_limit`` for policies be greater then the ``total_deposits`` value does not accurately represent how
the ``spend_limit`` should be used.

Decision
========
Validation was added in the ``SubsidyAccessPolicy`` model's ``clean()`` function to verify that a modified or created ``spend_limit``
would adhere to the following constraint:

* For ``active`` policies, (policies with the same ``subsidy_uuid`` and ``enterprise_customer``)
    * The sum of all policies ``spend_limit`` must not exceed the subsidy record's ``total_deposits``

The purpose of adding the validation within the ``clean()`` function ensures modification to the field via the Django admin screen
adhere to the constraint without modification to the original ``SubsidyAccesPolicy`` model. Modification of the
``SubsidyAccessPolicy`` model would require a backfill heuristic which was deemed too complex due to the ``spend_limit``
``null`` value corresponding to an unlimited ``spend_limit`` budget. 

Additionally, the ``clean()`` function call was added to the  ``subsidy_access_policy`` serializer to ensure that this
new constraint is enforced to any API views that allow modification of the ``spend_limit`` field


Consequences
============
This puts a stricter definition on ``spend_limit`` (amount of allowable spend per policy) not exceeding 
the ``total_deposits`` (amount of allowable spend per subsidy). This ensures that a learner cannot spend more
funds then the subsidy has available.

Furthermore, additional steps are now needed in two scenarios: when modifying ``spend_limit`` across multiple policies;
and when adding an ``adjustment`` to a subsidy.

For example:

* When modifying the ``spend_limit`` of polices that become inactive or set inactive
    * For Subsidy A with ``total_deposits`` value of 50,000 with two policies.
        * Policy A, an ``active`` policy with a ``spend_limit`` of 10,000
        * Policy B, an ``active`` policy with a ``spend_limit`` of 40,000
    * If Policy A were set to ``!active``
        * Policy B's ``spend_limit`` can now be set to 50,000 (even if spend has occured on Policy A or B)

* When modying the ``spend_limit`` of policies with a positive ``adjustment`` made on the subsidy (adding funds)
    * For Subsidy A with ``total_deposits`` value of 50,000 with two policies.
        * Policy A, an ``active`` policy with a ``spend_limit`` of 10,000
        * Policy B, an ``active`` policy with a ``spend_limit`` of 40,000
    * If Subsidy A has a positive adjustment of 10,000
        * Policy A and B has access to an additional 10,000 of ``spend_limit`` that can be added to either policy

* When modying the ``spend_limit`` of policies with a negative ``adjustment`` made on the subsidy (removing funds)
    * For Subsidy A with ``total_deposits`` value of 50,000 with two policies.
        * Policy A, an ``active`` policy with a ``spend_limit`` of 10,000
        * Policy B, an ``active`` policy with a ``spend_limit`` of 40,000
    * If Subsidy A has a negative adjustment of 10,000
        * Policy A and B's ``spend_limit`` would either need to be reduced by a total of 10,000 BEFORE the negative adjustment is made
        * If the negative adjustment already exists before the ``spend_limit`` was reduced a reduction of 10,000 would need to 
          made on either Policy A or Policy B

Alternatives Considered
=======================
* Modify the ``spend_limit`` model field. This alternative was discussed but the backfill heuristic was deemed to complex and inconsistent
  to accurately capture all permutations of ensuring the ``spend_limit`` value was less then the ``total_deposits`` given the unlimited spend polices.

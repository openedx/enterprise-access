0016 Policy Retirement
**********************

Status
======
Proposed - January 2024

Context
=======

Subsidies (i.e. plans) and subsidy access policies (i.e. budgets) each have object-level toggles to enable or disable
core lifecycle functions of those objects:

* ``Subsidy.active_datetime``/``Subsidy.expiration_datetime`` interval allows us to expire a subsidy in accordance with
  the sales contract,
* ``Subsidy.is_soft_deleted`` allows us walk back the creation of a subsidy if it was created mistakenly,
* ``Policy.active`` allows us to deactivate unwanted policies.

These toggles have several different outcomes, but the most relevant ones are:

* Policy visibility on "Learner Credit Management" page

  * When policies are visible, historical redemptions/spend on these policies can be audited by enterprise admins.

* Content redeemability via policy

  * For enterprise learners, this outcome means the ability to redeem for content.
  * For enterprise admins, this outcome means the ability to assign content.

+------------------+-----------------+---------------------+-------------------------------+-----------------------+
| Subsidy expired? | Subsidy         | SubsidyAccessPolicy | Budget visibility on “Learner | Content redeemability |
|                  | is_soft_deleted | active              | Credit Management” page       | via budget            |
+==================+=================+=====================+===============================+=======================+
| No               | FALSE           | TRUE                | ✅ visible                    | ✅ redeemable         |
+------------------+-----------------+---------------------+-------------------------------+-----------------------+
| No               | FALSE           | FALSE               | ❌ NOT visible                | ❌ NOT redeemable     |
+------------------+-----------------+---------------------+-------------------------------+-----------------------+
| No               | TRUE            | TRUE                | ❌ NOT visible                | ❌ NOT redeemable     |
+------------------+-----------------+---------------------+-------------------------------+-----------------------+
| No               | TRUE            | FALSE               | ❌ NOT visible                | ❌ NOT redeemable     |
+------------------+-----------------+---------------------+-------------------------------+-----------------------+
| Yes              | FALSE           | TRUE                | ✅ visible                    | ❌ NOT redeemable     |
+------------------+-----------------+---------------------+-------------------------------+-----------------------+
| Yes              | FALSE           | FALSE               | ❌ NOT visible                | ❌ NOT redeemable     |
+------------------+-----------------+---------------------+-------------------------------+-----------------------+
| Yes              | TRUE            | TRUE                | ❌ NOT visible                | ❌ NOT redeemable     |
+------------------+-----------------+---------------------+-------------------------------+-----------------------+
| Yes              | TRUE            | FALSE               | ❌ NOT visible                | ❌ NOT redeemable     |
+------------------+-----------------+---------------------+-------------------------------+-----------------------+

However, we recently discovered an inflexibility in situations when enterprise customers wish to change the number or
type of policies for their learner credit plan, specifically when there is any policy which is no longer wanted and
historical spend exists. The desired outcome is that content should NOT redeemable via the unwanted policy, however:

* We cannot just set ``SubsidyAccessPolicy.active`` = ``FALSE`` because that would have an undesirable side-effect of hiding
  the policy (and hiding all historical spend) from enterprise admins.
* We cannot just expire the Subsidy object because it may still be in-use by other policies, and we may be contractually
  required to maintain an active subsidy.

Decision
========

We will add configuration flexibility by adding a new field ``redeemability_disabled_at`` to the policy model which
signals that it is not redeemable BUT should remain visible to enterprise admins:

+------------------+-----------------+---------------------+-----------------------------+-------------------------------+-----------------------+
| Subsidy expired? | Subsidy         | SubsidyAccessPolicy | **SubsidyAccessPolicy       | Budget visibility on “Learner | Content redeemability |
|                  | is_soft_deleted | active              | redeemability_disabled_at** | Credit Management” page       | via budget            |
+==================+=================+=====================+=============================+===============================+=======================+
| No               | FALSE           | TRUE                | null                        | ✅ visible                    | ✅ redeemable         |
+------------------+-----------------+---------------------+-----------------------------+-------------------------------+-----------------------+
| No               | FALSE           | TRUE                | **set**                     | ✅ visible                    | ❌ **NOT redeemable** |
+------------------+-----------------+---------------------+-----------------------------+-------------------------------+-----------------------+
| ...              | ...             | ...                 | ...                         | ...                           | ...                   |
+------------------+-----------------+---------------------+-----------------------------+-------------------------------+-----------------------+

In summary:

* The budget/policy is ALWAYS VISIBLE to enterprise admins when subsidy is not deleted and the policy is active.
* The budget/policy is ONLY REDEEMABLE when the subsidy is not expired, the subsidy is not deleted, the policy is
  active, **and the policy does not have redeemability disabled**.

Impacted use cases:

+-----------------------------------------------------+----------------------------------------------------------------+
| Use Case                                            | Actions                                                        |
+=====================================================+================================================================+
| Need to change learner credit plan from 2 to 1      | 1. Set ``SubsidyAccessPolicy.redeemability_disabled_at`` =     |
| budgets. Spend exists.                              |    ``now()`` on unwanted budget.                               |
|                                                     | 2. Update ``SubsidyAccessPolicy.spend_limit`` on remaining     |
|                                                     |    budget(s) as needed.                                        |
+-----------------------------------------------------+----------------------------------------------------------------+
| Need to change the distribution mode of one budget. | 1. Set ``SubsidyAccessPolicy.redeemability_disabled_at`` =     |
| Spend exists.                                       |    ``now()``.                                                  |
|                                                     | 2. Create a new SubsidyAccessPolicy with a different           |
|                                                     |    distribution mode.                                          |
+-----------------------------------------------------+----------------------------------------------------------------+
| Need to change learner credit plan from 2 to 1      | 1. Set ``SubsidyAccessPolicy.active`` = ``FALSE`` on unwanted  |
| budgets. Spend does not exist.                      |    budget.                                                     |
|                                                     | 2. Update ``SubsidyAccessPolicy.spend_limit`` on remaining     |
|                                                     |    budget(s) as needed.                                        |
+-----------------------------------------------------+----------------------------------------------------------------+
| Need to change the distribution mode of one budget. | 1. Set ``SubsidyAccessPolicy.active`` = ``FALSE``.             |
| Spend does not exist.                               | 2. Create a new SubsidyAccessPolicy with a different           |
|                                                     |    distribution mode.                                          |
+-----------------------------------------------------+----------------------------------------------------------------+

Consequences
============

Configuration Complexity
------------------------

Multiple fields on the policy model will now control the redeemability of content, which can be confusing especially if
their behavior is not fully described by their name.

Rejected Alternatives
=====================

Renaming ``active`` -> ``is_soft_deleted`` in addition to adding ``redeemability_disabled_at``
----------------------------------------------------------------------------------------------

This ADR reinforces the concept that ``SubsidyAccessPolicy.active`` mirrors the intended behavior of an "is soft deleted"
field. Similar to ``Subsidy.is_soft_deleted``, ``SubsidyAccessPolicy.active`` allows ECS to agressively erase a policy from
existence, likely due to a mistake in provisioning.

As much as I may like to make this name change in isolation, it does not align with the definition of "active" for
other legacy subsidy types, for which we also use the term "active" to disable AND hide the subsidy. By keeping the
"active" name, we value naming consistency over naming accuracy.

As a compromise, we should clearly document all fields in code and in frontends.

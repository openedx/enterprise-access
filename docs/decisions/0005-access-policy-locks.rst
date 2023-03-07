5. Access Policy Redemption Locks
#################################

Status
******

**Proposed**

March 2023


Context
*******
We've introduced a new Django service, ``enterprise-subsidy``, to provide a new implementation of Learner Credit,
in which learners in an enterprise can redeem the balance of their enterprise's Learner Credit ledger to pay
for verified enrollments in any kind of content supported via Enterprise Catalogs.
See the `Access Policy Functionality ADR`_ for more context.

We need to treat redemptions of learner credit value via an access policy as a `shared resource`,
since they support limits on how much value can be spent, either "per-learner" (i.e. no learner covered by
a policy may spend more than some dollar or enrollment limit), or "per-policy" (i.e. no more than
some dollar or enrollment limit may be consumed in aggregate, across all learners, via the policy).

Decision
********
We'll use a distributed mechanism to lock policies during a redemption request.  See
the `Ledger Distributed Locking ADR`_ for more details. Note that we'll initially
rely on memcached (via Django's cache API) instead of Redis as our distributed store of locks,
as we don't yet have a good "paved road" for using Redis as anything `other than` a celery broker.

Consider the following types of access policies:

- Per-policy spend/enrollment limited: in these policies, value used through
  the policy can only succeed up to some limit; the limit applies `in aggregate` across all transactions
  originating from the policy.  Here, we want to lock the entire policy during redemption;
  no concurrent redemption should be allowed, because the limit on value redemption must take into
  account `all` transactions which originate from the policy.
- Per-learner spend/enrollment limited: in these policies, redemptions for `any user` in the policy
  may only succeed up to some limit.  Here, we want to lock `at least` at the policy level, which means
  no concurrent redemption is allowed by any other transaction (or for any other user) should be allowed.
  Ideally, we'd lock on both the policy ``uuid`` and the learner identifier, so that we are not too
  restrictive.
- Combination per-policy and per-learner limited: This policy type is the union of the first two:
  no more than some policy-level limit can be spent in aggregate, and no individual learner in the policy
  may spend more than some learner-level limit.  In this case, we `must` lock the entire policy during
  redemption, because again, the limit on value redemption must take into account `all` transactions
  which originate from the policy.

During redemption, we'll acquire a lock key that is scoped to the ``uuid`` of the policy through
which redemption occurs.  This means that no other, concurrent request can redeem through this policy
while this lock is held.  This is the simplest implementation of a lock key choice we can make
that covers all the access policy types described above.

Consequences
************

- Locking the entire policy when the policy only applies a per-learner spend cap is fairly restrictive.
  It implies that several learners from the same enterprise customer won't be able to
  concurrently redeem via the same policy.  In practice, this should be a relatively rare occurrence;
  the system throughput is not expected to be particularly high, although the lock duration could
  be on the order of seconds. In this scenario, the client would have to be responsible for any
  retry or communication to the user that a lock could not be acquired.
  We can eventually (or perhaps, immediately) choose to override the lock key choice in the implementing class of that policy type
  to take the learner identifier into account, which would mitigate the negative effects
  in this scenario.
- We're adding a locking layer on top of the existing locking proposed for the `Subsidy layer`
  in `Ledger Distributed Locking ADR`_.  We need both layers of locking, because both the `Policy layer`
  and the `Subsidy layer` have their own, distinct constraints on how much value can be consumed
  via redemption transactions.  Note that it is not necessary for either layer to have knowledger
  of the other layer's lock state - each lock type serves an independent (although related) purpose.

Rejected Alternatives
*********************

- No serious consideration given to other alternatives.


.. _Access Policy Functionality ADR: https://github.com/openedx/enterprise-access/blob/main/docs/decisions/0004-add-access-policy-functionality.rst
.. _Ledger Distributed Locking ADR: https://github.com/openedx/openedx-ledger/blob/main/docs/decisions/0002-ledger-balance-enforcement.rst#approach-3-distributed-locks-using-redis

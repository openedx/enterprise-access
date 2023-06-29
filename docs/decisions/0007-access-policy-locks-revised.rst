0007 Access Policy Redemption Locks, Revised
############################################

Status
******

**Accepted** (April 2023)

*Supercedes `<0005-access-policy-locks.rst>`_*

Context
*******

See `0005 Access Policy Redemption Locks`_.  That ADR was implemented, but leveraged ``TieredCache``, which uses
``get()`` and ``set()`` functions from Memcached to set locks, but ``add()`` is a better choice according to Memcached
authors.  See `Memcached Ghetto Central Locking`_ which describes using ``add()`` to use Memcached for locking purposes.

Decision
********

See `0005 Access Policy Redemption Locks`_.  This revised PR only adds one extra detail on how the locks are stored
to Memcached: instead of using ``TieredCache`` as an interface to Memcached, we'll access it directly via
``django.core.cache``.

Consequences
************

The consequences of this approach are functionally identical to those of `0005 Access Policy Redemption Locks`_.
Furthermore, bypassing ``TieredCache`` will make locking less susceptible to race conditions.

Rejected Alternatives
*********************

See all rejected locking approaches described in `0002 Ledger Balance Enforcement`_.

References
**********

* `0005 Access Policy Redemption Locks`_
* `Memcached Ghetto Central Locking`_
* `0002 Ledger Balance Enforcement`_
* `0004 Ledger Balance Enforcement Revised`_

.. _0005 Access Policy Redemption Locks: https://github.com/openedx/enterprise-access/blob/main/docs/decisions/0004-add-access-policy-functionality.rst
.. _Memcached Ghetto Central Locking: https://github.com/memcached/memcached/wiki/ProgrammingTricks#ghetto-central-locking
.. _0002 Ledger Balance Enforcement: https://github.com/openedx/openedx-ledger/blob/main/docs/decisions/0002-ledger-balance-enforcement.rst#approach-3-distributed-locks-using-redis
.. _0004 Ledger Balance Enforcement Revised: https://github.com/openedx/openedx-ledger/blob/main/docs/decisions/0004-ledger-balance-enforcement-revised.rst

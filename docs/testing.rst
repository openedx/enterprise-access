.. _chapter-testing:

Testing
=======

enterprise_access has an assortment of test cases and code quality
checks to catch potential problems during development.  To run them all in the
version of Python you chose for your virtualenv:

.. code-block:: bash

    $ make validate

To run just the unit tests:

.. code-block:: bash

    $ make test

To run just the unit tests and check diff coverage

.. code-block:: bash

    $ make diff_cover

To run just the code quality checks:

.. code-block:: bash

    $ make quality

To run the unit tests under every supported Python version and the code
quality checks:

.. code-block:: bash

    $ make test-all

To generate and open an HTML report of how much of the code is covered by
test cases:

.. code-block:: bash

    $ make coverage

To run pytest manually and generate a coverage report for a specified module,
use the ``pytest.local.ini`` configuration file, which does not "force" pytest to
report on coverage for the entire project:

.. code-block:: bash

    $ pytest -x enterprise_access/apps/events/  --cov=enterprise_access.apps.events -c pytest.local.ini

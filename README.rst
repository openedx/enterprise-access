Service to manage access to content for enterprise users.

Setting up enterprise-access
--------------------------

Prerequisites
^^^^^^^^^^^^^
- Set the ``DEVSTACK_WORKSPACE`` env variable (either locally or in your shell config file: ``.bash_rc``, ``.zshrc``, or equivalent) to the folder which contains this repo and the `devstack` repo.
  e.g ``export DEVSTACK_WORKSPACE=/home/<your_user>/edx``
- Set up `devstack <https://github.com/edx/devstack>`_

Quick Setup
^^^^^^^^^^^

::

  $ make docker_build
  $ make dev.provision
  $ make dev.up
  $ make app-shell
  # make requirements
  # make validate  # to run full test suite

The server will run on ``localhost:18270``

Running migrations
^^^^^^^^^^^^^^^^^^

::

  $ make app-shell
  # python ./manage.py migrate

Setting up openedx-events
^^^^^^^^^^^^^^^^^^^^^^^^^
Ensure you have installed the ``edx_event_bus_kafka`` and ``openedx_events`` requirements. Entering
a shell with ``make app-shell`` and then running ``make requirements`` should install these for you.

From your host, run ``make dev.up.with-events``, which will start a local kafka container for you.
Visit http://localhost:9021/clusters to access the local "Confluent Control Center".
Confluent is like a cloud wrapper around "vanilla" Kafka.

Your ``devstack.py`` settings should already be configured to point at this event broker,
and to configure enterprise-access as an openedx event consumer and produer.

We have a specific enterprise "ping" event and management command defined to test
that your local event bus is well-configured. Open a shell with ``make app-shell`` and run::

  ./manage.py consume_enterprise_ping_events

This will consume ping events from the ``dev-enterprise-core`` topic.
You may see a ``Broker: Unknown topic`` error the first time you run it.  When you run your
test event production below, that error will resolve (producing the event creates the topic
if it does not exist). **Leave the consumer running.** You should see the ``enterprise-access-service``
as a registered consumer in your local confluent control center.

Now, go over to your **enterprise-subsidy** directory. Make sure requirements are installed,
specifically the ``edx_event_bus_kafka`` and ``openedx_events`` packages. Use ``make app-shell``
in this repo and we'll *produce* a ping event::

  ./manage.py produce_enterprise_ping_event

If this event was successfully produced, you'll see a log message that says
``Message delivered to Kafka event bus: topic=dev-events-testing``.
You should also now see the ``dev-events-testing`` topic available in your local confluent
control center, and even the test events that are being published to the topic.

A note on creating SubsidyRequestCustomerConfiguration Objects locally
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

*Important note*

In a devstack enviroment, login to the LMS and navigate to any
MFE before creating SubsidyRequestCustomerConfiguration objects in the
enterprise-access Django admin.

*Why*

If you create a SubsidyRequestCustomerConfiguration in the Django
admin, because we keep track of who changed the field, we need to grab the
"who" from somewhere. In our case, we use the jwt payload header combined
with the signature, which will be populated in your cookies when you go to an
MFE while logged in. We can't use the edx-jwt-cookie outright because it
won't be set by default when navigating to the django admin.

Analytics
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
This project integrates with Segment and sends events through the analytics package.
Events are dispatched in endpoints that modify relevant data by calling `track_event` in the track app.
See `segment_events.rst <docs/segment_events.rst>`_ for more details on currently implemented events.

Every time you want to contribute something in this repo
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
.. code-block::

  # Make a new branch for your changes
  git checkout -b <your_github_username>/<short_description>

  # Run your new tests
  make app-shell
  pytest -c pytest.local.ini ./path/to/new/tests

  # Run all the tests and quality checks
  make validate

  # Commit all your changes
  git commit …
  git push

  # Open a PR and ask for review!


Documentation
-------------

(TODO: `Set up documentation <https://openedx.atlassian.net/wiki/spaces/DOC/pages/21627535/Publish+Documentation+on+Read+the+Docs>`_)


License
-------

The code in this repository is licensed under the AGPL 3.0 unless
otherwise noted.

Please see `LICENSE.txt <LICENSE.txt>`_ for details.

How To Contribute
-----------------

Contributions are very welcome.
Please read `How To Contribute <https://github.com/openedx/.github/blob/master/CONTRIBUTING.md>`_ for details.
should be followed for all Open edX projects.

The pull request description template should be automatically applied if you are creating a pull request from GitHub. Otherwise you
can find it at `PULL_REQUEST_TEMPLATE.md <.github/PULL_REQUEST_TEMPLATE.md>`_.

The issue report template should be automatically applied if you are creating an issue on GitHub as well. Otherwise you
can find it at `ISSUE_TEMPLATE.md <.github/ISSUE_TEMPLATE.md>`_.

Reporting Security Issues
-------------------------

Please do not report security issues in public. Please email security@openedx.org.

Getting Help
------------

If you're having trouble, we have discussion forums at https://discuss.openedx.org where you can connect with others in the community.

Our real-time conversations are on Slack. You can request a `Slack invitation`_, then join our `community Slack workspace`_.

For more information about these options, see the `Getting Help`_ page.

.. _Slack invitation: https://openedx-slack-invite.herokuapp.com/
.. _community Slack workspace: https://openedx.slack.com/
.. _Getting Help: https://openedx.org/getting-help

.. |pypi-badge| image:: https://img.shields.io/pypi/v/enterprise-access.svg
    :target: https://pypi.python.org/pypi/enterprise-access/
    :alt: PyPI

.. |ci-badge| image:: https://github.com/edx/enterprise-access/workflows/Python%20CI/badge.svg?branch=main
    :target: https://github.com/edx/enterprise-access/actions
    :alt: CI

.. |codecov-badge| image:: https://codecov.io/github/edx/enterprise-access/coverage.svg?branch=main
    :target: https://codecov.io/github/edx/enterprise-access?branch=main
    :alt: Codecov

.. |doc-badge| image:: https://readthedocs.org/projects/enterprise-access/badge/?version=latest
    :target: https://enterprise-access.readthedocs.io/en/latest/
    :alt: Documentation

.. |pyversions-badge| image:: https://img.shields.io/pypi/pyversions/enterprise-access.svg
    :target: https://pypi.python.org/pypi/enterprise-access/
    :alt: Supported Python versions

.. |license-badge| image:: https://img.shields.io/github/license/edx/enterprise-access.svg
    :target: https://github.com/edx/enterprise-access/blob/main/LICENSE.txt
    :alt: License

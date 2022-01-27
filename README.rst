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

A note on creating SubsidyRequestCustomerConfiguration Objects locally
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

*Important note*

In a devstack enviroment, login to the LMS and navigate to any
MFE before creating SubsidyRequestCustomerConfiguration objects in the
enterprise-access Django admin

*Why*

If you create a SubsidyRequestCustomerConfiguration in the Django
admin, because we keep track of who changed the field, we need to grab the
"who" from somewhere. In our case, we use the jwt payload header combined
with the signature, which will be populated in your cookies when you go to an
MFE while logged in. We can't use the edx-jwt-cookie outright because it
won't be set by default when navigating to the django admin.

Every time you want to contribute something in this repo
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
.. code-block::

  # Make a new branch for your changes
  git checkout -b <your_github_username>/<short_description>

  # Run your new tests
  make app-shell
  pytest ./path/to/new/tests

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
Please read `How To Contribute <https://github.com/edx/edx-platform/blob/master/CONTRIBUTING.rst>`_ for details.
Even though they were written with ``edx-platform`` in mind, the guidelines
should be followed for all Open edX projects.

The pull request description template should be automatically applied if you are creating a pull request from GitHub. Otherwise you
can find it at `PULL_REQUEST_TEMPLATE.md <.github/PULL_REQUEST_TEMPLATE.md>`_.

The issue report template should be automatically applied if you are creating an issue on GitHub as well. Otherwise you
can find it at `ISSUE_TEMPLATE.md <.github/ISSUE_TEMPLATE.md>`_.

Reporting Security Issues
-------------------------

Please do not report security issues in public. Please email security@edx.org.

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

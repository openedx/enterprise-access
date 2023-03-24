FROM ubuntu:focal as app
MAINTAINER sre@edx.org


# Packages installed:
# git; Used to pull in particular requirements from github rather than pypi,
# and to check the sha of the code checkout.

# build-essentials; so we can use make with the docker container

# language-pack-en locales; ubuntu locale support so that system utilities have a consistent
# language and time zone.

# python; ubuntu doesnt ship with python, so this is the python we will use to run the application

# python3-pip; install pip to install application requirements.txt files

# libmysqlclient-dev; to install header files needed to use native C implementation for
# MySQL-python for performance gains.

# libssl-dev; # mysqlclient wont install without this.

# python3-dev; to install header files for python extensions; much wheel-building depends on this

# gcc; for compiling python extensions distributed with python packages like mysql-client

# If you add a package here please include a comment above describing what it is used for
RUN apt-get update && apt-get -qy install --no-install-recommends \
 language-pack-en \
 locales \
 python3.8 \
 python3-pip \
 python3.8-venv \
 python3.8-dev \
 libmysqlclient-dev \
 libssl-dev \
 build-essential \
 git \ 
 wget

RUN pip install --upgrade pip setuptools
# delete apt package lists because we do not need them inflating our image
RUN rm -rf /var/lib/apt/lists/*

# Create a virtualenv for sanity
ENV VIRTUAL_ENV=/edx/venvs/enterprise-access
RUN python3.8 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /tmp
RUN wget https://packages.confluent.io/clients/deb/pool/main/libr/librdkafka/librdkafka_2.0.2.orig.tar.gz
RUN tar -xf librdkafka_2.0.2.orig.tar.gz
WORKDIR /tmp/librdkafka-2.0.2
RUN ./configure && make && make install && ldconfig

RUN ln -s /usr/bin/python3 /usr/bin/python

RUN locale-gen en_US.UTF-8
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US:en
ENV LC_ALL en_US.UTF-8
ENV DJANGO_SETTINGS_MODULE enterprise_access.settings.production

EXPOSE 18270
EXPOSE 18271
RUN useradd -m --shell /bin/false app

WORKDIR /edx/app/enterprise-access

# Copy the requirements explicitly even though we copy everything below
# this prevents the image cache from busting unless the dependencies have changed.
COPY requirements/production.txt /edx/app/enterprise-access/requirements/production.txt
COPY requirements/pip.txt /edx/app/enterprise-access/requirements/pip.txt

# Dependencies are installed as root so they cannot be modified by the application user.
RUN pip install -r requirements/pip.txt
RUN pip install -r requirements/production.txt

RUN mkdir -p /edx/var/log

# Code is owned by root so it cannot be modified by the application user.
# So we copy it before changing users.
USER app

# Gunicorn 19 does not log to stdout or stderr by default. Once we are past gunicorn 19, the logging to STDOUT need not be specified.
CMD gunicorn --workers=2 --name enterprise-access -c /edx/app/enterprise-access/enterprise_access/docker_gunicorn_configuration.py --log-file - --max-requests=1000 enterprise_access.wsgi:application

# This line is after the requirements so that changes to the code will not
# bust the image cache
COPY . /edx/app/enterprise-access

FROM app as newrelic
RUN pip install newrelic
CMD newrelic-admin run-program gunicorn --workers=2 --name enterprise-access -c /edx/app/enterprise-access/enterprise_access/docker_gunicorn_configuration.py --log-file - --max-requests=1000 enterprise_access.wsgi:application

FROM app as devstack
USER root
COPY requirements/dev.txt /edx/app/enterprise-access/requirements/dev.txt
RUN pip install -r requirements/dev.txt
USER app
CMD gunicorn --workers=2 --name enterprise-access -c /edx/app/enterprise-access/enterprise_access/docker_gunicorn_configuration.py --log-file - --max-requests=1000 enterprise_access.wsgi:application

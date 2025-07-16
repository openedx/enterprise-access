import os
from os.path import abspath, dirname, join

from corsheaders.defaults import default_headers as corsheaders_default_headers

from enterprise_access.apps.core.constants import (
    BFF_ADMIN_ROLE,
    BFF_LEARNER_ROLE,
    BFF_OPERATOR_ROLE,
    CONTENT_ASSIGNMENTS_ADMIN_ROLE,
    CONTENT_ASSIGNMENTS_LEARNER_ROLE,
    CONTENT_ASSIGNMENTS_OPERATOR_ROLE,
    CUSTOMER_BILLING_ADMIN_ROLE,
    CUSTOMER_BILLING_OPERATOR_ROLE,
    PROVISIONING_ADMIN_ROLE,
    REQUESTS_ADMIN_ROLE,
    REQUESTS_LEARNER_ROLE,
    SUBSIDY_ACCESS_POLICY_LEARNER_ROLE,
    SUBSIDY_ACCESS_POLICY_OPERATOR_ROLE,
    SYSTEM_ENTERPRISE_ADMIN_ROLE,
    SYSTEM_ENTERPRISE_LEARNER_ROLE,
    SYSTEM_ENTERPRISE_OPERATOR_ROLE,
    SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE
)
from enterprise_access.settings.utils import get_logger_config

# PATH vars
PROJECT_ROOT = join(abspath(dirname(__file__)), "..")


def root(*path_fragments):
    return join(abspath(PROJECT_ROOT), *path_fragments)


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('ENTERPRISE_ACCESS_SECRET_KEY', 'insecure-secret-key')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

ALLOWED_HOSTS = []

# Application definition

INSTALLED_APPS = (
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'drf_spectacular',
    'drf_yasg',
    'edx_api_doc_tools',
    'openedx_events',
    'release_util',
)

THIRD_PARTY_APPS = (
    'corsheaders',
    'crispy_forms',
    'crispy_bootstrap5',
    'csrf.apps.CsrfAppConfig',  # Enables frontend apps to retrieve CSRF tokens,
    'djangoql',
    'django_celery_results',
    'django_countries',
    'django_filters',
    'django_object_actions',
    'rest_framework',
    'rest_framework_swagger',
    'rules.apps.AutodiscoverRulesConfig',
    'simple_history',
    'social_django',
    'waffle',
)

PROJECT_APPS = (
    'enterprise_access.apps.track',
    'enterprise_access.apps.core',
    'enterprise_access.apps.subsidy_request',
    'enterprise_access.apps.api',
    'enterprise_access.apps.events',
    'enterprise_access.apps.subsidy_access_policy',
    'enterprise_access.apps.content_assignments',
    'enterprise_access.apps.enterprise_groups',
    'enterprise_access.apps.bffs',
    'enterprise_access.apps.provisioning',
    'enterprise_access.apps.customer_billing',
)

INSTALLED_APPS += THIRD_PARTY_APPS
INSTALLED_APPS += PROJECT_APPS

MIDDLEWARE = (
    'log_request_id.middleware.RequestIDMiddleware',
    # Resets RequestCache utility for added safety.
    'edx_django_utils.cache.middleware.RequestCacheMiddleware',
    'edx_django_utils.monitoring.DeploymentMonitoringMiddleware',
    # Enables monitoring utility for writing custom metrics.
    'edx_django_utils.monitoring.CachedCustomMonitoringMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'edx_rest_framework_extensions.auth.jwt.middleware.JwtAuthCookieMiddleware',
    'edx_rest_framework_extensions.auth.jwt.middleware.JwtRedirectToLoginIfUnauthenticatedMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'social_django.middleware.SocialAuthExceptionMiddleware',
    'waffle.middleware.WaffleMiddleware',
    # Enables force_django_cache_miss functionality for TieredCache.
    'edx_django_utils.cache.middleware.TieredCacheMiddleware',
    # Outputs monitoring metrics for a request.
    'edx_rest_framework_extensions.middleware.RequestCustomAttributesMiddleware',
    # Ensures proper DRF permissions in support of JWTs
    'edx_rest_framework_extensions.auth.jwt.middleware.EnsureJWTAuthSettingsMiddleware',
    # Track who made each change to a model using HistoryRequestMiddleware
    'simple_history.middleware.HistoryRequestMiddleware',
    # Used to get request inside serializers.
    'crum.CurrentRequestUserMiddleware',
)

# https://github.com/dabapps/django-log-request-id
LOG_REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"
GENERATE_REQUEST_ID_IF_NOT_IN_HEADER = False
REQUEST_ID_RESPONSE_HEADER = "X-Request-ID"
NO_REQUEST_ID = "None"
LOG_REQUESTS = False

# Enable CORS
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = corsheaders_default_headers + (
    'use-jwt-cookie',
)
CORS_ORIGIN_WHITELIST = []

ROOT_URLCONF = 'enterprise_access.urls'

# Python dotted path to the WSGI application used by Django's runserver.
WSGI_APPLICATION = 'enterprise_access.wsgi.application'

# Database
# https://docs.djangoproject.com/en/2.2/ref/settings/#databases
# Set this value in the environment-specific files (e.g. local.py, production.py, test.py)
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.',
        'NAME': '',
        'USER': '',
        'PASSWORD': '',
        'HOST': '',  # Empty for localhost through domain sockets or '127.0.0.1' for localhost through TCP.
        'PORT': '',  # Set to empty string for default.
        # The default isolation level for MySQL is REPEATABLE READ, which is a little too aggressive
        # for our needs, particularly around reading celery task state via django-celery-results.
        # https://dev.mysql.com/doc/refman/8.0/en/innodb-transaction-isolation-levels.html#isolevel_read-committed
        'OPTIONS': {
            'isolation_level': 'read committed',
        },
    }
}

# Django Rest Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'edx_rest_framework_extensions.auth.jwt.authentication.JwtAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
        'rest_framework.permissions.IsAdminUser',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'PAGE_SIZE': 100,
    'TEST_REQUEST_DEFAULT_FORMAT': 'json',
    'DEFAULT_THROTTLE_RATES': {
        'bff_unauthenticated': '100/hour',
    },
}

# DRF Spectacular settings
SPECTACULAR_SETTINGS = {
    'TITLE': 'Enterprise Access API',
    'DESCRIPTION': 'API for controlling request-based access to enterprise subsidized enrollments.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}

# Internationalization
# https://docs.djangoproject.com/en/dev/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True


USE_TZ = True

# Django 4.0+ uses zoneinfo if this is not set. We can remove this and
# migrate to zoneinfo after Django 4.2 upgrade. See more on following url
# https://docs.djangoproject.com/en/4.2/releases/4.0/#zoneinfo-default-timezone-implementation
USE_DEPRECATED_PYTZ = True

LOCALE_PATHS = (
    root('conf', 'locale'),
)


# MEDIA CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#media-root
MEDIA_ROOT = root('media')

# See: https://docs.djangoproject.com/en/dev/ref/settings/#media-url
MEDIA_URL = '/media/'
# END MEDIA CONFIGURATION


# STATIC FILE CONFIGURATION
# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-root
STATIC_ROOT = root('assets')

# See: https://docs.djangoproject.com/en/dev/ref/settings/#static-url
STATIC_URL = '/static/'

# See: https://docs.djangoproject.com/en/dev/ref/contrib/staticfiles/#std:setting-STATICFILES_DIRS
STATICFILES_DIRS = (
    root('static'),
)

# TEMPLATE CONFIGURATION
# See: https://docs.djangoproject.com/en/2.2/ref/settings/#templates
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'APP_DIRS': True,
        'DIRS': (
            root('templates'),
        ),
        'OPTIONS': {
            'context_processors': (
                'django.contrib.auth.context_processors.auth',
                'django.template.context_processors.debug',
                'django.template.context_processors.i18n',
                'django.template.context_processors.media',
                'django.template.context_processors.request',
                'django.template.context_processors.static',
                'django.template.context_processors.tz',
                'django.contrib.messages.context_processors.messages',
                'enterprise_access.apps.core.context_processors.core',
            ),
            'debug': True,  # Django will only display debug pages if the global DEBUG setting is set to True.
        }
    },
]
# END TEMPLATE CONFIGURATION


# COOKIE CONFIGURATION
# The purpose of customizing the cookie names is to avoid conflicts when
# multiple Django services are running behind the same hostname.
# Detailed information at: https://docs.djangoproject.com/en/dev/ref/settings/
SESSION_COOKIE_NAME = 'enterprise_access_sessionid'
CSRF_COOKIE_NAME = 'enterprise_access_csrftoken'
LANGUAGE_COOKIE_NAME = 'enterprise_access_language'
# END COOKIE CONFIGURATION

CSRF_COOKIE_SECURE = False
CSRF_TRUSTED_ORIGINS = []

# AUTHENTICATION CONFIGURATION
LOGIN_URL = '/login/'
LOGOUT_URL = '/logout/'

AUTH_USER_MODEL = 'core.User'

AUTHENTICATION_BACKENDS = (
    'auth_backends.backends.EdXOAuth2',
    'django.contrib.auth.backends.ModelBackend',
    'rules.permissions.ObjectPermissionBackend',
    'django.contrib.auth.backends.ModelBackend',
)

ENABLE_AUTO_AUTH = False
AUTO_AUTH_USERNAME_PREFIX = 'auto_auth_'

SOCIAL_AUTH_STRATEGY = 'auth_backends.strategies.EdxDjangoStrategy'

# Set these to the correct values for your OAuth2 provider (e.g., LMS)
OAUTH2_PROVIDER_URL = 'http://edx.devstack.lms:18000/oauth2'
SOCIAL_AUTH_EDX_OAUTH2_KEY = 'replace-me'
SOCIAL_AUTH_EDX_OAUTH2_SECRET = 'replace-me'
SOCIAL_AUTH_EDX_OAUTH2_URL_ROOT = 'replace-me'
SOCIAL_AUTH_EDX_OAUTH2_LOGOUT_URL = 'replace-me'
BACKEND_SERVICE_EDX_OAUTH2_KEY = 'replace-me'
BACKEND_SERVICE_EDX_OAUTH2_SECRET = 'replace-me'

JWT_AUTH = {
    'JWT_AUTH_HEADER_PREFIX': 'JWT',
    'JWT_ISSUER': 'http://127.0.0.1:18000/oauth2',
    'JWT_ALGORITHM': 'HS256',
    'JWT_VERIFY_EXPIRATION': True,
    'JWT_PAYLOAD_GET_USERNAME_HANDLER': lambda d: d.get('preferred_username'),
    'JWT_LEEWAY': 1,
    'JWT_DECODE_HANDLER': 'edx_rest_framework_extensions.auth.jwt.decoder.jwt_decode_handler',
    'JWT_PUBLIC_SIGNING_JWK_SET': None,
    'JWT_AUTH_COOKIE': 'edx-jwt-cookie',
    'JWT_AUTH_COOKIE_HEADER_PAYLOAD': 'edx-jwt-cookie-header-payload',
    'JWT_AUTH_COOKIE_SIGNATURE': 'edx-jwt-cookie-signature',
    'JWT_SECRET_KEY': 'SET-ME-PLEASE',
    # JWT_ISSUERS enables token decoding for multiple issuers (Note: This is not a native DRF-JWT field)
    # We use it to allow different values for the 'ISSUER' field, but keep the same SECRET_KEY and
    # AUDIENCE values across all issuers.
    'JWT_ISSUERS': [
        {
            'AUDIENCE': 'SET-ME-PLEASE',
            'ISSUER': 'http://localhost:18000/oauth2',
            'SECRET_KEY': 'SET-ME-PLEASE'
        },
    ],
}

EDX_DRF_EXTENSIONS = {
    "JWT_PAYLOAD_USER_ATTRIBUTE_MAPPING": {
        "administrator": "is_staff",
        "email": "email",
        "full_name": "full_name",
        "user_id": "lms_user_id",
    },
}

# Set up system-to-feature roles mapping for edx-rbac
SYSTEM_TO_FEATURE_ROLE_MAPPING = {
    SYSTEM_ENTERPRISE_OPERATOR_ROLE: [
        SUBSIDY_ACCESS_POLICY_OPERATOR_ROLE,
        CONTENT_ASSIGNMENTS_OPERATOR_ROLE,
        REQUESTS_ADMIN_ROLE,
        BFF_OPERATOR_ROLE,
        PROVISIONING_ADMIN_ROLE,
        CUSTOMER_BILLING_OPERATOR_ROLE,
    ],
    SYSTEM_ENTERPRISE_ADMIN_ROLE: [
        # enterprise admins only need learner-level access to Subsidy Access Policy APIs since they aren't responsible
        # for managing them.
        SUBSIDY_ACCESS_POLICY_LEARNER_ROLE,
        CONTENT_ASSIGNMENTS_ADMIN_ROLE,
        REQUESTS_ADMIN_ROLE,
        BFF_ADMIN_ROLE,
        CUSTOMER_BILLING_ADMIN_ROLE,
    ],
    SYSTEM_ENTERPRISE_LEARNER_ROLE: [
        SUBSIDY_ACCESS_POLICY_LEARNER_ROLE,
        CONTENT_ASSIGNMENTS_LEARNER_ROLE,
        REQUESTS_LEARNER_ROLE,
        BFF_LEARNER_ROLE,
    ],
    SYSTEM_ENTERPRISE_PROVISIONING_ADMIN_ROLE: [
        PROVISIONING_ADMIN_ROLE,
    ],
}

# Request the user's permissions in the ID token
EXTRA_SCOPE = ['permissions']

# TODO Set this to another (non-staff, ideally) path.
LOGIN_REDIRECT_URL = '/admin/'
# END AUTHENTICATION CONFIGURATION


# OPENEDX-SPECIFIC CONFIGURATION
PLATFORM_NAME = 'Your Platform Name Here'
# END OPENEDX-SPECIFIC CONFIGURATION

# Override the default logging format string (default defined within utils.py).
LOGGING_FORMAT_STRING = os.environ.get("LOGGING_FORMAT_STRING", None)

# Set up logging for development use (logging to stdout)
LOGGING = get_logger_config(debug=DEBUG, format_string=LOGGING_FORMAT_STRING)


"""############################# BEGIN CELERY CONFIG ##################################"""

# Message configuration
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_COMPRESSION = 'gzip'
CELERY_RESULT_COMPRESSION = 'gzip'

# Results configuration
CELERY_TASK_IGNORE_RESULT = False
CELERY_TASK_STORE_ERRORS_EVEN_IF_IGNORED = True

# Events configuration
CELERY_TASK_TRACK_STARTED = True
CELERY_WORKER_SEND_TASK_EVENTS = True
CELERY_TASK_SEND_SENT_EVENT = True

# Celery task routing configuration.
# Only the enterprise_access worker should receive enterprise_access tasks.
# Explicitly define these to avoid name collisions with other services
# using the same broker and the standard default queue name of "celery".
CELERY_TASK_DEFAULT_EXCHANGE = os.environ.get('CELERY_DEFAULT_EXCHANGE', 'enterprise_access')
CELERY_TASK_DEFAULT_ROUTING_KEY = os.environ.get('CELERY_DEFAULT_ROUTING_KEY', 'enterprise_access')
CELERY_TASK_DEFAULT_QUEUE = os.environ.get('CELERY_DEFAULT_QUEUE', 'enterprise_access.default')

# Celery Broker
# These settings need not be set if CELERY_TASK_ALWAYS_EAGER == True, like in Standalone.
# Devstack overrides these in its docker-compose.yml.
# Production environments can override these to be whatever they want.
CELERY_BROKER_TRANSPORT = os.environ.get('CELERY_BROKER_TRANSPORT', '')
CELERY_BROKER_HOSTNAME = os.environ.get('CELERY_BROKER_HOSTNAME', '')
CELERY_BROKER_VHOST = os.environ.get('CELERY_BROKER_VHOST', '')
CELERY_BROKER_USER = os.environ.get('CELERY_BROKER_USER', '')
CELERY_BROKER_PASSWORD = os.environ.get('CELERY_BROKER_PASSWORD', '')
CELERY_BROKER_URL = '{}://{}:{}@{}/{}'.format(
    CELERY_BROKER_TRANSPORT,
    CELERY_BROKER_USER,
    CELERY_BROKER_PASSWORD,
    CELERY_BROKER_HOSTNAME,
    CELERY_BROKER_VHOST
)
CELERY_RESULT_BACKEND = 'django-db'
# see https://github.com/celery/django-celery-results/issues/326
# on CELERY_RESULT_EXTENDED
CELERY_RESULT_EXTENDED = True

# Celery task time limits.
# Tasks will be asked to quit after four minutes, and un-gracefully killed
# after five.
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_TASK_TIME_LIMIT = 300

CELERY_BROKER_TRANSPORT_OPTIONS = {
    'fanout_patterns': True,
    'fanout_prefix': True,
}

TASK_MAX_RETRIES = 5
"""############################# END CELERY CONFIG ##################################"""


################### Kafka Related Settings ##############################

KAFKA_ENABLED = False

SERVICE_VARIANT = 'enterprise_access'

KAFKA_BOOTSTRAP_SERVER = ''
KAFKA_API_KEY = ''
KAFKA_API_SECRET = ''
SCHEMA_REGISTRY_API_KEY = ''
SCHEMA_REGISTRY_API_SECRET = ''
SCHEMA_REGISTRY_URL = ''
KAFKA_PARTITIONS_PER_TOPIC = 1
# This number is dictated by the cluster setup
KAFKA_REPLICATION_FACTOR_PER_TOPIC = 3

COUPON_CODE_REQUEST_TOPIC_NAME = "coupon-code-request"
LICENSE_REQUEST_TOPIC_NAME = "license-request"
ACCESS_POLICY_TOPIC_NAME = "access-policy"
SUBSIDY_REDEMPTION_TOPIC_NAME = "subsidy-redemption"
KAFKA_TOPICS = [
    COUPON_CODE_REQUEST_TOPIC_NAME,
    LICENSE_REQUEST_TOPIC_NAME,

    # Access policy events
    ACCESS_POLICY_TOPIC_NAME,
    SUBSIDY_REDEMPTION_TOPIC_NAME,
]


################### End Kafka Related Settings ##############################

# Default URLS for external services
LICENSE_MANAGER_URL = ''
LMS_URL = ''
ECOMMERCE_URL = ''
ENTERPRISE_LEARNER_PORTAL_URL = ''
ENTERPRISE_ADMIN_PORTAL_URL = ''
DISCOVERY_URL = ''
ENTERPRISE_CATALOG_URL = ''
ENTERPRISE_SUBSIDY_URL = ''
ENTERPRISE_ACCESS_URL = ''

# API Client timeouts
LICENSE_MANAGER_CLIENT_TIMEOUT = os.environ.get('LICENSE_MANAGER_CLIENT_TIMEOUT', 45)
LMS_CLIENT_TIMEOUT = os.environ.get('LMS_CLIENT_TIMEOUT', 45)
ECOMMERCE_CLIENT_TIMEOUT = os.environ.get('ECOMMERCE_CLIENT_TIMEOUT', 45)
DISCOVERY_CLIENT_TIMEOUT = os.environ.get('DISCOVERY_CLIENT_TIMEOUT', 45)
SUBSIDY_CLIENT_TIMEOUT = os.environ.get('SUBSIDY_CLIENT_TIMEOUT', 45)

# Braze campaigns for browse and request (apps.subsidy_request)
BRAZE_NEW_REQUESTS_NOTIFICATION_CAMPAIGN = ''
BRAZE_APPROVE_NOTIFICATION_CAMPAIGN = ''
BRAZE_DECLINE_NOTIFICATION_CAMPAIGN = ''
BRAZE_AUTO_DECLINE_NOTIFICATION_CAMPAIGN = ''

# Braze campaigns for content assignments (apps.content_assignments)
BRAZE_ASSIGNMENT_NOTIFICATION_CAMPAIGN = ''
BRAZE_ASSIGNMENT_REMINDER_NOTIFICATION_CAMPAIGN = ''

# Budget deactivation settings
ALLOW_BUDGET_DEACTIVATION_WITH_SPEND = False

BRAZE_ASSIGNMENT_REMINDER_POST_LOGISTRATION_NOTIFICATION_CAMPAIGN = ''
BRAZE_ASSIGNMENT_NUDGE_EXEC_ED_ACCEPTED_ASSIGNMENT_CAMPAIGN = ''
BRAZE_ASSIGNMENT_CANCELLED_NOTIFICATION_CAMPAIGN = ''
BRAZE_ASSIGNMENT_AUTOMATIC_CANCELLATION_NOTIFICATION_CAMPAIGN = ''

# Braze configuration
BRAZE_API_URL = ''
BRAZE_API_KEY = os.environ.get('BRAZE_API_KEY', '')
BRAZE_APP_ID = os.environ.get('BRAZE_APP_ID', '')

# Enterprise Subsidy API Client settings
ENTERPRISE_SUBSIDY_API_CLIENT_VERSION = 2

# Allows broader modification of access policy records from django admin
DJANGO_ADMIN_POLICY_SUPER_ADMIN = False

# Defines error bounds for allocation price validation
ALLOCATION_PRICE_VALIDATION_LOWER_BOUND_RATIO = .95
ALLOCATION_PRICE_VALIDATION_UPPER_BOUND_RATIO = 1.05

# disable indexing on history_date
SIMPLE_HISTORY_DATE_INDEX = False

# Cache timeouts
DEFAULT_CACHE_TIMEOUT = 60 * 5  # 5 minutes
CONTENT_METADATA_CACHE_TIMEOUT = 60 * 30  # 30 minutes
ENTERPRISE_USER_RECORD_CACHE_TIMEOUT = 60 * 10  # 10 minutes
SUBSIDY_AGGREGATES_CACHE_TIMEOUT = 60 * 10  # 10 minutes
SUBSCRIPTION_LICENSES_LEARNER_CACHE_TIMEOUT = 60 * 1  # 1 minute
ENTERPRISE_COURSE_ENROLLMENTS_CACHE_TIMEOUT = 0  # 0 seconds (no caching, as enrollments may be mutated frequently)
SUBSIDY_RECORD_CACHE_TIMEOUT = DEFAULT_CACHE_TIMEOUT
DEFAULT_ENTERPRISE_ENROLLMENT_INTENTIONS_CACHE_TIMEOUT = DEFAULT_CACHE_TIMEOUT
ALL_ENTERPRISE_GROUP_MEMBERS_CACHE_TIMEOUT = DEFAULT_CACHE_TIMEOUT
SECURED_ALGOLIA_API_KEY_CACHE_TIMEOUT = 60 * 30  # 30 minutes

BRAZE_GROUP_EMAIL_FORCE_REMIND_ALL_PENDING_LEARNERS = False
BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_5_CAMPAIGN = ''
BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_25_CAMPAIGN = ''
BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_50_CAMPAIGN = ''
BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_65_CAMPAIGN = ''
BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_85_CAMPAIGN = ''

# The "Desposit Funds" button (custom django object action) triggers an API call which needs to pass a sales contract
# reference provider slug matching one SalesContractReferenceProvider in the enterprise-subsidy database. Since these
# slugs are operator-defined at runtime, this codebase cannot hard-code the value. However, the least we can do is
# inherit the same default:
# https://github.com/openedx/enterprise-subsidy/blob/70e1a13f9f9b1be6a09a2c2f1a02e7a46315eaa6/enterprise_subsidy/apps/subsidy/models.py#L67
SALES_CONTRACT_REFERENCE_PROVIDER_NAME = 'Salesforce OpportunityLineItem'
SALES_CONTRACT_REFERENCE_PROVIDER_SLUG = 'salesforce_opportunity_line_item'

PROVISIONING_DEFAULTS = {
    'customer': {
        'site_domain': 'example.com',
    },
    'subscription': {
        'is_active': True,
        'product_id': 1,
        'for_internal_use_only': True,
        'all_product_choices': [
            (1, 'Standard Paid'),
            (2, 'Trial'),
        ],
        'trial_product_choices': [
            (1, 'Standard Paid'),
        ],
        'trial_catalog_query_choices': [
            (2, 'All open courses'),
        ],
    },
    'catalog': {
        'catalog_query_id': 1,
        'all_catalog_query_choices': [
            (2, 'All open courses'),
        ],
    },
}

# Add a mapping from product_id to catalog_query_id
# we type the keys as strings instead of ints and have related
# code look up by str(the_value) to avoid any complications
# with loading environment settings from yaml, where the keys
# may *always* be safely-loaded as strings.
PRODUCT_ID_TO_CATALOG_QUERY_ID_MAPPING = {
    '1': 1,  # Product 1 maps to catalog query 1
    '2': 2,
    # Add more mappings as needed
}

CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

################### Self-Service Purchasing (SSP) settings ###################

# Stripe API key used for privileged read/write operations from a system user.
STRIPE_API_KEY = None

# Duration of trial period.
SSP_TRIAL_PERIOD_DAYS = 14

# Placeholder Stripe products, override in prod.
SSP_PRODUCTS = {
    'quarterly_license_plan': {
        'stripe_price_id': 'price_1234_replace-me',
        'quantity_range': (5, 30),
    },
    'yearly_license_plan': {
        'stripe_price_id': 'price_9876_replace-me',
        'quantity_range': (5, 30),
    },
}

# Enable the customer billing API endpoints under /api/v1/customer-billing/*
ENABLE_CUSTOMER_BILLING_API = False

DEFAULT_SSP_PRICE_LOOKUP_KEY = 'subscription_licenses_yearly'

# How long we consider Stripe prices valid for
STRIPE_PRICE_DATA_CACHE_TIMEOUT = 300

################# End Self-Service Purchasing (SSP) settings #################

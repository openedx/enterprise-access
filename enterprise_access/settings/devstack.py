from enterprise_access.settings.local import *

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DB_NAME', 'enterprise_access'),
        'USER': os.environ.get('DB_USER', 'root'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'enterprise_access.mysql80'),
        'PORT': os.environ.get('DB_PORT', 3306),
        'ATOMIC_REQUESTS': False,
        'CONN_MAX_AGE': 60,
    }
}


CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'LOCATION': 'enterprise_access.memcache:11211',
    }
}


# Generic OAuth2 variables irrespective of SSO/backend service key types.
OAUTH2_PROVIDER_URL = 'http://edx.devstack.lms:18000/oauth2'

# OAuth2 variables specific to social-auth/SSO login use case.
SOCIAL_AUTH_EDX_OAUTH2_KEY = os.environ.get('SOCIAL_AUTH_EDX_OAUTH2_KEY', 'enterprise_access-sso-key')
SOCIAL_AUTH_EDX_OAUTH2_SECRET = os.environ.get('SOCIAL_AUTH_EDX_OAUTH2_SECRET', 'enterprise_access-sso-secret')
SOCIAL_AUTH_EDX_OAUTH2_ISSUER = os.environ.get('SOCIAL_AUTH_EDX_OAUTH2_ISSUER', 'http://localhost:18000')
SOCIAL_AUTH_EDX_OAUTH2_URL_ROOT = os.environ.get('SOCIAL_AUTH_EDX_OAUTH2_URL_ROOT', 'http://edx.devstack.lms:18000')
SOCIAL_AUTH_EDX_OAUTH2_LOGOUT_URL = os.environ.get('SOCIAL_AUTH_EDX_OAUTH2_LOGOUT_URL', 'http://localhost:18000/logout')
SOCIAL_AUTH_EDX_OAUTH2_PUBLIC_URL_ROOT = os.environ.get(
    'SOCIAL_AUTH_EDX_OAUTH2_PUBLIC_URL_ROOT', 'http://localhost:18000',
)

# OAuth2 variables specific to backend service API calls.
BACKEND_SERVICE_EDX_OAUTH2_KEY = os.environ.get(
    'BACKEND_SERVICE_EDX_OAUTH2_KEY', 'enterprise_access-backend-service-key')
BACKEND_SERVICE_EDX_OAUTH2_SECRET = os.environ.get(
    'BACKEND_SERVICE_EDX_OAUTH2_SECRET', 'enterprise_access-backend-service-secret')

JWT_AUTH.update({
    'JWT_SECRET_KEY': 'lms-secret',
    'JWT_ISSUER': 'http://localhost:18000/oauth2',
    'JWT_AUDIENCE': None,
    'JWT_VERIFY_AUDIENCE': False,
    'JWT_PUBLIC_SIGNING_JWK_SET': (
        '{"keys": [{"kid": "devstack_key", "e": "AQAB", "kty": "RSA", "n": "smKFSYowG6nNUAdeqH1jQQnH1PmIHphzBmwJ5vRf1vu'
        '48BUI5VcVtUWIPqzRK_LDSlZYh9D0YFL0ZTxIrlb6Tn3Xz7pYvpIAeYuQv3_H5p8tbz7Fb8r63c1828wXPITVTv8f7oxx5W3lFFgpFAyYMmROC'
        '4Ee9qG5T38LFe8_oAuFCEntimWxN9F3P-FJQy43TL7wG54WodgiM0EgzkeLr5K6cDnyckWjTuZbWI-4ffcTgTZsL_Kq1owa_J2ngEfxMCObnzG'
        'y5ZLcTUomo4rZLjghVpq6KZxfS6I1Vz79ZsMVUWEdXOYePCKKsrQG20ogQEkmTf9FT_SouC6jPcHLXw"}]}'
    ),
    'JWT_ISSUERS': [{
        'AUDIENCE': 'lms-key',
        'ISSUER': 'http://localhost:18000/oauth2',
        'SECRET_KEY': 'lms-secret',
    }],
})

# Install django-extensions for improved dev experiences
# https://github.com/django-extensions/django-extensions#using-it
INSTALLED_APPS += (
    'django_extensions',
    'edx_event_bus_kafka',
)

# BEGIN CELERY
CELERY_WORKER_HIJACK_ROOT_LOGGER = True
CELERY_TASK_ALWAYS_EAGER = (
    os.environ.get("CELERY_ALWAYS_EAGER", "false").lower() == "true"
)
# END CELERY


# CORS CONFIG
CORS_ORIGIN_WHITELIST = [
    'http://localhost:1991',  # frontend-app-admin-portal
    'http://localhost:8734',  # frontend-app-learner-portal-enterprise
    'http://localhost:18450',  # frontend-app-support-tools
    'http://localhost:1989',  # frontend-app-enterprise-checkout
]
# END CORS

# CSRF CONFIG
CSRF_TRUSTED_ORIGINS = [
    'http://localhost:1991',  # frontend-app-admin-portal
    'http://localhost:8734',  # frontend-app-learner-portal-enterprise
    'http://localhost:18450',  # frontend-app-support-tools
    'http://localhost:1989',  # frontend-app-enterprise-checkout
]
# END CSRF CONFIG

ECOMMERCE_URL = 'http://edx.devstack.ecommerce:18130'
LICENSE_MANAGER_URL = 'http://license-manager.app:18170'
LMS_URL = 'http://edx.devstack.lms:18000'
DISCOVERY_URL = 'http://edx.devstack.discovery:18381'
ENTERPRISE_LEARNER_PORTAL_URL = 'http://localhost:8734'
ENTERPRISE_ADMIN_PORTAL_URL = 'http://localhost:1991'
ENTERPRISE_CATALOG_URL = 'http://enterprise.catalog.app:18160'
ENTERPRISE_SUBSIDY_URL = 'http://enterprise-subsidy.app:18280'
ENTERPRISE_ACCESS_URL = 'http://localhost:18270'

# shell_plus
SHELL_PLUS_IMPORTS = [
    'from enterprise_access.apps.api.serializers import *',
    'from enterprise_access.apps.api_client import *',
    'from enterprise_access.utils import localized_utcnow',
    'from enterprise_access.apps.content_assignments import api as assignments_api',
    'from pprint import pprint',
    'from enterprise_access.apps.content_assignments import tasks as assignments_tasks',
    'from enterprise_access.apps.customer_billing import pricing_api',
    'from enterprise_access.apps.provisioning.models import *',
    'from enterprise_access.apps.provisioning import api as provisioning_api',
]


################### Kafka Related Settings ##############################

# "Standard" Kafka settings as defined in https://github.com/openedx/event-bus-kafka/tree/main
EVENT_BUS_KAFKA_SCHEMA_REGISTRY_URL = 'http://edx.devstack.schema-registry:8081'
EVENT_BUS_KAFKA_BOOTSTRAP_SERVERS = 'edx.devstack.kafka:29092'
EVENT_BUS_PRODUCER = 'edx_event_bus_kafka.create_producer'
EVENT_BUS_CONSUMER = 'edx_event_bus_kafka.KafkaEventConsumer'
EVENT_BUS_TOPIC_PREFIX = 'dev'

# Potentially deprecated kafka settings
KAFKA_ENABLED = False
KAFKA_REPLICATION_FACTOR_PER_TOPIC = 1
COUPON_CODE_REQUEST_TOPIC_NAME = "coupon-code-request-dev"
LICENSE_REQUEST_TOPIC_NAME = "license-request-dev"
ACCESS_POLICY_TOPIC_NAME = "access-policy-dev"
SUBSIDY_REDEMPTION_TOPIC_NAME = "subsidy-redemption-dev"
KAFKA_TOPICS = [
    COUPON_CODE_REQUEST_TOPIC_NAME,
    LICENSE_REQUEST_TOPIC_NAME,

    # Access policy events
    ACCESS_POLICY_TOPIC_NAME,
    SUBSIDY_REDEMPTION_TOPIC_NAME,
]

################### End Kafka Related Settings ##############################

import os
import tempfile

from enterprise_access.settings.base import *

INSTALLED_APPS += (
    'enterprise_access.apps.workflow.tests',
)

# IN-MEMORY TEST DATABASE
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'USER': '',
        'PASSWORD': '',
        'HOST': '',
        'PORT': '',
    },
}
# END IN-MEMORY TEST DATABASE

# BEGIN CELERY
CELERY_BROKER_URL = "memory://"
CELERY_TASK_ALWAYS_EAGER = True
results_dir = tempfile.TemporaryDirectory()
CELERY_RESULT_BACKEND = f'file://{results_dir.name}'
# END CELERY

ECOMMERCE_URL = 'http://ecommerce.example.com'
LICENSE_MANAGER_URL = 'http://license-manager.example.com'
LMS_URL = 'http://edx-platform.example.com'
ENTERPRISE_LEARNER_PORTAL_URL = 'http://enterprise-learner-portal.example.com'
ENTERPRISE_ADMIN_PORTAL_URL = 'http://enterprise-admin-portal.example.com'
DISCOVERY_URL = 'http://discovery.example.com'
ENTERPRISE_CATALOG_URL = 'http://enterprise-catalog.example.com'
ENTERPRISE_SUBSIDY_URL = 'http://enterprise-subsidy.example.com'
ENTERPRISE_ACCESS_URL = 'http://enterprise-access.example.com'

BRAZE_APPROVE_NOTIFICATION_CAMPAIGN = 'test-approve-campaign'
BRAZE_DECLINE_NOTIFICATION_CAMPAIGN = 'test-decline-campaign'
BRAZE_AUTO_DECLINE_NOTIFICATION_CAMPAIGN = 'test-campaign-id'
BRAZE_NEW_REQUESTS_NOTIFICATION_CAMPAIGN = 'test-new-subsidy-campaign'
BRAZE_ASSIGNMENT_REMINDER_NOTIFICATION_CAMPAIGN = 'test-assignment-remind-campaign'
BRAZE_ASSIGNMENT_REMINDER_POST_LOGISTRATION_NOTIFICATION_CAMPAIGN = 'test-assignment-remind-post-logistration-campaign'
BRAZE_ASSIGNMENT_CANCELLED_NOTIFICATION_CAMPAIGN = 'test-assignment-cancelled-campaign'
BRAZE_ASSIGNMENT_NOTIFICATION_CAMPAIGN = 'test-assignment-notification-campaign'
BRAZE_ASSIGNMENT_AUTOMATIC_CANCELLATION_NOTIFICATION_CAMPAIGN = 'test-assignment-expired-campaign'
BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_5_CAMPAIGN = 'test-day-5-reminder-campaign'
BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_25_CAMPAIGN = 'test-day-25-reminder-campaign'
BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_50_CAMPAIGN = 'test-day-50-reminder-campaign'
BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_65_CAMPAIGN = 'test-day-65-reminder-campaign'
BRAZE_GROUPS_EMAIL_AUTO_REMINDER_DAY_85_CAMPAIGN = 'test-day-85-reminder-campaign'

# SEGMENT CONFIGURATION
SEGMENT_KEY = 'test-key'

################### Kafka Related Settings ##############################
KAFKA_ENABLED = False

KAFKA_BOOTSTRAP_SERVER = 'edx.devstack.kafka:29092'
SCHEMA_REGISTRY_URL = 'http://edx.devstack.schema-registry:8081'
KAFKA_REPLICATION_FACTOR_PER_TOPIC = 1

COUPON_CODE_REQUEST_TOPIC_NAME = "coupon-code-request-test"
LICENSE_REQUEST_TOPIC_NAME = "license-request-test"
ACCESS_POLICY_TOPIC_NAME = "access-policy-test"
SUBSIDY_REDEMPTION_TOPIC_NAME = "subsidy-redemption-test"
KAFKA_TOPICS = [
    COUPON_CODE_REQUEST_TOPIC_NAME,
    LICENSE_REQUEST_TOPIC_NAME,

    # Access policy events
    ACCESS_POLICY_TOPIC_NAME,
    SUBSIDY_REDEMPTION_TOPIC_NAME,
]
################### End Kafka Related Settings ##############################

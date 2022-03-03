import os
import tempfile

from enterprise_access.settings.base import *

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
CELERY_TASK_ALWAYS_EAGER = True
results_dir = tempfile.TemporaryDirectory()
CELERY_RESULT_BACKEND = f'file://{results_dir.name}'
# END CELERY

ECOMMERCE_URL = 'http://ecommerce.example.com'
LICENSE_MANAGER_URL = 'http://license-manager.example.com'
LMS_URL = 'http://edx-platform.example.com'
ENTERPRISE_LEARNER_PORTAL_URL = 'http://enterprise-learner-portal.example.com'
DISCOVERY_URL = 'http://discovery.example.com'

BRAZE_APPROVE_NOTIFICATION_CAMPAIGN = 'test-approve-campaign'
BRAZE_DECLINE_NOTIFICATION_CAMPAIGN = 'test-decline-campaign'
BRAZE_AUTO_DECLINE_NOTIFICATION_CAMPAIGN = 'test-campaign-id'

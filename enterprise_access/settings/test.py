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

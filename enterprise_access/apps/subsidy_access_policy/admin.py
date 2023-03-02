""" Admin configuration for subsidy_access_policy models. """


from django.contrib import admin

from enterprise_access.apps.subsidy_access_policy import models

admin.site.register(models.SubscriptionAccessPolicy)
admin.site.register(models.PerLearnerEnrollmentCreditAccessPolicy)
admin.site.register(models.PerLearnerSpendCreditAccessPolicy)
admin.site.register(models.CappedEnrollmentLearnerCreditAccessPolicy)

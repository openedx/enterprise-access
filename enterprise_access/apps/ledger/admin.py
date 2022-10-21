from django.contrib import admin
from enterprise_access.apps.ledger import models

@admin.register(models.Ledger)
class LedgerAdmin(admin.ModelAdmin):
    class Meta:
        model = models.Ledger
        fields = '__all__'


@admin.register(models.Transaction)
class TransactionAdmin(admin.ModelAdmin):
    class Meta:
        model = models.Transaction
        fields = '__all__'


@admin.register(models.Reversal)
class ReversalAdmin(admin.ModelAdmin):
    class Meta:
        model = models.Reversal
        fields = '__all__'

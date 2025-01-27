from django.contrib import admin

from .models import (
    PizzaPipeline,
    StretchDoughStep,
    AddToppingsStep,
    BakePizzaStep,
)


@admin.register(PizzaPipeline)
class PizzaPipelineAdmin(admin.ModelAdmin):
    list_display = [
        'uuid',
        'succeeded_at',
        'failed_at',
        'input_data',
    ]

    list_filter = [
        ('succeeded_at', admin.EmptyFieldListFilter),
        ('failed_at', admin.EmptyFieldListFilter),
    ]

    readonly_fields = [
        'output_data',
        'succeeded_at',
        'failed_at',
    ]

    ordering = ['-succeeded_at']


@admin.register(StretchDoughStep)
class StretchDoughStepAdmin(admin.ModelAdmin):
    readonly_fields = [
        'output_data',
        'succeeded_at',
        'failed_at',
    ]


@admin.register(AddToppingsStep)
class AddToppingsStepAdmin(admin.ModelAdmin):
    readonly_fields = [
        'output_data',
        'succeeded_at',
        'failed_at',
    ]


@admin.register(BakePizzaStep)
class BakePizzaStepAdmin(admin.ModelAdmin):
    readonly_fields = [
        'output_data',
        'succeeded_at',
        'failed_at',
    ]

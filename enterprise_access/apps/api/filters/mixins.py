"""
Simplified nested field filter mixin for Django REST Framework.

This module provides a focused mixin for nested filtering capabilities
used by LearnerCreditRequestFilter.
"""

import logging
import re
from django.core.exceptions import ValidationError
from django.db.models import OuterRef, Subquery
from django_filters import rest_framework as filters

logger = logging.getLogger(__name__)


class NestedFilterMixin:
    """
    Mixin to add nested field filtering capabilities to any FilterSet.

    This mixin dynamically creates filter fields based on the nested_field_config
    attribute. It supports filtering by the "latest" related record using a
    configurable strategy (e.g., most recent by 'created' field).

    Attributes:
        nested_field_config (dict): Configuration for nested fields.
            Format:
            {
                'prefix': {
                    'related_name': 'related_field_name',
                    'latest_strategy': 'field_to_order_by',
                    'fields': ['field1', 'field2', ...]
                }
            }
        ALLOWED_NESTED_FIELDS (list): Security whitelist of allowed nested fields.
    """

    nested_field_config = {}  # Override in subclass
    ALLOWED_NESTED_FIELDS = ['actions']  # Security constraint - override in subclass
    MAX_FIELD_VALUE_LENGTH = 255

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_nested_filters()

    def _create_nested_filters(self):
        """Create filter fields dynamically based on nested_field_config."""
        if not hasattr(self, 'nested_field_config') or not self.nested_field_config:
            return

        for prefix, config in self.nested_field_config.items():
            self._validate_nested_config(prefix, config)
            self._create_filters_for_prefix(prefix, config)

    def _validate_nested_config(self, prefix, config):
        """Validate nested field configuration with security checks."""
        if not isinstance(config, dict):
            raise ValueError(f"Configuration for {prefix} must be a dictionary")

        required_keys = ['related_name', 'latest_strategy', 'fields']
        for key in required_keys:
            if key not in config:
                raise ValueError(f"Missing required key '{key}' in configuration for {prefix}")

        # Security validation
        related_name = config.get('related_name')
        if self.ALLOWED_NESTED_FIELDS and related_name not in self.ALLOWED_NESTED_FIELDS:
            raise ValueError(f"Nested filtering not allowed for: {related_name}")

        # Validate field names
        for field_name in config.get('fields', []):
            if not self._is_valid_field_name(field_name):
                raise ValueError(f"Invalid field name: {field_name}")

    def _create_filters_for_prefix(self, prefix, config):
        """Create filter fields for a given prefix configuration."""
        fields = config.get('fields', [])

        for field_name in fields:
            # Create basic field filter
            filter_name = f"{prefix}_{field_name}"
            filter_method = f"filter_by_{filter_name}"

            # Create the filter
            field_filter = filters.CharFilter(
                method=filter_method,
                help_text=f'Filter by {prefix} {field_name}'
            )

            # Add filter to the filterset
            setattr(self, filter_name, field_filter)

            # Create the filter method
            self._create_filter_method(filter_method, config, field_name)

            # Create datetime filters for datetime fields if applicable
            if field_name in ['created', 'modified', 'updated']:
                self._create_datetime_filters(prefix, config, field_name)

    def _create_filter_method(self, method_name, config, field_name):
        """Create a filter method for nested field filtering."""
        def filter_method(queryset, name, value):
            if not value:
                return queryset

            # Sanitize input
            sanitized_value = self._sanitize_field_value(value)

            # Apply nested filter
            return self._apply_nested_filter(queryset, config, field_name, sanitized_value)

        # Add method to the filterset
        setattr(self, method_name, filter_method)

    def _create_datetime_filters(self, prefix, config, field_name):
        """Create datetime-specific filters (gte, lte) for datetime fields."""
        for lookup in ['gte', 'lte']:
            filter_name = f"{prefix}_{field_name}__{lookup}"
            filter_method = f"filter_by_{filter_name}"

            # Create the filter
            field_filter = filters.DateTimeFilter(
                method=filter_method,
                help_text=f'Filter by {prefix} {field_name} {lookup}'
            )

            # Add filter to the filterset
            setattr(self, filter_name, field_filter)

            # Create the filter method
            def datetime_filter_method(queryset, name, value, lookup_type=lookup):
                if not value:
                    return queryset
                return self._apply_nested_datetime_filter(queryset, config, field_name, lookup_type, value)

            setattr(self, filter_method, datetime_filter_method)

    def _apply_nested_filter(self, queryset, config, field_name, value):
        """Apply nested field filtering using subquery for latest record."""
        related_name = config['related_name']
        latest_strategy = config['latest_strategy']

        # Create subquery to get the latest related record
        latest_record_subquery = (
            self.Meta.model._meta.get_field(related_name)
            .related_model.objects.filter(**{
                f"{self._get_reverse_relation_field(related_name)}": OuterRef('pk'),
                field_name: value
            })
            .order_by(f'-{latest_strategy}')
            .values('pk')[:1]
        )

        # Filter the main queryset
        filter_kwargs = {f"{related_name}__pk__in": Subquery(latest_record_subquery)}
        return queryset.filter(**filter_kwargs).distinct()

    def _apply_nested_datetime_filter(self, queryset, config, field_name, lookup, value):
        """Apply nested datetime field filtering."""
        related_name = config['related_name']
        latest_strategy = config['latest_strategy']

        # Create subquery for datetime filtering
        latest_record_subquery = (
            self.Meta.model._meta.get_field(related_name)
            .related_model.objects.filter(**{
                f"{self._get_reverse_relation_field(related_name)}": OuterRef('pk'),
                f"{field_name}__{lookup}": value
            })
            .order_by(f'-{latest_strategy}')
            .values('pk')[:1]
        )

        # Filter the main queryset
        filter_kwargs = {f"{related_name}__pk__in": Subquery(latest_record_subquery)}
        return queryset.filter(**filter_kwargs).distinct()

    def _get_reverse_relation_field(self, related_name):
        """Get the reverse relation field name for the related model."""
        # For LearnerCreditRequest -> actions, this would be 'learner_credit_request'
        # This is a simplified approach - in a real implementation you might want to
        # inspect the model to get the actual reverse relation field name
        if related_name == 'actions':
            return 'learner_credit_request'
        return 'learner_credit_request'  # Default fallback

    def _is_valid_field_name(self, field_name):
        """Validate field name to prevent injection."""
        # Allow only alphanumeric characters, underscores
        pattern = r'^[a-zA-Z_][a-zA-Z0-9_]*$'
        return re.match(pattern, field_name) is not None

    def _sanitize_field_value(self, value):
        """Sanitize field values to prevent injection."""
        if value is None:
            return value

        if isinstance(value, str):
            # Limit length
            if len(value) > self.MAX_FIELD_VALUE_LENGTH:
                raise ValidationError(f"Field value too long. Maximum {self.MAX_FIELD_VALUE_LENGTH} characters allowed.")

            # Remove potentially dangerous characters for string fields
            # Allow alphanumeric, spaces, hyphens, underscores, @ for emails, dots for domains
            sanitized = re.sub(r'[^\w\s\-@._]', '', value)
            return sanitized.strip()

        return value


def create_nested_filter_aliases(filterset_class, alias_mapping):
    """
    Helper function to create backward compatibility aliases.

    Args:
        filterset_class: The FilterSet class to add aliases to
        alias_mapping: Dict mapping old parameter names to new ones
                      {'old_param': 'new_param', ...}

    Example:
        create_nested_filter_aliases(MyFilter, {
            'latest_action_status': 'action_status',
            'latest_action_recent_action': 'action_recent_action'
        })
    """
    for old_param, new_param in alias_mapping.items():
        if hasattr(filterset_class, new_param):
            # Get the existing filter
            existing_filter = getattr(filterset_class, new_param)

            # Create alias with same configuration
            alias_filter = filters.CharFilter(
                method=existing_filter.method,
                help_text=f"Alias for {new_param} (deprecated, use {new_param})"
            )

            setattr(filterset_class, old_param, alias_filter)

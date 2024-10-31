"""
Serializers for bffs.
"""
from rest_framework import serializers


class BaseBFFMessageSerializer(serializers.Serializer):
    """
    Base Serializer for BFF messages.

    Fields:
        user_message (str): A user-friendly message.
        developer_message (str): A more detailed message for debugging purposes.
    """
    developer_message = serializers.CharField()
    user_message = serializers.CharField()

    def create(self, validated_data):
        return validated_data

    def update(self, instance, validated_data):
        return validated_data


class ErrorSerializer(BaseBFFMessageSerializer):
    pass


class WarningSerializer(BaseBFFMessageSerializer):
    pass

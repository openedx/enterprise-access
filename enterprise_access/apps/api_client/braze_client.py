"""
API client for calls to Braze.
"""
import logging

from braze.client import BrazeClient
from django.conf import settings

logger = logging.getLogger(__name__)

ENTERPRISE_BRAZE_ALIAS_LABEL = 'Enterprise'  # Do Not change this, this is consistent with other uses across edX repos.


class BrazeApiClient(BrazeClient):
    """
    API client for calls to Braze.
    """

    def __init__(self):

        required_settings = ['BRAZE_API_KEY', 'BRAZE_API_URL', 'BRAZE_APP_ID']

        for setting in required_settings:
            if not getattr(settings, setting, None):
                msg = f'Missing {setting} in settings required for Braze API Client.'
                logger.error(msg)
                raise ValueError(msg)

        super().__init__(
            api_key=settings.BRAZE_API_KEY,
            api_url=settings.BRAZE_API_URL,
            app_id=settings.BRAZE_APP_ID
        )

    def generate_mailto_link(self, emails):
        """
        Generate a mailto link for the given emails.
        """
        if emails:
            return f'mailto:{",".join(emails)}'

        return None

    def create_recipient(
        self,
        user_email,
        lms_user_id,
        trigger_properties=None,
    ):
        """
        Create a recipient object using the given user_email and lms_user_id.
        Identifies the given email address with any existing Braze alias records
        via the provided ``lms_user_id``.
        """

        user_alias = {
            'alias_label': ENTERPRISE_BRAZE_ALIAS_LABEL,
            'alias_name': user_email,
        }

        # Identify the user alias in case it already exists. This is necessary so
        # we don't accidently create a duplicate Braze profile.
        self.identify_users([{
            'external_id': lms_user_id,
            'user_alias': user_alias
        }])

        attributes = {
            "user_alias": user_alias,
            "email": user_email,
            "is_enterprise_learner": True,
            "_update_existing_only": False,
        }

        return {
            'external_user_id': lms_user_id,
            'attributes': attributes,
            # If a profile does not already exist, Braze will create a new profile before sending a message.
            'send_to_existing_only': False,
            'trigger_properties': trigger_properties or {},
        }

    def create_recipient_no_external_id(self, user_email):
        """
        Create a Braze recipient dict identified only by an alias based on their email.
        """
        return {
            'attributes': {
                'email': user_email,
                'is_enterprise_learner': True,
            },
            'user_alias': {
                'alias_label': ENTERPRISE_BRAZE_ALIAS_LABEL,
                'alias_name': user_email,
            },
        }

    def create_braze_recipient(self, user_email: str, lms_user_id: int = None) -> dict:
        """
        Creates a Braze recipient with appropriate handling for both LMS users and email-only users.

        For users with an LMS ID, creates a recipient with external ID and identifies the user.
        For users without an LMS ID, creates a recipient with email alias and ensures alias exists.

        Args:
            user_email (str): Email address of the recipient
            lms_user_id (int, optional): LMS user ID if available

        Returns:
            dict: Braze recipient object suitable for campaign sending
        """
        if lms_user_id:
            recipient = self.create_recipient(
                user_email=user_email,
                lms_user_id=lms_user_id,
            )
        else:
            recipient = self.create_recipient_no_external_id(user_email)
            self.create_braze_alias(
                [user_email],
                ENTERPRISE_BRAZE_ALIAS_LABEL,
            )
        return recipient

"""
Version 1 API Exceptions.
"""
from rest_framework import status
from rest_framework.exceptions import APIException

from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.subsidy_access_policy import constants


class RedemptionRequestException(APIException):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = 'Could not redeem'


class SubsidyAPIRedemptionRequestException(RedemptionRequestException):
    """
    An API exception that has a response payload structured like
    {
        'code': 'some_error_code',
        'detail': {
            'reason': 'reason_for_error',
            'user_message': 'User friendly string describing the error.',
            # additional metadata describing the error, possibly including admin emails.
            'metadata': {
                'key': 'value',
            }
        }
    }

    There are some sane defaults set at initialization for the reason, code, and user_message
    values.
    """
    default_detail = 'Error redeeming through Subsidy API'
    default_code = constants.SubsidyRedemptionErrorCodes.DEFAULT_ERROR

    # Custom keys of the `detail` field returned in the response payload.
    default_reason = constants.SubsidyRedemptionErrorReasons.DEFAULT_REASON
    default_user_message = constants.SubsidyRedemptionErrorReasons.USER_MESSAGES_BY_REASON[default_reason]

    def __init__(self, code=None, detail=None, policy=None, subsidy_api_error=None):
        """
        Initializes all of the attributes of this exception instance.

        args:
          code (str): A reusable error code constant.
          detail ([list,str,dict]): Details about the exception we're raising.
          policy (SubsidyAccessPolicy): A policy object, from which we can fetch admin email addresses.
          subsidy_api_error (SubsidyAPIHTTPError): The exception object that was caught, from which
            we can infer more specific causes about the redemption error this exception encapsulates.
        """
        super().__init__(code=code, detail=detail)

        self.reason = self.default_reason
        self.user_message = self.default_user_message
        self.metadata = {}

        if policy and subsidy_api_error:
            try:
                self._build_subsidy_api_error_payload(policy, subsidy_api_error)
            except Exception:  # pylint: disable=broad-except
                self.metadata = {
                    'subsidy_error_detail_raw': str(subsidy_api_error.error_response.content),
                }

        self.detail = {
            'code': self.code,
            'detail': {
                'reason': self.reason,
                'user_message': self.user_message,
                'metadata': self.metadata,
            }
        }

    def _build_subsidy_api_error_payload(self, policy, subsidy_api_error):
        """
        Helper to build error response payload on Subsidy API errors.
        """
        subsidy_error_detail = subsidy_api_error.error_payload().get('detail')
        subsidy_error_code = subsidy_api_error.error_payload().get('code')

        self.metadata = {
            'enterprise_administrators': LmsApiClient().get_enterprise_customer_data(
                policy.enterprise_customer_uuid
            ).get('admin_users')
        }

        # We currently only have enough data to say more specific things
        # about fulfillment errors during subsidy API redemption.
        if subsidy_error_code == constants.SubsidyRedemptionErrorCodes.FULFILLMENT_ERROR:
            self._set_subsidy_fulfillment_error_reason(subsidy_error_detail)

    def _set_subsidy_fulfillment_error_reason(self, subsidy_error_detail):
        """
        Helper to set the reason, user_message, and metadata
        for the given subsidy_error_detail.
        """
        self.metadata['subsidy_error_detail'] = subsidy_error_detail
        self.reason = constants.SubsidyFulfillmentErrorReasons.DEFAULT_REASON

        if subsidy_error_detail:
            message_string = self._get_subsidy_fulfillment_error_message(subsidy_error_detail)
            if cause_of_message := constants.SubsidyFulfillmentErrorReasons.get_cause_from_error_message(
                message_string
            ):
                self.reason = cause_of_message
                # pylint: disable=attribute-defined-outside-init
                self.code = constants.SubsidyRedemptionErrorCodes.FULFILLMENT_ERROR

        self.user_message = constants.SubsidyFulfillmentErrorReasons.USER_MESSAGES_BY_REASON.get(self.reason)

    def _get_subsidy_fulfillment_error_message(self, subsidy_error_detail):
        """
        ``subsidy_error_detail`` is either a string describing an error message,
        a dict with a "message" key describing an error message, or a list of such
        dicts.  This helper method widdles any of those things down into a single
        error message string.
        """
        if isinstance(subsidy_error_detail, str):
            return subsidy_error_detail

        subsidy_message_dict = subsidy_error_detail
        if isinstance(subsidy_error_detail, list):
            subsidy_message_dict = subsidy_error_detail[0]

        return subsidy_message_dict.get('message')


class SubsidyAccessPolicyLockedException(APIException):
    """
    Throw this exception when an attempt to acquire a policy lock failed because it was already locked by another agent.

    Note: status.HTTP_423_LOCKED is NOT acceptable as a status code for delivery to web browsers.  According to Mozilla:

      > The ability to lock a resource is specific to some WebDAV servers. Browsers accessing web pages will never
      > encounter this status code; in the erroneous cases it happens, they will handle it as a generic 400 status code.

    See: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/423

    HTTP 429 Too Many Requests is the next best thing, and implies retryability.
    """
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_detail = 'Enrollment currently locked for this subsidy access policy.'

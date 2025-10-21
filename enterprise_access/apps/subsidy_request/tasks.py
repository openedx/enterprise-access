"""
Tasks for subsidy requests app.
"""

import logging
from datetime import datetime

from celery import shared_task
from django.apps import apps
from django.conf import settings

from enterprise_access.apps.api_client.braze_client import BrazeApiClient
from enterprise_access.apps.api_client.discovery_client import DiscoveryApiClient
from enterprise_access.apps.api_client.lms_client import LmsApiClient
from enterprise_access.apps.content_assignments.tasks import BrazeCampaignSender, _get_assignment_or_raise
from enterprise_access.apps.subsidy_request.constants import SubsidyRequestStates
from enterprise_access.tasks import LoggedTaskWithRetry
from enterprise_access.utils import get_subsidy_model

logger = logging.getLogger(__name__)


class BaseLearnerCreditRequestRetryAndErrorActionTask(LoggedTaskWithRetry):
    """
    Base class that logs errors for learner credit request tasks.
    Provides a place to define retry failure handling logic. This helps ensure
    that task failures are properly logged with relevant context.
    """
    def log_errored_action(self, learner_credit_request, exc):
        """
        Log error information for the failed task.
        """
        raise NotImplementedError

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """
        If the task fails for any reason (whether or not retries were involved), log the error.

        Function signature documented at: https://docs.celeryq.dev/en/stable/userguide/tasks.html#on_failure
        """
        learner_credit_request = self.get_learner_credit_request_from_args(args)
        self.log_errored_action(learner_credit_request, exc)
        if self.request.retries == settings.TASK_MAX_RETRIES:
            logger.error(
                f'The task id: {task_id} failure resulted from exceeding the locally defined max number of retries '
                '(settings.TASK_MAX_RETRIES).'
            )

    def get_learner_credit_request_from_args(self, args):
        """
        Extract learner credit request from task arguments.
        Default implementation assumes first argument is assignment UUID and gets credit request from assignment.
        Override in subclasses if different logic is needed.
        """
        if not args:
            raise ValueError("No arguments provided to extract assignment")
        assignment = _get_assignment_or_raise(args[0])
        return assignment.credit_request


# pylint: disable=abstract-method
class SendLearnerCreditApprovalEmailTask(BaseLearnerCreditRequestRetryAndErrorActionTask):
    """
    Base class for the ``send_learner_credit_bnr_request_approve_task`` task.
    """
    def log_errored_action(self, learner_credit_request, exc):
        logger.error(
            f'Learner credit approval email task failed. '
            f'Request ID: {learner_credit_request.uuid}, '
            f'Enterprise ID: {learner_credit_request.enterprise_customer_uuid}, '
            f'Exception: {exc}'
        )


# pylint: disable=abstract-method
class SendLearnerCreditReminderEmailTask(BaseLearnerCreditRequestRetryAndErrorActionTask):
    """
    Base class for the ``send_reminder_email_for_pending_learner_credit_request`` task.
    """
    def log_errored_action(self, learner_credit_request, exc):
        logger.error(
            f'Learner credit reminder email task failed. '
            f'Request ID: {learner_credit_request.uuid}, '
            f'Enterprise ID: {learner_credit_request.enterprise_customer_uuid}, '
            f'Exception: {exc}'
        )
        learner_credit_request.add_errored_reminded_action(exc)


# pylint: disable=abstract-method
class SendLearnerCreditCancelEmailTask(BaseLearnerCreditRequestRetryAndErrorActionTask):
    """
    Base class for the ``send_learner_credit_bnr_cancel_notification_task`` task.
    """
    def log_errored_action(self, learner_credit_request, exc):
        logger.error(
            f'Learner credit cancel email task failed. '
            f'Request ID: {learner_credit_request.uuid}, '
            f'Enterprise ID: {learner_credit_request.enterprise_customer_uuid}, '
            f'Exception: {exc}'
        )


# pylint: disable=abstract-method
class SendLearnerCreditDeclineEmailTask(BaseLearnerCreditRequestRetryAndErrorActionTask):
    """
    Base class for the ``send_learner_credit_bnr_decline_notification_task`` task.
    """
    def log_errored_action(self, learner_credit_request, exc):
        logger.error(
            f'Learner credit decline email task failed. '
            f'Request ID: {learner_credit_request.uuid}, '
            f'Enterprise ID: {learner_credit_request.enterprise_customer_uuid}, '
            f'Exception: {exc}'
        )

    def get_learner_credit_request_from_args(self, args):
        """
        For decline task, the argument is learner credit request UUID, not assignment UUID.
        """
        if not args:
            raise ValueError("No arguments provided to extract learner credit request")

        learner_credit_request_model = apps.get_model('subsidy_request.LearnerCreditRequest')

        try:
            return learner_credit_request_model.objects.get(uuid=args[0])
        except learner_credit_request_model.DoesNotExist:
            logger.warning(f'LearnerCreditRequest with uuid: {args[0]} does not exist.')
            raise


def _get_course_partners(course_data):
    """
    Returns a list of course partner data for subsidy requests given a course dictionary.
    """
    owners = course_data.get('owners') or []
    return [{'uuid': owner.get('uuid'), 'name': owner.get('name')} for owner in owners]


@shared_task(base=LoggedTaskWithRetry)
def update_course_info_for_subsidy_request_task(model_name, subsidy_request_uuid):
    """
    Get course info (e.g. title, partners) from lms and update subsidy_request with it.
    """
    subsidy_model = apps.get_model('subsidy_request', model_name)
    subsidy_request = subsidy_model.objects.get(uuid=subsidy_request_uuid)

    discovery_client = DiscoveryApiClient()
    course_data = discovery_client.get_course_data(subsidy_request.course_id)
    subsidy_request.course_title = course_data['title']
    subsidy_request.course_partners = _get_course_partners(course_data)

    # Use bulk_update so we don't trigger save() again
    subsidy_model.bulk_update([subsidy_request], ['course_title', 'course_partners'])


def _get_manage_requests_url(subsidy_model, enterprise_slug):
    """
    Get a manage_requests url based on the type of subsidy.

    Args:
        subsidy_model (class):  class of the subsidy object
        enterprise_slug (string): slug of the enterprise's name
    Returns:
        string: a url to the manage learners page.
    """
    if subsidy_model == apps.get_model('subsidy_request.LicenseRequest'):
        subsidy_string = 'subscriptions'
    else:
        subsidy_string = 'coupons'

    url = f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/{enterprise_slug}/admin/{subsidy_string}/manage-requests'
    return url


@shared_task(base=LoggedTaskWithRetry)
def send_admins_email_with_new_requests_task(enterprise_customer_uuid):
    """
    Task to send new-request emails to admins.

    Args:
        enterprise_customer_uuid (str): enterprise customer uuid identifier
    Raises:
        HTTPError if Braze client call fails with an HTTPError
    """
    config_model = apps.get_model('subsidy_request.SubsidyRequestCustomerConfiguration')
    customer_config = config_model.objects.get(
        enterprise_customer_uuid=enterprise_customer_uuid,
    )

    subsidy_model = get_subsidy_model(customer_config.subsidy_type)
    subsidy_requests = subsidy_model.objects.filter(
        enterprise_customer_uuid=enterprise_customer_uuid,
        state=SubsidyRequestStates.REQUESTED,
    )
    # Filter when we last run this unless we never ran before
    # "future" is greater than "past"
    # so if created is greater than last remind date, it means
    # it was created after cron was last run
    if customer_config.last_remind_date is not None:
        subsidy_requests = subsidy_requests.filter(created__gte=customer_config.last_remind_date)

    subsidy_requests = subsidy_requests.order_by("-created")

    if not subsidy_requests:
        logger.info(
            'No new subsidy requests. Not sending new requests '
            f'email to admins for enterprise {enterprise_customer_uuid}.'
        )
        return

    braze_trigger_properties = {}
    lms_client = LmsApiClient()
    enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_uuid)
    enterprise_slug = enterprise_customer_data.get('slug')
    braze_trigger_properties['manage_requests_url'] = _get_manage_requests_url(subsidy_model, enterprise_slug)

    braze_trigger_properties['requests'] = []
    for subsidy_request in subsidy_requests:

        user_email = subsidy_request.user.email
        course_title = subsidy_request.course_title

        braze_trigger_properties['requests'].append({
            'user_email': user_email,
            'course_title': course_title,
        })

    admin_users = enterprise_customer_data['admin_users']

    logger.info(
        f'Sending new-requests email to admins for enterprise {enterprise_customer_uuid}. '
        f'The email includes {len(subsidy_requests)} subsidy requests. '
        f'Sending to: {admin_users}'
    )
    braze_client = BrazeApiClient()
    recipients = [
        braze_client.create_recipient(
            user_email=admin_user['email'],
            lms_user_id=admin_user['lms_user_id']
        )
        for admin_user in admin_users
    ]
    try:
        braze_client.send_campaign_message(
            settings.BRAZE_NEW_REQUESTS_NOTIFICATION_CAMPAIGN,
            recipients=recipients,
            trigger_properties=braze_trigger_properties,
        )

    except Exception:
        logger.exception(f'Exception sending braze campaign email message for enterprise {enterprise_customer_uuid}.')
        raise

    customer_config.last_remind_date = datetime.now()
    customer_config.save()


@shared_task(base=LoggedTaskWithRetry)
def send_learner_credit_bnr_admins_email_with_new_requests_task(
        policy_uuid, lc_request_config_uuid, enterprise_customer_uuid
):
    """
    Task to send new learner credit request emails to admins.

    This task can be manually triggered from browse_and_request when a new
    LearnerCreditRequest is created. It will send the latest 10 requests in
    REQUESTED state to enterprise admins.

    Args:
        policy_uuid (str): subsidy access policy uuid identifier
        lc_request_config_uuid (str): learner credit request config uuid identifier
        enterprise_customer_uuid (str): enterprise customer uuid identifier
    Raises:
        HTTPError if Braze client call fails with an HTTPError
    """

    subsidy_model = apps.get_model('subsidy_request.LearnerCreditRequest')
    subsidy_requests = subsidy_model.objects.filter(
        enterprise_customer_uuid=enterprise_customer_uuid,
        learner_credit_request_config__uuid=lc_request_config_uuid,
        state=SubsidyRequestStates.REQUESTED,
    )
    latest_subsidy_requests = subsidy_requests.order_by('-created')[:10]

    if not subsidy_requests:
        logger.info(
            'No learner credit requests in REQUESTED state. Not sending new requests '
            f'email to admins for enterprise {enterprise_customer_uuid}.'
        )
        return

    lms_client = LmsApiClient()
    enterprise_customer_data = lms_client.get_enterprise_customer_data(enterprise_customer_uuid)
    enterprise_slug = enterprise_customer_data.get('slug')
    organization = enterprise_customer_data.get('name')

    manage_requests_url = (f'{settings.ENTERPRISE_ADMIN_PORTAL_URL}/{enterprise_slug}'
                           f'/admin/learner-credit/{policy_uuid}/requests')
    admin_users = enterprise_customer_data['admin_users']

    if not admin_users:
        logger.info(
            f'No admin users found for enterprise {enterprise_customer_uuid}. '
            'Not sending new requests email.'
        )
        return
    braze_client = BrazeApiClient()
    recipients = [
        braze_client.create_recipient(
            user_email=admin_user['email'],
            lms_user_id=admin_user['lms_user_id']
        )
        for admin_user in admin_users
    ]

    braze_trigger_properties = {
        'manage_requests_url': manage_requests_url,
        'requests': [],
        'total_requests': len(subsidy_requests),
        'organization': organization,
    }

    for subsidy_request in latest_subsidy_requests:
        braze_trigger_properties['requests'].append({
            'user_email': subsidy_request.user.email,
            'course_title': subsidy_request.course_title,
        })

    logger.info(
        f'Sending learner credit requests email to admins for enterprise {enterprise_customer_uuid}. '
        f'This includes {len(subsidy_requests)} requests. '
        f'Sending to: {admin_users}'
    )

    try:
        braze_client.send_campaign_message(
            settings.BRAZE_LEARNER_CREDIT_BNR_NEW_REQUESTS_NOTIFICATION_CAMPAIGN,
            recipients=recipients,
            trigger_properties=braze_trigger_properties,
        )
    except Exception:
        logger.exception(
            f'Exception sending Braze campaign email for enterprise {enterprise_customer_uuid}.'
        )
        raise


@shared_task(base=SendLearnerCreditApprovalEmailTask)
def send_learner_credit_bnr_request_approve_task(approved_assignment_uuid):
    """
    Send email via braze for approving bnr learner credit request.

    Args:
        approved_assignment_uuid: (string) the approved assignment uuid
    """
    assignment = _get_assignment_or_raise(approved_assignment_uuid)
    campaign_sender = BrazeCampaignSender(assignment)

    braze_trigger_properties = campaign_sender.get_properties(
        'contact_admin_link',
        'organization',
        'course_title',
        'start_date',
        'course_partner',
        'course_card_image'
    )
    campaign_uuid = settings.BRAZE_LEARNER_CREDIT_BNR_APPROVED_NOTIFICATION_CAMPAIGN
    campaign_sender.send_campaign_message(
        braze_trigger_properties,
        campaign_uuid,
    )
    logger.info(f'Sent braze campaign approved uuid={campaign_uuid} message for assignment {assignment}')


@shared_task(base=SendLearnerCreditReminderEmailTask)
def send_reminder_email_for_pending_learner_credit_request(assignment_uuid):
    """
    Send email via braze for reminding users of their pending learner credit request
    Args:
        assignment_uuid (str): The UUID of the LearnerContentAssignment associated with the LCR.
    """
    assignment = _get_assignment_or_raise(assignment_uuid)

    campaign_sender = BrazeCampaignSender(assignment)
    braze_trigger_properties = campaign_sender.get_properties(
        'contact_admin_link',
        'organization',
        'course_title',
        'start_date',
        'course_partner',
        'course_card_image',
    )
    campaign_uuid = settings.BRAZE_LEARNER_CREDIT_BNR_REMIND_NOTIFICATION_CAMPAIGN
    campaign_sender.send_campaign_message(
        braze_trigger_properties,
        campaign_uuid,
    )

    if hasattr(assignment, 'credit_request') and assignment.credit_request:
        assignment.credit_request.add_successful_reminded_action()
    logger.info(f'Sent braze campaign reminder uuid={campaign_uuid} message for assignment {assignment}')


@shared_task(base=SendLearnerCreditCancelEmailTask)
def send_learner_credit_bnr_cancel_notification_task(assignment_uuid):
    """
    Send email via braze for canceling a learner credit request.

    Args:
        assignment_uuid (str): The UUID of the LearnerContentAssignment associated with the cancelled LCR.
    """
    assignment = _get_assignment_or_raise(assignment_uuid)

    campaign_sender = BrazeCampaignSender(assignment)
    braze_trigger_properties = campaign_sender.get_properties(
        'contact_admin_link',
        'organization',
        'course_title',
        'enterprise_dashboard_url',
    )
    campaign_uuid = settings.BRAZE_LEARNER_CREDIT_BNR_CANCEL_NOTIFICATION_CAMPAIGN
    campaign_sender.send_campaign_message(
        braze_trigger_properties,
        campaign_uuid,
    )
    logger.info(f'Sent braze campaign cancel uuid={campaign_uuid} message for assignment {assignment}')


@shared_task(base=SendLearnerCreditDeclineEmailTask)
def send_learner_credit_bnr_decline_notification_task(learner_credit_request_uuid):
    """
    Send email via braze for declining a learner credit request.

    Args:
        learner_credit_request_uuid (str): The UUID of the LearnerCreditRequest being declined.
    """
    try:
        subsidy_model = apps.get_model('subsidy_request.LearnerCreditRequest')
        learner_credit_request = subsidy_model.objects.get(uuid=learner_credit_request_uuid)
    except subsidy_model.DoesNotExist:
        logger.warning(f'LearnerCreditRequest with uuid: {learner_credit_request_uuid} does not exist.')
        return

    braze_client_instance = BrazeApiClient()
    lms_client = LmsApiClient()

    user = learner_credit_request.user
    recipient = braze_client_instance.create_recipient(
        user_email=user.email,
        lms_user_id=user.lms_user_id
    )

    enterprise_customer_data = lms_client.get_enterprise_customer_data(
        learner_credit_request.enterprise_customer_uuid
    )

    organization = enterprise_customer_data.get('name')
    admin_emails = [user['email'] for user in enterprise_customer_data['admin_users']]
    enterprise_slug = enterprise_customer_data['slug']

    braze_trigger_properties = {
        'contact_admin_link': braze_client_instance.generate_mailto_link(admin_emails),
        'organization': organization,
        'course_title': learner_credit_request.course_title,
        'enterprise_dashboard_url': f'{settings.ENTERPRISE_LEARNER_PORTAL_URL}/{enterprise_slug}',
    }

    campaign_uuid = settings.BRAZE_LEARNER_CREDIT_BNR_DECLINE_NOTIFICATION_CAMPAIGN

    logger.info(f'Sending braze campaign decline message for learner credit request {learner_credit_request}')
    braze_client_instance.send_campaign_message(
        campaign_uuid,
        recipients=[recipient],
        trigger_properties=braze_trigger_properties,
    )
    logger.info(f'Sent braze campaign decline uuid={campaign_uuid} message for request {learner_credit_request}')

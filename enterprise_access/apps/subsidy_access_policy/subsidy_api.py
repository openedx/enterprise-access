"""
Python API for fetching and interacting
with transaction and subsidy/ledger data
from the enterprise-subsidy service.
"""
import logging
from collections import defaultdict

from .utils import get_versioned_subsidy_client, request_cache, versioned_cache_key

logger = logging.getLogger(__name__)


class TransactionPolicyMismatchError(Exception):
    """
    Should be raised in a context where, for a given policy,
    if this policy's uuid doesn't match the recorded one in a transaction
    that we searched for by the policy's subsidy_uuid value.
    """


def learner_transaction_cache_key(subsidy_uuid, lms_user_id):
    return versioned_cache_key('get_transactions_for_learner', subsidy_uuid, lms_user_id)


def get_and_cache_transactions_for_learner(subsidy_uuid, lms_user_id):
    """
    Get all transactions for a learner in a given subsidy.  This can
    include transactions from multiple access policies.
    """
    cache_key = learner_transaction_cache_key(subsidy_uuid, lms_user_id)
    cached_response = request_cache().get_cached_response(cache_key)
    if cached_response.is_found:
        logger.info('[LEARNER TRANSACTIONS CACHE HIT] for key %s', cache_key)
        return cached_response.value

    logger.info('[LEARNER TRANSACTIONS CACHE MISS] for key %s', cache_key)
    client = get_versioned_subsidy_client()
    response_payload = client.list_subsidy_transactions(
        subsidy_uuid=subsidy_uuid,
        lms_user_id=lms_user_id,
    )
    result = {
        'transactions': response_payload['results'],
        'aggregates': response_payload['aggregates'],
    }
    logger.info('[LEARNER TRANSACTIONS CACHE SET] for key %s', cache_key)
    request_cache().set(cache_key, result)
    return result


def get_redemptions_by_content_and_policy_for_learner(policies, lms_user_id):
    """
    Returns a mapping of content keys to a mapping of policy uuids to lists of transactions
    for the given learner, filtered to only those transactions associated with **subsidies**
    to which any of the given **policies** are associated.
    """
    policies_by_subsidy_uuid = {policy.subsidy_uuid: policy for policy in policies}
    result = defaultdict(lambda: defaultdict(list))

    for subsidy_uuid, policy in policies_by_subsidy_uuid.items():
        logger.info(f'Fetching learner transactions for subsidy {subsidy_uuid} via policy {policy.uuid}')
        transactions_in_subsidy = get_and_cache_transactions_for_learner(subsidy_uuid, lms_user_id)['transactions']
        for redemption in transactions_in_subsidy:
            transaction_uuid = redemption['uuid']
            content_key = redemption['content_key']
            subsidy_access_policy_uuid = redemption['subsidy_access_policy_uuid']

            if subsidy_access_policy_uuid != str(policy.uuid):
                message = (
                    f"Transaction {transaction_uuid} has mismatched policy uuids for subsidy {subsidy_uuid}: "
                    f"Looked in policy {policy.uuid} but found other policy uuid {subsidy_access_policy_uuid}"
                )
                logger.error(message)
                raise TransactionPolicyMismatchError(message)

            result[content_key][subsidy_access_policy_uuid].append(redemption)

    return result

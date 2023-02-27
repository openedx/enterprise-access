"""
Custom signals definitions that represent enterprise-access events.
"""

from openedx_events.tooling import OpenEdxPublicSignal

from .data import AccessPolicyData, CouponCodeRequestData, LicenseRequestData, SubsidyRedemption

# TODO: Move the signals to openedx_events

COUPON_CODE_REQUEST_APPROVED = OpenEdxPublicSignal(
    event_type="org.openedx.enterprise.access.coupon-code-request.approved.v1",
    data={
        "request": CouponCodeRequestData,
    }
)

LICENSE_REQUEST_APPROVED = OpenEdxPublicSignal(
    event_type="org.openedx.enterprise.access.license-request.approved.v1",
    data={
        "request": LicenseRequestData,
    }
)

ACCESS_POLICY_CREATED = OpenEdxPublicSignal(
    event_type="org.openedx.enterprise.access.access-policy.created.v1",
    data={
        "access-policy": AccessPolicyData,
    }
)

ACCESS_POLICY_UPDATED = OpenEdxPublicSignal(
    event_type="org.openedx.enterprise.access.access-policy.updated.v1",
    data={
        "access-policy": AccessPolicyData,
    }
)

ACCESS_POLICY_DELETED = OpenEdxPublicSignal(
    event_type="org.openedx.enterprise.access.access-policy.deleted.v1",
    data={
        "access-policy": AccessPolicyData,
    }
)


SUBSIDY_REDEEMED = OpenEdxPublicSignal(
    event_type="org.openedx.enterprise.access.subsidy.redeemed.v1",
    data={
        "redemption": SubsidyRedemption,
    }
)

SUBSIDY_REDEMPTION_REVERSED = OpenEdxPublicSignal(
    event_type="org.openedx.enterprise.access.subsidy.redemption-reversed.v1",
    data={
        "redemption": SubsidyRedemption,
    }
)

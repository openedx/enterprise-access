"""
Custom signals definitions that represent enterprise-access events.
"""

from openedx_events.tooling import OpenEdxPublicSignal

from .data import CouponCodeRequestData, LicenseRequestData

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

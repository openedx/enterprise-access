Segment Events
==============

``enterprise-access`` emits Segment events in the format: ``edx.server.enterprise-access.[model]-lifecycle.<status>``.

Following are the currently implemented Segment events:

``subsidy_request.LicenseRequest``
----------------------------------
* edx.server.enterprise-access.license-request-lifecycle.created - emitted when a license request is created
* edx.server.enterprise-access.license-request-lifecycle.approved - emitted when a license request is approved
* edx.server.enterprise-access.license-request-lifecycle.declined - emitted when a license request is declined

All events for ``subsidy_request.LicenseRequest`` contain these properties, some of which might be null:

- **uuid**: The UUID of this request

- **lms_user_id**: The LMS user id of the user who created this request

- **course_id**: The id of the course that this request is for

- **enterprise_customer_uuid**: The UUID of the enterprise that this request falls under

- **state**: The state of this request

- **reviewed_at**: ISO-8601 Date String representing when this request was reviewed

- **reviewer_lms_user_id**: The LMS user id of the user who reviewed this request

- **decline_reason**: The reason this request was declined

- **subscription_plan_uuid**: The UUID of the subscription used to fulfill this request

- **license_uuid**: The UUID of the license that was assigned for this request


``subsidy_request.CouponCodeRequest``
-------------------------------------
* edx.server.enterprise-access.coupon-code-request-lifecycle.created - emitted when a coupon code request is created
* edx.server.enterprise-access.coupon-code-request-lifecycle.approved - emitted when a coupon code request is approved
* edx.server.enterprise-access.coupon-code-request-lifecycle.declined - emitted when a coupon code request is declined

All events for ``subsidy_request.CouponCodeRequest`` contain these properties, some of which might be null:

- **uuid**: The UUID of this request

- **lms_user_id**: The LMS user id of the user who created this request

- **course_id**: The id of the course that this request is for

- **enterprise_customer_uuid**: The UUID of the enterprise that this request falls under

- **state**: The state of this request

- **reviewed_at**: ISO-8601 Date String representing when this request was reviewed

- **reviewer_lms_user_id**: The LMS user id of the user who reviewed this request

- **decline_reason**: The reason this request was declined

- **coupon_id**: The id of the coupon used to fulfill this request

- **coupon_code**: The coupon code that was assigned for this request


``subsidy_request.SubsidyRequestCustomerConfiguration``
-------------------------------------
* edx.server.enterprise-access.subsidy-request-configuration-lifecycle.created - emitted when a configuration is created
* edx.server.enterprise-access.subsidy-request-configuration-lifecycle.updated - emitted when a configuration is updated

All events for ``subsidy_request.SubsidyRequestCustomerConfiguration`` contain these properties, some of which might be null:

- **enterprise_customer_uuid**: The UUID of the enterprise the configuration is for

- **subsidy_requests_enabled**: Whether subsidy requests are for the enterprise customer

- **subsidy_type**: The subisdy type that can be requested

- **changed_by_lms_user_id**: The LMS user id of the user that changed this configuration

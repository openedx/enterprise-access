"""
Flows for the workflows app
"""

import logging

from viewflow import this
from viewflow.workflow import flow

from enterprise_access.apps.workflows import services
from enterprise_access.apps.workflows.models import DefaultEnterpriseCourseEnrollmentProcess

logger = logging.getLogger(__name__)


class DefaultEnterpriseCourseEnrollmentFlow(flow.Flow):
    """
    Flow for enrolling learners in default enterprise courses.
    """
    process_class = DefaultEnterpriseCourseEnrollmentProcess

    start = flow.StartHandle().Next(this.step_retrieve_required_data)

    # Parallel steps: retrieve both subscription licenses and course enrollments
    step_retrieve_required_data = flow.Split().Next(
        this.step_activated_subscription_licenses,
    ).Next(this.step_default_course_enrollments)

    step_activated_subscription_licenses = flow.Function(
        this.retrieve_activated_subscription_licenses
    ).Next(this.step_wait_for_data_retrieval)

    step_default_course_enrollments = flow.Function(
        this.retrieve_default_course_enrollments
    ).Next(this.step_wait_for_data_retrieval)

    # After both steps complete, move on to validate resolved data
    step_wait_for_data_retrieval = flow.Join().Next(this.step_validate_data)

    # Conditional step: Check if both licenses and enrollments exist
    step_validate_data = flow.If(
        this.ensure_activated_licenses_and_default_enrollments_exist
    ).Then(this.step_determine_redeemable_enrollments).Else(this.end)

    # Conditional step: Check redeemability of enrollments
    step_determine_redeemable_enrollments = flow.Function(
        this.determine_redeemable_enrollments
    ).Next(this.step_check_redeemable_enrollments)

    # Conditional step: Check if redeemable enrollments exist
    step_check_redeemable_enrollments = flow.If(
        this.ensure_redeemable_enrollments_exist
    ).Then(this.step_enroll_in_redeemable_courses).Else(this.end)

    step_enroll_in_redeemable_courses = flow.Function(this.enroll_in_redeemable_courses).Next(this.end)

    end = flow.End()

    def ensure_redeemable_enrollments_exist(self, activation):
        """
        Conditional check to determine if redeemable enrollments exist.
        Returns True if redeemable enrollments are present and not empty, otherwise returns False.
        """
        return bool(activation.process.redeemable_default_enterprise_course_enrollments)

    def ensure_activated_licenses_and_default_enrollments_exist(self, activation):
        """
        Conditional check to determine if both subscription licenses and enrollments exist.
        Returns True if both are present and not empty, otherwise returns False.
        """
        return bool(activation.process.activated_subscription_licenses and
                    activation.process.default_enterprise_course_enrollments)

    def retrieve_activated_subscription_licenses(self, activation):
        """
        Retrieve activated subscription licenses for the process.
        """
        activation.process.activated_subscription_licenses =\
            services.activated_subscription_licenses(process_id=activation.process.pk)

        logger.info(
            f"Fetched activated susbcription licenses for "
            f"process {activation.process.pk}: {activation.process.activated_subscription_licenses}"
        )

        activation.process.save()

    def retrieve_default_course_enrollments(self, activation):
        """
        Retrieve default enterprise course enrollments for the process.
        """
        activation.process.default_enterprise_course_enrollments = \
            services.default_enterprise_course_enrollments(process_id=activation.process.pk)

        logger.info(
            f"Fetched default course enrollments for "
            f"process {activation.process.pk}: {activation.process.default_enterprise_course_enrollments}"
        )

        activation.process.save()

    def determine_redeemable_enrollments(self, activation):
        """
        Determine which enrollments can be redeemed using the available subscription licenses.
        """
        activated_subscription_licenses = activation.process.activated_subscription_licenses
        enrollments = activation.process.default_enterprise_course_enrollments

        redeemable_enrollments = []

        # Pre-extract all enterprise catalog UUIDs from licenses for quick lookup
        license_catalog_uuids = {license["enterprise_catalog_uuid"] for license in activated_subscription_licenses}

        # Filter redeemable enrollments by checking if their catalog UUIDs exist in any active licenses
        redeemable_enrollments = [
            enrollment
            for enrollment in enrollments
            if any(
                catalog_uuid in license_catalog_uuids
                for catalog_uuid in enrollment["applicable_enterprise_catalog_uuids"]
            )
        ]

        # Store the redeemable enrollments
        activation.process.redeemable_default_enterprise_course_enrollments = redeemable_enrollments

        logger.info(
            f"Determined redeemable enrollments for process {activation.process.pk}: "
            f"{activation.process.redeemable_default_enterprise_course_enrollments}"
        )
        activation.process.save()

    def enroll_in_redeemable_courses(self, activation):
        """
        Enroll learners in redeemable courses.
        """
        services.enroll_courses(
            redeemable_enrollments=activation.process.redeemable_default_enterprise_course_enrollments
        )
        logger.info(
            f"Enrolled learners in redeemable courses for process {activation.process.pk}: "
            f"{activation.process.redeemable_default_enterprise_course_enrollments}"
        )

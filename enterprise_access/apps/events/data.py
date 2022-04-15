
"""
Data attributes for events..
"""

import attr


class CourseEnrollmentEvent:
    """
    Coupon code request events to be put on event bus.
    """

    def __init__(self, *args, **kwargs):
        """
        Initialize a CourseEnrollmentEvent.
        """
        self.lms_user_id = kwargs['lms_user_id']
        self.course_key = kwargs['course_key']

    AVRO_SCHEMA = """
        {
            "namespace": "org.openedx.learning.course.enrollment",
            "name": "CourseEnrollmentEvent",
            "type": "record",
            "fields": [
                {"name": "lms_user_id", "type": "int"},
                {"name": "course_id", "type": "string"},
            ]
        }
    """

    @staticmethod
    def from_dict(dict_instance, ctx):  # pylint: disable=unused-argument
        """
        Create an instance of CourseEnrollmentEvent from dict.
        """
        return CourseEnrollmentEvent(**dict_instance)

    @staticmethod
    def to_dict(obj, ctx):  # pylint: disable=unused-argument
        """
        Convert an instance of CourseEnrollmentEvent to dict.
        """
        return {
            'lms_user_id': obj.lms_user_id,
            'course_id': obj.course_id,
        }

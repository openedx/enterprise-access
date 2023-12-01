"""
Tests for the ``content_metadata_api.py`` module of the content_assignments app.
"""
import ddt
from django.test import TestCase

from ..content_metadata_api import get_card_image_url, get_course_partners, get_human_readable_date


@ddt.ddt
class TestContentMetadataApi(TestCase):
    """
    Tests functions of the ``content_assignment_api.py`` file.
    """

    @ddt.data(
        (
            {'card_image_url': 'my-card-image', 'image_url': 'my-image-url'},
            'my-card-image',
        ),
        (
            {'card_image_url': 'my-card-image', 'image_url': None},
            'my-card-image',
        ),
        (
            {'card_image_url': 'my-card-image'},
            'my-card-image',
        ),
        (
            {'card_image_url': None, 'image_url': 'my-image-url'},
            'my-image-url',
        ),
        (
            {},
            None,
        )
    )
    @ddt.unpack
    def test_get_card_image_url(self, content_metadata, expected_output):
        self.assertEqual(expected_output, get_card_image_url(content_metadata))

    @ddt.data(
        ('2023-01-01T00:00:00.000000Z', 'Jan 01, 2023'),
        ('2023-01-01T00:00:00Z', 'Jan 01, 2023'),
        ('2023-01-01 00:00:00.000000Z', 'Jan 01, 2023'),
        ('2023-01-01 00:00:00Z', 'Jan 01, 2023'),
    )
    @ddt.unpack
    def test_get_human_readable_date(self, datetime_string, expected_output):
        self.assertEqual(expected_output, get_human_readable_date(datetime_string))

    def test_get_human_readable_date_exception(self):
        with self.assertRaisesRegex(ValueError, 'does not match format'):
            get_human_readable_date('2023-01-01')

    @ddt.data(
        (
            {'owners': [
                {'name': 'bob', 'id': 1}, {'name': 'sam', 'id': 2},
                {'name': 'dave', 'id': 3}, {'name': 'jill', 'id': 4}
            ]},
            'bob, sam, dave, and jill',
        ),
        (
            {'owners': [{'name': 'bob', 'id': 1}, {'name': 'sam', 'id': 2}]},
            'bob and sam',
        ),
        (
            {'owners': [{'name': 'bob', 'id': 1}]},
            'bob',
        ),
    )
    @ddt.unpack
    def test_get_course_partners(self, content_metadata, expected_output):
        self.assertEqual(expected_output, get_course_partners(content_metadata))

    def test_get_course_partners_exception(self):
        with self.assertRaisesRegex(Exception, 'must have a partner'):
            get_course_partners({'foo': 'bar'})

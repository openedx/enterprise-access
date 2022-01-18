from unittest import mock

from django.test import override_settings

from enterprise_access.apps.track.segment import track_event

mock_lms_user_id = 'lms_user_id'
mock_event_name = 'mock.event.name'

@override_settings(SEGMENT_KEY=None)
@mock.patch('enterprise_access.apps.track.segment.logger', return_value=mock.MagicMock())
def test_track_event_no_segment_key(mock_logger):
    track_event(mock_lms_user_id, mock_event_name, {})
    mock_logger.warning.assert_called_with(
        "Event %s for user_id %s not tracked because SEGMENT_KEY not set", mock_event_name, mock_lms_user_id
    )

@override_settings(SEGMENT_KEY=None)
@mock.patch('enterprise_access.apps.track.segment.logger', return_value=mock.MagicMock())
@mock.patch('enterprise_access.apps.track.segment.analytics', return_value=mock.MagicMock())
def test_track_event_catches_exceptions(mock_analytics, mock_logger):
    mock_analytics.track.side_effect = Exception('Something went wrong')
    track_event(mock_lms_user_id, mock_event_name, {})
    mock_logger.exception.called_with('Something went wrong')
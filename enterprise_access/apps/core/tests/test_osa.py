# pylint: skip-file
from enum import Enum
from unittest import mock

from rest_framework import generics, mixins, serializers, viewsets
from rest_framework.decorators import action

try:
    from django.db.models.enums import TextChoices
except ImportError:
    TextChoices = object  # type: ignore  # django < 3.0 handling

from drf_spectacular.plumbing import list_hash, load_enum_name_overrides
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.views import APIView

from enterprise_access.apps.core.tests import assert_schema, generate_schema

language_choices = (
    ('en', 'en'),
    ('es', 'es'),
    ('ru', 'ru'),
    ('cn', 'cn'),
)

blank_null_language_choices = (
    ('en', 'en'),
    ('es', 'es'),
    ('ru', 'ru'),
    ('cn', 'cn'),
    ('', 'not provided'),
    (None, 'unknown'),
)

vote_choices = (
    (1, 'Positive'),
    (0, 'Neutral'),
    (-1, 'Negative'),
)

language_list = ['en']


class LanguageEnum(Enum):
    EN = 'en'


class LanguageStrEnum(str, Enum):
    EN = 'en'


class LanguageChoices(TextChoices):
    EN = 'en'


blank_null_language_list = ['en', '', None]


class BlankNullLanguageEnum(Enum):
    EN = 'en'
    BLANK = ''
    NULL = None


class BlankNullLanguageStrEnum(str, Enum):
    EN = 'en'
    BLANK = ''
    # These will still be included since the values get cast to strings so 'None' != None
    NULL = None


class BlankNullLanguageChoices(TextChoices):
    EN = 'en'
    BLANK = ''
    # These will still be included since the values get cast to strings so 'None' != None
    NULL = None


class ASerializer(serializers.Serializer):
    language = serializers.ChoiceField(choices=language_choices)
    vote = serializers.ChoiceField(choices=vote_choices)


class BSerializer(serializers.Serializer):
    language = serializers.ChoiceField(choices=language_choices, allow_blank=True, allow_null=True)


class AViewset(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = ASerializer

    @extend_schema(responses=BSerializer)
    @action(detail=False, serializer_class=BSerializer)
    def selection(self, request):
        pass  # pragma: no cover


def test_postprocessing():
    schema = generate_schema('a', AViewset)
    assert_schema(schema, 'tests/test_postprocessing.yml')


def test_no_blank_and_null_in_enum_choices():
    schema = generate_schema('a', AViewset)
    assert 'NullEnum' not in schema['components']['schemas']
    assert 'BlankEnum' not in schema['components']['schemas']


def test_global_enum_naming_override():
    # the override will prevent the warning for multiple names
    class XSerializer(serializers.Serializer):
        foo = serializers.ChoiceField(choices=language_choices)
        bar = serializers.ChoiceField(choices=language_choices)

    class XView(generics.RetrieveAPIView):
        serializer_class = XSerializer

    schema = generate_schema('/x', view=XView)
    assert 'FooEnum' in schema['components']['schemas']['X']['properties']['foo']['$ref']
    assert 'BarEnum' in schema['components']['schemas']['X']['properties']['bar']['$ref']


def test_global_enum_naming_override_with_empty_string_and_nullable():
    """Test that choices with blank values can still have their name overridden."""
    class XSerializer(serializers.Serializer):
        foo = serializers.ChoiceField(choices=blank_null_language_choices)

    class XView(generics.RetrieveAPIView):
        serializer_class = XSerializer

    schema = generate_schema('/x', view=XView)
    enum_data = schema['components']['schemas']['FooEnum']

    assert '' in enum_data['enum']
    assert enum_data['nullable'] is True


def test_enum_name_reuse_warning(capsys):
    class XSerializer(serializers.Serializer):
        foo = serializers.ChoiceField(choices=language_choices)
        bar = serializers.ChoiceField(choices=language_choices)

    class XView(generics.RetrieveAPIView):
        serializer_class = XSerializer

    generate_schema('/x', view=XView)
    assert 'encountered multiple names for the same choice set' in capsys.readouterr().err


def test_enum_collision_without_override(capsys):
    class XSerializer(serializers.Serializer):
        foo = serializers.ChoiceField(choices=[('A', 'A')])

    class YSerializer(serializers.Serializer):
        foo = serializers.ChoiceField(choices=[('A', 'A'), ('B', 'B')])

    class ZSerializer(serializers.Serializer):
        foo = serializers.ChoiceField(choices=[('A', 'A'), ('B', 'B')])

    class XAPIView(APIView):
        @extend_schema(responses=ZSerializer)
        def get(self, request):
            pass  # pragma: no cover

        @extend_schema(request=XSerializer, responses=YSerializer)
        def post(self, request):
            pass  # pragma: no cover

    generate_schema('x', view=XAPIView)
    assert 'enum naming encountered a non-optimally resolvable' in capsys.readouterr().err


def test_resolvable_enum_collision():
    class XSerializer(serializers.Serializer):
        foo = serializers.ChoiceField(choices=[('A', 'A')])

    class YSerializer(serializers.Serializer):
        foo = serializers.ChoiceField(choices=[('A', 'A'), ('B', 'B')])

    class XAPIView(APIView):
        @extend_schema(request=XSerializer, responses=YSerializer)
        def post(self, request):
            pass  # pragma: no cover

    schema = generate_schema('x', view=XAPIView)
    assert 'XFooEnum' in schema['components']['schemas']
    assert 'YFooEnum' in schema['components']['schemas']


@mock.patch('drf_spectacular.settings.spectacular_settings.COMPONENT_SPLIT_PATCH', True)
@mock.patch('drf_spectacular.settings.spectacular_settings.COMPONENT_SPLIT_REQUEST', True)
def test_enum_resolvable_collision_with_patched_and_request_splits():
    class XSerializer(serializers.Serializer):
        foo = serializers.ChoiceField(choices=[('A', 'A')])

    class YSerializer(serializers.Serializer):
        foo = serializers.ChoiceField(choices=[('A', 'A'), ('B', 'B')])

    class XViewset(viewsets.GenericViewSet):
        @extend_schema(request=XSerializer, responses=YSerializer)
        def create(self, request):
            pass  # pragma: no cover

        @extend_schema(
            request=XSerializer,
            responses=YSerializer,
            parameters=[OpenApiParameter('id', int, OpenApiParameter.PATH)]
        )
        def partial_update(self, request):
            pass  # pragma: no cover

    schema = generate_schema('/x', XViewset)
    components = schema['components']['schemas']
    assert 'XFooEnum' in components and 'YFooEnum' in components
    assert '/XFooEnum' in components['XRequest']['properties']['foo']['$ref']
    assert '/XFooEnum' in components['PatchedXRequest']['properties']['foo']['$ref']

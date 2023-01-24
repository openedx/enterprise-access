"""
Extended DRF_SPECTACULAR hooks.
"""

import re
from collections import defaultdict

from drf_spectacular.plumbing import ResolvedComponent, list_hash, load_enum_name_overrides, safe_ref, warn
from drf_spectacular.settings import spectacular_settings
from inflection import camelize
from rest_framework.settings import api_settings


def postprocess_schema_enums(result, generator, **kwargs):  # pylint: disable=too-many-statements
    """
    This code has been taken from
    https://github.com/tfranzel/drf-spectacular/blob/1a124eef42f5ed2aaf13907ba89cc78ae2e0b250/drf_spectacular/hooks.py
    Extra thing we are handling in this hook is we are setting nullable = True for null values and inserting empty
    strings for empty values. We need this for graphQL package which doesn't support union of
    enums(using oneOf or anyOf) correctly.
    """

    def iter_prop_containers(schema, component_name=None):
        if not component_name:
            for component_name, schema in schema.items():  # lint-amnesty, pylint: disable=redefined-argument-from-local
                if spectacular_settings.COMPONENT_SPLIT_PATCH:
                    component_name = re.sub('^Patched(.+)', r'\1', component_name)
                if spectacular_settings.COMPONENT_SPLIT_REQUEST:
                    component_name = re.sub('(.+)Request$', r'\1', component_name)
                yield from iter_prop_containers(schema, component_name)
        elif isinstance(schema, list):
            for item in schema:
                yield from iter_prop_containers(item, component_name)
        elif isinstance(schema, dict):
            if schema.get('properties'):
                yield component_name, schema['properties']
            yield from iter_prop_containers(schema.get('oneOf', []), component_name)
            yield from iter_prop_containers(schema.get('allOf', []), component_name)
            yield from iter_prop_containers(schema.get('anyOf', []), component_name)

    def create_enum_component(name, schema):
        component = ResolvedComponent(
            name=name,
            type=ResolvedComponent.SCHEMA,
            schema=schema,
            object=name,
        )
        generator.registry.register_on_missing(component)
        return component

    schemas = result.get('components', {}).get('schemas', {})

    overrides = load_enum_name_overrides()

    prop_hash_mapping = defaultdict(set)
    hash_name_mapping = defaultdict(set)
    # collect all enums, their names and choice sets
    for component_name, props in iter_prop_containers(schemas):
        for prop_name, prop_schema in props.items():
            if prop_schema.get('type') == 'array':
                prop_schema = prop_schema.get('items', {})
            if 'enum' not in prop_schema:
                continue
            # remove blank/null entry for hashing. will be reconstructed in the last step
            prop_enum_cleaned_hash = list_hash([i for i in prop_schema['enum'] if i not in ['', None]])
            prop_hash_mapping[prop_name].add(prop_enum_cleaned_hash)
            hash_name_mapping[prop_enum_cleaned_hash].add((component_name, prop_name))

    # traverse all enum properties and generate a name for the choice set. naming collisions
    # are resolved and a warning is emitted. giving a choice set multiple names is technically
    # correct but potentially unwanted. also emit a warning there to make the user aware.
    enum_name_mapping = {}
    for prop_name, prop_hash_set in prop_hash_mapping.items():
        for prop_hash in prop_hash_set:
            if prop_hash in overrides:
                enum_name = overrides[prop_hash]
            elif len(prop_hash_set) == 1:
                # prop_name has been used exclusively for one choice set (best case)
                enum_name = f'{camelize(prop_name)}Enum'
            elif len(hash_name_mapping[prop_hash]) == 1:
                # prop_name has multiple choice sets, but each one limited to one component only
                component_name, _ = next(iter(hash_name_mapping[prop_hash]))
                enum_name = f'{camelize(component_name)}{camelize(prop_name)}Enum'
            else:
                enum_name = f'{camelize(prop_name)}{prop_hash[:3].capitalize()}Enum'
                warn(
                    f'enum naming encountered a non-optimally resolvable collision for fields '
                    f'named "{prop_name}". The same name has been used for multiple choice sets '
                    f'in multiple components. The collision was resolved with "{enum_name}". '
                    f'add an entry to ENUM_NAME_OVERRIDES to fix the naming.'
                )
            if enum_name_mapping.get(prop_hash, enum_name) != enum_name:
                warn(
                    f'encountered multiple names for the same choice set ({enum_name}). This '
                    f'may be unwanted even though the generated schema is technically correct. '
                    f'Add an entry to ENUM_NAME_OVERRIDES to fix the naming.'
                )
                del enum_name_mapping[prop_hash]
            else:
                enum_name_mapping[prop_hash] = enum_name
            enum_name_mapping[(prop_hash, prop_name)] = enum_name

    # replace all enum occurrences with a enum schema component. cut out the
    # enum, replace it with a reference and add a corresponding component.
    for _, props in iter_prop_containers(schemas):
        for prop_name, prop_schema in props.items():
            is_array = prop_schema.get('type') == 'array'
            if is_array:
                prop_schema = prop_schema.get('items', {})

            if 'enum' not in prop_schema:
                continue

            prop_enum_original_list = prop_schema['enum']
            prop_schema['enum'] = [i for i in prop_schema['enum'] if i not in ['', None]]
            prop_hash = list_hash(prop_schema['enum'])
            # when choice sets are reused under multiple names, the generated name cannot be
            # resolved from the hash alone. fall back to prop_name and hash for resolution.
            enum_name = enum_name_mapping.get(prop_hash) or enum_name_mapping[prop_hash, prop_name]

            # split property into remaining property and enum component parts
            enum_schema = {k: v for k, v in prop_schema.items() if k in ['type', 'enum']}
            prop_schema = {k: v for k, v in prop_schema.items() if k not in ['type', 'enum']}

            if '' in prop_enum_original_list:
                enum_schema['enum'].append('')

            if None in prop_enum_original_list:
                enum_schema['nullable'] = True

            components = [
                create_enum_component(enum_name, schema=enum_schema)
            ]

            if len(components) == 1:
                prop_schema.update(components[0].ref)
            else:
                prop_schema.update({'oneOf': [c.ref for c in components]})

            if is_array:
                props[prop_name]['items'] = safe_ref(prop_schema)
            else:
                props[prop_name] = safe_ref(prop_schema)

    # sort again with additional components
    result['components'] = generator.registry.build(spectacular_settings.APPEND_COMPONENTS)
    return result


def preprocess_exclude_path_format(endpoints, **kwargs):
    """
        preprocessing hook that filters out {format} suffixed paths, in case
        format_suffix_patterns is used and {format} path params are unwanted.
    """
    format_path = f'{{{api_settings.FORMAT_SUFFIX_KWARG}}}'
    return [
        (path, path_regex, method, callback)
        for path, path_regex, method, callback in endpoints
        if not (path.endswith(format_path) or path.endswith(format_path + '/'))
    ]

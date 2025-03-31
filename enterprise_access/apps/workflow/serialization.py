"""
Workflow Steps do work on objects, not on dicts.  The BaseInputOutput
class below can be subclassed by attrs classes, so that every input/output
class utilized by workflow steps can be counted on to have a from_dict()
classmethod to generate an object from a dict, and a to_dict() method
to turn an instance of an input/output class back into a dict (in other words
to structure and unstructure, or to deserialize and then serialize).
"""
import uuid

import attrs
import cattrs

CONVERTER = cattrs.Converter()


@CONVERTER.register_structure_hook
def uuid_structure_hook(val: str, _) -> uuid.UUID:
    """
    cattrs has a good number of built-in hooks to structure/unstructure
    data, but UUIDs are not one of them. This hook function ensures
    that any field declared as a uuid type that is structured via the
    default converter object (i.e. via inheritance from the ``BaseInputOutput``
    class below) ends up as an actual UUID.
    """
    return uuid.UUID(val)


@CONVERTER.register_unstructure_hook
def uuid_unstructure_hook(val: uuid.UUID) -> str:
    """
    cattrs has a good number of built-in hooks to structure/unstructure
    data, but UUIDs are not one of them. This hook function ensures
    that any field declared as a uuid type that is *un*structured via the
    default converter object (i.e. via inheritance from the ``BaseInputOutput``
    class below) ends up as a string representation of the UUID value
    stored in the field.
    """
    return str(val)


@attrs.define
class BaseInputOutput:
    """
    Base class that other attrs-defined workflow input and output classes
    should inherit from.
    """
    @classmethod
    def from_dict(cls, data_dict):
        return CONVERTER.structure(data_dict, cls)

    def to_dict(self):
        return CONVERTER.unstructure(self)

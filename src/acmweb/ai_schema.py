"""Adapt task schemas to strict structured output without changing validation.

The model must emit every property. A field may be null only when its original
schema permits null; local Pydantic validation remains the final authority.
"""
from copy import deepcopy


def strict_output_schema(schema):
    if not isinstance(schema, dict):
        raise ValueError('invalid output schema')
    result = deepcopy(schema)

    def visit(node):
        if not isinstance(node, dict):
            return
        node.pop('default', None)
        properties = node.get('properties')
        if isinstance(properties, dict) or node.get('type') == 'object':
            # Arbitrary dictionaries cannot be silently converted into empty
            # closed objects: that would change the task's output contract.
            additional = node.get('additionalProperties')
            if isinstance(additional, dict) or additional is True:
                raise ValueError('dynamic output fields are not supported')
            if properties is None:
                properties = node['properties'] = {}
            if not isinstance(properties, dict):
                raise ValueError('invalid output properties')
            node['additionalProperties'] = False
            node['required'] = list(properties)
            for child in properties.values():
                visit(child)
        for key in ('$defs', 'definitions'):
            for child in (node.get(key) or {}).values():
                visit(child)
        for key in ('items', 'not', 'if', 'then', 'else'):
            visit(node.get(key))
        for key in ('anyOf', 'oneOf', 'allOf', 'prefixItems'):
            for child in node.get(key, []):
                visit(child)

    visit(result)
    return result

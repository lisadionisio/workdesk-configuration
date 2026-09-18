#!/usr/bin/env python3
"""Validate this extractor's JSON contract, not factual accuracy or attribution.

Supports only the schema vocabulary used by the bundled meeting extractor.
Unknown schema keywords and undeclared output fields fail closed.
"""
import argparse
import json
import sys


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON property')
        result[key] = value
    return result


def parse(text):
    def invalid_constant(_):
        raise ValueError('Non-JSON numeric constant')
    return json.loads(text, object_pairs_hook=unique_object,
                      parse_constant=invalid_constant)


def check_schema(schema):
    if not isinstance(schema, dict):
        raise ValueError('Schema must be an object')
    if set(schema) - {'type', 'description', 'properties', 'required', 'items', 'enum'}:
        raise ValueError('Unsupported extraction schema keyword')
    kind = schema.get('type')
    if kind not in ('object', 'array', 'string', 'boolean'):
        raise ValueError('Unsupported extraction schema type')
    if 'description' in schema and not isinstance(schema['description'], str):
        raise ValueError('Schema description must be a string')
    if kind == 'object':
        props = schema.get('properties')
        required = schema.get('required', [])
        if not isinstance(props, dict) or not isinstance(required, list):
            raise ValueError('Invalid object schema')
        if any(not isinstance(key, str) or key not in props for key in required):
            raise ValueError('Invalid required property')
        if len(set(required)) != len(required):
            raise ValueError('Duplicate required property')
        for child in props.values():
            check_schema(child)
    elif 'properties' in schema or 'required' in schema:
        raise ValueError('Object keywords require object type')
    if kind == 'array':
        check_schema(schema.get('items'))
    elif 'items' in schema:
        raise ValueError('Items requires array type')
    if 'enum' in schema:
        if kind != 'string' or not isinstance(schema['enum'], list) or not schema['enum']:
            raise ValueError('Only nonempty string enums are supported')
        if any(not isinstance(item, str) for item in schema['enum']):
            raise ValueError('Invalid enum value')


def check_value(value, schema, path='$'):
    expected = {'object': dict, 'array': list, 'string': str, 'boolean': bool}[schema['type']]
    if type(value) is not expected:
        raise ValueError(f'{path}: wrong property type')
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(f'{path}: invalid enum value')
    if isinstance(value, dict):
        props = schema['properties']
        if set(value) - set(props):
            raise ValueError(f'{path}: undeclared property')
        if set(schema.get('required', [])) - set(value):
            raise ValueError(f'{path}: missing required property')
        for key, item in value.items():
            check_value(item, props[key], f'{path}.{key}')
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_value(item, schema['items'], f'{path}[{index}]')


def extract(response, schema):
    if not isinstance(response, dict) or 'error' in response:
        raise ValueError('Invalid or error provider response')
    feedback = response.get('promptFeedback', {})
    if not isinstance(feedback, dict) or feedback.get('blockReason'):
        raise ValueError('Provider blocked the prompt')
    candidates = response.get('candidates')
    if not isinstance(candidates, list) or len(candidates) != 1:
        raise ValueError('Expected exactly one provider candidate')
    candidate = candidates[0]
    if not isinstance(candidate, dict) or candidate.get('finishReason') != 'STOP':
        raise ValueError('Provider candidate did not finish normally')
    content = candidate.get('content')
    parts = content.get('parts') if isinstance(content, dict) else None
    if not isinstance(parts, list) or not parts:
        raise ValueError('Provider candidate has no parts')
    texts = []
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError('Invalid provider part')
        if 'thought' in part and type(part['thought']) is not bool:
            raise ValueError('Invalid thought flag')
        if part.get('thought') is True:
            continue
        if not isinstance(part.get('text'), str):
            raise ValueError('Expected text in final response parts')
        texts.append(part['text'])
    if not texts:
        raise ValueError('Provider candidate has no final text')
    value = parse(''.join(texts))
    check_value(value, schema)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema-json', required=True)
    parser.add_argument('--check-schema', action='store_true')
    args = parser.parse_args()
    try:
        schema = parse(args.schema_json)
        check_schema(schema)
        if schema['type'] != 'object':
            raise ValueError('Extraction root schema must be an object')
        if not args.check_schema:
            result = extract(parse(sys.stdin.read()), schema)
            print(json.dumps(result, ensure_ascii=False))
    except (ValueError, RecursionError) as exc:
        # Do not echo transcript bodies or invalid provider content into error logs.
        print(f'ERROR: extraction response contract: {exc}', file=sys.stderr)
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())

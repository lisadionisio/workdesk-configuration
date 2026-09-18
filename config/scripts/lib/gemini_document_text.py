"""Extract one Transcript tab without silently omitting unsupported elements.

Google Docs Document/Tab/DateElementProperties reference:
https://developers.google.com/workspace/docs/api/reference/rest/v1/documents
Formatting styles are not reproduced. TextRun content and supplied date display
text are preserved in document order. Unsupported structural content is rejected.
"""
import json
import sys
from pathlib import Path


class MissingTranscript(ValueError):
    pass


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate document key')
        result[key] = value
    return result


def extract(data, expected):
    if not isinstance(data, dict) or data.get('documentId') != expected or 'error' in data:
        raise ValueError('Document identity mismatch')
    selected = []

    def visit(tabs, depth=0):
        if not isinstance(tabs, list) or depth > 32:
            raise ValueError('Unsupported tab tree')
        for tab in tabs:
            if not isinstance(tab, dict) or not isinstance(tab.get('tabProperties'), dict):
                raise ValueError('Invalid tab')
            if tab['tabProperties'].get('title') == 'Transcript':
                selected.append(tab)
            visit(tab.get('childTabs', []), depth + 1)

    visit(data.get('tabs'))
    if not selected:
        raise MissingTranscript('No Transcript tab returned')
    if len(selected) != 1:
        raise ValueError('Expected exactly one Transcript tab')
    content = selected[0].get('documentTab', {}).get('body', {}).get('content')
    if not isinstance(content, list):
        raise ValueError('Missing transcript body')
    chunks = []
    for block in content:
        if not isinstance(block, dict):
            raise ValueError('Invalid structural element')
        kinds = set(block) - {'startIndex', 'endIndex'}
        if kinds == {'sectionBreak'}:
            continue
        if kinds != {'paragraph'} or not isinstance(block['paragraph'], dict):
            raise ValueError('Unsupported transcript structure')
        paragraph = block['paragraph']
        if paragraph.get('positionedObjectIds'):
            raise ValueError('Unrepresented positioned content')
        elements = paragraph.get('elements')
        if not isinstance(elements, list):
            raise ValueError('Invalid paragraph elements')
        for element in elements:
            if not isinstance(element, dict):
                raise ValueError('Invalid paragraph element')
            kinds = set(element) - {'startIndex', 'endIndex'}
            if kinds == {'textRun'}:
                value = element['textRun'].get('content')
                properties = element['textRun']
            elif kinds == {'dateElement'}:
                properties = element['dateElement']
                value = properties.get('dateElementProperties', {}).get('displayText')
            else:
                raise ValueError('Unsupported transcript element')
            if any(properties.get(k) for k in ['suggestedInsertionIds', 'suggestedDeletionIds']):
                raise ValueError('Unresolved content suggestion')
            if not isinstance(value, str):
                raise ValueError('Missing source display text')
            chunks.append(value)
    return ''.join(chunks)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3:
            raise ValueError('Expected JSON path and document ID')
        data = json.loads(Path(sys.argv[1]).read_text(), object_pairs_hook=unique)
        text = extract(data, sys.argv[2])
        sys.stdout.buffer.write(text.encode('utf-8'))
    except MissingTranscript:
        print('No Transcript tab returned; summary tabs are not a substitute.', file=sys.stderr)
        sys.exit(3)
    except (OSError, ValueError, TypeError, AttributeError, KeyError, RecursionError):
        print('Transcript structure is missing, ambiguous or unsupported; source not published.', file=sys.stderr)
        sys.exit(2)

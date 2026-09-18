#!/usr/bin/env python3
"""Extract file destinations from direct-edit payloads without executing edits.

Returns original and resolved paths for a separate policy classifier. This
module neither grants permission nor installs or changes enforcement.
"""
import json
from pathlib import Path
import sys


def patch_paths(patch):
    if not isinstance(patch, str):
        raise ValueError('Patch text is required')
    lines = patch.splitlines()
    if len(lines) < 3 or lines[0] != '*** Begin Patch' or lines[-1] != '*** End Patch':
        raise ValueError('Unsupported patch envelope')
    paths = []
    operation = None
    for line in lines[1:-1]:
        header = next((kind for kind in ('Add File', 'Update File', 'Delete File')
                       if line.startswith('*** '+kind+': ')), None)
        if header:
            value = line[len('*** '+header+': '):]
            if not value.strip(): raise ValueError('Empty edit destination')
            paths.append(value)
            operation = header
        elif line.startswith('*** Move to: '):
            if operation != 'Update File': raise ValueError('Move without update')
            value = line[len('*** Move to: '):]
            if not value.strip(): raise ValueError('Empty move destination')
            paths.append(value)
        elif line.startswith('***'):
            if line != '*** End of File' or operation != 'Update File':
                raise ValueError('Unsupported patch directive')
        elif operation is None:
            raise ValueError('Patch content without a destination')
        elif operation == 'Delete File':
            raise ValueError('Unexpected deleted-file content')
        elif operation == 'Add File' and not line.startswith('+'):
            raise ValueError('Invalid added-file content')
        elif operation == 'Update File' and not (
                line.startswith((' ', '+', '-', '@@')) or line == ''):
            raise ValueError('Invalid update content')
    if not paths: raise ValueError('No edit destinations')
    return paths


def edit_targets(payload):
    if not isinstance(payload, dict): raise ValueError('Expected a payload object')
    cwd = payload.get('cwd')
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError('Absolute working directory required')
    data = payload.get('tool_input')
    if not isinstance(data, dict): raise ValueError('Expected tool input object')
    tool = payload.get('tool_name')
    if tool == 'apply_patch':
        paths = patch_paths(data.get('command'))
    elif tool in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
        paths = [data[key] for key in ('file_path', 'path', 'notebook_path') if key in data]
        if not paths or any(x != paths[0] for x in paths):
            raise ValueError('Missing or conflicting destination fields')
    else:
        raise ValueError('Unsupported direct-edit tool')
    result = []
    for raw in paths:
        if not isinstance(raw, str) or not raw.strip() or '\x00' in raw or '\n' in raw or '\r' in raw:
            raise ValueError('Invalid edit destination')
        target = Path(raw).expanduser()
        if not target.is_absolute(): target = Path(cwd)/target
        # Keep lexical and resolved paths: either may matter to policy, and
        # symlinks must not disguise the destination. Resolution errors deny.
        result.append({'path': raw, 'absolute': str(target), 'resolved': str(target.resolve())})
    return result


if __name__ == '__main__':
    try:
        print(json.dumps({'targets': edit_targets(json.load(sys.stdin))}))
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        print(json.dumps({'error': str(exc)}))
        sys.exit(2)

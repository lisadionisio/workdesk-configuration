#!/usr/bin/env python3
"""Verify, archive without replacement, then mark a transcript complete.

Requires PyYAML. This is a single-writer operation: scheduler ownership must
already be established. Receipts and original bytes support reconciliation
after interruption; a receipt is not a factual-accuracy certification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid

def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Expected a vault-relative path without parent traversal')
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlink paths require reconciliation')
    if not current.resolve().is_relative_to(root):
        raise ValueError('Path escapes vault')
    return current


def parse_source(raw):
    import yaml
    text = raw.decode('utf-8')
    if not text.startswith('---\n') or '\n---\n' not in text[4:]:
        raise ValueError('Source needs YAML frontmatter')
    header, body = text[4:].split('\n---\n', 1)
    # Duplicate metadata keys must not disappear silently during serialization.
    node = yaml.compose(header)
    if not isinstance(node, yaml.MappingNode):
        raise ValueError('Source metadata must be a mapping')
    keys = [key.value for key, _ in node.value]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate source metadata keys require reconciliation')
    metadata = yaml.safe_load(header)
    if metadata.get('processed') is not False and metadata.get('processed') is not None:
        raise ValueError('Already-completed or ambiguous source requires reconciliation')
    return metadata, body


def verify_output_properties(root, outputs):
    """Check declared properties, preserving legacy notes without frontmatter.

    This is not a complete object-schema or factual-accuracy validator.
    """
    import yaml
    root = root.resolve()
    for name in outputs:
        path = safe_path(root, name)
        if Path(name).parts[0] not in ('atlas', 'gtd', 'intel') or path.suffix != '.md':
            raise ValueError('Expected a managed Markdown output')
        text = path.read_text()
        if not text.startswith('---\n'):
            continue
        if '\n---\n' not in text[4:]:
            raise ValueError('Incomplete output frontmatter: ' + name)
        header = text[4:].split('\n---\n', 1)[0]
        node = yaml.compose(header)
        if not isinstance(node, yaml.MappingNode):
            raise ValueError('Output frontmatter must be a mapping: ' + name)
        keys = [key.value for key, _ in node.value]
        if any(not isinstance(key, yaml.ScalarNode) or key.tag != 'tag:yaml.org,2002:str'
               for key, _ in node.value) or len(keys) != len(set(keys)):
            raise ValueError('Output properties must have unique string keys: ' + name)
        data = yaml.safe_load(header)
        if Path(name).parts[:2] == ('atlas', 'meetings') or data.get('type') == 'meeting':
            if 'transcript' in data and (not isinstance(data['transcript'], str) or
                    re.fullmatch(r'\[\[[^\n]+\]\]', data['transcript']) is None):
                raise ValueError('Meeting transcript must be a quoted wikilink string: ' + name)
            if 'attendees' in data and (not isinstance(data['attendees'], list) or
                    any(not isinstance(value, str) or not value.strip() for value in data['attendees'])):
                raise ValueError('Meeting attendees must be a list of strings: ' + name)


def verify_outputs(root, outputs):
    verify_output_properties(root, outputs)
    checker = root / 'config/scripts/check-wikilinks.sh'
    if outputs:
        subprocess.run(['bash', str(checker), '--require-links', *outputs], cwd=root, check=True)


def verify(root, outputs, source):
    verify_outputs(root, outputs)
    checker = root / 'config/scripts/check-wikilinks.sh'
    subprocess.run(['bash', str(checker), str(source)], cwd=root, check=True)


def verify_extraction(root, outputs, source_name):
    """Read-only extraction gate: check outputs and their retained source together."""
    root = root.resolve()
    source = safe_path(root, source_name)
    if Path(source_name).parts[:2] not in (('system', 'intake'), ('system', 'transcripts')) or source.suffix != '.md' or not source.is_file():
        raise ValueError('Expected an existing intake or transcript Markdown source')
    if not outputs:
        raise ValueError('Extraction verification requires outputs')
    verify(root, outputs, source)


def verify_receipt(root, receipt):
    """Read-only revalidation; mutable receipts do not prove factual accuracy."""
    root = root.resolve()
    ledger = safe_path(root, 'system/session-log')
    if receipt.is_symlink() or not receipt.resolve().is_relative_to(ledger.resolve()):
        raise ValueError('Receipt must be inside the source-processing ledger')
    receipt = safe_path(root, str(receipt.resolve().relative_to(root)))
    record = json.loads(receipt.read_text())
    if record.get('complete') is not True or record.get('events', [])[-1:] != ['complete']:
        raise ValueError('Receipt does not establish a completed transition')
    source = safe_path(root, record['source'])
    destination = safe_path(root, record['destination'])
    if source.parent != root/'system/intake' or destination != root/'system/transcripts'/source.name:
        raise ValueError('Receipt source locations do not match the transcript contract')
    if source.exists():
        raise ValueError('Intake copy still exists; reconcile before skipping')
    snapshot = safe_path(root, str((receipt.parent/'source-before.snapshot').relative_to(root)))
    if digest(snapshot.read_bytes()) != record['source_sha256']:
        raise ValueError('Original source snapshot changed')
    if digest(destination.read_bytes()) != record['final_sha256']:
        raise ValueError('Archived source changed since completion')
    outputs = record.get('output_hashes')
    no_content = record.get('disposition') == 'no-content' and isinstance(record.get('disposition_reason'), str) and bool(record['disposition_reason'].strip())
    if not isinstance(outputs, dict) or (not outputs and not no_content):
        raise ValueError('Receipt has no verified output set')
    if no_content and outputs:
        raise ValueError('No-content disposition conflicts with knowledge outputs')
    for name, expected in outputs.items():
        path = safe_path(root, name)
        if Path(name).parts[0] not in ('atlas', 'gtd', 'intel') or path.suffix != '.md':
            raise ValueError('Receipt references an unsupported output')
        if digest(path.read_bytes()) != expected:
            raise ValueError('Output changed since completion; re-review current state')
    verify(root, list(outputs), destination)
    return record


def resume(root, receipt):
    """Recover only a recognized interrupted transition with unchanged outputs."""
    import yaml
    root = root.resolve()
    ledger = safe_path(root, 'system/session-log')
    if receipt.is_symlink() or not receipt.resolve().is_relative_to(ledger.resolve()):
        raise ValueError('Recovery receipt must be within the processing ledger')
    receipt = receipt.resolve()
    record = json.loads(receipt.read_text())
    if record.get('complete') is True:
        verify_receipt(root, receipt)
        return receipt
    source = safe_path(root, record['source'])
    destination = safe_path(root, record['destination'])
    if source.parent != root/'system/intake' or destination != root/'system/transcripts'/source.name:
        raise ValueError('Unsupported recovery locations')
    original = safe_path(root, str((receipt.parent/'source-before.snapshot').relative_to(root))).read_bytes()
    if digest(original) != record['source_sha256']:
        raise ValueError('Original snapshot changed')
    metadata, body = parse_source(original)
    outputs = record['output_hashes']
    if not isinstance(outputs, dict):
        raise ValueError('Invalid output manifest')
    for name, expected in outputs.items():
        path = safe_path(root, name)
        if Path(name).parts[0] not in ('atlas', 'gtd', 'intel') or path.suffix != '.md':
            raise ValueError('Unsupported recovery output')
        if digest(path.read_bytes()) != expected:
            raise ValueError('Output changed; manual reconciliation required')
    metadata['processed'] = True
    metadata['processed-into'] = ['[[' + str(Path(name).with_suffix('')) + ']]' for name in outputs]
    disposition = record.get('disposition', 'processed')
    reason = record.get('disposition_reason')
    if disposition not in ('processed', 'no-content') or (disposition == 'processed' and not outputs):
        raise ValueError('Invalid recovery disposition')
    if disposition == 'no-content' and (outputs or not isinstance(reason, str) or not reason.strip()):
        raise ValueError('Invalid no-content recovery manifest')
    if disposition == 'no-content':
        metadata['processing-disposition'] = disposition
        metadata['processing-reason'] = reason
    finalized = ('---\n' + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True) + '---\n' + body).encode('utf-8')
    if source.exists() and source.read_bytes() != original:
        raise ValueError('Intake source changed; manual reconciliation required')
    archived = destination.read_bytes() if destination.exists() else None
    if archived is not None and archived not in (original, finalized):
        raise ValueError('Archive source changed; manual reconciliation required')
    if not source.exists() and archived is None:
        raise ValueError('Both source locations are missing; restore requires explicit review')
    # A separate immutable recovery record explains the rollback of this
    # helper's own known metadata. No downstream note is rewritten.
    recovery = ledger / ('recovery-' + uuid.uuid4().hex)
    recovery.mkdir(mode=0o700)
    (recovery/'prior-receipt.json').write_bytes(receipt.read_bytes())
    if archived is not None:
        (recovery/'archive-before.snapshot').write_bytes(archived)
    if not source.exists():
        with source.open('xb') as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
    if destination.exists():
        if source.read_bytes() != original or destination.read_bytes() != archived:
            raise ValueError('Source changed during recovery; both copies retained')
        destination.unlink()
    next_receipt = complete(root, record['source'], list(outputs), ledger, disposition, reason)
    (recovery/'result.json').write_text(json.dumps({'prior_receipt': str(receipt), 'resumed_receipt': str(next_receipt)}, indent=2))
    return next_receipt


def complete(root, source_name, output_names, receipts, disposition='processed', reason=None):
    import yaml
    root = root.resolve()
    source = safe_path(root, source_name)
    if source.parent != root / 'system/intake' or source.suffix != '.md':
        raise ValueError('Expected a direct Markdown intake transcript')
    if disposition not in ('processed', 'no-content'):
        raise ValueError('Unsupported processing disposition')
    if disposition == 'no-content':
        if output_names or not isinstance(reason, str) or not reason.strip():
            raise ValueError('No-content requires a reviewed reason and no knowledge outputs')
    elif not output_names:
        raise ValueError('Processed sources require knowledge outputs')
    outputs = []
    for name in output_names:
        path = safe_path(root, name)
        if Path(name).parts[0] not in ('atlas', 'gtd', 'intel') or not path.is_file() or path.suffix != '.md':
            raise ValueError('Expected an existing managed knowledge output')
        outputs.append(path)
    destination = safe_path(root, 'system/transcripts/' + source.name)
    if not destination.parent.is_dir() or destination.exists():
        raise ValueError('Archive directory missing or destination already exists; reconcile first')
    receipt_root = safe_path(root, 'system/session-log')
    if not receipts.is_dir() or receipts.is_symlink() or not receipts.resolve().is_relative_to(receipt_root.resolve()):
        raise ValueError('Provide an existing receipt directory within system/session-log')
    raw = source.read_bytes()
    metadata, body = parse_source(raw)
    before = {str(p.relative_to(root)): digest(p.read_bytes()) for p in outputs}
    transaction = receipts / ('transcript-' + uuid.uuid4().hex)
    transaction.mkdir(mode=0o700)
    (transaction / 'source-before.snapshot').write_bytes(raw)
    record = {'source': source_name, 'destination': str(destination.relative_to(root)),
              'source_sha256': digest(raw), 'output_hashes': before, 'events': [], 'complete': False,
              'disposition': disposition, 'disposition_reason': reason}

    def save(event):
        record['events'].append(event)
        temporary = transaction / 'receipt.next.json'
        with temporary.open('w') as handle:
            json.dump(record, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(transaction / 'receipt.json')

    def unchanged():
        if any(digest(safe_path(root, name).read_bytes()) != value for name, value in before.items()):
            raise ValueError('An output changed during completion; reconcile')

    save('prepared')
    try:
        verify(root, output_names, source)
        unchanged()
        if source.read_bytes() != raw:
            raise ValueError('Source changed during verification')
        save('pre_archive_verified')
        # link() atomically refuses an existing destination. It also fails
        # across filesystems rather than falling back to an unsafe overwrite.
        os.link(source, destination, follow_symlinks=False)
        save('archive_linked_source_retained')
        if source.read_bytes() != raw or destination.read_bytes() != raw:
            raise ValueError('Source changed during archive; both locations retained')
        source.unlink()
        save('archived_uncompleted')
        verify(root, output_names, destination)
        unchanged()
        save('post_archive_verified')
        metadata['processed'] = True
        metadata['processed-into'] = ['[[' + str(p.relative_to(root).with_suffix('')) + ']]' for p in outputs]
        if disposition == 'no-content':
            metadata['processing-disposition'] = disposition
            metadata['processing-reason'] = reason
        final = ('---\n' + yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True) + '---\n' + body).encode('utf-8')
        if destination.read_bytes() != raw:
            raise ValueError('Archived source changed before metadata finalization')
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.completion-', delete=False) as handle:
            pending = Path(handle.name)
            handle.write(final)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(pending, destination.stat().st_mode & 0o777)
        pending.replace(destination)
        save('metadata_finalized')
        verify(root, output_names, destination)
        unchanged()
        record['complete'] = True
        record['final_sha256'] = digest(destination.read_bytes())
        save('complete')
        return transaction / 'receipt.json'
    except Exception as error:
        record['failure_type'] = type(error).__name__
        save('incomplete_reconciliation_required')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vault', type=Path, required=True)
    parser.add_argument('--source')
    parser.add_argument('--output', action='append')
    parser.add_argument('--receipts', type=Path)
    parser.add_argument('--disposition', choices=['processed', 'no-content'], default='processed')
    parser.add_argument('--reason', help='Reviewed source-grounded reason for a no-content disposition')
    parser.add_argument('--verify-receipt', type=Path, help='Read-only revalidation of an existing completion')
    parser.add_argument('--verify-outputs', action='store_true', help='Read-only declared-property and link checks for --output notes')
    parser.add_argument('--verify-extraction', action='store_true', help='Read-only source links plus declared properties and links for --output notes')
    parser.add_argument('--resume-receipt', type=Path, help='Recover an interrupted transition with unchanged source/output hashes')
    args = parser.parse_args()
    if args.verify_extraction:
        if not args.source or not args.output or args.verify_outputs or args.receipts or args.verify_receipt or args.resume_receipt or args.reason or args.disposition != 'processed':
            parser.error('Extraction verification requires --source and --output and cannot be combined with other modes')
        verify_extraction(args.vault, args.output, args.source)
        print('Source links and output properties/links verified; no archival performed; factual review remains separate.')
    elif args.verify_outputs:
        if not args.output or args.source or args.receipts or args.verify_receipt or args.resume_receipt or args.reason or args.disposition != 'processed':
            parser.error('Output verification requires --output and cannot be combined with completion arguments')
        verify_outputs(args.vault.resolve(), args.output)
        print('Declared output properties and links verified; factual review remains separate.')
    elif args.resume_receipt:
        if args.verify_receipt or args.source or args.output or args.receipts or args.reason or args.disposition != 'processed':
            parser.error('Recovery cannot be combined with other completion arguments')
        print(resume(args.vault, args.resume_receipt))
    elif args.verify_receipt:
        if args.source or args.output or args.receipts or args.reason or args.disposition != 'processed':
            parser.error('Receipt verification cannot be combined with completion arguments')
        verify_receipt(args.vault, args.verify_receipt)
        print('Receipt and current source/output hashes verified; factual review remains separate.')
    else:
        if not args.source or not args.receipts:
            parser.error('Completion requires --source and --receipts')
        print(complete(args.vault, args.source, args.output or [], args.receipts, args.disposition, args.reason))

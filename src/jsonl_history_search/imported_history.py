"""Structural imported-message normalization without native identity authority.

The opened file owns identity. Exported roles, clocks and origins are claims;
raw wrappers remain available without becoming native provider transports.
"""
from __future__ import annotations

import json
from pathlib import Path

from .records import Record, text_from_item
from .timeutil import parse_datetime


def imported_message(data: dict, *, path: Path, line: int, default_tz,
                     message: dict, locator: str, projected: bool) -> Record | None:
    declared_role = message.get('role')
    message_type = message.get('type')
    if declared_role is not None and not isinstance(declared_role, str):
        return None
    if message_type is not None and not isinstance(message_type, str):
        return None
    role = declared_role or {'user_message': 'user', 'agent_message': 'assistant',
        'assistant_message': 'assistant'}.get(message_type)
    text = ''
    text_field = None
    if message_type in {'function_call', 'custom_tool_call'}:
        role = 'tool'
        arguments = message.get('arguments', message.get('input', ''))
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        text = f"{message.get('name') or 'function_call'} {arguments}"
    elif message_type in {'function_call_output', 'custom_tool_call_output'}:
        role = 'tool'
        text = text_from_item(message.get('output'))
        text_field = 'output'
    elif message_type in {'CommandExecution', 'CollabAgentToolCall', 'FileChange'}:
        role = 'tool'
        # Event fields describe the declared action/result only. Do not infer
        # a paired call, session edge or successful child execution.
        fields = ('command', 'aggregated_output', 'stdout', 'stderr') if message_type == 'CommandExecution' else (
            ('tool', 'prompt') if message_type == 'CollabAgentToolCall' else ('stdout', 'stderr'))
        parts = []
        for key in fields:
            value = message.get(key)
            if isinstance(value, list) and key == 'command':
                value = ' '.join(str(part) for part in value)
            if isinstance(value, str) and value:
                parts.append(f'{key}: {value}')
            if key == 'aggregated_output' and value:
                break  # The aggregate already includes the output streams.
        text = message_type + ('\n' + '\n'.join(parts) if parts else '')
    elif message.get('projection_notice') and isinstance(message.get('text'), str):
        role, text, text_field = 'unattributed', message['text'], 'text'
    elif role in {'user', 'assistant', 'tool', 'system', 'developer'} and message_type in {
            None, 'message', 'user_message', 'agent_message', 'assistant_message'}:
        for key in ('text', 'content', 'message'):
            value = text_from_item(message.get(key))
            if value:
                text, text_field = value, key
                break
    if role not in {'user', 'assistant', 'tool', 'system', 'developer', 'unattributed'} or not text:
        return None
    raw_timestamp = data.get('timestamp') if projected else message.get('created_at', message.get('timestamp'))
    if raw_timestamp is not None and not isinstance(raw_timestamp, (str, int, float)):
        return None
    provenance = {
        'attribution': 'declared_projected_message' if projected else 'declared_imported_message',
        'declared_role': declared_role, 'declared_type': message_type,
        'value_locator': locator + ('/' + text_field if text_field else ''),
        'text_coordinate_basis': 'utf8_bytes_of_decoded_normalized_message_text',
        'native_authorship_verified': False,
        'request_boundary': 'declared_user_message' if role == 'user' else None,
        'coverage_notes': ['Imported messages do not establish native session, worker, parent or call-pair identity.'],
    }
    if role == 'unattributed':
        provenance['attribution'] = 'unattributed_projected_text'
    if raw_timestamp is not None:
        provenance['declared_timestamp'] = raw_timestamp
    if message.get('projection_notice'):
        provenance['coverage_notes'].append('Source declares a shortened projection: ' + str(message['projection_notice']))
    if projected and isinstance(data.get('native_ref'), str):
        provenance['claimed_origin'] = {'value': data['native_ref'], 'status': 'unverified_claim_only'}
    try:
        timestamp = parse_datetime(raw_timestamp, default_tz)
    except (ValueError, OverflowError, OSError):
        timestamp = None
    if raw_timestamp is not None and timestamp is None:
        provenance['coverage_notes'].append('Declared timestamp could not be interpreted; no record time inferred.')
    return Record(path=str(path), line=line, role=role, text=text,
        timestamp=timestamp,
        raw_timestamp=str(raw_timestamp) if raw_timestamp is not None else None,
        timestamp_quality='declared_projected' if projected else 'declared_imported',
        raw=data, source_format='projected_message_jsonl' if projected else
            'imported_message_jsonl' if locator == '#' else 'imported_message_array_json',
        provider='imported', import_provenance=provenance)


def retained_excerpt(data: dict, *, path: Path) -> Record:
    provenance = {
        'attribution': 'unattributed_retained_text',
        'value_locator': '#/history_excerpt',
        'physical_location': 'document_start',
        'text_coordinate_basis': 'utf8_bytes_of_decoded_history_excerpt',
        'native_authorship_verified': False,
        'coverage_notes': ['Retained text does not establish authors, native clocks, request boundaries or executed tool calls.'],
    }
    for key in ('source_scope', 'scope_end'):
        if isinstance(data.get(key), str):
            provenance['coverage_notes'].append(f"Declared {key}: {data[key]}")
    return Record(path=str(path), line=1, role='unattributed', text=data['history_excerpt'],
        timestamp=None, timestamp_quality='unknown', raw=data,
        source_format='retained_history_excerpt_json', provider='imported', import_provenance=provenance)

"""Structural imported-message normalization without native identity authority.

The opened file owns identity. Exported roles, clocks and origins are claims;
raw wrappers remain available without becoming native provider transports.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .conversation_records import UNKNOWN, classify_conversation_record
from .records import Record, text_from_item
from .timeutil import parse_datetime


_MESSAGE_TYPES = {'message', 'user_message', 'agent_message', 'assistant_message'}
_FUNCTION_CALL_TYPES = {'function_call', 'custom_tool_call'}
_FUNCTION_OUTPUT_TYPES = {'function_call_output', 'custom_tool_call_output'}
_ACTION_TYPES = {'CommandExecution', 'CollabAgentToolCall', 'FileChange'}
_DOCUMENTARY_ROLE = 'human_self_mail_document'
_DOCUMENTARY_DESCRIPTIONS = ('representation', 'historical_agent_timestamp', 'historical_agent_role')
_DOCUMENTARY_FIELDS = {'role', 'document_alias', 'timestamp', 'text', *_DOCUMENTARY_DESCRIPTIONS}


def is_documentary_report(data: dict) -> bool:
    """Reserve documentary declarations even when their schema is malformed."""
    return data.get('role') == _DOCUMENTARY_ROLE or (
        'document_alias' in data and any(key in data for key in _DOCUMENTARY_DESCRIPTIONS))


def documentary_report(data: dict, *, path: Path, line: int) -> Record | None:
    """Preserve a declared document; its mailbox clock never dates quoted actions."""
    if set(data) != _DOCUMENTARY_FIELDS or any(
            not isinstance(value, str) or not value.strip() for value in data.values()):
        return None
    if data['role'] != _DOCUMENTARY_ROLE:
        return None
    raw_timestamp = data['timestamp']
    try:
        timestamp = datetime.fromisoformat(
            raw_timestamp[:-1] + '+00:00' if raw_timestamp.endswith('Z') else raw_timestamp)
    except ValueError:
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None
    provenance = {
        'attribution': 'declared_documentary_report',
        'declared_role': data['role'],
        'declared_timestamp': raw_timestamp,
        'clock_basis': 'mailbox_document_cutoff_only',
        'request_boundary': None,
        'native_authorship_verified': False,
        'value_locator': '#/text',
        'text_coordinate_basis': 'utf8_bytes_of_decoded_documentary_text',
        'documentary_metadata': {
            'document_alias': data['document_alias'],
            **{key: data[key] for key in _DOCUMENTARY_DESCRIPTIONS},
        },
        'coverage_notes': [
            'Documentary declarations do not establish native authors, action clocks, sessions, workers, parents or call pairs.',
        ],
    }
    return Record(path=str(path), line=line, role='unattributed', text=data['text'],
        timestamp=timestamp, raw_timestamp=raw_timestamp,
        timestamp_quality='declared_documentary_mailbox', raw=data,
        source_format='documentary_mail_jsonl', provider='imported',
        source_ref=f'{path}:{line}', import_provenance=provenance)


def imported_message(data: dict, *, path: Path, line: int, default_tz,
                     message: dict, locator: str, projected: bool,
                     allow_unattributed: bool = False) -> Record | None:
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
    if message_type in _FUNCTION_CALL_TYPES:
        role = 'tool'
        arguments = message.get('arguments', message.get('input', ''))
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        text = f"{message.get('name') or 'function_call'} {arguments}"
    elif message_type in _FUNCTION_OUTPUT_TYPES:
        role = 'tool'
        text = text_from_item(message.get('output'))
        text_field = 'output'
    elif message_type in _ACTION_TYPES:
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
    elif (role in {'user', 'assistant', 'tool', 'system', 'developer'}
          or (allow_unattributed and role == 'unattributed')) and (
            message_type is None or message_type in _MESSAGE_TYPES):
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


def retained_metadata(data: dict) -> bool:
    """Recognized retained transport metadata is not a conversation passage."""
    inner = data.get('record')
    return (isinstance(data.get('source_ref', data.get('ref')), str) and isinstance(inner, dict)
            and inner.get('type') in {'session_meta', 'turn_context', 'progress', 'status'})


def _retained_content(value) -> tuple[str, bool]:
    """Decode explicit message/tool blocks, never arbitrary wrapper metadata."""
    if isinstance(value, str):
        return value, False
    if not isinstance(value, list):
        return '', False
    parts, tools = [], False
    for block in value:
        if not isinstance(block, dict):
            continue
        kind = block.get('type')
        if kind in {'text', 'input_text', 'output_text'} and isinstance(block.get('text'), str):
            parts.append(block['text'])
        elif kind == 'tool_use' and isinstance(block.get('name'), str) and 'input' in block:
            parts.append(block['name'] + ' ' + json.dumps(block['input'], ensure_ascii=False))
            tools = True
        elif kind == 'tool_result':
            text = text_from_item(block.get('content'))
            if text:
                parts.append(text)
            tools = True
    return '\n'.join(parts), tools


def retained_record(data: dict, *, path: Path, line: int, default_tz) -> Record | None:
    """Read declared retained transports without trusting their claimed origin."""
    inner = data.get('record')
    origin_key = 'source_ref' if 'source_ref' in data else 'ref'
    if not isinstance(data.get(origin_key), str) or not isinstance(inner, dict):
        return None
    message, locator = inner, '#/record'
    content_tools = False
    prompt_history = (set(inner) <= {'session_id', 'ts', 'text'} and isinstance(inner.get('text'), str)
                      and 'ts' in inner)
    if prompt_history:
        # A prompt-history transport declares text and a clock, not its author.
        message = {'role': 'unattributed', 'text': inner['text'], 'timestamp': inner['ts']}
    elif inner.get('type') in {'response_item', 'event_msg'}:
        message, locator = inner.get('payload'), '#/record/payload'
        if isinstance(message, dict) and message.get('type') == 'item_completed':
            message, locator = message.get('item'), locator + '/item'
            if isinstance(message, dict) and message.get('type') in {'UserMessage', 'AgentMessage'}:
                message = {**message, 'type': 'message',
                           'role': 'user' if message['type'] == 'UserMessage' else 'assistant',
                           'content': message.get('content') or message.get('text')}
    elif inner.get('type') in {'user', 'assistant'} and isinstance(inner.get('message'), dict):
        message, locator = inner['message'], '#/record/message'
        if message.get('role') != inner['type']:
            return None
        text, content_tools = _retained_content(message.get('content'))
        if not text:
            return None
        message = {**message, 'type': 'message', 'text': text}
    if not isinstance(message, dict):
        return None
    if not prompt_history and message.get('type') not in (
            _MESSAGE_TYPES | _FUNCTION_CALL_TYPES | _FUNCTION_OUTPUT_TYPES | _ACTION_TYPES):
        return None
    # The embedded clock is a declaration. The outer claimed source supplies no
    # time, native identity, lineage or instruction to open another path.
    if 'created_at' not in message and 'timestamp' not in message:
        message = {**message, 'timestamp': inner.get('timestamp', inner.get('created_at'))}
    record = imported_message(data, path=path, line=line, default_tz=default_tz,
        message=message, locator=locator, projected=False, allow_unattributed=prompt_history)
    if record is None:
        return None
    record.source_format = 'retained_record_jsonl'
    provenance = record.import_provenance
    provenance['attribution'] = 'unattributed_prompt_history' if prompt_history else 'declared_retained_record'
    provenance['claimed_origin'] = {'value': data[origin_key], 'status': 'unverified_claim_only'}
    if prompt_history:
        provenance['declared_role'] = None
        provenance['declared_type'] = None
        provenance['value_locator'] = '#/record/text'
        provenance['clock_basis'] = 'declared_prompt_history_ts'
        provenance['request_boundary'] = None
    elif locator == '#/record/message':
        provenance['value_locator'] = locator + '/content'
        if content_tools:
            provenance['request_boundary'] = None
            provenance['coverage_notes'].append(
                'Declared message includes tool blocks; tool arguments/results do not prove execution or call-pair identity.')
        if record.role == 'user' and isinstance(inner['message'].get('content'), list) and all(
                isinstance(block, dict) and block.get('type') == 'tool_result' for block in inner['message']['content']):
            record.role = 'tool'
    if record.role == 'user':
        classification = classify_conversation_record({'role': record.role, 'text': record.text})
        if classification.label != UNKNOWN:
            provenance['request_boundary'] = None
    return record


def retained_text(data: dict, *, path: Path, line: int) -> Record | None:
    """A standalone retained excerpt has no declared speaker, clock or lineage."""
    value = data.get('retained_pre_followup_exchange')
    if not isinstance(value, str) or not value:
        return None
    return Record(path=str(path), line=line, role='unattributed', text=value,
        timestamp=None, timestamp_quality='unknown', raw=data, provider='imported',
        source_format='retained_text_jsonl', import_provenance={
            'attribution': 'unattributed_retained_text', 'declared_role': None,
            'value_locator': '#/retained_pre_followup_exchange',
            'text_coordinate_basis': 'utf8_bytes_of_decoded_retained_text',
            'request_boundary': None, 'native_authorship_verified': False,
            'coverage_notes': ['Retained text does not establish authors, native clocks, parents or executed tool calls.'],
        })


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

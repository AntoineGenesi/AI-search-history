"""Native human boundaries for ordinary ask, using its SourcePlan/OperationBudget.

Ownership and limitations: docs/package-map.md#chronological-intervals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .adapters import RawLineScanStats, iter_records_for_file
from .conversation_records import (ASSISTANT_OR_AGENT_MESSAGE, COMPACTED_HISTORY_SUMMARY,
    INJECTED_ENVIRONMENT_CONTEXT, SUBAGENT_NOTIFICATION, TURN_CONTROL_OR_STATUS,
    classify_conversation_record)
from .history_evidence import TEXT_LIMIT, RESPONSE_LIMIT, RETAIN_LIMIT
from .records import text_from_item
from .source_refs import evidence_ref
from .sources import cap_labels_from_warnings, enumerate_jsonl_files, source_kind_for_path


def is_direct_latest_message_history_query(query):
    text = ' '.join(query.lower().split())
    if re.match(r'^(?:explain|implement|fix|debug|write|why\b)', text):
        return False
    return bool(re.search(r'\bwhat(?:\s+the\s+\w+)?\s+(?:(?:has|have)\s+)?(?:happened|did\s+you\s+do).*\b(?:since\s+(?:my|the)\s+last\s+(?:message|turn)|while\s+i\s+was\s+away)', text))


def clip(text, allowance):
    return text.encode('utf-8')[:max(0, allowance)].decode('utf-8', errors='ignore')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


@dataclass
class NativeSession:
    path: Path
    session: str = ''
    parent: str | None = None
    worker: bool = False
    cwd: str | None = None
    anchor: dict | None = None
    anchor_time: object = None
    current_turn: str | None = None
    last_transport: tuple | None = None
    complete: bool = False
    temporal_complete: bool = True
    records: int = 0
    controls: int = 0
    mirrors: int = 0
    after_anchor: int = 0
    future: int = 0
    untimed: int = 0
    reversed: int = 0
    last_time: object = None
    entries: list = field(default_factory=list)
    retained_cost: int = 0
    retention_omitted: int = 0
    clipped: int = 0
    pending: dict = field(default_factory=dict)
    call_omitted: int = 0


def excerpt(record, *, kind, mask_context, text=None, limit=4096):
    text = record.text if text is None else text
    if mask_context:
        text = mask_context.mask_text(text, source_path=record.path, source_index=record.line)
    original = len(text.encode())
    result = {'source_ref': evidence_ref(record),
        'timestamp': record.timestamp.astimezone(timezone.utc).isoformat() if record.timestamp else None,
        'timestamp_quality': record.timestamp_quality, 'kind': kind,
        'speaker': 'assistant' if kind == 'reported_claim' else 'tool' if kind.startswith('tool_') else 'user',
        'text': clip(text, limit), 'text_complete': original <= limit, 'original_text_bytes': original}
    if original > limit and kind == 'tool_result':
        # Exact returned process/git disposition lines may occur anywhere in a
        # long output; retain byte offsets, not an invented outcome summary.
        spans, offset = [], 0
        for line in text.splitlines(keepends=True):
            if re.match(r'\s*(?:\[[^\]\n]+ [0-9a-f]{7,40}\]|Process exited with|Exit code:|Session ID|Script running with cell ID|\d+ files? changed)', line):
                spans.append({'text_byte_start': offset, 'text': clip(line.rstrip(), 1024)})
                if len(spans) >= 16:
                    break
            offset += len(line.encode())
        if spans:
            result['outcome_spans'] = spans
    return result


def admit(state, entry, limit):
    cost = len(encoded(entry))
    if state.retained_cost + cost > limit:
        state.retention_omitted += 1
        return
    state.entries.append(entry)
    state.retained_cost += cost
    state.clipped += not entry['text_complete']


def scan_session(state, *, kind, default_tz, budget, stats, cutoff, mask_context,
                 retained_limit, request_session, request_turn):
    if not state.path.stat().st_mode & 0o444:
        raise PermissionError('source has no read permission bits')
    for record in iter_records_for_file(state.path, kind, default_tz,
            raw_line_stats=stats, budget=budget, budget_phase='chronology'):
        state.records += 1
        raw = record.raw or {}
        body = raw.get('payload', raw)
        if not isinstance(body, dict):
            continue
        top, event = raw.get('type'), body.get('type', raw.get('type'))
        if top == 'session_meta':
            state.session = str(body.get('id') or body.get('session_id') or '')
            state.cwd = body.get('cwd')
            source = body.get('source')
            state.worker = body.get('thread_source') == 'subagent' or (
                isinstance(source, dict) and 'subagent' in source) or body.get('agent_role') in {'worker', 'reviewer'}
            edge = body.get('parent_thread_id') or body.get('parent_session_id')
            state.parent = str(edge) if edge else None
            state.controls += 1
            continue
        if top == 'turn_context':
            state.current_turn = body.get('turn_id')
            state.controls += 1
            continue
        timestamp = record.timestamp.astimezone(timezone.utc) if record.timestamp else None
        if timestamp and state.last_time and timestamp < state.last_time:
            state.temporal_complete = False
            state.reversed += 1
        if timestamp:
            state.last_time = timestamp
        # Parse to EOF: later native rows can be backdated across the cutoff.
        if timestamp and timestamp > cutoff:
            state.future += 1
            continue
        classification = classify_conversation_record(record)
        if classification.label in {COMPACTED_HISTORY_SUMMARY, INJECTED_ENVIRONMENT_CONTEXT,
                                    TURN_CONTROL_OR_STATUS, SUBAGENT_NOTIFICATION}:
            state.controls += 1
            continue
        if not record.text.strip():
            continue
        if raw.get('inherited') or body.get('inherited') or body.get('is_replay'):
            state.controls += 1
            continue
        signature = (classification.label, hashlib.sha256(record.text.encode()).hexdigest())
        previous = state.last_transport
        mirror = (state.session and previous and record.line == previous[0] + 1
                  and signature == previous[1] and {top, previous[2]} == {'response_item', 'event_msg'})
        state.last_transport = (record.line, signature, top)
        if mirror:
            state.mirrors += 1
            continue
        human = classification.is_human_authored and not state.worker
        is_current = (request_session and request_turn and state.session == request_session
                      and (body.get('turn_id') or state.current_turn) == request_turn)
        if human and not is_current:
            if not timestamp:
                state.temporal_complete = False
                state.untimed += 1
            if not state.anchor or (timestamp and (not state.anchor_time or timestamp >= state.anchor_time)):
                state.anchor = excerpt(record, kind='human_message_start', mask_context=mask_context)
                state.anchor_time = timestamp
                state.entries.clear()
                state.pending.clear()
                state.retained_cost = state.after_anchor = state.retention_omitted = state.clipped = 0
            continue
        if not state.anchor or is_current:
            continue
        state.after_anchor += 1
        if not timestamp:
            state.untimed += 1
            state.temporal_complete = False
        if event in {'function_call', 'custom_tool_call', 'tool_call'}:
            arguments = body.get('input') or body.get('arguments')
            text = str(body.get('name') or '') + ' ' + (arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False))
            entry = excerpt(record, kind='tool_invocation', mask_context=mask_context, text=text)
            call = str(body.get('call_id') or body.get('id') or '')
            if call:
                entry['call_id'] = clip(call, 256)
                if len(state.pending) < 128:
                    state.pending[call] = {'source_ref': entry['source_ref'], 'call_id': entry['call_id']}
                else:
                    state.call_omitted += 1
            admit(state, entry, retained_limit)
        elif event in {'function_call_output', 'custom_tool_call_output', 'tool_result', 'tool_output'}:
            entry = excerpt(record, kind='tool_result', mask_context=mask_context,
                text=text_from_item(body.get('output') or body.get('content')) or record.text)
            call = str(body.get('call_id') or body.get('id') or '')
            invocation = state.pending.pop(call, None) if call else None
            entry['call_id'] = clip(call, 256) if call else None
            entry['invocation_ref'] = invocation['source_ref'] if invocation else None
            admit(state, entry, retained_limit)
        elif classification.label == ASSISTANT_OR_AGENT_MESSAGE:
            admit(state, excerpt(record, kind='reported_claim', mask_context=mask_context), retained_limit)
    state.complete = True


def retain_interval(entries, anchor, *, cutoff, omissions):
    directory = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'jsonl-history-search' / 'interval-evidence'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='native-', suffix='.jsonl', dir=directory)
    with os.fdopen(fd, 'wb') as handle:
        header = {'kind': 'bounded_native_interval_excerpts', 'anchor': anchor,
            'cutoff': cutoff.isoformat(), 'omissions': omissions,
            'claim_boundary': 'Exact bounded excerpts; claims/invocations/returned outcomes differ. Child sessions not established complete.'}
        handle.write(encoded(header) + b'\n')
        for entry in entries:
            handle.write(encoded(entry) + b'\n')
    path = Path(name)
    return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'bytes': path.stat().st_size, 'format': 'readable JSONL exact excerpts/native refs', 'requires_continuation': False}


def display_entries(entries, allowance, contexts):
    if not entries or contexts <= 0 or allowance <= 0:
        return []
    count = min(contexts, len(entries))
    groups = [entries[i * len(entries) // count:(i + 1) * len(entries) // count] for i in range(count)]
    selected, per_group = [], allowance // count
    for group in groups:
        # Distribute native structural episodes over the full interval, with
        # claims and actual returned dispositions. No lexical/task ranking.
        important = [e for e in group if e['kind'] == 'reported_claim' or e.get('outcome_spans')]
        chosen = important or group
        pieces, quota = [], max(0, per_group // len(chosen))
        for item in chosen:
            text = clip(item['text'], quota)
            if item.get('outcome_spans'):
                text = clip('\n'.join(s['text'] for s in item['outcome_spans']) + '\n' + text, quota)
            if text:
                pieces.append({'source_ref': item['source_ref'], 'timestamp': item['timestamp'],
                    'kind': item['kind'], 'text': text, 'text_complete': item['text_complete'] and text == item['text'],
                    **({'invocation_ref': item['invocation_ref']} if 'invocation_ref' in item else {})})
        if pieces:
            selected.append({'kind': 'chronological_context', 'source_ref': pieces[0]['source_ref'],
                'timestamp': pieces[0]['timestamp'], 'text': '\n'.join(p['text'] for p in pieces),
                'records': [{k: v for k, v in p.items() if k != 'text'} for p in pieces], 'native_record_count': len(group)})
    return selected


def retrieve_chronological_evidence(*, query, intent, plan, default_tz, budget,
                                  cutoff, mask_context=None, output_format='json'):
    cutoff = cutoff.astimezone(timezone.utc)
    files = list(enumerate_jsonl_files(plan, budget=budget, measured_byte_admission=True))
    states, errors, stats = [], [], RawLineScanStats()
    request_session = os.environ.get('CODEX_THREAD_ID') or os.environ.get('CODEX_SESSION_ID')
    request_turn = os.environ.get('CODEX_TURN_ID')
    retained_limit = max(0, RETAIN_LIMIT // max(1, len(files)) - 8192)
    for path, _, _ in files:
        state = NativeSession(path=path)
        states.append(state)
        kind = source_kind_for_path(path, plan)
        if path.suffix.lower() != '.jsonl' or kind.startswith(('grok_', 'gemini_', 'aider_', 'cursor_', 'opencode_')):
            errors.append({'path': str(path), 'reason': 'native session boundary/ordering unsupported; ordinary provider retrieval remains available'})
            continue
        try:
            scan_session(state, kind=kind, default_tz=default_tz, budget=budget, stats=stats,
                cutoff=cutoff, mask_context=mask_context, retained_limit=retained_limit,
                request_session=request_session, request_turn=request_turn)
        except (OSError, ValueError, TypeError) as exc:
            errors.append({'path': str(path), 'reason': str(exc)})
        if not budget.available('chronology'):
            break
    errors.extend(stats.partial_failures or [])
    candidates = [s for s in states if s.anchor and not s.worker]
    requesting = [s for s in states if s.session == request_session] if request_session else []
    parent = requesting[0].parent if len(requesting) == 1 else None
    parents = [s for s in candidates if s.session == parent] if parent else []
    scope = 'proven_parent' if len(parents) == 1 else 'selected_session' if len(candidates) == 1 and not parent else 'ambiguous_sessions' if candidates else 'unavailable_session'
    chosen = parents[0] if scope == 'proven_parent' else candidates[0] if scope == 'selected_session' else None
    unavailable = [str(r.path) for r in plan.roots if not r.path.exists()]
    unavailable.extend(str(r.path) for r in plan.rejected_roots)
    caps = sorted(set(cap_labels_from_warnings(plan.warnings) + budget.exhausted_reasons))
    source_complete = not (caps or errors or unavailable or len(states) != len(files) or any(not s.complete for s in states))
    temporal = bool(chosen and chosen.temporal_complete)
    complete = source_complete and temporal and chosen is not None
    entries = chosen.entries if chosen else [s.anchor for s in candidates][:max(0, min(30, plan.limits.max_matches))]
    omissions = {'native_control_records': sum(s.controls for s in states), 'native_event_mirrors': sum(s.mirrors for s in states),
        'after_cutoff_records': sum(s.future for s in states), 'retention_records': sum(s.retention_omitted for s in states),
        'clipped_retained_records': sum(s.clipped for s in states), 'pending_association_cap': sum(s.call_omitted for s in states)}
    artifact = None
    if chosen:
        try:
            artifact = retain_interval(entries, chosen.anchor, cutoff=cutoff, omissions=omissions)
        except OSError as exc:
            errors.append({'reason': 'interval artifact unavailable: ' + str(exc)})
    limit = max(0, min(30, plan.limits.max_matches))
    evidence = display_entries(entries, TEXT_LIMIT, limit) if chosen else entries
    display_count = sum(len(e.get('records', [e])) for e in evidence)
    payload = {'query': query, 'status': 'chronological_evidence' if chosen else 'ambiguous' if candidates else 'unavailable' if not source_complete else 'not_found',
        'evidence': evidence, 'scope': {'roots': [str(r.path) for r in plan.roots], 'cutoff': cutoff.isoformat()},
        'coverage': {'complete': complete, 'source_complete': source_complete, 'files_admitted': len(files),
            'files_read': len(budget.opened_sources), 'records_inspected': sum(s.records for s in states),
            'source_errors': errors[:20], 'source_error_count': len(errors), 'cap_reasons': caps,
            'unavailable_roots': unavailable[:20]},
        'chronology': {'scope': scope, 'anchor': chosen.anchor if chosen else None,
            'parent_relationship_verified': scope == 'proven_parent',
            'candidate_anchors': [s.anchor for s in candidates][:limit] if not chosen else [],
            'temporal_complete': temporal, 'cutoff': cutoff.isoformat(),
            'untimed_records': sum(s.untimed for s in states), 'reversed_timestamps': sum(s.reversed for s in states),
            'post_anchor_records': chosen.after_anchor if chosen else None, 'interval_artifact': artifact,
            'retained': {'bytes': sum(s.retained_cost for s in states), 'limit_bytes': RETAIN_LIMIT,
                'complete': not (omissions['retention_records'] or omissions['clipped_retained_records'])},
            'display': {'contexts': len(evidence), 'records': display_count, 'retained_records': len(entries),
                'complete': display_count == len(entries) and all(e.get('text_complete', all(r.get('text_complete') for r in e.get('records', []))) for e in evidence)},
            'unfinished_invocations': list(chosen.pending.values())[:limit] if chosen else []},
        'omissions': omissions, 'work': budget.to_dict(),
        'limits': {'max_files': plan.limits.effective_files(), 'max_bytes': budget.limit_bytes,
            'max_elapsed': budget.max_elapsed, 'max_matches': limit, 'evidence_text_bytes': TEXT_LIMIT,
            'response_bytes': RESPONSE_LIMIT, 'retained_payload_limit': RETAIN_LIMIT},
        'claim_boundary': 'Native selected-session chronology; selected session does not prove requesting-side parent lineage. Source/retained/displayed/final-answer completeness differ. Reported claims are not independent verification.'}
    from .output import render
    while len((render(payload, output_format) + '\n').encode()) > RESPONSE_LIMIT and payload['evidence']:
        payload['evidence'].pop()
        payload['chronology']['display']['complete'] = False
    if len((render(payload, output_format) + '\n').encode()) > RESPONSE_LIMIT:
        payload['chronology']['unfinished_invocations'] = []
        payload['chronology']['candidate_anchors'] = []
        payload['query'] = clip(query, 4096)
    return payload

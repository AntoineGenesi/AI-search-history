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
import stat
import tempfile

from .adapters import RawLineScanStats, iter_records_for_file
from .conversation_records import (ASSISTANT_OR_AGENT_MESSAGE, COMPACTED_HISTORY_SUMMARY,
    INJECTED_ENVIRONMENT_CONTEXT, SUBAGENT_NOTIFICATION, TURN_CONTROL_OR_STATUS,
    classify_conversation_record)
from .history_evidence import TEXT_LIMIT, RESPONSE_LIMIT, RETAIN_LIMIT
from .records import text_from_item, native_message_payload
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
    interval_raw_records: int = 0
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
    if kind == 'tool_result':
        # Exact returned process/git disposition lines may occur anywhere in a
        # long output; retain byte offsets, not an invented outcome summary.
        spans, offset = [], 0
        for line in text.splitlines(keepends=True):
            if re.match(r'\s*(?:\[[^\]\n]+ [0-9a-f]{7,40}\]|Process exited with code [1-9]|Exit code: [1-9]|Session ID|Script running with cell ID|\d+ files? changed)', line):
                span_text = clip(line.rstrip(), 1024)
                spans.append({'text_byte_start': offset, 'text_byte_end': offset + len(span_text.encode()), 'text': span_text})
                if len(spans) >= 16:
                    break
            offset += len(line.encode())
        if spans:
            result['outcome_spans'] = spans
    return result


def admit(state, entry, limit):
    # All interval records bind to this session's single physical source. Store
    # that identity once in the artifact header rather than thousands of times.
    entry['source_ref'] = {k: v for k, v in entry['source_ref'].items() if k in {'line', 'content_index'}}
    if entry.get('invocation_ref'):
        entry['invocation_ref'] = {k: v for k, v in entry['invocation_ref'].items() if k in {'line', 'content_index'}}
    cost = len(encoded(entry)) + 1
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
        body = native_message_payload(raw)
        if not isinstance(body, dict):
            continue
        top, event = raw.get('type'), body.get('type', raw.get('type'))
        if raw.get('inherited') or body.get('inherited') or body.get('is_replay'):
            state.controls += 1
            continue
        claude = 'sessionId' in raw and top in {'user', 'assistant'}
        if claude:
            owner = raw.get('sessionId')
            if not isinstance(owner, str) or not owner or not isinstance(raw.get('isSidechain'), bool):
                raise ValueError('Claude native session/sidechain ownership unavailable')
            if state.session and (state.session != owner or state.worker != raw['isSidechain']):
                raise ValueError('conflicting Claude native session/sidechain ownership')
            if not state.session:
                state.session, state.worker, state.cwd = owner, raw['isSidechain'], raw.get('cwd')
        if state.session and body.get('thread_id') and str(body['thread_id']) != state.session:
            raise ValueError('native event thread conflicts with owning session')
        if top == 'session_meta':
            owner = str(body.get('id') or body.get('session_id') or '')
            if state.session:
                if owner != state.session:
                    raise ValueError('conflicting native session ownership metadata')
                continue
            state.session = owner
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
            if state.anchor and (not record.timestamp or record.timestamp <= cutoff):
                state.interval_raw_records += 1
            continue
        if event == 'task_started' and body.get('turn_id'):
            state.current_turn = body['turn_id']
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
        if state.anchor:
            state.interval_raw_records += 1
        classification = classify_conversation_record(record)
        if classification.label in {COMPACTED_HISTORY_SUMMARY, INJECTED_ENVIRONMENT_CONTEXT,
                                    TURN_CONTROL_OR_STATUS, SUBAGENT_NOTIFICATION}:
            state.controls += 1
            continue
        content = (raw.get('message') or {}).get('content') if claude and isinstance(raw.get('message'), dict) else None
        if isinstance(content, list):
            if state.anchor:
                for index, item in enumerate(content):
                    if not isinstance(item, dict) or item.get('type') not in {'tool_use', 'tool_result'}:
                        continue
                    invocation = item['type'] == 'tool_use'
                    value = str(item.get('name') or '') + ' ' + json.dumps(item.get('input'), ensure_ascii=False) if invocation else text_from_item(item.get('content'))
                    entry = excerpt(record, kind='tool_invocation' if invocation else 'tool_result', mask_context=mask_context, text=value)
                    entry['source_ref']['content_index'] = index
                    raw_call = str((item.get('id') if invocation else item.get('tool_use_id')) or '')
                    call = hashlib.sha256(raw_call.encode()).hexdigest() if raw_call else ''
                    entry['call_id'] = call or None
                    if invocation and call:
                        if len(state.pending) < 128:
                            state.pending[call] = {'source_ref': entry['source_ref'], 'call_id': call}
                        else:
                            state.call_omitted += 1
                    elif not invocation:
                        matched = state.pending.pop(call, None) if call else None
                        entry['invocation_ref'] = matched['source_ref'] if matched else None
                        entry['native_is_error'] = item.get('is_error')
                    admit(state, entry, retained_limit)
                    state.after_anchor += 1
                    if not timestamp:
                        state.temporal_complete = False
                        state.untimed += 1
            # Mixed tool content remains structurally classified from raw data;
            # only actual text blocks can become assistant narrative excerpts.
            record.text = text_from_item([item for item in content if isinstance(item, dict) and item.get('type') == 'text'])
        if not record.text.strip():
            continue
        signature = (classification.label, hashlib.sha256(record.text.encode()).hexdigest())
        previous = state.last_transport
        transport_id = body.get('message_id') or body.get('id')
        proof = (body.get('turn_id') or state.current_turn, str(transport_id) if transport_id else None)
        mirror = (state.session and previous and record.line == previous[0] + 1
                  and signature == previous[1] and {top, previous[2]} == {'response_item', 'event_msg'}
                  and ((proof[0] and proof[0] == previous[3][0]) or (proof[1] and proof[1] == previous[3][1])))
        state.last_transport = (record.line, signature, top, proof)
        if mirror:
            state.mirrors += 1
            original = state.entries[-1] if state.entries and state.entries[-1]['source_ref']['line'] == previous[0] else state.anchor if state.anchor and state.anchor['source_ref']['line'] == previous[0] else None
            if original is not None:
                before = len(encoded(original))
                original['native_mirror_lines'] = [record.line]
                extra = len(encoded(original)) - before
                if original is state.anchor or state.retained_cost + extra <= retained_limit:
                    if original is not state.anchor:
                        state.retained_cost += extra
                else:
                    original.pop('native_mirror_lines', None)
            continue
        claude_plain_human = (claude and top == 'user' and raw['message'].get('role') == 'user'
                              and isinstance(content, str) and bool(content.strip()))
        human = (classification.is_human_authored or claude_plain_human) and not state.worker
        is_current = (request_session and request_turn and state.session == request_session
                      and (body.get('turn_id') or state.current_turn) == request_turn)
        if human and not is_current:
            if not timestamp:
                state.temporal_complete = False
                state.untimed += 1
            # Native record order defines the latest eligible turn; reversed or
            # untimed rows retain their explicit temporal uncertainty.
            state.anchor = excerpt(record, kind='human_message_start', mask_context=mask_context)
            state.anchor_time = timestamp
            state.entries.clear()
            state.pending.clear()
            state.retained_cost = state.after_anchor = state.interval_raw_records = state.retention_omitted = state.clipped = 0
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
            raw_call = str(body.get('call_id') or body.get('id') or '')
            call = hashlib.sha256(raw_call.encode()).hexdigest() if raw_call else ''
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
            raw_call = str(body.get('call_id') or body.get('id') or '')
            call = hashlib.sha256(raw_call.encode()).hexdigest() if raw_call else ''
            invocation = state.pending.pop(call, None) if call else None
            entry['call_id'] = clip(call, 256) if call else None
            entry['invocation_ref'] = invocation['source_ref'] if invocation else None
            admit(state, entry, retained_limit)
        elif classification.label == ASSISTANT_OR_AGENT_MESSAGE:
            admit(state, excerpt(record, kind='reported_claim', mask_context=mask_context), retained_limit)
    state.complete = True


def retain_interval(entries, anchor, *, cutoff, omissions):
    directory = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'jsonl-history-search' / 'interval-evidence'
    # Check existing ancestors before creating anything: a rejected link must
    # not create directories outside the caller-selected cache location.
    for candidate in [directory] + list(directory.parents)[:-1]:
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
            raise OSError('unsafe linked or writable interval-artifact directory')
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    for candidate in [directory] + list(directory.parents)[:-1]:
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
            raise OSError('unsafe linked or writable interval-artifact directory')
    info = directory.lstat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OSError('interval-artifact directory must be owner-only')
    fd, name = tempfile.mkstemp(prefix='native-', suffix='.jsonl', dir=directory)
    digest, total, saved, omitted = hashlib.sha256(), 0, 0, 0
    with os.fdopen(fd, 'wb') as handle:
        header = {'kind': 'bounded_native_interval_excerpts', 'anchor': anchor,
            'source_path': anchor['source_ref']['path'],
            'cutoff': cutoff.isoformat(), 'omissions': omissions,
            'claim_boundary': 'Exact bounded excerpts; claims/invocations/returned outcomes differ. Child sessions not established complete.'}
        data = encoded(header) + b'\n'
        if len(data) > RETAIN_LIMIT:
            raise OSError('interval artifact header exceeds retained allowance')
        handle.write(data)
        digest.update(data)
        total += len(data)
        for entry in entries:
            data = encoded(entry) + b'\n'
            if total + len(data) > RETAIN_LIMIT:
                omitted += 1
                continue
            handle.write(data)
            digest.update(data)
            total += len(data)
            saved += 1
    return {'path': name, 'sha256': digest.hexdigest(), 'bytes': total,
            'records': saved, 'omitted_records': omitted,
            'format': 'readable JSONL exact excerpts/native refs; source_path header binds per-record lines', 'requires_continuation': False}


def display_entries(entries, allowance, contexts, path):
    if not entries or contexts <= 0 or allowance <= 0:
        return []
    count = min(contexts, len(entries))
    groups = [entries[i * len(entries) // count:(i + 1) * len(entries) // count] for i in range(count)]
    selected, per_group = [], allowance // count
    for group in groups:
        # Distribute native structural episodes over the full interval, with
        # claims and actual returned dispositions. No lexical/task ranking.
        chosen = [e for e in group if e['kind'] in {'human_message_start', 'reported_claim'}]
        for event in [e for e in group if e['kind'] == 'tool_result']:
            position = group.index(event)
            following = []
            for subsequent in group[position + 1:]:
                if subsequent['kind'] == 'tool_invocation':
                    break
                if subsequent['kind'] == 'reported_claim':
                    following.append(subsequent)
            if not following and not event.get('outcome_spans'):
                continue
            calls = [e for e in group[:position] if e['kind'] == 'tool_invocation' and e.get('call_id') == event.get('call_id')]
            for e in (calls[-1:] + [event] + following):
                if e not in chosen:
                    chosen.append(e)
        if not chosen:
            chosen = group[:1] + group[-1:] if len(group) > 1 else group
        # Complete short reported assessments are independently useful; do not
        # shrink every episode to a tiny call prefix. Admit actual complete
        # pieces against each chronological group's allowance, then expose the
        # remaining native omissions through the retained interval contract.
        pieces, remaining = [], per_group
        ordered = ([e for e in chosen if e['kind'] in {'human_message_start', 'reported_claim'}]
                   + [e for e in chosen if e['kind'] == 'tool_result']
                   + [e for e in chosen if e['kind'] == 'tool_invocation'])
        for item in ordered:
            text = item['text']
            spans = [{'text_byte_start': 0, 'text_byte_end': len(text.encode()), 'text': text}]
            if item.get('outcome_spans'):
                # Keep original text offsets. Never move disposition lines under
                # a different source, or present a synthetic contiguous passage.
                spans = []
                for span in item['outcome_spans']:
                    value = span['text']
                    spans.append({'text_byte_start': span['text_byte_start'], 'text_byte_end': span['text_byte_start'] + len(value.encode()), 'text': value})
                text = '\n'.join(s['text'] for s in spans)
            cost = len(text.encode()) + 1
            if cost > remaining:
                continue
            if text:
                pieces.append({'source_ref': item['source_ref'], 'timestamp': item['timestamp'],
                    'kind': item['kind'], 'text': text, 'spans': spans,
                    'text_complete': item['text_complete'] and text == item['text'],
                    **({'invocation_ref': item['invocation_ref']} if 'invocation_ref' in item else {})})
                remaining -= cost
        pieces.sort(key=lambda e: (e['source_ref']['line'], e['source_ref'].get('content_index', -1)))
        if not pieces and chosen:
            item = chosen[0]
            text = clip(item['text'], max(0, per_group - 1))
            if text:
                pieces = [{'source_ref': item['source_ref'], 'timestamp': item['timestamp'], 'kind': item['kind'],
                    'text': text, 'spans': [{'text_byte_start': 0, 'text_byte_end': len(text.encode()), 'text': text}],
                    'text_complete': False, **({'invocation_ref': item['invocation_ref']} if 'invocation_ref' in item else {})}]
        if pieces:
            text, references = '', []
            for piece in pieces:
                start = len(text.encode())
                text += piece['text'] + '\n'
                references.append({'line': piece['source_ref']['line'], 'kind': piece['kind'],
                    **({'content_index': piece['source_ref']['content_index']} if 'content_index' in piece['source_ref'] else {}),
                    'display_text_byte_start': start, 'display_text_byte_end': len(text.encode()) - 1,
                    'native_spans': [{k: v for k, v in s.items() if k != 'text'} for s in piece['spans']],
                    'text_complete': piece['text_complete'], 'invocation_ref': piece.get('invocation_ref')})
            selected.append({'kind': 'chronological_context', 'source_ref': {'path': path, **pieces[0]['source_ref']},
                'timestamp': pieces[0]['timestamp'], 'text': text, 'records': references, 'native_record_count': len(group)})
    return selected


def retrieve_chronological_evidence(*, query, intent, plan, default_tz, budget,
                                  cutoff, requested_cwd=None, mask_context=None, output_format='json'):
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
            if state.records and not state.session:
                state.anchor = None
                state.entries.clear()
                errors.append({'path': str(path), 'reason': 'native owning session/worker provenance unavailable; provider chronology unsupported'})
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
    if chosen and requested_cwd and chosen.cwd and Path(chosen.cwd).resolve() != Path(requested_cwd).resolve():
        complete = False
        errors.append({'reason': 'selected native session cwd differs from caller cwd; useful selected-source evidence is returned without parent verification'})
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
    if artifact and artifact['omitted_records']:
        omissions['artifact_records'] = artifact['omitted_records']
    if parent and not parents:
        errors.append({'reason': 'native requesting-side parent is outside or unavailable in admitted scope'})
        complete = False
    limit = max(0, min(30, plan.limits.max_matches))
    allowance = TEXT_LIMIT - len((chosen.anchor['text'] if chosen else '').encode())
    def present(allowance):
        if chosen:
            return display_entries(entries, allowance, limit, str(chosen.path))
        evidence = []
        plausible = candidates[:limit]
        for session in plausible:
            evidence.extend(display_entries([session.anchor] + session.entries,
                allowance // max(1, len(plausible)), limit // max(1, len(plausible)), str(session.path)))
        return evidence
    evidence = present(allowance)
    display_count = sum(len(e.get('records', [e])) for e in evidence)
    payload = {'query': query, 'status': 'chronological_evidence' if chosen else 'ambiguous' if candidates else 'unavailable' if not source_complete else 'not_found',
        'evidence': evidence, 'scope': {'roots': [clip(str(r.path), 2048) for r in plan.roots[:20]],
            'root_count': len(plan.roots), 'roots_display_complete': len(plan.roots) <= 20,
            'cutoff': cutoff.isoformat()},
        'coverage': {'complete': complete, 'source_complete': source_complete, 'files_admitted': len(files),
            'files_read': len(budget.opened_sources), 'records_inspected': sum(s.records for s in states),
            'source_errors': [{k: clip(str(v), 1024) for k, v in e.items()} for e in errors[:20]],
            'source_error_count': len(errors), 'cap_reasons': caps,
            'unavailable_roots': [clip(p, 2048) for p in unavailable[:20]]},
        'chronology': {'scope': scope, 'anchor': chosen.anchor if chosen else None,
            'parent_relationship_verified': scope == 'proven_parent',
            'candidate_anchors': [],
            'temporal_complete': temporal, 'cutoff': cutoff.isoformat(),
            'untimed_records': sum(s.untimed for s in states), 'reversed_timestamps': sum(s.reversed for s in states),
            'post_anchor_records': chosen.after_anchor if chosen else None, 'interval_artifact': artifact,
            'interval_raw_records': chosen.interval_raw_records if chosen else None,
            'retained': {'bytes': sum(s.retained_cost for s in states), 'limit_bytes': RETAIN_LIMIT,
                'complete': not (omissions['retention_records'] or omissions['clipped_retained_records'])},
            'display': {'contexts': len(evidence), 'records': display_count, 'retained_records': len(entries),
                'complete': display_count == len(entries) and all(e.get('text_complete', all(r.get('text_complete') for r in e.get('records', []))) for e in evidence)},
            'unfinished_invocations': [{'source_line': e['source_ref']['line'], 'call_id': e['call_id']}
                for e in list(chosen.pending.values())[:limit]] if chosen else []},
        'omissions': omissions, 'work': budget.to_dict(),
        'limits': {'max_files': plan.limits.effective_files(), 'max_bytes': budget.limit_bytes,
            'max_elapsed': budget.max_elapsed, 'max_matches': limit, 'evidence_text_bytes': TEXT_LIMIT,
            'response_bytes': RESPONSE_LIMIT, 'retained_payload_limit': RETAIN_LIMIT},
        'claim_boundary': 'Native selected-session chronology; selected session does not prove requesting-side parent lineage. Source/retained/displayed/final-answer completeness differ. Reported claims are not independent verification.'}
    from .output import render
    if len(query.encode()) > 4096 and len((render(payload, output_format) + '\n').encode()) > RESPONSE_LIMIT:
        payload['query_display'] = {'complete': False, 'original_bytes': len(query.encode()), 'sha256': hashlib.sha256(query.encode()).hexdigest()}
        payload['query'] = clip(query, 4096)
    while len((render(payload, output_format) + '\n').encode()) > RESPONSE_LIMIT and payload['evidence']:
        allowance //= 2
        payload['evidence'] = present(allowance)
        payload['chronology']['display']['complete'] = False
    while len((render(payload, output_format) + '\n').encode()) > RESPONSE_LIMIT:
        # All source/anchor facts retain counts and native omissions. Reduce
        # metadata presentation, not source authority or operation allowances.
        if payload['evidence']:
            payload['evidence'] = []
            payload['chronology']['display']['complete'] = False
        elif payload['coverage']['source_errors']:
            payload['coverage']['source_errors'] = []
            payload['coverage']['errors_display_complete'] = False
        elif payload['scope']['roots']:
            payload['scope']['roots'] = []
            payload['scope']['roots_display_complete'] = False
        else:
            raise ValueError('fixed chronological response metadata exceeds response allowance')
    payload['chronology']['display']['contexts'] = len(payload['evidence'])
    payload['chronology']['display']['records'] = sum(len(e.get('records', [e])) for e in payload['evidence'])
    return payload

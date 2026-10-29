"""One local streaming history operation; see docs/package-map.md#ordinary-history-workflow.

One full-question vector prevents header-only OR matches. A relevant body span
does not replace its independently observed native message start.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import heapq
import json
import math
import re

from .adapters import AdapterReadError, RawLineScanStats, iter_records_for_file
from .conversation_records import (ASSISTANT_OR_AGENT_MESSAGE, COMPACTED_HISTORY_SUMMARY,
    INJECTED_ENVIRONMENT_CONTEXT, TOOL_ACTION_OUTPUT, TURN_CONTROL_OR_STATUS, UNKNOWN,
    classify_conversation_record)
from .operation_budget import BudgetExceeded
from .query import DATE_TOKEN_RE
from .source_refs import evidence_identity, evidence_ref
from .sources import cap_labels_from_warnings, enumerate_jsonl_files, source_kind_for_path

TEXT_LIMIT, RESPONSE_LIMIT, RETAIN_LIMIT, PASSAGE_LIMIT = 65536, 131072, 4194304, 16384
WORD = re.compile(r'[a-z0-9][a-z0-9_.:/-]*')


@dataclass
class Context:
    passage: dict
    opening: dict | None
    score: float
    cost: int


def lexical_score(words, profile):
    """TF cosine; norm includes ALL vocabulary in the actual returned passage."""
    norm = math.sqrt(sum(n * n for n in words.values()))
    query, query_norm = profile
    return (sum(words.get(t, 0) * n for t, n in query.items()) / (norm * query_norm)
            if norm and query_norm else 0)


def logical_speaker(record, classification):
    if classification.is_human_authored:
        return 'user'
    return {ASSISTANT_OR_AGENT_MESSAGE: 'assistant', TOOL_ACTION_OUTPUT: 'tool'}.get(classification.label, record.role)


def visible(record, intent, speaker):
    if intent.role_filter_explicit and speaker != intent.role:
        return False
    return not intent.date_window_filter or (record.timestamp is not None
        and (not intent.date_window_start or record.timestamp >= intent.date_window_start)
        and (not intent.date_window_end or record.timestamp < intent.date_window_end))


def utf8_end(original, start):
    """An exact <=16KiB native span; never discard a split UTF-8 character."""
    end = min(start + PASSAGE_LIMIT, len(original))
    while end < len(original) and original[end] & 0xc0 == 0x80:
        end -= 1
    return end


def native_passage(record, mask_context, *, kind='matched_excerpt', speaker=None,
                   profile=None, budget=None, message_start=False):
    original = record.text.encode('utf-8')
    start, stop, text, best, offset = 0, 0, '', -1, 0
    while offset < len(original):
        if offset and budget and not budget.available('history'):
            break
        end = utf8_end(original, offset)
        excerpt = original[offset:end].decode('utf-8')
        if mask_context:
            excerpt = mask_context.mask_text(excerpt, source_path=record.path, source_index=record.line)
        value = lexical_score(Counter(WORD.findall(excerpt.lower())), profile) if profile else 0
        if value > best:
            start, stop, text, best = offset, end, excerpt, value
        if message_start:
            break
        offset = end
    item = {'source_ref': evidence_ref(record), 'role': record.role, 'speaker': speaker or record.role,
            'timestamp': record.timestamp.isoformat() if record.timestamp else None,
            'timestamp_quality': record.timestamp_quality, 'kind': kind, 'text': text,
            'passage': {'text_byte_start': start, 'text_byte_end': stop,
                        'original_text_bytes': len(original),
                        'complete': start == 0 and stop == len(original),
                        'spans': [{'text_byte_start': start, 'text_byte_end': stop, 'views': [kind]}],
                        'gaps': []}}
    if record.source_refs:
        item['source_refs'] = list(record.source_refs)
    if record.import_provenance is not None:
        item['source_format'] = record.source_format
        item['import_provenance'] = record.import_provenance
        item['passage']['coordinate_basis'] = record.import_provenance['text_coordinate_basis']
    return item, best


def collect_contexts(*, query, intent, plan, default_tz, budget, mask_context=None):
    words = Counter(WORD.findall(query.lower()))
    profile = (words, math.sqrt(sum(n * n for n in words.values())))
    terms = set(words)
    files = list(enumerate_jsonl_files(plan, preferred_terms=list(terms), budget=budget,
                                      measured_byte_admission=True))
    states = [{'path': p, 'kind': source_kind_for_path(p, plan), 'reader': None,
               'opening': None, 'opening_cost': 0, 'mirrors': {},
               'turn': None, 'last_native': None,
               'session': None, 'worker': False, 'complete': False, 'records': 0} for p, _, _ in files]
    contexts, errors, heap = {}, [], []
    cost = opening_cost = matched = controls = mirrors = 0
    stats = RawLineScanStats()
    limit = max(0, min(30, plan.limits.max_matches))
    queue = deque(states)
    try:
        while queue and budget.available('history'):
            state = queue.popleft()
            if state['reader'] is None:
                state['reader'] = iter_records_for_file(state['path'], state['kind'], default_tz,
                    raw_line_stats=stats, budget=budget, budget_phase='history')
            finished = False
            try:
                for _ in range(128):
                    budget.check('history')
                    record = next(state['reader'])
                    state['records'] += 1
                    raw = record.raw or {}
                    imported = record.import_provenance is not None
                    payload = raw.get('payload', raw)
                    if not imported and raw.get('type') == 'session_meta' and isinstance(payload, dict):
                        session = payload.get('id') or payload.get('session_id')
                        state['session'] = hashlib.sha256(str(session).encode()).hexdigest() if session else None
                        state['worker'] = payload.get('thread_source') == 'subagent' or (
                            isinstance(payload.get('source'), dict) and 'subagent' in payload['source'])
                        continue
                    if not imported and raw.get('type') == 'turn_context' and isinstance(payload, dict):
                        turn = payload.get('turn_id')
                        state['turn'] = hashlib.sha256(str(turn).encode()).hexdigest() if turn else None
                        state['last_native'] = None
                        controls += 1
                        continue
                    if not record.text.strip():
                        continue
                    classification = classify_conversation_record(
                        {'role': record.role, 'text': record.text} if imported else record)
                    speaker = record.role if imported else logical_speaker(record, classification)
                    if not visible(record, intent, speaker):
                        continue
                    if not imported and classification.label in {COMPACTED_HISTORY_SUMMARY, INJECTED_ENVIRONMENT_CONTEXT,
                                                TURN_CONTROL_OR_STATUS}:
                        controls += 1
                        continue
                    kind = raw.get('type')
                    if not imported and state['session'] and record.timestamp and kind in {'response_item', 'event_msg'}:
                        signature = (classification.label, record.timestamp.isoformat(), hashlib.sha256(record.text.encode()).hexdigest())
                        previous = state['mirrors'].get(signature)
                        last = state['last_native']
                        adjacent_turn_mirror = (state['turn'] and last and last[0] == state['turn']
                            and last[1] + 1 == record.line and last[2] == signature[0] and last[3] == signature[2]
                            and {last[4], kind} == {'response_item', 'event_msg'})
                        if adjacent_turn_mirror or (previous and {previous, kind} == {'response_item', 'event_msg'}):
                            mirrors += 1
                            continue
                        state['last_native'] = (state['turn'], record.line, signature[0], signature[2], kind)
                        state['mirrors'][signature] = kind
                        if len(state['mirrors']) > 64:
                            del state['mirrors'][next(iter(state['mirrors']))]
                    plain_user = (classification.label == UNKNOWN and record.role == 'user'
                        and raw.get('role') == 'user' and not raw.get('type') and isinstance(raw.get('text'), str))
                    declared_start = (imported and record.role == 'user'
                        and record.import_provenance.get('request_boundary') == 'declared_user_message'
                        and classification.label == UNKNOWN)
                    opening = declared_start if imported else classification.is_human_authored or plain_user
                    if opening:
                        observed, value = native_passage(record, mask_context,
                            kind='declared_user_message_start' if imported else
                                'worker_assignment_start' if state['worker'] else 'human_message_start',
                            speaker=speaker, message_start=True)
                        observed_cost = len(json.dumps(observed, ensure_ascii=False).encode())
                        opening_cost -= state['opening_cost']
                        state['opening'] = observed if cost + opening_cost + observed_cost <= RETAIN_LIMIT else None
                        state['opening_cost'] = observed_cost if state['opening'] else 0
                        opening_cost += state['opening_cost']
                    lower = record.text.lower()
                    if not any(t in lower for t in terms):
                        continue
                    passage, value = native_passage(record, mask_context,
                        speaker=speaker, profile=profile, budget=budget)
                    if passage is None:
                        continue
                    if value <= 0 or not limit:
                        continue
                    matched += 1
                    identity = evidence_identity(record)
                    if len(heap) >= limit and (value, identity) <= heap[0]:
                        continue
                    observed = state['opening']
                    item_cost = len(json.dumps([passage, observed], ensure_ascii=False).encode())
                    weakest = heap[0][1] if len(heap) >= limit else None
                    replaced_cost = contexts[weakest].cost if weakest is not None else 0
                    if cost - replaced_cost + opening_cost + item_cost <= RETAIN_LIMIT:
                        if weakest is not None:
                            heapq.heappop(heap)
                            cost -= contexts.pop(weakest).cost
                        contexts[identity] = Context(passage, observed, value, item_cost)
                        heapq.heappush(heap, (value, identity))
                        cost += item_cost
            except StopIteration:
                state['complete'] = finished = True
            except BudgetExceeded:
                finished = True
            except (AdapterReadError, OSError) as exc:
                errors.append({'path': str(state['path']), 'reason': str(exc)})
                finished = True
            if not finished:
                queue.append(state)
    finally:
        for state in states:
            if state['reader'] is not None:
                state['reader'].close()
    errors.extend(stats.partial_failures or [])
    return list(contexts.values()), {'states': states, 'errors': errors, 'matched': matched,
        'records': sum(s['records'] for s in states), 'retained_cost': cost + opening_cost,
        'warnings': list(dict.fromkeys(plan.warnings)), 'controls': controls, 'mirrors': mirrors}


def select_contexts(contexts):
    """Small local seam for later chronological/causal/optional model children."""
    ranked = sorted(contexts, key=lambda c: (c.score, evidence_identity(c.passage['source_ref'])), reverse=True)
    first, rest, seen = [], [], set()
    for context in ranked:
        identity = evidence_identity((context.opening or context.passage)['source_ref'])
        (rest if identity in seen else first).append(context)
        seen.add(identity)
    return first + rest


def union_native_views(views):
    """Same validated event only. Text once; nonadjacent spans explicitly gapped.

    Views share the UTF-8 partition, so ranges are identical or disjoint even
    when display redaction changes text length. No masked/native byte guessing.
    """
    pieces = {}
    for view in views:
        p = view['passage']
        key = (p['text_byte_start'], p['text_byte_end'])
        if key not in pieces:
            pieces[key] = [view['text'], []]
        for label in p['spans'][0]['views']:
            if label not in pieces[key][1]:
                pieces[key][1].append(label)
    spans, gaps, parts = [], [], []
    for (start, end), (text, labels) in sorted(pieces.items()):
        if spans and start < spans[-1]['text_byte_end']:
            raise ValueError('Native views must use the same exact UTF-8 partition.')
        if spans and start > spans[-1]['text_byte_end']:
            gap = {'text_byte_start': spans[-1]['text_byte_end'], 'text_byte_end': start}
            gaps.append(gap)
            parts.append('\n[Native text bytes %s:%s omitted]\n' % (gap['text_byte_start'], start))
        parts.append(text)
        if spans and start == spans[-1]['text_byte_end']:
            spans[-1]['text_byte_end'] = end
            spans[-1]['views'] = list(dict.fromkeys(spans[-1]['views'] + labels))
        else:
            spans.append({'text_byte_start': start, 'text_byte_end': end, 'views': labels})
    item = dict(views[0])
    original_bytes = item['passage']['original_text_bytes']
    item['text'] = ''.join(parts)
    item['passage'] = {'text_byte_start': spans[0]['text_byte_start'],
        'text_byte_end': spans[-1]['text_byte_end'], 'original_text_bytes': original_bytes,
        'complete': len(spans) == 1 and spans[0]['text_byte_start'] == 0
                    and spans[0]['text_byte_end'] == original_bytes,
        'spans': spans, 'gaps': gaps}
    if item.get('import_provenance'):
        item['passage']['coordinate_basis'] = item['import_provenance']['text_coordinate_basis']
    return item


def pack_evidence(payload, contexts, output_format):
    from .output import render
    items = [item for context in contexts for item in (context.opening, context.passage) if item]
    original_status = payload['status']
    payload['status'] = 'evidence_found'
    # Reserve final metadata before ANY response-size admission.
    payload['omissions']['presentation_contexts'] = len(items)
    payload['omissions']['clipped_passages'] = len(items)
    # Bound copied diagnostics/query too; never slice serialized JSON or mutate the local request.
    payload['omissions']['diagnostic_examples'] = 0
    payload['query_display'] = {'original_bytes': len(payload['query'].encode()), 'complete': True}
    if len(payload['query'].encode()) > PASSAGE_LIMIT:
        payload['query'] = payload['query'].encode()[:PASSAGE_LIMIT].decode(errors='ignore')
        payload['query_display']['complete'] = False
    diagnostics = [payload['coverage']['source_errors'], payload['coverage']['unavailable_roots'],
                   payload['scope']['roots']]
    while len((render(payload, output_format) + '\n').encode()) > RESPONSE_LIMIT:
        examples = next((rows for rows in diagnostics if rows), None)
        if examples is not None:
            examples.pop()
            payload['omissions']['diagnostic_examples'] += 1
        else:
            payload['query'] = payload['query'][:len(payload['query']) // 2]
            payload['query_display']['complete'] = False
    admitted, text_bytes, omitted = {}, 0, 0
    for item in items:
        identity = evidence_identity(item['source_ref'])
        previous = admitted.get(identity)
        views = previous[1] if previous else []
        if any(view['passage']['text_byte_start'] == item['passage']['text_byte_start']
               and view['passage']['text_byte_end'] == item['passage']['text_byte_end']
               and view['kind'] == item['kind'] for view in views):
            continue
        merged = union_native_views(views + [item])
        old_size = len(payload['evidence'][previous[0]]['text'].encode()) if previous else 0
        delta = len(merged['text'].encode()) - old_size
        if text_bytes + delta > TEXT_LIMIT:
            omitted += 1
            continue
        if previous:
            old_item = payload['evidence'][previous[0]]
            payload['evidence'][previous[0]] = merged
        else:
            payload['evidence'].append(merged)
        if len((render(payload, output_format) + '\n').encode()) > RESPONSE_LIMIT:
            if previous:
                payload['evidence'][previous[0]] = old_item
            else:
                payload['evidence'].pop()
            omitted += 1
        else:
            admitted[identity] = (previous[0] if previous else len(payload['evidence']) - 1, views + [item])
            text_bytes += delta
    payload['omissions']['presentation_contexts'] = omitted
    payload['omissions']['clipped_passages'] = sum(not i['passage']['complete'] for i in payload['evidence'])
    payload['status'] = 'evidence_found' if payload['evidence'] else original_status
    return payload


def retrieve_history_evidence(*, query, intent, plan, default_tz, budget,
                              mask_context=None, output_format='json'):
    contexts, scan = collect_contexts(query=query, intent=intent, plan=plan, default_tz=default_tz,
                                     budget=budget, mask_context=mask_context)
    reasons = sorted(set(cap_labels_from_warnings(scan['warnings']) + budget.exhausted_reasons))
    unavailable = [str(r.path) for r in plan.roots if not r.path.exists()]
    unavailable.extend(str(r) for r in getattr(plan, 'rejected_roots', []))
    complete = not (reasons or scan['errors'] or unavailable or any(not s['complete'] for s in scan['states']))
    status = 'not_found' if complete and scan['states'] else 'empty_scope' if complete else 'unavailable' if unavailable and not scan['states'] else 'partial'
    payload = {'query': query, 'status': status, 'evidence': [],
        'scope': {'roots': [str(r.path) for r in plan.roots], 'root_count': len(plan.roots),
                  'role': intent.role if intent.role_filter_explicit else None,
                  'since': intent.date_window_start.isoformat() if intent.date_window_filter and intent.date_window_start else None,
                  'until': intent.date_window_end.isoformat() if intent.date_window_filter and intent.date_window_end else None,
                  'unresolved_natural_date_qualifier': bool(DATE_TOKEN_RE.search(query)) and intent.date_reason != 'explicit_cli_window',
                  'constraint_boundary': 'Roots/explicit role/applied record-time filters only. Natural session-date/local-time wording is not resolved.'},
        'coverage': {'complete': complete, 'files_admitted': len(scan['states']),
            'files_read': len(budget.opened_sources), 'records_inspected': scan['records'],
            'cap_reasons': reasons, 'unavailable_roots': unavailable[:20], 'unavailable_root_count': len(unavailable),
            'source_errors': scan['errors'][:20], 'source_error_count': len(scan['errors'])},
        'omissions': {'candidate_contexts': max(0, scan['matched'] - len(contexts)),
                      'presentation_contexts': 0, 'clipped_passages': 0,
                      'native_control_records': scan['controls'], 'native_event_mirrors': scan['mirrors']},
        'limits': {'max_files': plan.limits.effective_files(), 'max_bytes': budget.limit_bytes,
                   'max_elapsed': budget.max_elapsed, 'max_matches': min(30, plan.limits.max_matches),
                   'evidence_text_bytes': TEXT_LIMIT, 'response_bytes': RESPONSE_LIMIT,
                   'retained_payload_bytes': scan['retained_cost'], 'retained_payload_limit': RETAIN_LIMIT},
        'work': budget.to_dict(),
        'claim_boundary': 'Opened local passages; lexical matching does not establish causal or complete chronological reconstruction.'}
    return pack_evidence(payload, select_contexts(contexts), output_format)

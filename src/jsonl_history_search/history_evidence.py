"""One local streaming history operation: native passages, not packet gates."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import heapq
import json
import math
import re

from .adapters import AdapterReadError, RawLineScanStats, iter_records_for_file
from .conversation_records import (COMPACTED_HISTORY_SUMMARY, INJECTED_ENVIRONMENT_CONTEXT,
    TURN_CONTROL_OR_STATUS, UNKNOWN, classify_conversation_record)
from .operation_budget import BudgetExceeded
from .query import infer_terms
from .source_refs import evidence_identity, evidence_ref
from .sources import cap_labels_from_warnings, enumerate_jsonl_files, source_kind_for_path

TEXT_LIMIT, RESPONSE_LIMIT, RETAIN_LIMIT, PASSAGE_LIMIT = 65536, 131072, 4194304, 16384
WORD = re.compile(r'[a-z0-9][a-z0-9_.:/-]*')


@dataclass
class Context:
    passage: dict
    opening: dict | None
    terms: dict
    length: int
    score: float
    cost: int


def query_spans(query):
    paragraphs = [p for p in re.split(r'\n\s*\n', query) if p.strip()]
    return paragraphs or [query]


def lexical_score(words, profiles):
    """TF cosine; norm includes ALL vocabulary in the actual returned passage."""
    norm = math.sqrt(sum(n * n for n in words.values()))
    return max((sum(words.get(t, 0) * n for t, n in profile.items()) / (norm * query_norm)
                for profile, query_norm in profiles if norm and query_norm), default=0)


def visible(record, intent):
    if intent.role_filter_explicit and record.role != intent.role:
        return False
    return not intent.date_window_filter or (record.timestamp is not None
        and (not intent.date_window_start or record.timestamp >= intent.date_window_start)
        and (not intent.date_window_end or record.timestamp < intent.date_window_end))


def native_passage(record, terms, mask_context, *, kind='passage', role=None, profiles=(), budget=None):
    original = record.text.encode('utf-8')
    start, text, best = 0, '', -1
    for offset in range(0, len(original), PASSAGE_LIMIT):
        if offset and budget and not budget.available('history'):
            break
        excerpt = original[offset:offset + PASSAGE_LIMIT].decode('utf-8', errors='ignore')
        if mask_context:
            excerpt = mask_context.mask_text(excerpt, source_path=record.path, source_index=record.line)
        value = lexical_score(Counter(WORD.findall(excerpt.lower())), profiles) if profiles else 0
        if value > best:
            start, text, best = offset, excerpt, value
    item = {'source_ref': evidence_ref(record), 'role': role or record.role,
            'timestamp': record.timestamp.isoformat() if record.timestamp else None,
            'timestamp_quality': record.timestamp_quality, 'kind': kind, 'text': text,
            'passage': {'text_byte_start': start, 'original_text_bytes': len(original),
                        'complete': start == 0 and len(original) <= PASSAGE_LIMIT}}
    if record.source_refs:
        item['source_refs'] = list(record.source_refs)
    return item


def collect_contexts(*, query, intent, plan, default_tz, budget, mask_context=None):
    profiles = []
    for span in query_spans(query):
        eligible = set(infer_terms(span.lower(), []))
        words = Counter(t for t in WORD.findall(span.lower()) if t in eligible)
        profiles.append((words, math.sqrt(sum(n * n for n in words.values()))))
    terms = set(t for profile, _ in profiles for t in profile)
    files = list(enumerate_jsonl_files(plan, preferred_terms=list(terms), budget=budget,
                                      measured_byte_admission=True))
    states = [{'path': p, 'kind': source_kind_for_path(p, plan), 'reader': None,
               'opening': None, 'opening_cost': 0, 'mirrors': {},
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
                    payload = raw.get('payload', raw)
                    if raw.get('type') == 'session_meta' and isinstance(payload, dict):
                        state['session'] = payload.get('id') or payload.get('session_id')
                        state['worker'] = payload.get('thread_source') == 'subagent' or (
                            isinstance(payload.get('source'), dict) and 'subagent' in payload['source'])
                        continue
                    if not visible(record, intent) or not record.text.strip():
                        continue
                    classification = classify_conversation_record(record)
                    if classification.label in {COMPACTED_HISTORY_SUMMARY, INJECTED_ENVIRONMENT_CONTEXT,
                                                TURN_CONTROL_OR_STATUS}:
                        controls += 1
                        continue
                    kind = raw.get('type')
                    if state['session'] and record.raw_timestamp and kind in {'response_item', 'event_msg'}:
                        signature = (record.role, record.raw_timestamp, hashlib.sha256(record.text.encode()).hexdigest())
                        previous = state['mirrors'].get(signature)
                        if previous and {previous, kind} == {'response_item', 'event_msg'}:
                            mirrors += 1
                            continue
                        state['mirrors'][signature] = kind
                        if len(state['mirrors']) > 64:
                            del state['mirrors'][next(iter(state['mirrors']))]
                    plain_user = (classification.label == UNKNOWN and record.role == 'user'
                        and raw.get('role') == 'user' and not raw.get('type') and isinstance(raw.get('text'), str))
                    opening = classification.is_human_authored or plain_user
                    if opening:
                        observed = native_passage(record, terms, mask_context,
                            kind='worker_assignment' if state['worker'] else 'request', role='user',
                            profiles=profiles, budget=budget)
                        observed_cost = len(json.dumps(observed, ensure_ascii=False).encode())
                        opening_cost -= state['opening_cost']
                        state['opening'] = observed if cost + opening_cost + observed_cost <= RETAIN_LIMIT else None
                        state['opening_cost'] = observed_cost if state['opening'] else 0
                        opening_cost += state['opening_cost']
                    lower = record.text.lower()
                    if not any(t in lower for t in terms):
                        continue
                    passage = state['opening'] if opening else native_passage(record, terms, mask_context,
                                                                           profiles=profiles, budget=budget)
                    if passage is None:
                        continue
                    words = Counter(WORD.findall(passage['text'].lower()))
                    length = sum(words.values())
                    if not terms.intersection(words) or not limit:
                        continue
                    matched += 1
                    identity = evidence_identity(record)
                    value = lexical_score(words, profiles)
                    if len(heap) >= limit and (value, identity) <= heap[0]:
                        continue
                    observed = state['opening'] if not opening else None
                    item_cost = len(json.dumps([passage, observed, words], ensure_ascii=False).encode())
                    weakest = heap[0][1] if len(heap) >= limit else None
                    replaced_cost = contexts[weakest].cost if weakest is not None else 0
                    if cost - replaced_cost + opening_cost + item_cost <= RETAIN_LIMIT:
                        if weakest is not None:
                            heapq.heappop(heap)
                            cost -= contexts.pop(weakest).cost
                        contexts[identity] = Context(passage, observed, words, length, value, item_cost)
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


def pack_evidence(payload, contexts, output_format):
    from .output import render
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
    seen, text_bytes, omitted = set(), 0, 0
    items = [item for context in contexts for item in (context.passage, context.opening) if item]
    for item in items:
        identity = evidence_identity(item['source_ref'])
        if identity in seen:
            continue
        seen.add(identity)
        size = len(item['text'].encode())
        if text_bytes + size > TEXT_LIMIT:
            omitted += 1
            continue
        payload['evidence'].append(item)
        if len((render(payload, output_format) + '\n').encode()) > RESPONSE_LIMIT:
            payload['evidence'].pop()
            omitted += 1
        else:
            text_bytes += size
    payload['omissions']['presentation_contexts'] = omitted
    payload['omissions']['clipped_passages'] = sum(not i['passage']['complete'] for i in payload['evidence'])
    original_status = payload['status']
    payload['status'] = 'evidence_found' if payload['evidence'] else original_status
    while len((render(payload, output_format) + '\n').encode()) > RESPONSE_LIMIT and payload['evidence']:
        payload['evidence'].pop()
        payload['omissions']['presentation_contexts'] += 1
    payload['omissions']['clipped_passages'] = sum(not i['passage']['complete'] for i in payload['evidence'])
    if not payload['evidence']:
        payload['status'] = original_status
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
                  'since': intent.date_window_start.isoformat() if intent.date_window_start else None,
                  'until': intent.date_window_end.isoformat() if intent.date_window_end else None},
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

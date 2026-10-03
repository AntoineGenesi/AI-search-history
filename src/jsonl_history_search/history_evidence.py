"""One local streaming history operation: native passages, not packet gates."""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import hashlib
import json
import math
import re

from .adapters import AdapterReadError, RawLineScanStats, iter_records_for_file
from .conversation_records import classify_conversation_record
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
    return [p for p in paragraphs if '?' in p] or paragraphs or [query]


def lexical_score(features, length, df, count, average, profiles):
    """Standard BM25 k1=1.2,b=.75; no identifier, role or case bonuses."""
    norm = 1.2 * (.25 + .75 * length / max(average, 1))
    values = {t: math.log(1 + (count - df[t] + .5) / (df[t] + .5))
              * n * 2.2 / (n + norm) for t, n in features.items()}
    return max((sum(values.get(t, 0) for t in p) for p in profiles), default=0)


def visible(record, intent):
    if intent.role_filter_explicit and record.role != intent.role:
        return False
    return not intent.date_window_filter or (record.timestamp is not None
        and (not intent.date_window_start or record.timestamp >= intent.date_window_start)
        and (not intent.date_window_end or record.timestamp < intent.date_window_end))


def native_passage(record, terms, mask_context, *, kind='passage', role=None):
    original = record.text.encode('utf-8')
    start = 0
    if len(original) > PASSAGE_LIMIT:
        lower = record.text.lower()
        first = min((lower.find(t) for t in terms if lower.find(t) >= 0), default=0)
        start = len(record.text[:max(0, first - 256)].encode('utf-8'))
    text = original[start:start + PASSAGE_LIMIT].decode('utf-8', errors='ignore')
    if mask_context:
        text = mask_context.mask_text(text, source_path=record.path, source_index=record.line)
    item = {'source_ref': evidence_ref(record), 'role': role or record.role,
            'timestamp': record.timestamp.isoformat() if record.timestamp else None,
            'timestamp_quality': record.timestamp_quality, 'kind': kind, 'text': text,
            'passage': {'text_byte_start': start, 'original_text_bytes': len(original),
                        'complete': start == 0 and len(original) <= PASSAGE_LIMIT}}
    if record.source_refs:
        item['source_refs'] = list(record.source_refs)
    return item


def collect_contexts(*, query, intent, plan, default_tz, budget, mask_context=None):
    profiles = [infer_terms(span.lower(), []) for span in query_spans(query)]
    terms = set(t for profile in profiles for t in profile)
    files = list(enumerate_jsonl_files(plan, preferred_terms=list(terms), budget=budget,
                                      measured_byte_admission=True))
    states = [{'path': p, 'kind': source_kind_for_path(p, plan), 'reader': None,
               'opening': None, 'opening_cost': 0, 'signature': None,
               'session': None, 'worker': False, 'complete': False, 'records': 0} for p, _, _ in files]
    contexts, errors = {}, []
    cost = opening_cost = count = total_length = matched = 0
    df, stats = Counter(), RawLineScanStats()
    limit = max(0, min(30, plan.limits.max_matches))
    def score(features, length):
        return lexical_score(features, length, df, count, total_length / max(count, 1), profiles)
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
                    plain_user = record.role == 'user' and raw.get('role') == 'user' and not raw.get('type') and isinstance(raw.get('text'), str)
                    opening = classification.is_human_authored or plain_user
                    if opening:
                        kind = raw.get('type')
                        signature = (state['session'], record.raw_timestamp, hashlib.sha256(record.text.encode()).hexdigest())
                        previous = state['signature']
                        mirror = (state['session'] and record.raw_timestamp and previous
                            and previous[0] == signature and {previous[1], kind} == {'response_item', 'event_msg'})
                        if mirror:
                            continue
                        opening_cost -= state['opening_cost']
                        state['opening'] = native_passage(record, terms, mask_context,
                            kind='worker_assignment' if state['worker'] else 'request', role='user')
                        state['opening_cost'] = len(json.dumps(state['opening'], ensure_ascii=False).encode())
                        opening_cost += state['opening_cost']
                        state['signature'] = (signature, kind)
                    count += 1
                    words = Counter(WORD.findall(record.text.lower()))
                    length = sum(words.values())
                    total_length += length
                    features = {t: words[t] for t in terms if words[t]}
                    df.update(features.keys())
                    if not features or not limit:
                        continue
                    matched += 1
                    identity = evidence_identity(record)
                    value = score(features, length)
                    weakest = min(contexts, key=lambda k: contexts[k].score) if len(contexts) >= limit else None
                    if weakest is not None and value < contexts[weakest].score:
                        continue
                    passage = state['opening'] if opening else native_passage(record, terms, mask_context)
                    observed = state['opening'] if not opening else None
                    item_cost = len(json.dumps([passage, observed], ensure_ascii=False).encode())
                    if weakest is not None:
                        cost -= contexts.pop(weakest).cost
                    if cost + opening_cost + item_cost <= RETAIN_LIMIT:
                        contexts[identity] = Context(passage, observed, features, length, value, item_cost)
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
    for context in contexts.values():
        context.score = score(context.terms, context.length)
    return list(contexts.values()), {'states': states, 'errors': errors, 'matched': matched,
        'records': sum(s['records'] for s in states), 'retained_cost': cost + opening_cost,
        'warnings': list(dict.fromkeys(plan.warnings))}


def select_contexts(contexts):
    """Small local seam for later chronological/causal/optional model children."""
    ranked = sorted(contexts, key=lambda c: -c.score)
    first, rest, seen = [], [], set()
    for context in ranked:
        identity = evidence_identity((context.opening or context.passage)['source_ref'])
        (rest if identity in seen else first).append(context)
        seen.add(identity)
    return first + rest


def pack_evidence(payload, contexts, output_format):
    from .output import render
    seen, text_bytes, omitted = set(), 0, 0
    items = [c.opening or c.passage for c in contexts
             if c.opening or c.passage['kind'] in {'request', 'worker_assignment'}]
    items.extend(c.passage for c in contexts)
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
    payload['status'] = 'evidence_found' if payload['evidence'] else payload['status']
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
        'coverage': {'complete': complete, 'files_admitted': len(scan['states']),
            'files_read': len(budget.opened_sources), 'records_inspected': scan['records'],
            'cap_reasons': reasons, 'unavailable_roots': unavailable[:20],
            'source_errors': scan['errors'][:20], 'source_error_count': len(scan['errors'])},
        'omissions': {'candidate_contexts': max(0, scan['matched'] - len(contexts)),
                      'presentation_contexts': 0, 'clipped_passages': 0},
        'limits': {'max_files': plan.limits.effective_files(), 'max_bytes': budget.limit_bytes,
                   'max_elapsed': budget.max_elapsed, 'max_matches': min(30, plan.limits.max_matches),
                   'evidence_text_bytes': TEXT_LIMIT, 'response_bytes': RESPONSE_LIMIT,
                   'retained_payload_bytes': scan['retained_cost'], 'retained_payload_limit': RETAIN_LIMIT},
        'work': budget.to_dict(),
        'claim_boundary': 'Opened local passages; lexical matching does not establish causal or complete chronological reconstruction.'}
    return pack_evidence(payload, select_contexts(contexts), output_format)

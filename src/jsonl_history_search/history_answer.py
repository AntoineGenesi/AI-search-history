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
from .native_observations import NativeOrigin, native_metadata_identity
from .records import text_from_item, native_message_payload, native_retention_projection
from .source_refs import evidence_ref
from .sources import cap_labels_from_warnings, enumerate_jsonl_files, source_kind_for_path


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
    preview_allowance: int | None = None
    preview_reductions: int = 0
    first_unrepresented_ref: dict | None = None
    report_core_omitted: int = 0
    clipped: int = 0
    pending: dict = field(default_factory=dict)
    pending_overflow: int = 0
    invocation_admission_omitted: int = 0
    open_turn: str | None = None
    open_turn_ref: dict | None = None
    turn_conflict: bool = False
    previous_interval: dict | None = None
    request_candidate: dict | None = None
    request_boundary: dict | None = None
    boundary_complete: bool = True
    parent_conflict: bool = False
    native_origin: object = None
    native_scan: dict = field(default_factory=dict)
    record_origin: dict | None = None


class NativeTargetIntegrityError(ValueError):
    """Observed owning metadata no longer supports the selected target."""


@dataclass(frozen=True)
class ChronologyBoundary:
    """Boundary policy is independent of native ownership and acquisition."""
    start: object = None
    end: object = None
    origin: str = 'latest_native_human'

    @property
    def supplied(self):
        return self.start is not None and self.end is not None

    def active(self, state):
        return self.supplied or state.anchor is not None

    def eligible(self, timestamp):
        return not self.supplied or (timestamp is not None and self.start <= timestamp < self.end)

    def selectable(self, state):
        return bool(state.session and (self.supplied or state.anchor) and not state.worker)

    def to_dict(self):
        return {'type': 'record_time_interval' if self.supplied else 'latest_native_human',
                'origin': self.origin, 'since_inclusive': self.start.isoformat() if self.supplied else None,
                'until_exclusive': self.end.isoformat() if self.supplied else None,
                'human_anchor_required': not self.supplied}


def discover_native_sessions(files, *, plan, budget, default_tz, on_metadata=None):
    """Complete owning record only; every header read shares the body budget."""
    metadata, stats = [], RawLineScanStats()
    for path, _, _ in files:
        kind = source_kind_for_path(path, plan)
        if path.suffix.lower() != '.jsonl' or kind.startswith(('grok_', 'gemini_', 'aider_', 'cursor_', 'opencode_')):
            stats.note_partial(path=path, reason='native metadata target discovery unsupported')
            continue
        records = iter_records_for_file(path, kind, default_tz, raw_line_stats=stats,
                                       budget=budget, budget_phase='chronology_metadata')
        try:
            if not path.stat().st_mode & 0o444:
                raise PermissionError('source has no read permission bits')
            record = next(records)
            raw, body = record.raw or {}, native_message_payload(record.raw or {})
            if not isinstance(body, dict) or raw.get('inherited') or body.get('inherited') or body.get('is_replay'):
                raise ValueError('native owning metadata unavailable')
            if raw.get('type') == 'session_meta':
                owner, worker, parent, conflict = native_metadata_identity(body)
                state = NativeSession(path, session=owner, worker=worker, parent=parent,
                                      parent_conflict=conflict, cwd=body.get('cwd'))
            elif raw.get('type') in {'user', 'assistant'} and isinstance(raw.get('sessionId'), str) and isinstance(raw.get('isSidechain'), bool):
                state = NativeSession(path, session=raw['sessionId'], worker=raw['isSidechain'], cwd=raw.get('cwd'))
            else:
                raise ValueError('native owning metadata unavailable')
            metadata.append(state)
            if on_metadata:
                on_metadata(state)
            if state.parent_conflict:
                stats.note_partial(path=path, reason='conflicting native direct-parent metadata')
        except (StopIteration, OSError, ValueError, TypeError) as exc:
            stats.note_partial(path=path, reason=str(exc) or 'native metadata source empty')
        finally:
            records.close()
        if not budget.available('chronology_metadata'):
            break
    return metadata, stats


def resolve_native_target(sessions, request_session, *, parent_requested, main_requested):
    """One scope policy for discovered metadata and confined explicit sources."""
    owners = [session for session in sessions if request_session and session.session == request_session]
    if len(owners) != 1:
        return None, 'unresolved', 'requesting native owner missing or conflicting; no unique target verified'
    owner = owners[0]
    needs_parent = parent_requested or (main_requested and (owner.parent or owner.worker or owner.parent_conflict))
    if not needs_parent:
        return owner, 'requesting_session', None
    parents = [session for session in sessions if owner.parent and session.session == owner.parent]
    if (len(parents) == 1 and not parents[0].worker and not owner.parent_conflict
            and owner.parent != owner.session):
        return parents[0], 'proven_parent', None
    return owner, 'unverified_parent', 'requested native parent missing, conflicting or unavailable; own-session candidate activity only'


INTERVAL_FIELDS = ('anchor', 'anchor_time', 'entries', 'pending', 'retained_cost',
                   'after_anchor', 'interval_raw_records', 'retention_omitted', 'clipped',
                   'preview_allowance', 'preview_reductions', 'first_unrepresented_ref', 'report_core_omitted')


def resolve_request_boundary(state, request_turn, live_request, boundary):
    candidate, prior = state.request_candidate, state.previous_interval
    if not candidate:
        return
    turn = candidate['turn']
    closed = bool(candidate.get('terminal_ref'))
    attributable = candidate['anchor'].get('authorship') != 'uncertain'
    explicit = bool(attributable and request_turn and turn == request_turn and not closed and not state.turn_conflict)
    inferred = bool(attributable and live_request and state.complete and turn and state.open_turn == turn
                    and state.open_turn_ref and not state.turn_conflict)
    state.request_boundary = {'request_ref': candidate['anchor']['source_ref'],
        'mirror_lines': candidate['anchor'].get('native_mirror_lines', []),
        'mirror_refs': candidate['anchor'].get('native_mirror_refs', []),
        'start_ref': candidate.get('start_ref'),
        'terminal_ref': candidate.get('terminal_ref'),
        'basis': 'environment turn corroborated by native occurrence' if explicit else
                 'owned session, terminal native open turn and exact request' if inferred else
                 'native terminal proof; historical human retained' if closed else
                 'conflicting native live-turn identity' if state.turn_conflict else
                 'native source inspection incomplete' if not state.complete else
                 'native live open-turn identity unavailable',
        'status': 'excluded_current_request' if explicit or inferred else 'historical_human' if closed else 'uncertain'}
    if boundary.supplied:
        if (explicit or inferred) and prior:
            for name in INTERVAL_FIELDS:
                setattr(state, name, prior[name])
        elif not closed and live_request:
            state.boundary_complete = False
        state.previous_interval = None
        return
    if explicit or inferred:
        if prior:
            for field in INTERVAL_FIELDS:
                setattr(state, field, prior[field])
        else:
            state.anchor = None
            state.entries, state.pending, state.retained_cost = [], {}, 0
    elif closed or not live_request:
        if prior:
            state.retained_cost -= prior['retained_cost']
    else:
        # Keep attributable prior/current facts, not an empty confident recap.
        # Both lists were admitted against one retained_cost, never two grants.
        state.boundary_complete = False
        if prior:
            state.anchor, state.anchor_time = prior['anchor'], prior['anchor_time']
            state.entries = prior['entries'] + state.entries
            for call, value in prior['pending'].items():
                if call not in state.pending and len(state.pending) < 128:
                    state.pending[call] = value
                elif call not in state.pending:
                    state.pending_overflow += 1
            allowances = [value for value in (state.preview_allowance, prior['preview_allowance']) if value is not None]
            state.preview_allowance = min(allowances) if allowances else None
            refs = [ref for ref in (state.first_unrepresented_ref, prior['first_unrepresented_ref']) if ref]
            state.first_unrepresented_ref = min(refs, key=reference_key) if refs else None
            for field in ('after_anchor', 'interval_raw_records', 'retention_omitted', 'clipped', 'preview_reductions', 'report_core_omitted'):
                setattr(state, field, getattr(state, field) + prior[field])
    state.previous_interval = None


def finalize_native_attribution(state, boundary):
    """Canonical file identity is not proof of mixed legacy record authorship."""
    origin = state.native_origin
    if not origin or not origin.mixed:
        return
    if boundary.supplied:
        for entry in state.entries:
            if not entry.get('own_history_proven'):
                state.boundary_complete = False
    anchors = [state.anchor,
        state.previous_interval.get('anchor') if state.previous_interval else None,
        state.request_candidate.get('anchor') if state.request_candidate else None]
    for anchor in anchors:
        if anchor and not anchor.get('own_history_proven'):
            anchor['authorship'] = 'uncertain'
            state.boundary_complete = False


def excerpt(record, *, kind, mask_context, text=None, limit=4096, protected=False):
    text = record.text if text is None else text
    if mask_context:
        text = mask_context.mask_text(text, source_path=record.path, source_index=record.line)
    original = len(text.encode())
    result = {'source_ref': evidence_ref(record),
        'timestamp': record.timestamp.astimezone(timezone.utc).isoformat() if record.timestamp else None,
        'kind': kind,
        'text': clip(text, limit), 'text_complete': original <= limit, 'original_text_bytes': original}
    if record.timestamp_quality != 'record':
        result['timestamp_quality'] = record.timestamp_quality
    projection = native_retention_projection(record.raw or {}, text)
    if projection:
        auxiliary = projection.pop('auxiliary')
        protected = protected or not auxiliary
        projected_text = projection.pop('text')
        projection['preview_complete'] = len(projected_text.encode()) <= limit
        fields = {'text': clip(projected_text, limit), 'text_complete': False, 'native_projection': projection}
        # A compact telemetry envelope must pay for its own disclosure fields.
        # Opaque content is never represented as readable reasoning.
        if projection['omitted_fields'] or len(encoded(fields)) < len(encoded({key: result[key] for key in ('text', 'text_complete')})):
            result.update(fields)
    if kind == 'tool_invocation' and original > min(256, limit):
        result.update(text='', text_complete=False)
    if protected:
        result['text_is_core'] = True
    body = native_message_payload(record.raw or {})
    for source, target in (('type', 'native_type'), ('channel', 'native_channel'), ('sender', 'native_origin'), ('name', 'tool_name')):
        if isinstance(body, dict) and isinstance(body.get(source), str):
            result[target] = clip(body[source], 256)
    item = body.get('item') if isinstance(body, dict) else None
    if isinstance(item, dict) and isinstance(item.get('type'), str):
        result['native_item_type'] = clip(item['type'], 256)
    if kind == 'tool_result':
        # Exact returned process/git disposition lines may occur anywhere in a
        # long output; retain byte offsets, not an invented outcome summary.
        spans, offset = [], 0
        for line in text.splitlines(keepends=True):
            if re.match(r'\s*(?:\[[^\]\n]+ [0-9a-f]{7,40}\]|Process exited with code -?\d+|Exit code: -?\d+|Session ID|Script running with cell ID|\d+ files? changed)', line):
                span_text = clip(line.rstrip(), 1024)
                spans.append({'text_byte_start': offset, 'text_byte_end': offset + len(span_text.encode()), 'text': span_text})
                if len(spans) >= 16:
                    break
            offset += len(line.encode())
        if spans:
            result['outcome_spans'] = spans
    return result


def preview_candidate(entry, allowance):
    """An exact literal prefix, never a guessed summary or altered native span."""
    if entry.get('text_is_core'):
        return entry
    text = clip(entry['text'], allowance)
    if text == entry['text']:
        return entry
    candidate = {**entry, 'text': text, 'text_complete': False}
    if entry.get('native_projection'):
        candidate['native_projection'] = {**entry['native_projection'], 'preview_complete': False}
    # A tiny complete string can cost less than its explicit gap metadata.
    return candidate if entry_cost(candidate) <= entry_cost(entry) else entry


def reduce_previews(state, allowance, budget):
    """One shared allowance; prior/current snapshots retain their real costs."""
    budget.check('chronology_retention')
    current, prior = vars(state), state.previous_interval
    for interval in ([prior] if prior else []) + [current]:
        for entry in interval['entries']:
            candidate = preview_candidate(entry, allowance)
            if candidate is not entry:
                entry.update(candidate)
        interval['preview_allowance'] = allowance
        interval['preview_reductions'] += 1
    # Shared-prefix snapshots reference the same mutable entries. Recount only
    # after both views have been reduced, then charge a disjoint prior once.
    for interval in ([prior] if prior else []) + [current]:
        interval['retained_cost'] = sum(entry_cost(entry) for entry in interval['entries'])
        interval['clipped'] = sum(not entry['text_complete'] for entry in interval['entries'])
    if prior and not prior.get('shared_entries'):
        state.retained_cost += prior['retained_cost']
    budget.check('chronology_retention')


def next_preview_allowance(state, entry=None):
    if state.preview_allowance is not None:
        return state.preview_allowance // 2
    intervals = [state.entries] + ([state.previous_interval['entries']] if state.previous_interval else [])
    return max((len(item['text'].encode()) for entries in intervals + ([[entry]] if entry else [])
                for item in entries if not item.get('text_is_core')), default=0) // 2


def note_unrepresented(state, ref):
    if state.first_unrepresented_ref is None or reference_key(ref) < reference_key(state.first_unrepresented_ref):
        state.first_unrepresented_ref = dict(ref)


def admit(state, entry, limit, *, budget):
    # All interval records bind to this session's single physical source. Store
    # that identity once in the artifact header rather than thousands of times.
    if state.record_origin and state.record_origin['authorship'] == 'uncertain':
        entry['authorship'] = 'uncertain'
    if state.record_origin and state.record_origin['own_history_proven']:
        entry['own_history_proven'] = True
    for field in ('source_ref', 'invocation_ref'):
        ref = entry.get(field)
        if ref:
            keys = {'line', 'content_index'}
            if 'byte_start' in ref:
                keys.update({'native_ref', 'byte_start', 'byte_end'})
            entry[field] = {k: v for k, v in ref.items() if k in keys}
    if state.preview_allowance is not None:
        entry.update(preview_candidate(entry, state.preview_allowance))
    cost = entry_cost(entry)
    while state.retained_cost + cost > limit and state.preview_allowance != 0:
        allowance = next_preview_allowance(state, entry)
        reduce_previews(state, allowance, budget)
        entry.update(preview_candidate(entry, allowance))
        cost = entry_cost(entry)
    if state.retained_cost + cost > limit:
        state.retention_omitted += 1
        note_unrepresented(state, entry['source_ref'])
        if entry['kind'] == 'tool_invocation' and entry.get('call_id'):
            state.invocation_admission_omitted += 1
        return
    state.entries.append(entry)
    state.retained_cost += cost
    state.clipped += not entry['text_complete']
    if entry['kind'] == 'tool_invocation' and entry.get('call_id'):
        if len(state.pending) < 128:
            # Pending associations reference admitted entries, never a second
            # uncharged copy of their native refs/arguments.
            state.pending[entry['call_id']] = entry
        else:
            state.pending_overflow += 1
            before = entry_cost(entry)
            entry.pop('call_id')
            state.retained_cost += entry_cost(entry) - before


def take_pending_invocation(state, call):
    """Resolved pairs use native refs; hashes belong only to pending lookup."""
    invocation = state.pending.pop(call, None) if call else None
    prior = state.previous_interval
    if invocation and not (prior and prior['pending'].get(call) is invocation):
        before = entry_cost(invocation)
        invocation.pop('call_id', None)
        state.retained_cost += entry_cost(invocation) - before
    return invocation


def begin_human_interval(state, record, *, body, timestamp, origin, uncertain_tail,
                         request_session, request_turn, query, live_request, mask_context):
    """Latest-human replacement policy, separate from streaming acquisition."""
    native_turn = body.get('turn_id') or state.current_turn
    if state.open_turn and native_turn and native_turn != state.open_turn:
        state.turn_conflict = True
    if state.previous_interval:
        state.retained_cost -= state.previous_interval['retained_cost']
        state.previous_interval = None
    owned = bool(request_session and state.session == request_session)
    candidate = owned and ((live_request and record.text == query)
        or (request_turn and native_turn == request_turn))
    if candidate and state.anchor:
        state.previous_interval = {field: getattr(state, field) for field in INTERVAL_FIELDS}
    if not timestamp:
        state.temporal_complete = False
        state.untimed += 1
    # Native record order defines the latest eligible turn; reversed or
    # untimed rows retain their explicit temporal uncertainty.
    state.anchor = excerpt(record, kind='human_message_start', mask_context=mask_context)
    if origin and origin['own_history_proven']:
        state.anchor['own_history_proven'] = True
    if uncertain_tail:
        state.anchor['authorship'] = 'uncertain'
        state.boundary_complete = False
    state.anchor_time = timestamp
    state.request_candidate = {'anchor': state.anchor, 'turn': native_turn,
        'start_ref': state.open_turn_ref if state.open_turn == native_turn else None} if candidate else None
    state.entries, state.pending = [], {}
    if not state.previous_interval:
        state.retained_cost = 0
    state.after_anchor = state.interval_raw_records = state.retention_omitted = state.clipped = 0
    state.preview_allowance = None
    state.preview_reductions = state.report_core_omitted = 0
    state.first_unrepresented_ref = None


def scan_session(state, *, kind, default_tz, budget, stats, cutoff, mask_context,
                 retained_limit, request_session, request_turn, query, live_request, boundary, expected_identity=None):
    if not state.path.stat().st_mode & 0o444:
        raise PermissionError('source has no read permission bits')
    native_origin = NativeOrigin()
    state.native_origin = native_origin

    def segment_boundary():
        state.pending.clear()
        if state.previous_interval:
            state.previous_interval['pending'].clear()
        state.current_turn, state.open_turn, state.open_turn_ref = None, None, None
        state.last_transport = None
        # A request occurrence cannot borrow lifecycle evidence across headers.
        if state.request_candidate:
            state.turn_conflict = True

    for record in iter_records_for_file(state.path, kind, default_tz,
            raw_line_stats=stats, budget=budget, budget_phase='chronology', native_scan=state.native_scan):
        state.records += 1
        if state.native_scan.get('prefix_omitted_bytes') and native_origin.owner:
            # An unseen copied header cannot be ruled out by canonical file
            # ownership. Paginated own-history ordinals can still prove origin.
            native_origin.mixed = True
        raw = record.raw or {}
        body = native_message_payload(raw)
        if not isinstance(body, dict):
            continue
        top, event = raw.get('type'), body.get('type', raw.get('type'))
        if raw.get('inherited') or body.get('inherited') or raw.get('is_replay') or body.get('is_replay'):
            if native_origin.owner:
                native_origin.snapshot(raw.get('ordinal'), raw, evidence_ref(record))
            segment_boundary()
            state.controls += 1
            continue
        claude = 'sessionId' in raw and top in {'user', 'assistant'}
        if claude:
            owner = raw.get('sessionId')
            if not isinstance(owner, str) or not owner or not isinstance(raw.get('isSidechain'), bool):
                raise (NativeTargetIntegrityError if expected_identity else ValueError)('Claude native session/sidechain ownership unavailable')
            if not isinstance(raw.get('message'), dict):
                raise (NativeTargetIntegrityError if expected_identity else ValueError)('Claude native message shape unavailable')
            if state.session and (state.session != owner or state.worker != raw['isSidechain']):
                raise (NativeTargetIntegrityError if expected_identity else ValueError)('conflicting Claude native session/sidechain ownership')
            if expected_identity and (owner, raw['isSidechain'], None, False) != expected_identity:
                raise NativeTargetIntegrityError('changed Claude native owning metadata')
            if not state.session:
                state.session, state.worker, state.cwd = owner, raw['isSidechain'], raw.get('cwd')
        if top == 'session_meta':
            try:
                owner, worker, parent, conflict = native_metadata_identity(body)
                _, first = native_origin.metadata(body, evidence_ref(record), raw.get('ordinal'))
            except ValueError as exc:
                raise (NativeTargetIntegrityError if expected_identity else ValueError)(str(exc))
            if expected_identity and first and (owner, worker, parent, conflict) != expected_identity:
                raise NativeTargetIntegrityError('changed native owning metadata')
            if not first:
                segment_boundary()
                state.controls += 1
                continue
            if state.session:
                if owner != state.session or worker != state.worker:
                    raise (NativeTargetIntegrityError if expected_identity else ValueError)('conflicting native session ownership metadata')
                if conflict or parent != state.parent:
                    state.parent, state.parent_conflict = None, True
                continue
            state.session = owner
            state.cwd = body.get('cwd')
            state.worker, state.parent, state.parent_conflict = worker, parent, conflict
            state.controls += 1
            continue
        if expected_identity and not state.session:
            raise NativeTargetIntegrityError('selected body owning metadata unavailable')
        origin = native_origin.snapshot(raw.get('ordinal'), raw, evidence_ref(record)) if native_origin.owner else None
        state.record_origin = origin
        uncertain_tail = bool(origin and origin['authorship'] == 'uncertain'
                              and state.native_scan.get('prefix_omitted_bytes'))
        if boundary.supplied and uncertain_tail:
            state.boundary_complete = False
        if origin and origin['authorship'] != 'own' and not uncertain_tail:
            # Copied model context is not this session's genuine human boundary.
            segment_boundary()
            state.controls += 1
            state.boundary_complete = state.boundary_complete and origin['authorship'] == 'inherited'
            continue
        if state.session and body.get('thread_id') and str(body['thread_id']) != state.session:
            raise (NativeTargetIntegrityError if expected_identity else ValueError)('native event thread conflicts with owning session')
        timestamp = record.timestamp.astimezone(timezone.utc) if record.timestamp else None
        if timestamp and state.last_time and timestamp < state.last_time:
            state.temporal_complete = False
            state.reversed += 1
        if timestamp:
            state.last_time = timestamp
        # As-of filtering precedes lifecycle: future metadata is not authority.
        if timestamp and timestamp > cutoff:
            state.future += 1
            continue
        if boundary.supplied and not boundary.eligible(timestamp):
            if timestamp is None:
                state.untimed += 1
                state.temporal_complete = False
            continue
        if top == 'turn_context':
            state.current_turn = body.get('turn_id')
            if state.open_turn and state.current_turn and state.current_turn != state.open_turn:
                state.turn_conflict = True
            state.controls += 1
            if boundary.active(state) and (not record.timestamp or record.timestamp <= cutoff):
                state.interval_raw_records += 1
            continue
        if event == 'task_started' and body.get('turn_id'):
            if state.request_candidate and state.request_candidate['turn'] == body['turn_id']:
                state.request_candidate['terminal_ref'] = None
                state.request_candidate['start_ref'] = evidence_ref(record)
            if state.open_turn and state.open_turn != body['turn_id']:
                state.turn_conflict = True
            elif not state.open_turn:
                state.turn_conflict = False
            state.open_turn = body['turn_id']
            state.open_turn_ref = evidence_ref(record)
            state.current_turn = body['turn_id']
        if event in {'task_complete', 'task_completed', 'turn_aborted'} and body.get('turn_id'):
            if state.request_candidate and state.request_candidate['turn'] == body['turn_id']:
                state.request_candidate['terminal_ref'] = evidence_ref(record)
            if state.open_turn == body['turn_id']:
                state.open_turn = None
                state.current_turn, state.open_turn_ref = None, None
            elif state.open_turn:
                state.turn_conflict = True
        if boundary.active(state):
            state.interval_raw_records += 1
        classification = classify_conversation_record(record)
        if classification.label in {COMPACTED_HISTORY_SUMMARY, INJECTED_ENVIRONMENT_CONTEXT,
                                    TURN_CONTROL_OR_STATUS, SUBAGENT_NOTIFICATION}:
            state.controls += 1
            if boundary.supplied and classification.label == TURN_CONTROL_OR_STATUS and record.text.strip():
                # Status records can document genuine provider stops. Retain
                # their source text without promoting them to human/tool proof.
                admit(state, excerpt(record, kind='native_record', mask_context=mask_context, protected=True), retained_limit, budget=budget)
                state.after_anchor += 1
            continue
        content = (raw.get('message') or {}).get('content') if claude and isinstance(raw.get('message'), dict) else None
        if isinstance(content, list):
            if boundary.active(state):
                for index, item in enumerate(content):
                    if not isinstance(item, dict) or item.get('type') not in {'tool_use', 'tool_result'}:
                        continue
                    invocation = item['type'] == 'tool_use'
                    value = str(item.get('name') or '') + ' ' + json.dumps(item.get('input'), ensure_ascii=False) if invocation else text_from_item(item.get('content'))
                    entry = excerpt(record, kind='tool_invocation' if invocation else 'tool_result', mask_context=mask_context, text=value)
                    entry['source_ref']['content_index'] = index
                    if invocation and isinstance(item.get('name'), str):
                        entry['tool_name'] = clip(item['name'], 256)
                    raw_call = str((item.get('id') if invocation else item.get('tool_use_id')) or '')
                    call = hashlib.sha256(raw_call.encode()).hexdigest() if raw_call else ''
                    if invocation:
                        entry['call_id'] = call or None
                    else:
                        matched = take_pending_invocation(state, call)
                        entry['invocation_ref'] = matched['source_ref'] if matched else None
                        entry['native_is_error'] = item.get('is_error')
                    admit(state, entry, retained_limit, budget=budget)
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
        current_ref = evidence_ref(record)
        adjacent = bool(previous and (current_ref.get('byte_start') == previous[0].get('byte_end')
            if 'byte_start' in current_ref else record.line == previous[0]['line'] + 1))
        mirror = (state.session and previous and adjacent
                  and signature == previous[1] and {top, previous[2]} == {'response_item', 'event_msg'}
                  and ((proof[0] and proof[0] == previous[3][0]) or (proof[1] and proof[1] == previous[3][1])))
        state.last_transport = (current_ref, signature, top, proof)
        if mirror:
            state.mirrors += 1
            original = state.entries[-1] if state.entries and reference_key(state.entries[-1]['source_ref']) == reference_key(previous[0]) else state.anchor if state.anchor and reference_key(state.anchor['source_ref']) == reference_key(previous[0]) else None
            if original is not None:
                before = entry_cost(original)
                mirror_field = 'native_mirror_lines' if record.line else 'native_mirror_refs'
                original[mirror_field] = [record.line] if record.line else [current_ref]
                extra = entry_cost(original) - before
                if original is state.anchor or state.retained_cost + extra <= retained_limit:
                    if original is not state.anchor:
                        state.retained_cost += extra
                else:
                    original.pop(mirror_field, None)
            continue
        claude_plain_human = (claude and top == 'user' and raw['message'].get('role') == 'user'
                              and isinstance(content, str) and bool(content.strip()))
        human = (classification.is_human_authored or claude_plain_human) and not state.worker
        if human:
            if boundary.supplied:
                entry = excerpt(record, kind='human_message', mask_context=mask_context)
                if origin and origin['own_history_proven']:
                    entry['own_history_proven'] = True
                if uncertain_tail:
                    entry['authorship'] = 'uncertain'
                    state.boundary_complete = False
                native_turn = body.get('turn_id') or state.current_turn
                owned = bool(request_session and state.session == request_session)
                candidate = owned and ((live_request and record.text == query)
                    or (request_turn and native_turn == request_turn))
                if candidate:
                    state.previous_interval = {name: getattr(state, name) for name in INTERVAL_FIELDS}
                    state.previous_interval['shared_entries'] = True
                    state.previous_interval['interval_raw_records'] = max(0, state.interval_raw_records - 1)
                    state.entries, state.pending = list(state.entries), dict(state.pending)
                    state.request_candidate = {'anchor': {**entry, 'source_ref': dict(entry['source_ref'])}, 'turn': native_turn,
                        'start_ref': state.open_turn_ref if state.open_turn == native_turn else None}
                else:
                    state.request_candidate, state.previous_interval = None, None
                admit(state, entry, retained_limit, budget=budget)
                state.after_anchor += 1
                continue
            begin_human_interval(state, record, body=body, timestamp=timestamp, origin=origin,
                uncertain_tail=uncertain_tail, request_session=request_session, request_turn=request_turn,
                query=query, live_request=live_request, mask_context=mask_context)
            continue
        if not boundary.active(state):
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
            admit(state, entry, retained_limit, budget=budget)
        elif event in {'function_call_output', 'custom_tool_call_output', 'tool_result', 'tool_output'}:
            entry = excerpt(record, kind='tool_result', mask_context=mask_context,
                text=text_from_item(body.get('output') or body.get('content')) or record.text)
            raw_call = str(body.get('call_id') or body.get('id') or '')
            call = hashlib.sha256(raw_call.encode()).hexdigest() if raw_call else ''
            invocation = take_pending_invocation(state, call)
            entry['invocation_ref'] = invocation['source_ref'] if invocation else None
            admit(state, entry, retained_limit, budget=budget)
        elif classification.label == ASSISTANT_OR_AGENT_MESSAGE:
            admit(state, excerpt(record, kind='reported_claim', mask_context=mask_context), retained_limit, budget=budget)
        elif boundary.supplied:
            # Unsupported native items retain source text without inventing a
            # command invocation/result structure or association.
            admit(state, excerpt(record, kind='native_record', mask_context=mask_context), retained_limit, budget=budget)
    state.complete = bool(state.native_scan.get('end_reached') and state.native_scan.get('end_complete'))


def reference_key(ref):
    return ref.get('byte_start', ref['line']), ref.get('content_index', -1)


def entry_gaps(entry):
    """Complement of exact literal ranges in the normalized/masked source text."""
    original = entry['original_text_bytes']
    if entry.get('native_projection'):
        return [(0, original)] if original else []
    ranges = [(0, len(entry['text'].encode()))]
    ranges.extend((span['text_byte_start'], span['text_byte_end']) for span in entry.get('outcome_spans', []))
    end, gaps = 0, []
    for start, stop in sorted(ranges):
        if start > end:
            gaps.append((end, start))
        end = max(end, stop)
    if end < original:
        gaps.append((end, original))
    return gaps


def entry_payload(entry):
    """The report and retention account use exactly the same record encoding."""
    return {**entry, 'omitted_text_gaps': entry_gaps(entry)}


def entry_cost(entry):
    return len(encoded(entry_payload(entry))) + 1


def chronology_omissions(states):
    return {'native_control_records': sum(s.controls for s in states),
        'native_event_mirrors': sum(s.mirrors for s in states),
        'after_cutoff_records': sum(s.future for s in states),
        'retention_records': sum(s.retention_omitted for s in states),
        'clipped_retained_records': sum(s.clipped for s in states),
        'pending_association_cap': sum(s.pending_overflow for s in states),
        'invocation_admission_omitted_records': sum(s.invocation_admission_omitted for s in states),
        'invocation_previews_omitted_records': sum(entry['kind'] == 'tool_invocation' and not entry['text']
            and entry['original_text_bytes'] > 0 for s in states for entry in s.entries),
        'structurally_projected_retained_records': sum('native_projection' in entry for s in states for entry in s.entries),
        'opaque_payload_retained_records': sum(entry.get('native_projection', {}).get('kind') == 'reasoning_with_opaque_payload'
            for s in states for entry in s.entries),
        'usage_observation_readback_records': sum('info' in entry.get('native_projection', {}).get('omitted_fields', [])
            for s in states for entry in s.entries),
        'projected_original_text_bytes': sum(entry['original_text_bytes'] for s in states for entry in s.entries if 'native_projection' in entry),
        'preview_reduction_passes': sum(s.preview_reductions for s in states),
        'report_core_omitted_records': sum(s.report_core_omitted for s in states)}


def chronology_report(state, *, states, cutoff, scope, source_complete,
                      temporal_complete, masked, budget, boundary=None):
    """Preserve cores before previews, including actual report framing cost."""
    for entry in state.entries:
        call = entry.get('call_id')
        if call and state.pending.get(call) is not entry:
            before = entry_cost(entry)
            entry.pop('call_id')
            state.retained_cost += entry_cost(entry) - before
    other_cost = sum(s.retained_cost for s in states if s is not state)
    limit = max(0, RETAIN_LIMIT - other_cost)

    def framing():
        omissions = chronology_omissions(states)
        header = ('BOUNDED NATIVE CHRONOLOGY REPORT\nSource: %s\nScope: %s; parent verified: %s\n'
            'Cutoff: %s\nSource complete: %s; temporal complete: %s\n'
            'Human boundary: %s\nAcquisition boundary: %s\nNative snapshot: %s\nNative origin: %s\nRequest boundary: %s\n'
            'Retention omissions: %s\nMasking enabled: %s\n'
            'Each JSON line is one native event core plus its bounded preview. source_ref binds to Source; '
            'line 0 means physical line unknown. Native order, not timestamp rank. '
            'native_ref/byte ranges retain original native identity. Content indices identify native parts.\n'
            'reported_claim is unverified assistant narrative; tool_result is actual returned output; '
            'native_record has no inferred execution pairing. Missing channel/origin/name is unknown. '
            'Absent timestamp_quality means record timestamp; temporal uncertainty remains separate.\n'
            'text is a literal normalized/masked prefix unless native_projection is present. '
            'outcome_spans are exact supplemental literal ranges. omitted_text_gaps are the exact complement '
            'within original_text_bytes, not guessed unseen content. A native_projection has no literal '
            'source-text coordinates; its complete original representation requires readback.\n'
            'Readback: read-more <Source>:<line> --mode record, or read-more <native_ref> --mode record; '
            'reuse the admitted root. Refs do not establish unseen arguments/outcomes. '
            'Core membership, preview text, source/temporal coverage and final answers are independent.\n'
            'Unresolved invocation refs: %s\nBEGIN NATIVE RECORDS\n') % (
                state.path, scope, scope == 'proven_parent', cutoff.isoformat(), source_complete, temporal_complete,
                encoded(state.anchor).decode() if state.anchor else 'none (supplied record-time interval)',
                encoded(boundary).decode(), encoded(state.native_scan).decode(),
                encoded(state.native_origin.retained() if state.native_origin else None).decode(), encoded(state.request_boundary).decode(),
                encoded(omissions).decode(), bool(masked), encoded([value['source_ref'] for value in state.pending.values()]).decode())
        classes, returned_gaps, requires_readback = {}, 0, bool(state.retention_omitted)
        for entry in state.entries:
            budget.check('chronology_report')
            classes[entry['kind']] = classes.get(entry['kind'], 0) + 1
            gaps = entry_gaps(entry)
            requires_readback = requires_readback or bool(gaps)
            if entry['kind'] == 'tool_result':
                returned_gaps += sum(end - start for start, end in gaps)
        metadata = {'records': len(state.entries),
            'union_records': len(state.entries) + state.report_core_omitted,
            'omitted_records': state.report_core_omitted,
            'projection_complete': not state.report_core_omitted,
            'core_membership_complete': not state.retention_omitted,
            'selected_classes': classes, 'retained_classes': classes,
            'routed_records': 0, 'returned_result_routes_complete': not state.retention_omitted,
            'returned_text_complete': not (returned_gaps or state.retention_omitted),
            'omitted_returned_text_bytes_in_emitted_records': returned_gaps,
            'requires_source_readback': requires_readback,
            'first_unrepresented_ref': ({'path': str(state.path), **state.first_unrepresented_ref}
                if state.first_unrepresented_ref else None),
            'limit_bytes': RETAIN_LIMIT}
        footer = ('END NATIVE RECORDS\nCoverage: ' + encoded(metadata).decode()
                  + '\nEND OF BOUNDED CHRONOLOGY REPORT\n')
        return header.encode(), footer.encode(), metadata

    while True:
        budget.check('chronology_report')
        header, footer, metadata = framing()
        deficit = len(header) + state.retained_cost + len(footer) - limit
        if deficit <= 0:
            break
        if state.preview_allowance != 0:
            reduce_previews(state, next_preview_allowance(state), budget)
            continue
        # Only measured minimum-core/framing overflow may omit event cores.
        # Keep one earliest unrepresented native ref, never an unbounded ledger.
        if not state.entries:
            raise OSError('minimum chronology framing exceeds retained allowance')
        freed = 0
        while state.entries and freed < deficit:
            entry = state.entries.pop()
            cost = entry_cost(entry)
            freed += cost
            state.retained_cost -= cost
            state.clipped -= not entry['text_complete']
            state.retention_omitted += 1
            state.report_core_omitted += 1
            note_unrepresented(state, entry['source_ref'])
            call = entry.get('call_id')
            if call and state.pending.get(call) is entry:
                del state.pending[call]
    core_bytes = sum(min(entry_cost(entry), entry_cost(preview_candidate(entry, 0))) for entry in state.entries)
    records = b''.join(encoded(entry_payload(entry)) + b'\n' for entry in state.entries)
    framing_bytes = len(header) + len(footer)
    return header + records + footer, {**metadata,
        'record_bytes': state.retained_cost, 'core_bytes': core_bytes,
        'optional_preview_bytes': state.retained_cost - core_bytes,
        'framing_bytes': framing_bytes, 'shared_retained_bytes': other_cost + state.retained_cost + framing_bytes}


def retain_interval(state, *, states, cutoff, scope, source_complete,
                    temporal_complete, masked, budget, boundary=None):
    data, projection = chronology_report(state, states=states, cutoff=cutoff,
        scope=scope, source_complete=source_complete, temporal_complete=temporal_complete,
        masked=masked, budget=budget, boundary=boundary)
    budget.check('chronology_report')
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
    fd, name = tempfile.mkstemp(prefix='native-', suffix='.txt', dir=directory)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
    return {'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
            **projection, 'format': 'plain UTF-8 chronology report', 'requires_continuation': False}


def display_entries(entries, allowance, contexts, path):
    if not entries or contexts <= 0 or allowance <= 0:
        return []
    count = min(contexts, len(entries))
    groups = [entries[i * len(entries) // count:(i + 1) * len(entries) // count] for i in range(count)]
    selected, per_group = [], allowance // count
    for group in groups:
        # Distribute native structural episodes over the full interval, with
        # claims and actual returned dispositions. No lexical/task ranking.
        chosen = [e for e in group if e['kind'] in {'human_message_start', 'human_message', 'reported_claim', 'native_record'}]
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
            calls = [e for e in group[:position] if e['kind'] == 'tool_invocation' and event.get('invocation_ref')
                     and reference_key(e['source_ref']) == reference_key(event['invocation_ref'])]
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
        ordered = ([e for e in chosen if e['kind'] in {'human_message_start', 'human_message', 'reported_claim', 'native_record'}]
                   + [e for e in chosen if e['kind'] == 'tool_result']
                   + [e for e in chosen if e['kind'] == 'tool_invocation'])
        for item in ordered:
            text = item['text']
            spans = [{'text_byte_start': 0, 'text_byte_end': len(text.encode()), 'text': text}]
            if item.get('outcome_spans') and (not item['text_complete'] or len(text.encode()) + 1 > remaining):
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
                    **({'native_projection': item['native_projection']} if 'native_projection' in item else {}),
                    **({'authorship': item['authorship']} if 'authorship' in item else {}),
                    'text_complete': item['text_complete'] and text == item['text'],
                    **({'invocation_ref': item['invocation_ref']} if 'invocation_ref' in item else {})})
                remaining -= cost
        pieces.sort(key=lambda e: reference_key(e['source_ref']))
        if not pieces and chosen:
            item = chosen[0]
            text = clip(item['text'], max(0, per_group - 1))
            if text:
                pieces = [{'source_ref': item['source_ref'], 'timestamp': item['timestamp'], 'kind': item['kind'],
                    **({'native_projection': {**item['native_projection'], 'preview_complete': False}} if 'native_projection' in item else {}),
                    **({'authorship': item['authorship']} if 'authorship' in item else {}),
                    'text': text, 'spans': [{'text_byte_start': 0, 'text_byte_end': len(text.encode()), 'text': text}],
                    'text_complete': False, **({'invocation_ref': item['invocation_ref']} if 'invocation_ref' in item else {})}]
        if pieces:
            text, references = '', []
            for piece in pieces:
                start = len(text.encode())
                text += piece['text'] + '\n'
                references.append({**piece['source_ref'], 'kind': piece['kind'],
                    **({'authorship': piece['authorship']} if 'authorship' in piece else {}),
                    'display_text_byte_start': start, 'display_text_byte_end': len(text.encode()) - 1,
                    'native_spans': [] if piece.get('native_projection') else [{k: v for k, v in s.items() if k != 'text'} for s in piece['spans']],
                    **({'native_projection': piece['native_projection']} if 'native_projection' in piece else {}),
                    'text_complete': piece['text_complete'], 'invocation_ref': piece.get('invocation_ref')})
            selected.append({'kind': 'chronological_context', 'source_ref': {'path': path, **pieces[0]['source_ref']},
                'timestamp': pieces[0]['timestamp'], 'text': text, 'records': references, 'native_record_count': len(group)})
    return selected


def retrieve_chronological_evidence(*, query, intent, plan, default_tz, budget,
                                  cutoff, requested_cwd=None, mask_context=None, output_format='json', live_request=False,
                                  explicit_roots=True):
    cutoff = cutoff.astimezone(timezone.utc)
    boundary = (ChronologyBoundary(intent.date_window_start, intent.date_window_end, intent.interval_origin)
                if intent.chronology_mode == 'supplied_interval' else ChronologyBoundary())
    states, stats = [], RawLineScanStats()
    request_session = os.environ.get('CODEX_THREAD_ID')
    request_turn = os.environ.get('CODEX_TURN_ID')
    discovery_stats, metadata = RawLineScanStats(), []
    target, target_scope = None, None
    parent_requested = bool(re.search(r'\bparent\s+(?:thread|session|conversation|chat)\b', query.lower()))
    main_requested = bool(re.search(r'\bmain\s+(?:thread|session|conversation|chat)\b', query.lower()))
    priority = [request_session]
    files = []
    def admitted_files():
        for item in enumerate_jsonl_files(plan, budget=budget, measured_byte_admission=True,
                priority_identifiers=(lambda: priority) if not explicit_roots and request_session else None):
            files.append(item)
            yield item
    def observe_owner(state):
        if state.session == request_session and state.parent and (parent_requested or main_requested):
            priority.insert(0, state.parent)

    if not explicit_roots:
        metadata, discovery_stats = discover_native_sessions(admitted_files(), plan=plan, budget=budget,
            default_tz=default_tz, on_metadata=observe_owner)
        target, target_scope, diagnostic = resolve_native_target(metadata, request_session,
            parent_requested=parent_requested, main_requested=main_requested)
        if diagnostic:
            discovery_stats.note_partial(reason=diagnostic)
    else:
        list(admitted_files())
    scan_files = [f for f in files if f[0] == target.path] if target else files if explicit_roots else []
    for path, _, _ in scan_files:
        state = NativeSession(path=path)
        states.append(state)
        kind = source_kind_for_path(path, plan)
        if path.suffix.lower() != '.jsonl' or kind.startswith(('grok_', 'gemini_', 'aider_', 'cursor_', 'opencode_')):
            stats.note_partial(path=path, reason='native session boundary/ordering unsupported; ordinary provider retrieval remains available')
            continue
        try:
            retained_limit = max(0, RETAIN_LIMIT - sum(s.retained_cost for s in states))
            scan_session(state, kind=kind, default_tz=default_tz, budget=budget, stats=stats,
                cutoff=cutoff, mask_context=mask_context, retained_limit=retained_limit,
                request_session=request_session, request_turn=request_turn, query=query, live_request=live_request,
                boundary=boundary,
                expected_identity=(target.session, target.worker, target.parent, target.parent_conflict) if target else None)
            if target and not state.session:
                raise NativeTargetIntegrityError('selected body owning metadata unavailable')
            if state.records and not state.session:
                state.anchor = None
                state.entries.clear()
                stats.note_partial(path=path, reason='native owning session/worker provenance unavailable; provider chronology unsupported')
        except NativeTargetIntegrityError as exc:
            # Finalization must not restore a pending earlier interval after
            # native identity becomes ineligible, including later contradictions.
            state.anchor, state.previous_interval, state.request_candidate, state.request_boundary = None, None, None, None
            state.entries, state.pending, state.retained_cost = [], {}, 0
            state.session = ''
            state.complete = False
            target_scope = 'target_integrity_unavailable'
            stats.note_partial(path=path, reason='target integrity: ' + str(exc))
        except (OSError, ValueError, TypeError) as exc:
            stats.note_partial(path=path, reason=str(exc))
        finally:
            finalize_native_attribution(state, boundary)
            resolve_request_boundary(state, request_turn, live_request, boundary)
            finalize_native_attribution(state, boundary)
        if state.parent_conflict:
            stats.note_partial(path=path, reason='conflicting native direct-parent metadata')
        if not budget.available('chronology'):
            break
    candidates = [s for s in states if boundary.selectable(s)]
    requesting = [s for s in states if s.session == request_session] if request_session else []
    chosen = candidates[0] if len(candidates) == 1 else None
    scope = 'selected_session' if chosen else 'ambiguous_sessions' if candidates else 'unavailable_session'
    if explicit_roots and requesting:
        selected, selected_scope, diagnostic = resolve_native_target(states, request_session,
            parent_requested=parent_requested, main_requested=main_requested)
        chosen = selected if selected and boundary.selectable(selected) else None
        scope = selected_scope if chosen else 'ambiguous_sessions' if candidates else 'unavailable_session'
        if diagnostic:
            stats.note_partial(reason=diagnostic)
    elif not explicit_roots:
        chosen = states[0] if target and states and boundary.selectable(states[0]) else None
        scope = target_scope if chosen else 'ambiguous_sessions' if candidates else 'unavailable_session'
        if not target:
            chosen = None
    unavailable = [str(r.path) for r in plan.roots if not r.path.exists()]
    unavailable.extend(str(r.path) for r in plan.rejected_roots)
    caps = sorted(set(cap_labels_from_warnings(plan.warnings) + budget.exhausted_reasons))
    discovery_complete = not (caps or discovery_stats.partial_failure_count) if not explicit_roots else None
    source_complete = not (caps or stats.partial_failure_count or (unavailable if explicit_roots else []) or len(states) != len(scan_files) or any(not s.complete or s.native_scan.get('prefix_omitted_bytes') for s in states))
    if not explicit_roots and not target:
        source_complete = False
    temporal = bool(chosen and chosen.temporal_complete)
    complete = source_complete and temporal and chosen is not None and chosen.boundary_complete
    cutoff_truncated = bool(boundary.supplied and cutoff < boundary.end)
    complete = complete and not cutoff_truncated
    boundary_metadata = {**boundary.to_dict(), 'cutoff_truncated': cutoff_truncated,
        'native_boundary_complete': bool(chosen and chosen.boundary_complete),
        'activity_authorship_complete': bool(chosen and (not chosen.native_origin or not chosen.native_origin.mixed
            or all(entry.get('own_history_proven') for entry in chosen.entries)))}
    if not explicit_roots:
        complete = complete and bool(discovery_complete) and scope in {'requesting_session', 'proven_parent'}
    if chosen and requested_cwd and chosen.cwd and Path(chosen.cwd).resolve() != Path(requested_cwd).resolve():
        complete = False
        stats.note_partial(reason='selected native session cwd differs from caller cwd; useful selected-source evidence is returned without parent verification')
    artifact = None
    if chosen:
        try:
            artifact = retain_interval(chosen, states=states, cutoff=cutoff,
                scope=scope, source_complete=source_complete, temporal_complete=temporal,
                masked=mask_context, budget=budget, boundary=boundary_metadata)
        except OSError as exc:
            stats.note_partial(reason='interval artifact unavailable: ' + str(exc))
            complete = False
        caps = sorted(set(cap_labels_from_warnings(plan.warnings) + budget.exhausted_reasons))
    entries = chosen.entries if chosen else [s.anchor for s in candidates if s.anchor][:max(0, min(30, plan.limits.max_matches))]
    omissions = chronology_omissions(states)
    if artifact and artifact['omitted_records']:
        omissions['artifact_records'] = artifact['omitted_records']
    limit = max(0, min(30, plan.limits.max_matches))
    allowance = TEXT_LIMIT - len((chosen.anchor['text'] if chosen and chosen.anchor else '').encode())
    def present(allowance):
        if chosen:
            return display_entries(entries, allowance, limit, str(chosen.path))
        evidence = []
        plausible = candidates[:limit]
        for session in plausible:
            evidence.extend(display_entries(([session.anchor] if session.anchor else []) + session.entries,
                allowance // max(1, len(plausible)), limit // max(1, len(plausible)), str(session.path)))
        return evidence
    evidence = present(allowance)
    display_count = sum(len(e.get('records', [e])) for e in evidence)
    all_stats = RawLineScanStats()
    all_stats.merge_failures(stats)
    all_stats.merge_failures(discovery_stats)
    payload = {'query': query, 'status': 'chronological_evidence' if chosen else 'ambiguous' if candidates else 'unavailable' if not source_complete else 'not_found',
        'evidence': evidence, 'scope': {'roots': [clip(str(r.path), 2048) for r in plan.roots[:20]],
            'root_count': len(plan.roots), 'roots_display_complete': len(plan.roots) <= 20,
            'cutoff': cutoff.isoformat()},
        'coverage': {'complete': complete, 'source_complete': source_complete, 'files_admitted': len(files),
            **({'discovery_complete': discovery_complete, 'metadata_records_verified': len(metadata),
                'target_resolution': target_scope or 'unresolved'} if not explicit_roots else {}),
            'native_snapshots': [{'path': clip(str(s.path), 2048), **s.native_scan} for s in states[:20]],
            'native_snapshots_display_complete': len(states) <= 20,
            'files_read': len(budget.opened_sources), 'records_inspected': sum(s.records for s in states),
            **all_stats.source_error_summary(), 'cap_reasons': caps,
            'unavailable_roots': [clip(p, 2048) for p in unavailable[:20]]},
        'chronology': {'scope': scope, 'anchor': chosen.anchor if chosen else None,
            'boundary': boundary_metadata,
            'native_snapshot': chosen.native_scan if chosen else None,
            'interval_complete': bool(chosen and chosen.complete and boundary.active(chosen) and chosen.boundary_complete
                and chosen.temporal_complete and not stats.partial_failure_count and not cutoff_truncated
                and (not boundary.supplied or not chosen.native_scan.get('prefix_omitted_bytes'))),
            'request_boundary': chosen.request_boundary if chosen else requesting[0].request_boundary if len(requesting) == 1 else None,
            'parent_relationship_verified': scope == 'proven_parent',
            'candidate_anchors': [],
            'temporal_complete': temporal, 'cutoff': cutoff.isoformat(),
            'untimed_records': sum(s.untimed for s in states), 'reversed_timestamps': sum(s.reversed for s in states),
            'post_anchor_records': chosen.after_anchor if chosen and not boundary.supplied else None, 'interval_artifact': artifact,
            'interval_raw_records': chosen.interval_raw_records if chosen else None,
            'retained': {'bytes': sum(s.retained_cost for s in states) + (artifact['framing_bytes'] if artifact else 0),
                'record_bytes': sum(s.retained_cost for s in states),
                'framing_bytes': artifact['framing_bytes'] if artifact else 0,
                'first_unrepresented_ref': ({'path': str(chosen.path), **chosen.first_unrepresented_ref}
                    if chosen and chosen.first_unrepresented_ref else None),
                'limit_bytes': RETAIN_LIMIT, 'core_membership_complete': not omissions['retention_records'],
                'text_complete': not omissions['clipped_retained_records'],
                'complete': not (omissions['retention_records'] or omissions['clipped_retained_records'])},
            'display': {'contexts': len(evidence), 'records': display_count, 'retained_records': len(entries),
                'complete': display_count == len(entries) and all(e.get('text_complete', all(r.get('text_complete') for r in e.get('records', []))) for e in evidence)},
            'unfinished_invocations': [{'source_line': e['source_ref']['line'], 'source_ref': {'path': str(chosen.path), **e['source_ref']}, 'call_id': e['call_id']}
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
            payload['coverage']['source_error_sample_count'] = 0
            payload['coverage']['source_error_omitted_count'] = payload['coverage']['source_error_count']
            payload['coverage']['source_error_clipped_sample_count'] = 0
        elif payload['scope']['roots']:
            payload['scope']['roots'] = []
            payload['scope']['roots_display_complete'] = False
        else:
            raise ValueError('fixed chronological response metadata exceeds response allowance')
    payload['chronology']['display']['contexts'] = len(payload['evidence'])
    payload['chronology']['display']['records'] = sum(len(e.get('records', [e])) for e in payload['evidence'])
    return payload

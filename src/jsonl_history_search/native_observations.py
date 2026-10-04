"""Pure native ownership/origin for chronological history evidence.

Ownership/rationale: docs/package-map.md#provider-records-and-identity.
No source I/O, tool observations, execution extraction or ranking.
"""
from __future__ import annotations

import hashlib

def identity(value):
    return hashlib.sha256(str(value).encode()).hexdigest() if value else None


def native_metadata_identity(body):
    """Protocol id is the thread; session_id is its root, not an alias.

    Native ownership: docs/package-map.md#provider-records-and-identity.
    """
    owner = body.get('id')
    if not isinstance(owner, str) or not owner:
        raise ValueError('native owning thread identity missing')
    source = body.get('source')
    subagent = source.get('subagent') if isinstance(source, dict) else None
    spawn = subagent.get('thread_spawn') if isinstance(subagent, dict) else None
    # Session/root compatibility keys do not establish a native thread edge.
    edges = [body.get('parent_thread_id')]
    if isinstance(spawn, dict):
        edges.append(spawn.get('parent_thread_id'))
    present = [e for e in edges if e is not None]
    conflict = (any(not isinstance(e, str) or not e for e in present)
                or len({e for e in present if isinstance(e, str)}) > 1)
    worker = (body.get('thread_source') == 'subagent'
              or (isinstance(source, dict) and 'subagent' in source)
              or body.get('agent_role') in {'worker', 'reviewer'})
    return owner, worker, present[0] if present and not conflict else None, conflict


def unsigned_ordinal(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2**64


class NativeOrigin:
    """First metadata owns the file, never all of its inherited text.

    Only explicit paginated rollout ordinals establish own-history authorship;
    neither physical line numbers nor logical fork boundaries substitute for it.
    Stored metadata is bounded hashed identity, not an extra transcript cache.
    """
    def __init__(self):
        self.owner = None
        self.ref = None
        self.segment = None
        self.prefix = False
        self.paginated = False
        self.start = None
        self.last = None
        self.ordinal_invalid = False
        self.invalid = False
        self.mixed = False
        self.flag = None
        self.flag_segment = None

    def metadata(self, body, ref, ordinal):
        owner, worker, parent, conflict = native_metadata_identity(body)
        decoded = identity(owner), worker, identity(parent), conflict
        if self.owner is None:
            self.owner, self.ref = decoded, ref
            self.segment = identity(ref)
            self.prefix = bool(body.get('forked_from_id') or body.get('history_base')
                               or 'subagent_history_start_ordinal' in body)
            self.paginated = body.get('history_mode') == 'paginated'
            marker = body.get('subagent_history_start_ordinal')
            self.start = marker if unsigned_ordinal(marker) else None
            base = body.get('history_base')
            base_ordinal = base.get('end_ordinal_exclusive') if isinstance(base, dict) else 0
            if self.paginated and (not unsigned_ordinal(base_ordinal)
                    or not unsigned_ordinal(ordinal) or ordinal != base_ordinal
                    or ('subagent_history_start_ordinal' in body and self.start is None)):
                self.ordinal_invalid = True
            self.advance(ordinal)
            return decoded, True
        self.advance(ordinal)
        if decoded != self.owner:
            # First metadata owns the file. Different later owners can be
            # copied context even without a legacy fork marker; they do not
            # relabel records. Same-owner parent/worker contradictions differ.
            if decoded[0] == self.owner[0]:
                self.invalid = True
                self.segment = identity(ref)
                raise ValueError('conflicting native owning metadata')
            self.mixed = True
        self.segment = identity(ref)
        self.flag, self.flag_segment = None, None
        return self.owner, False

    def advance(self, ordinal):
        if self.paginated:
            if not unsigned_ordinal(ordinal) or (self.last is not None and ordinal <= self.last):
                self.ordinal_invalid = True
            if unsigned_ordinal(ordinal):
                self.last = ordinal

    def snapshot(self, ordinal, raw=None, ref=None):
        self.advance(ordinal)
        authored = ('uncertain' if self.invalid or self.owner is None else 'own')
        if self.prefix or self.mixed:
            authored = ('uncertain' if not self.paginated or self.ordinal_invalid
                        or self.start is None or not unsigned_ordinal(ordinal)
                        else 'own' if ordinal >= self.start else 'inherited')
        if self.invalid or (self.paginated and self.ordinal_invalid):
            authored = 'uncertain'
        payload = (raw or {}).get('payload', raw or {})
        payload = payload if isinstance(payload, dict) else {}
        flag = ('replay' if (raw or {}).get('is_replay') or payload.get('is_replay') else
                'inherited' if (raw or {}).get('inherited') or payload.get('inherited') else None)
        if flag != self.flag:
            self.flag, self.flag_segment = flag, identity(ref)
        if flag:
            authored = 'inherited' if flag == 'inherited' else 'uncertain'
        proven = (authored == 'own' and self.paginated and self.start is not None
                  and not self.ordinal_invalid and unsigned_ordinal(ordinal) and ordinal >= self.start)
        return {'authorship': authored, 'segment': identity((self.segment, self.flag_segment, authored)),
                'ownership': 'ambiguous' if self.invalid else 'verified' if self.owner and authored == 'own' else 'unverified',
                'worker_origin': bool(self.owner[1]) if self.owner and authored == 'own' else None,
                'canonical_worker': bool(self.owner[1]) if self.owner else None,
                'own_history_proven': proven,
                'owning_metadata_ref': self.ref,
                'native_ordinal': ordinal if unsigned_ordinal(ordinal) else None}

    def retained(self):
        return vars(self)




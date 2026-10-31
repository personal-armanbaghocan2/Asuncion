"""Bounded local semantic candidates; scores never establish procurement compliance.

Uses the already installed OCR semantic encoder without downloads or retraining.
The installed FastText classifier and its input/decision contract stay separate.
"""
from threading import RLock
import math

_ENCODER_LOCK = RLock()


def suggest_candidates(pr_items, po_items):
    from pr_po_item_matching_v0_1_0 import _read_document, _bindings
    requested, _ = _read_document(pr_items, 'pr')
    offered, _ = _read_document(po_items, 'po')
    bound = _bindings(requested, offered)
    used_pr = {r['requested_position'] for r in bound}; used_po = {r['offered_position'] for r in bound}
    left = [r for r in requested if r['position'] not in used_pr and r['name']]
    right = [r for r in offered if r['position'] not in used_po and r['name']]
    # Duplicate source names require allocation review, never model tie-breaking.
    left = [r for r in left if sum(x['identity']==r['identity'] for x in requested)==1]
    right = [r for r in right if sum(x['identity']==r['identity'] for x in offered)==1]
    if not left or not right: return []
    if len(left)+len(right)>80: return []  # Bound synchronous encoder work.
    with _ENCODER_LOCK:
        from offline_ocr_mapper import load_local_model
        encoder = load_local_model()
        if encoder is None: return []
        vectors = encoder.encode([r['name'][:500] for r in left+right], normalize_embeddings=True)
    result = []
    for i, request in enumerate(left):
        ranked = []
        for j, offer in enumerate(right):
            score = float(vectors[i] @ vectors[len(left)+j])
            # A candidate floor is not a probability/compliance threshold.
            # Modest similarities may still help a reviewer identify aliases.
            if math.isfinite(score) and .4 <= score <= 1.00001:
                ranked.append(dict(requested_position=request['position'], offered_position=offer['position'],
                                   score=min(score,1.0), provenance='LOCAL_SEMANTIC_CANDIDATE_ONLY'))
        result.extend(sorted(ranked,key=lambda c:c['score'],reverse=True)[:3])
    return result

"""Use unchanged LP 1.1; explicit opt-in experimental history allowance."""
from dataclasses import asdict, replace
import math
from balance_handoff.vendor.balance_lp_v11 import Zone, Policy, optimize_fair
from balance_handoff.vendor.balance_lp_v11.optimizer import finite_number
from .ledger import digest


def compare_policies(zones, source_budget_ml, snapshot, *, disparity_budget_ratio=.02):
    delta = finite_number(disparity_budget_ratio, 'disparity_budget_ratio', maximum=.1)
    checksum = {k: v for k, v in snapshot.items() if k != 'snapshot_sha256'}
    if snapshot.get('context') != 'SIMULASI' or digest(checksum) != snapshot.get('snapshot_sha256'):
        raise ValueError('Invalid simulation history snapshot')
    lookup = {r['zone_id']: r for r in snapshot['rows']}
    if len(lookup) != len(snapshot['rows']) or set(lookup) != {z.zone_id for z in zones}:
        raise ValueError('history snapshot zone mismatch')
    frozen, audit = [], []
    for z in zones:
        if z.received_to_date_ml != 0:
            raise ValueError('This comparison starts a new full cycle; current credit must be zero')
        row = lookup[z.zone_id]
        state = row['history_state']
        known = state in {'KNOWN', 'NO_POSITIVE_NEED'}
        if state not in {'KNOWN', 'NO_POSITIVE_NEED', 'UNKNOWN'}:
            raise ValueError('unsupported history_state')
        # LP requires neutral numeric zeros when unknown. The external snapshot
        # and audit retain null/UNKNOWN rather than claiming an observed zero.
        historical_need = row['history_need_ml'] if known else 0.
        historical_deficit = row['history_deficit_ml'] if known else 0.
        if state == 'UNKNOWN' and any(row[k] is not None for k in ['history_need_ml','history_deficit_ml','history_score']):
            raise ValueError('UNKNOWN history must remain null externally')
        item = replace(z, history_known=known, history_need_ml=historical_need,
                       history_deficit_ml=historical_deficit)
        score = item.history_score
        if state == 'KNOWN':
            if historical_need <= 0 or row['history_score'] is None:
                raise ValueError('KNOWN history needs a positive denominator')
            declared_score = finite_number(row['history_score'], 'history_score', maximum=1)
            if abs(score-declared_score) > 1e-12:
                raise ValueError('history score disagrees with ledger')
        if state == 'NO_POSITIVE_NEED' and (historical_need != 0 or historical_deficit != 0 or row['history_score'] is not None):
            raise ValueError('invalid zero-need history')
        frozen.append(item)
        audit.append({'zone_id': z.zone_id, 'history_state': state, 'external_history_score': row['history_score'],
                      'neutral_lp_fallback_used': not known, 'lp_history_score': score})
    strict = optimize_fair(frozen, source_budget_ml, Policy())
    bounded = optimize_fair(frozen, source_budget_ml, Policy(history_mode='HISTORY_BOUNDED', history_disparity_budget=delta))
    if any(r['status'] in {'SOLVER_FAILED','INFEASIBLE'} for r in [strict,bounded]):
        raise RuntimeError('LP failed; no history comparison emitted')
    reference_range = strict['stage_objectives']['min_current_disparity_ratio']
    reference_floor = strict['stage_objectives']['current_floor_ratio']
    envelope = {'allowed_extra_disparity_ratio': delta, 'reference_min_disparity_ratio': reference_range,
                'allowed_total_disparity_ratio': reference_range+delta,
                'minimum_allowed_fulfillment_ratio': max(0., reference_floor-delta),
                'reported_bounded_disparity_ratio': bounded['disparity_ratio'],
                'reported_bounded_min_ratio': bounded['min_fulfillment_ratio'],
                'numeric_ratio_tolerance': 5e-7}
    envelope['bounds_satisfied'] = ((bounded['disparity_ratio'] is None or bounded['disparity_ratio'] <= reference_range+delta+5e-7)
        and (bounded['min_fulfillment_ratio'] is None or bounded['min_fulfillment_ratio'] >= max(0., reference_floor-delta)-5e-7))
    if not envelope['bounds_satisfied']:
        raise RuntimeError('Independent history fairness-envelope check failed')
    before = {r['zone_id']: r for r in strict['zones']}
    changes = []
    for row in bounded['zones']:
        original = before[row['zone_id']]
        changes.append({'zone_id': row['zone_id'],
                        'history_score': lookup[row['zone_id']]['history_score'],
                        'strict_release_ml': original['release_ml'], 'bounded_release_ml': row['release_ml'],
                        'release_change_ml': row['release_ml']-original['release_ml'],
                        'strict_fulfillment_ratio': original['fulfillment_ratio'],
                        'bounded_fulfillment_ratio': row['fulfillment_ratio'],
                        'fulfillment_change_pp': 100*(row['fulfillment_ratio']-original['fulfillment_ratio']) if row['fulfillment_ratio'] is not None else None})
    return {'context': 'SIMULASI', 'policy_status': 'EXPERIMENTAL_FOR_REVIEW',
            'history_snapshot_sha256': snapshot['snapshot_sha256'],
            'source_budget_ml': finite_number(source_budget_ml, 'source_budget_ml'),
            'frozen_inputs': [asdict(z) for z in frozen], 'history_bridge_audit': audit,
            'strict_current': strict, 'history_bounded': bounded,
            'fairness_envelope': envelope, 'zone_changes': changes,
            'same_inputs_for_both_policies': True, 'old_deficit_added_to_current_need': False,
            'execution_allowed': False, 'actual_measured': None}

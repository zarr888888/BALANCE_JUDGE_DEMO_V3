"""Explicit counterfactual controls; never measured or observed history."""
from copy import deepcopy
import math
from balance_handoff.vendor.balance_lp_v11 import Zone
from .ledger import HistoryLedger
from .optimizer_bridge import compare_policies


def score_sensitivity(disparity_budget_ratio):
    zones=[Zone('A','hulu',100.),Zone('B','tengah',100.),Zone('C','hilir',100.)]
    outputs={}
    for priority in ['A','C']:
        ledger=HistoryLedger(['A','B','C'])
        ledger.finalize_cycle({'cycle_id':'CONTROL-priority-'+priority,'cycle_index':1,
            'cycle_start':'2021-01-01T00:00:00+07:00','cycle_end':'2021-01-02T00:00:00+07:00',
            'context':'SIMULASI','source_ref':'CONTROLLED_EQUAL_NEED_FIXTURE_NOT_MEASUREMENT',
            'complete':True,'quality':'VALID','source_budget_ml':300.,'source_remaining_ml':0.,
            'in_transit_release_ml':0.,'rows':[{'zone_id':z,'frozen_need_ml':100.,'source_released_ml':100.,
            'simulated_received_ml':0. if z==priority else 100.} for z in 'ABC']})
        outputs[priority]=compare_policies(zones,150.,ledger.snapshot('2021-01-02T00:00:00+07:00'),
                                          disparity_budget_ratio=disparity_budget_ratio)
    lookup={priority:{r['zone_id']:r['release_ml'] for r in report['history_bounded']['zones']}
            for priority,report in outputs.items()}
    a_gain=lookup['A']['A']-lookup['C']['A']
    c_gain=lookup['C']['C']-lookup['A']['C']
    if disparity_budget_ratio > 5e-7:
        passed=a_gain>1e-5 and c_gain>1e-5
        status='PASS' if passed else 'REVIEW_REQUIRED'
    else:
        passed=abs(a_gain)<1e-3 and abs(c_gain)<1e-3
        status='NO_RESOLVABLE_HEADROOM_EXPECTED'
    if not passed:raise RuntimeError('History score sensitivity control failed')
    return {'context':'SIMULASI','kind':'CONTROLLED_EQUAL_NEED_FIXTURE',
            'same_current_need_eta_budget_policy':True,'only_history_priority_zone_changed':True,
            'priority_A':outputs['A'],'priority_C':outputs['C'],
            'A_release_gain_when_A_prioritized_ml':a_gain,'C_release_gain_when_C_prioritized_ml':c_gain,
            'status':status,'check_passed':passed,'actual_measured':None}


def uniform_history_control(history, frozen_history, zones, source_budget, actual_comparison, delta):
    scores=[r['history_score'] for r in frozen_history['rows'] if r['history_score'] is not None]
    common=math.fsum(scores)/len(scores) if scores else 0.
    control=HistoryLedger([z.zone_id for z in zones],window_cycles=frozen_history['window_cycles'])
    chosen=set(frozen_history['cycle_ids_used'])
    for index,entry in enumerate([e for e in history.export_state()['entries'] if e['record']['cycle_id'] in chosen],1):
        record=deepcopy(entry['record'])
        record.update(cycle_id=f'CONTROL-uniform-{index}',cycle_index=index,
                      source_ref='UNIFORM_HISTORY_COUNTERFACTUAL_NOT_MEASUREMENT')
        for row in record['rows']:
            row['simulated_received_ml']=row['frozen_need_ml']*(1-common)
        control.finalize_cycle(record)
    snapshot=control.snapshot(frozen_history['as_of'])
    comparison=compare_policies(zones,source_budget,snapshot,disparity_budget_ratio=delta)
    original={r['zone_id']:r['release_ml'] for r in actual_comparison['history_bounded']['zones']}
    largest=max(abs(r['release_ml']-original[r['zone_id']]) for r in comparison['history_bounded']['zones'])
    isolated=largest>1e-4
    return {'context':'SIMULASI','kind':'UNIFORM_SCORE_COUNTERFACTUAL',
            'uniform_score_assumed':common,'history_snapshot':snapshot,'policy_comparison':comparison,
            'max_release_difference_vs_nonuniform_history_ml':largest,
            'difference_tolerance_ml':1e-4,'zone_specific_effect_isolated_in_main_replay':isolated,
            'interpretation_code':('ALLOCATION_CHANGES_WITH_ZONE_HISTORY_CONTRAST' if isolated else
                                    'MAIN_REPLAY_ALSO_REFLECTS_BOUNDED_OBJECTIVE_HEADROOM'),
            'actual_measured':None}

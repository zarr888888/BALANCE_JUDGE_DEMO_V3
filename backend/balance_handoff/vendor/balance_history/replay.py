"""Three synthetic cycles, same repeated scenario, no field or ML retraining."""
from datetime import timedelta
from balance_handoff.vendor.balance_lp_v11 import Zone, Policy
from balance_handoff.vendor.balance_scheduler import SimulationCycle, Timing
from .ledger import HistoryLedger, cycle_record, time_value
from .optimizer_bridge import compare_policies
from .controls import score_sensitivity, uniform_history_control


def complete_replay(zones, budget, *, policy, cycle_id, tail_received_factor=1.):
    timings = {z.zone_id: Timing(6., {'A':2., 'B':4., 'C':6.}[z.zone_id], 1., 600.) for z in zones}
    cycle = SimulationCycle(zones, budget, timings, cycle_id=cycle_id, policy=policy)
    plan = cycle.create_initial_plan()
    for slot in plan['schedule']['slots']:
        key = cycle_id+'-'+slot['zone_id']
        packet = cycle.simulate_slot_release(1, slot['zone_id'], 'rel-'+key)
        factor = tail_received_factor if slot['zone_id'] == 'C' else 1.
        cycle.settle_packet(packet['packet_id'], 'rec-'+key,
            simulated_received_ml=slot['expected_received_ml']*factor, time_s=slot['verification_end_s'])
    cycle.complete_current()
    return cycle.snapshot()


def metrics(ledger, positions):
    rows = ledger['zones']
    ratios = [r['simulated_fulfillment_ratio'] for r in rows if r['simulated_fulfillment_ratio'] is not None]
    mean = sum(ratios)/len(ratios) if ratios else None
    return {'disparity_ratio': max(ratios)-min(ratios) if ratios else None,
            'min_fulfillment_ratio': min(ratios) if ratios else None,
            'source_released_ml': ledger['source_released_ml'],
            'received_simulated_ml': ledger['settled_simulated_received_new_ml'],
            'source_balance_ok': ledger['source_balance_ok'],
            'zero_fulfillment_zone_count': sum(r['frozen_full_cycle_need_ml'] > 0 and r['simulated_received_total_ml'] <= 1e-6 for r in rows),
            'tail_gap_ratio': max([max(0., .9*mean-r['simulated_fulfillment_ratio']) for r in rows
                    if positions[r['zone_id']]=='hilir' and r['simulated_fulfillment_ratio'] is not None] or [0.]) if mean is not None else None}


def replay_history(engine, *, history_window_cycles=3, disparity_budget_ratio=.02):
    upstream = engine.calculate({'prediction_mode':'ml', 'loss_mode':'learned', 'supply_fraction':1.,
                                'tail_capacity_ml':5000., 'loss_flow_ml_s':6., 'loss_head_cm':20.})
    zones = [Zone(r['zone_id'], r['position'], r['need_ml'], r['delivery_efficiency'], r['max_release_ml'])
             for r in upstream['allocation_inputs']]
    budget = upstream['source_budget']['available_demo_ml']
    positions = {z.zone_id: z.position for z in zones}
    history = HistoryLedger([z.zone_id for z in zones], window_cycles=history_window_cycles)
    start = time_value(upstream['scenario']['as_of'])
    unknown = history.snapshot(start.isoformat())
    seeds = []
    for index, factor in enumerate([.8,.7], 1):
        ledger = complete_replay(zones, budget, policy=Policy(),
                                cycle_id=f'history-seed-{index:03d}', tail_received_factor=factor)
        record = cycle_record(ledger, cycle_index=index,
                              cycle_start=(start+timedelta(days=index-1)).isoformat(),
                              cycle_end=(start+timedelta(days=index)).isoformat())
        history.finalize_cycle(record)
        seeds.append({'cycle_index':index, 'tail_received_factor_assumed':factor,
                      'policy_mode':'STRICT_CURRENT', 'metrics':metrics(ledger,positions), 'ledger':ledger})
    cutoff = (start+timedelta(days=2)).isoformat()
    frozen_history = history.snapshot(cutoff)
    comparison = compare_policies(zones, budget, frozen_history, disparity_budget_ratio=disparity_budget_ratio)
    uniform = uniform_history_control(history,frozen_history,zones,budget,comparison,disparity_budget_ratio)
    sensitivity = score_sensitivity(disparity_budget_ratio)
    frozen_zones = [Zone(**z) for z in comparison['frozen_inputs']]
    strict = complete_replay(frozen_zones, budget, policy=Policy(), cycle_id='history-current-003-strict-counterfactual')
    bounded = complete_replay(frozen_zones, budget,
                policy=Policy(history_mode='HISTORY_BOUNDED', history_disparity_budget=disparity_budget_ratio),
                cycle_id='history-current-003-bounded')
    # Alternative rollouts share current inputs. Only the bounded branch is
    # appended, once; counterfactual water/receipts never enter the same history.
    chosen_record = cycle_record(bounded, cycle_index=3, cycle_start=cutoff,
                                 cycle_end=(start+timedelta(days=3)).isoformat())
    history.finalize_cycle(chosen_record)
    state = history.export_state()
    HistoryLedger.from_state(state)  # Export/reload semantic and checksum check.
    after = history.snapshot(chosen_record['cycle_end'])
    if history.snapshot(cutoff) != frozen_history:
        raise RuntimeError('Future cycle leaked into the frozen decision history')
    return {'schema_version':'balance-history-lab-report/0.1', 'module_version':'1.0-simulation',
        'status':'HISTORY_SIMULATION_COMPLETE', 'context':'SIMULASI',
        'ml_status':'TRAINED_SYNTHETIC_ONLY', 'history_component_kind':'CALCULATION_NOT_ML',
        'real_measurement_rows':0, 'execution_allowed':False, 'device_connected':False,
        'actual_measured':None, 'field_validated':False, 'proposal_targets_proven':False,
        'upstream':{'model_run':upstream['model_run'], 'model_hashes':upstream['model_hashes'],
                    'loss_model':upstream['loss_model'], 'loss_snapshot_ref':upstream['loss_profile']['snapshot_ref'],
                    'repeated_scenario':upstream['scenario'], 'conversion':upstream['conversion'],
                    'warnings':upstream['warnings']},
        'experiment':{'history_window_cycles':history_window_cycles,
                      'disparity_budget_ratio':disparity_budget_ratio,
                      'policy_status':'EXPERIMENTAL_FOR_REVIEW',
                      'default_demo13_policy_changed':False,
                      'same_synthetic_features_and_need_repeated_across_cycles':True,
                      'cycle_dates_relabelled_for_synthetic_order_only':True,
                      'source_budget_replenished_by_assumption_each_cycle':True,
                      'third_cycle_later_receipts_match_frozen_eta_by_construction':True},
        'initial_unknown_history':unknown, 'seed_cycles':seeds,
        'history_before_cycle3':frozen_history, 'policy_comparison':comparison,
        'uniform_history_control':uniform, 'score_sensitivity_control':sensitivity,
        'current_cycle_rollouts':{'strict_counterfactual':strict, 'bounded_chosen':bounded,
             'strict_metrics':metrics(strict,positions), 'bounded_metrics':metrics(bounded,positions)},
        'history_after_cycle3':after, 'history_state':state,
        'checks':{'fairness_bounds_satisfied':comparison['fairness_envelope']['bounds_satisfied'],
                  'source_balances_satisfied':all(s['ledger']['source_balance_ok'] for s in seeds)
                     and strict['source_balance_ok'] and bounded['source_balance_ok'],
                  'only_chosen_branch_finalized':len(state['entries'])==3,
                  'future_cycle_excluded_from_decision_snapshot':True,
                  'export_reload_verified':True},
        'limitations':['SIMULATION_ONLY', 'DEFICIT_RELATIVE_TO_PREDICTED_FROZEN_NEED_NOT_MEASURED_AGRONOMIC_NEED',
            'TWO_CONTROLLED_SEED_CYCLES_ONE_DECISION_CYCLE_NOT_LONG_TERM_FIELD_EVIDENCE',
            'NO_REAL_MEASUREMENTS', 'NO_NEW_ML_TRAINING', 'NO_DEVICE_APPROVAL_OR_ACTUATION',
            'IN_MEMORY_LEDGER_WITH_EXPERIMENT_JSON_EXPORT_NOT_TRANSACTIONAL_DATABASE',
            'REQUIRES_COMPLETE_VALID_EQUAL_HORIZON_CYCLES', 'NO_DEFAULT_POLICY_CHANGE',
            'HISTORY_PRIORITY_CAN_BE_LIMITED_BY_CAPACITY_OR_FAIRNESS', 'CURRENT_NEED_NOT_INCREASED_TO_REPAY_OLD_DEFICIT']}

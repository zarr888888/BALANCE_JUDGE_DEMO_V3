"""Controlled whole-cycle experiment, not an automatic irrigation service."""
from dataclasses import asdict
from time import perf_counter

from balance_handoff.vendor.balance_lp_v11 import Zone
from balance_handoff.vendor.balance_lp_v11.optimizer import finite_number
from .core import SimulationCycle, Timing


def replay_from_demo(engine, *, received_factor=0.8, supply_fraction=1.0,
                     horizon_s=600.0):
    factor = finite_number(received_factor, 'received_factor', maximum=1)
    fraction = finite_number(supply_fraction, 'supply_fraction', maximum=1)
    horizon = finite_number(horizon_s, 'horizon_s')
    if horizon <= 0:
        raise ValueError('horizon_s must be positive')
    started = perf_counter()
    upstream = engine.calculate({'prediction_mode': 'ml', 'loss_mode': 'learned',
        'supply_fraction': fraction, 'tail_capacity_ml': 5000,
        'loss_flow_ml_s': 6, 'loss_head_cm': 20})
    zones = [Zone(r['zone_id'], r['position'], r['need_ml'],
                  r['delivery_efficiency'], r['max_release_ml']) for r in upstream['allocation_inputs']]
    # Fixed assumptions, NOT learned delay or calibrated device discharge.
    timings = {z.zone_id: Timing(6., {'A': 2., 'B': 4., 'C': 6.}[z.zone_id], 1., horizon) for z in zones}
    cycle = SimulationCycle(zones, upstream['source_budget']['available_demo_ml'], timings,
                            cycle_id=upstream['scenario']['cycle_id'], horizon_end_s=horizon)
    initial = cycle.create_initial_plan()
    observations, child = [], None
    if initial['schedule']['slots']:
        first = initial['schedule']['slots'][0]
        packet = cycle.simulate_slot_release(1, first['zone_id'], 'sim-release-v1-first')
        due = first['verification_end_s']
        cycle.settle_packet(packet['packet_id'], 'sim-receipt-v1-first',
                            simulated_received_ml=first['expected_received_ml']*factor, time_s=due)
        observations.append(cycle.observe_settled_packet(packet['packet_id'], 'sim-sample-1', due))
        # Second synthetic observation of the same fully collected packet.
        # Not an independent experiment; only exercises the debounce mechanism.
        observations.append(cycle.observe_settled_packet(packet['packet_id'], 'sim-sample-2', due+0.5))
        if observations[-1]['trigger_id']:
            ready_start = perf_counter()
            child = cycle.reoptimize(1, observations[-1]['trigger_id'])
            candidate_compute_s = perf_counter()-ready_start
            active = child
            slots = child['schedule']['slots']
        else:
            candidate_compute_s = None
            active = initial
            slots = initial['schedule']['slots'][1:]
        for slot in slots:
            key = f"v{active['version']}-{slot['zone_id']}"
            released = cycle.simulate_slot_release(active['version'], slot['zone_id'], 'sim-release-'+key)
            cycle.settle_packet(released['packet_id'], 'sim-receipt-'+key,
                                simulated_received_ml=slot['expected_received_ml'],
                                time_s=slot['verification_end_s'])
    else:
        candidate_compute_s = None
    cycle.complete_current()
    # Counterfactual: same initial allocation, same first-packet shortfall and
    # subsequent frozen eta; keep the original slots instead of reoptimizing.
    static = SimulationCycle(zones, upstream['source_budget']['available_demo_ml'], timings,
                              cycle_id=upstream['scenario']['cycle_id']+'-static', horizon_end_s=horizon)
    static_plan = static.create_initial_plan()
    for i, slot in enumerate(static_plan['schedule']['slots']):
        key = f'static-{slot["zone_id"]}'
        packet = static.simulate_slot_release(1, slot['zone_id'], 'rel-'+key)
        static.settle_packet(packet['packet_id'], 'rec-'+key,
            simulated_received_ml=slot['expected_received_ml']*(factor if i == 0 else 1.),
            time_s=slot['verification_end_s'])
    static.complete_current()
    static_ledger = static.snapshot()
    adaptive_ledger = cycle.snapshot()

    def metrics(ledger):
        ratios = [r['simulated_fulfillment_ratio'] for r in ledger['zones'] if r['simulated_fulfillment_ratio'] is not None]
        return {'source_released_ml': ledger['source_released_ml'],
                'simulated_received_new_ml': ledger['settled_simulated_received_new_ml'],
                'disparity_ratio': max(ratios)-min(ratios) if ratios else None,
                'min_fulfillment_ratio': min(ratios) if ratios else None,
                'simulated_end_s': ledger['simulated_clock_s'],
                'zones': ledger['zones']}
    return {'schema_version': 'balance-scheduler-replay/0.1', 'module_version': '1.0-simulation',
        'status': 'SIMULATION_REPLAY_COMPLETE', 'context': 'SIMULASI',
        'ml_status': 'TRAINED_SYNTHETIC_ONLY', 'real_measurement_rows': 0,
        'field_validated': False, 'proposal_targets_proven': False,
        'execution_allowed': False, 'device_connected': False, 'actual_measured': None,
        'upstream': {'demo_version': upstream['demo_version'], 'model_run': upstream['model_run'],
                     'model_hashes': upstream['model_hashes'], 'loss_model': upstream['loss_model'],
                     'loss_eta_snapshot_ref': upstream['loss_profile']['snapshot_ref'],
                     'prediction_options': upstream['options'], 'scenario': upstream['scenario'],
                     'conversion': upstream['conversion'], 'warnings': upstream['warnings']},
        'experiment': {'first_packet_received_factor': factor, 'supply_fraction': fraction,
                       'horizon_simulated_s': horizon,
                       'timings_assumed': {k: asdict(v) for k, v in timings.items()},
                       'monitor_threshold_ratio': 0.1, 'monitor_consecutive_samples': 2,
                       'second_observation_of_same_packet': True,
                       'later_packets_match_frozen_eta_by_construction': True},
        'observations': observations, 'child_plan_created': child is not None,
        'latency': {'candidate_compute_wall_s': candidate_compute_s,
                    'total_replay_compute_wall_s': perf_counter()-started,
                    'hardware_latency_measured': False, 'target_30s_proven': False,
                    'filter_delay_is_simulated': True},
        'ledger': adaptive_ledger,
        'static_comparison': {'context': 'SIMULASI',
            'baseline': 'FROZEN_INITIAL_SLOTS_NO_REOPTIMIZATION',
            'same_initial_inputs_budget_eta_and_first_packet_shortfall': True,
            'static': metrics(static_ledger), 'adaptive': metrics(adaptive_ledger),
            'interpretation': 'Controlled simulator comparison only; not field improvement evidence.'},
        'limitations': ['SIMULATED_DATA_ONLY', 'FIXED_FLOW_DELAY_SETUP_ARE_UNCALIBRATED_ASSUMPTIONS',
            'ONE_SOURCE_INDEPENDENT_BRANCHES_SERIAL_SLOTS', 'NO_DEVICE_APPROVAL_OR_ACTUATION',
            'IN_MEMORY_LEDGER_NO_DURABLE_DATABASE', 'WHOLE_PACKET_CHECKPOINTS_ONLY',
            'HORIZON_FAILURE_REQUIRES_REVIEW_NO_AUTOMATIC_CAPACITY_REPAIR',
            'FROZEN_ETA_NEED_POLICY_WITHIN_CYCLE', 'HISTORY_REPLAY_ACROSS_CYCLES_NOT_IMPLEMENTED',
            'MONITOR_DEBOUNCE_NOT_SENSOR_NOISE_VALIDATION', 'NO_FIELD_OR_30S_TARGET_PROOF']}

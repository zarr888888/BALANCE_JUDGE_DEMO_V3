"""No device commands, approval implementation, background worker, or real data.

One source, independent branches, one simulated slot at a time, complete-packet
reconciliation before the next slot. Time is an explicit simulator clock.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math

from balance_handoff.vendor.balance_lp_v11 import Policy, Zone, optimize_fair
from balance_handoff.vendor.balance_lp_v11.optimizer import finite_number

TOL_ML = 1e-6


def identifier(value, name='id'):
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise ValueError(f'{name} must be a nonempty string of at most 160 characters')
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class Timing:
    release_flow_ml_s: float
    travel_delay_s: float = 0.0
    setup_s: float = 1.0
    deadline_s: float = 600.0

    def __post_init__(self):
        if finite_number(self.release_flow_ml_s, 'release_flow_ml_s') <= 0:
            raise ValueError('release flow must be positive')
        for name in ('travel_delay_s', 'setup_s', 'deadline_s'):
            finite_number(getattr(self, name), name)


def build_schedule(allocation, zones, timings, *, start_s=0.0, horizon_end_s=600.0):
    """Earliest deadline, history score, tail first, then stable zone id.

Wait for each packet to clear before another slot. No volume rounding or
truncation; an infeasible horizon returns NO slots for review.
"""
    start = finite_number(start_s, 'start_s')
    horizon = finite_number(horizon_end_s, 'horizon_end_s')
    if start > horizon:
        raise ValueError('start exceeds horizon')
    zs = {z.zone_id: z for z in zones}
    if len(zs) != len(zones) or set(timings) != set(zs):
        raise ValueError('unique zones and matching timing keys required')
    if not all(isinstance(t, Timing) for t in timings.values()):
        raise ValueError('timings must contain Timing objects')
    rows = allocation['zones']
    if len(rows) != len(zs) or {r['zone_id'] for r in rows} != set(zs):
        raise ValueError('allocation zone mismatch')
    lookup = {r['zone_id']: r for r in rows}
    order = sorted(zs, key=lambda key: (timings[key].deadline_s, -zs[key].history_score,
                                       zs[key].position != 'hilir', key))
    slots, cursor = [], start
    for key in order:
        z, timing, row = zs[key], timings[key], lookup[key]
        release = finite_number(row['release_ml'], 'release_ml')
        expected = finite_number(row['expected_delivered_ml'], 'expected_delivered_ml')
        if release > z.release_bound + TOL_ML or abs(expected-release*z.delivery_efficiency) > TOL_ML:
            raise ValueError('allocation violates release bound or frozen eta')
        if release == 0:
            continue
        begin = cursor + timing.setup_s
        end = begin + release/timing.release_flow_ml_s
        verified = end + timing.travel_delay_s
        if not math.isfinite(verified):
            raise ValueError('unsupported derived schedule scale')
        if verified > min(horizon, timing.deadline_s) + 1e-9:
            return {'status': 'REVIEW_UNSCHEDULABLE', 'slots': [],
                    'failed_zone_id': key, 'required_verification_end_s': verified,
                    'horizon_end_s': horizon, 'reason': 'NO_VOLUME_SILENTLY_TRUNCATED'}
        slots.append({'order': len(slots)+1, 'zone_id': key,
                      'start_s': begin, 'release_end_s': end, 'verification_end_s': verified,
                      'release_ml': release, 'expected_received_ml': expected,
                      'release_flow_assumed_ml_s': timing.release_flow_ml_s,
                      'travel_delay_assumed_s': timing.travel_delay_s})
        cursor = verified
    return {'status': 'READY_FOR_SIMULATION' if slots else 'EMPTY', 'slots': slots,
            'start_s': start, 'end_s': cursor, 'horizon_end_s': horizon,
            'order_policy': 'EARLIEST_DEADLINE_HISTORY_TAIL_ZONE_ID',
            'one_slot_at_a_time': True, 'wait_for_complete_collection': True,
            'timing_kind': 'ASSUMPTION', 'execution_allowed': False}


class DeviationMonitor:
    """Two distinct, time-ordered valid samples; strict >10%, with cooldown.

    A complete, isolated packet is the comparison window in this lab. This does
    not claim a continuous-flow travel-delay model or calibrated sensor noise.
    """
    def __init__(self, *, threshold=0.10, consecutive=2, cooldown_s=5.0,
                 zero_flow_tolerance_ml=0.0):
        self.threshold = finite_number(threshold, 'threshold', maximum=1)
        if isinstance(consecutive, bool) or not isinstance(consecutive, int) or consecutive < 1:
            raise ValueError('consecutive must be a positive integer')
        self.consecutive = consecutive
        self.cooldown_s = finite_number(cooldown_s, 'cooldown_s')
        self.zero_flow_tolerance_ml = finite_number(zero_flow_tolerance_ml, 'zero_flow_tolerance_ml')
        self._samples, self._states, self._triggers = {}, {}, {}

    @property
    def triggers(self):
        return deepcopy(self._triggers)

    def observe(self, sample_id, *, plan_version, zone_id, expected_received_ml,
                simulated_received_ml, time_s, quality='VALID', collection_complete=True):
        identifier(sample_id, 'sample_id'); identifier(zone_id, 'zone_id')
        if isinstance(plan_version, bool) or not isinstance(plan_version, int) or plan_version < 1:
            raise ValueError('positive plan_version required')
        timestamp = finite_number(time_s, 'time_s')
        expected = finite_number(expected_received_ml, 'expected_received_ml')
        if quality not in {'VALID', 'INVALID', 'STALE'} or not isinstance(collection_complete, bool):
            raise ValueError('invalid quality/collection_complete')
        payload = dict(plan_version=plan_version, zone_id=zone_id,
                       expected_received_ml=expected, simulated_received_ml=simulated_received_ml,
                       time_s=timestamp, quality=quality, collection_complete=collection_complete)
        digest = fingerprint(payload)
        if sample_id in self._samples:
            previous = self._samples[sample_id]
            if digest != previous[0]:
                raise ValueError('sample_id reused with different payload')
            return deepcopy(previous[1])
        key = (plan_version, zone_id)
        state = deepcopy(self._states.get(key, {'last_s': -1., 'count': 0, 'first_s': None,
                                                'cooldown_until_s': -1., 'sign': None}))
        if timestamp <= state['last_s']:
            raise ValueError('new samples must advance time within a plan and zone')
        state['last_s'] = timestamp
        result = {'sample_id': sample_id, 'plan_version': plan_version, 'zone_id': zone_id,
                  'time_s': timestamp, 'context': 'SIMULASI', 'execution_allowed': False,
                  'status': 'WITHIN_THRESHOLD', 'deviation_ratio': None, 'trigger_id': None}
        if quality != 'VALID' or not collection_complete or simulated_received_ml is None:
            result['status'] = 'DATA_HOLD'
            state.update(count=0, first_s=None, sign=None)
        else:
            actual = finite_number(simulated_received_ml, 'simulated_received_ml')
            if expected == 0:
                result['status'] = 'UNEXPECTED_FLOW_REVIEW' if actual > self.zero_flow_tolerance_ml else 'WITHIN_THRESHOLD'
                state.update(count=0, first_s=None, sign=None)
            else:
                deviation = (actual-expected)/expected
                result['deviation_ratio'] = deviation
                # Prevent binary roundoff at exactly +/-10% from becoming >10%.
                outside = abs(deviation) > self.threshold + 1e-12
                if not outside:
                    state.update(count=0, first_s=None, sign=None)
                elif timestamp < state['cooldown_until_s']:
                    result['status'] = 'COOLDOWN'
                    state.update(count=0, first_s=None, sign=None)
                else:
                    sign = 1 if deviation > 0 else -1
                    if state['count'] == 0 or sign != state['sign']:
                        state.update(count=0, first_s=timestamp, sign=sign)
                    state['count'] += 1
                    result['status'] = 'PENDING_CONFIRMATION'
                    if state['count'] >= self.consecutive:
                        trigger_id = f'trigger-{sample_id}'
                        result.update(status='REOPTIMIZE_CANDIDATE', trigger_id=trigger_id,
                                      first_crossing_s=state['first_s'],
                                      filter_delay_simulated_s=timestamp-state['first_s'])
                        self._triggers[trigger_id] = deepcopy(result)
                        state.update(count=0, first_s=None, sign=None,
                                     cooldown_until_s=timestamp+self.cooldown_s)
        self._states[key] = state
        self._samples[sample_id] = (digest, deepcopy(result))
        return result


class SimulationCycle:
    """In-memory replay ledger. Every added volume is explicitly SIMULATED.

    Immutable needs/eta/policy per cycle; cumulative receipt credit is kept in
    the original full-cycle denominator. Initial capacity is a cycle total.
    Pending transit blocks reoptimization; never refund delivery losses.
    """
    def __init__(self, zones, source_budget_ml, timings, *, cycle_id='synthetic-scheduler-cycle',
                 horizon_end_s=600.0, policy=None):
        if not zones or not all(isinstance(z, Zone) for z in zones):
            raise ValueError('Zone objects required')
        self._zones = tuple(zones)
        self._by_id = {z.zone_id: z for z in zones}
        if len(self._by_id) != len(zones) or set(timings) != set(self._by_id):
            raise ValueError('zone/timing mismatch')
        if not all(isinstance(t, Timing) for t in timings.values()):
            raise ValueError('Timing objects required')
        self._timings = dict(timings)
        self._budget = finite_number(source_budget_ml, 'source_budget_ml')
        self._horizon = finite_number(horizon_end_s, 'horizon_end_s')
        self._policy = policy if policy is not None else Policy()
        if not isinstance(self._policy, Policy):
            raise ValueError('Policy required')
        self._cycle_id = identifier(cycle_id, 'cycle_id')
        self._spent = {z.zone_id: 0.0 for z in zones}
        self._received = {z.zone_id: z.received_to_date_ml for z in zones}
        self._packets, self._events, self._idempotency, self._plans = {}, [], {}, {}
        self._slot_done, self._children = set(), {}
        self._clock, self._version = 0.0, 0
        self.monitor = DeviationMonitor()

    def _event_retry(self, event_id, payload):
        identifier(event_id, 'event_id')
        digest = fingerprint(payload)
        if event_id in self._idempotency:
            old_digest, result = self._idempotency[event_id]
            if old_digest != digest:
                raise ValueError('event_id reused with different payload')
            return digest, deepcopy(result)
        return digest, None

    def _remember(self, event_id, digest, result):
        self._idempotency[event_id] = (digest, deepcopy(result))
        self._events.append(deepcopy(result))
        return deepcopy(result)

    def _require_current(self, version):
        if isinstance(version, bool) or not isinstance(version, int) or version != self._version:
            raise ValueError('stale or invalid plan version')
        plan = self._plans.get(version)
        if not plan or plan['replay_state'] != 'CURRENT':
            raise ValueError('plan is not current')
        return plan

    def _remaining_zones(self):
        return [replace(z, received_to_date_ml=min(z.need_ml, self._received[z.zone_id]),
                        max_release_ml=(max(0., z.max_release_ml-self._spent[z.zone_id])
                                        if z.max_release_ml is not None else None))
                for z in self._zones]

    def _candidate(self, version, parent=None, trigger=None):
        zones = self._remaining_zones()
        remaining = max(0., self._budget-math.fsum(self._spent.values()))
        result = optimize_fair(zones, remaining, self._policy)
        if result['status'] in {'SOLVER_FAILED', 'INFEASIBLE'}:
            raise RuntimeError('LP failed; no replacement plan created')
        if result['total_release_ml'] > remaining + TOL_ML:
            raise RuntimeError('LP release exceeds remaining source')
        schedule = build_schedule(result, zones, self._timings,
                                  start_s=self._clock, horizon_end_s=self._horizon)
        if schedule['status'] == 'REVIEW_UNSCHEDULABLE':
            raise RuntimeError('REVIEW_UNSCHEDULABLE: horizon/deadline exceeded; no replacement emitted')
        payload = {'cycle_id': self._cycle_id, 'version': version, 'parent_version': parent,
                   'parent_plan_id': self._plans[parent]['plan_id'] if parent is not None else None,
                   'trigger_ref': trigger, 'context': 'SIMULASI', 'execution_allowed': False,
                   'proposal_status': 'SIMULATION_FOR_REVIEW', 'replay_state': 'CURRENT',
                   'cutoff_simulated_s': self._clock, 'source_budget_remaining_ml': remaining,
                   'frozen_cycle_inputs': [asdict(z) for z in zones],
                   'policy': asdict(self._policy), 'allocation': result, 'schedule': schedule}
        # Exclude nondeterministic CPU timing/solver trace from identity.
        payload['plan_id'] = 'sim-plan-'+fingerprint({
            'cycle': self._cycle_id, 'version': version, 'parent': parent,
            'zones': payload['frozen_cycle_inputs'], 'budget': remaining,
            'policy': payload['policy'], 'schedule': schedule})[:24]
        return payload

    def create_initial_plan(self):
        if self._version:
            raise ValueError('initial plan already exists')
        plan = self._candidate(1)
        self._plans[1], self._version = plan, 1
        return deepcopy(plan)

    def simulate_slot_release(self, version, zone_id, event_id):
        """Replay a whole planned slot. This does not send a gate command."""
        digest, retry = self._event_retry(event_id, {'op': 'SIMULATE_RELEASE', 'version': version, 'zone': zone_id})
        if retry is not None:
            return retry
        plan = self._require_current(version)
        pending_slots = [s for s in plan['schedule']['slots'] if (version, s['zone_id']) not in self._slot_done]
        if not pending_slots or pending_slots[0]['zone_id'] != zone_id:
            raise ValueError('slot must follow planned order and cannot be replayed twice')
        if any(not p['settled'] for p in self._packets.values()):
            raise ValueError('DATA_HOLD: reconcile in-transit packet first')
        slot = pending_slots[0]
        if self._clock > slot['start_s']+1e-9:
            raise ValueError('slot expired in simulator; request a new plan')
        release = slot['release_ml']
        if math.fsum(self._spent.values()) + release > self._budget + TOL_ML:
            raise ValueError('source budget exceeded')
        packet = {'packet_id': event_id, 'plan_version': version, 'zone_id': zone_id,
                  'release_ml': release, 'expected_received_ml': slot['expected_received_ml'],
                  'release_end_s': slot['release_end_s'], 'settlement_due_s': slot['verification_end_s'],
                  'settled': False, 'simulated_received_ml': None, 'actual_measured': None}
        self._packets[event_id] = packet
        self._spent[zone_id] += release
        self._slot_done.add((version, zone_id))
        self._clock = slot['release_end_s']
        return self._remember(event_id, digest, {'event_id': event_id, 'kind': 'SIMULATED_RELEASE',
                              'context': 'SIMULASI', 'time_s': self._clock, **deepcopy(packet)})

    def settle_packet(self, packet_id, event_id, *, simulated_received_ml, time_s,
                      quality='VALID', collection_complete=True):
        payload = {'op': 'SETTLE_SIMULATED_PACKET', 'packet_id': packet_id,
                   'simulated_received_ml': simulated_received_ml, 'time_s': time_s,
                   'quality': quality, 'collection_complete': collection_complete}
        digest, retry = self._event_retry(event_id, payload)
        if retry is not None:
            return retry
        if quality != 'VALID' or collection_complete is not True or simulated_received_ml is None:
            raise ValueError('DATA_HOLD: missing/invalid/stale/incomplete collection')
        value = finite_number(simulated_received_ml, 'simulated_received_ml')
        timestamp = finite_number(time_s, 'time_s')
        if packet_id not in self._packets:
            raise ValueError('unknown packet_id')
        packet = self._packets[packet_id]
        if packet['settled']:
            raise ValueError('packet already reconciled; use the original event_id for retry')
        if timestamp < max(self._clock, packet['settlement_due_s']):
            raise ValueError('collection precedes complete travel/reconciliation window')
        if value > packet['release_ml']:
            raise ValueError('isolated received volume cannot exceed released volume')
        packet.update(settled=True, simulated_received_ml=value, settled_at_s=timestamp)
        self._received[packet['zone_id']] += value
        self._clock = timestamp
        return self._remember(event_id, digest, {'event_id': event_id, 'kind': 'SIMULATED_RECEIPT',
                              'packet_id': packet_id, 'zone_id': packet['zone_id'],
                              'context': 'SIMULASI', 'simulated_received_ml': value,
                              'time_s': timestamp, 'actual_measured': None})

    def observe_settled_packet(self, packet_id, sample_id, time_s):
        packet = self._packets[packet_id]
        self._require_current(packet['plan_version'])
        if not packet['settled'] or time_s < packet['settled_at_s']:
            raise ValueError('DATA_HOLD: packet has not finished collection')
        if time_s < self._clock:
            raise ValueError('observation precedes latest event')
        result = self.monitor.observe(sample_id, plan_version=packet['plan_version'],
                                      zone_id=packet['zone_id'],
                                      expected_received_ml=packet['expected_received_ml'],
                                      simulated_received_ml=packet['simulated_received_ml'], time_s=time_s)
        self._clock = float(time_s)
        return result

    def reoptimize(self, parent_version, trigger_id):
        if trigger_id in self._children:
            old_parent, child = self._children[trigger_id]
            if old_parent != parent_version:
                raise ValueError('trigger reused with different parent')
            return deepcopy(child)
        parent = self._require_current(parent_version)
        if any(not p['settled'] for p in self._packets.values()):
            raise ValueError('DATA_HOLD: in-transit source must be reconciled first')
        trigger = self.monitor.triggers.get(trigger_id)
        if not trigger or trigger['plan_version'] != parent_version:
            raise ValueError('confirmed trigger for current plan required')
        child = self._candidate(parent_version+1, parent_version, trigger_id)
        # Switch only after LP and schedule both succeed. No approvals simulated.
        parent['replay_state'] = 'SUPERSEDED_IN_REPLAY'
        self._version += 1
        self._plans[self._version] = child
        self._children[trigger_id] = (parent_version, deepcopy(child))
        return deepcopy(child)

    def complete_current(self):
        plan = self._require_current(self._version)
        if any(not p['settled'] for p in self._packets.values()):
            raise ValueError('pending packet cannot be marked complete')
        if any((self._version, s['zone_id']) not in self._slot_done for s in plan['schedule']['slots']):
            raise ValueError('unplayed slots remain')
        plan['replay_state'] = 'COMPLETED_IN_REPLAY'

    def snapshot(self):
        settled = [p for p in self._packets.values() if p['settled']]
        spent = math.fsum(self._spent.values())
        pending = math.fsum(p['release_ml'] for p in self._packets.values() if not p['settled'])
        received_new = math.fsum(p['simulated_received_ml'] for p in settled)
        loss = math.fsum(p['release_ml']-p['simulated_received_ml'] for p in settled)
        remaining = max(0., self._budget-spent)
        residual = self._budget-(remaining+pending+received_new+loss)
        return deepcopy({'cycle_id': self._cycle_id, 'context': 'SIMULASI',
             'ledger_scope': 'IN_MEMORY_SYNTHETIC_REPLAY_ONLY', 'simulated_clock_s': self._clock,
             'source_start_ml': self._budget, 'source_released_ml': spent,
             'source_remaining_ml': remaining, 'in_transit_release_ml': pending,
             'settled_simulated_received_new_ml': received_new, 'settled_simulated_loss_ml': loss,
             'conservation_residual_ml': residual, 'numeric_tolerance_ml': TOL_ML,
             'source_balance_ok': abs(residual) <= TOL_ML,
             'zones': [{'zone_id': z.zone_id, 'frozen_full_cycle_need_ml': z.need_ml,
                        'initial_simulated_received_credit_ml': z.received_to_date_ml,
                        'simulated_source_released_ml': self._spent[z.zone_id],
                        'simulated_received_total_ml': self._received[z.zone_id],
                        'credited_received_ml': min(z.need_ml, self._received[z.zone_id]),
                        'simulated_fulfillment_ratio': min(z.need_ml, self._received[z.zone_id])/z.need_ml if z.need_ml else None,
                        'actual_measured': None} for z in self._zones],
             'plans': list(self._plans.values()), 'packets': list(self._packets.values()),
             'events': self._events, 'triggers': list(self.monitor.triggers.values()),
             'execution_allowed': False, 'actual_measured': None})

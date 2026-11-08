"""Append-only in-memory history, exportable as a checked JSON experiment.

No ingestion of real measurements, device command, production persistence or
field-history claim. Missing data never becomes a zero receipt.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from balance_handoff.vendor.balance_lp_v11.optimizer import finite_number

TOL = 1e-6


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 180:
        raise ValueError(f'{name}: nonempty string of at most 180 characters required')
    return value


def time_value(value):
    if not isinstance(value, str):
        raise ValueError('ISO timestamp with timezone required')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as error:
        raise ValueError('Invalid ISO timestamp') from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Timestamp timezone required')
    return result


class HistoryLedger:
    def __init__(self, zone_ids, *, window_cycles=3):
        if not isinstance(zone_ids, (list, tuple)) or not zone_ids:
            raise ValueError('zone_ids required')
        ids = tuple(sorted(text(z, 'zone_id') for z in zone_ids))
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate catalog zone_id')
        if isinstance(window_cycles, bool) or not isinstance(window_cycles, int) or not 1 <= window_cycles <= 10:
            raise ValueError('window_cycles must be integer 1..10')
        self._zone_ids, self._window = ids, window_cycles
        self._entries = []

    def _normalize(self, record):
        required = {'cycle_id', 'cycle_index', 'cycle_start', 'cycle_end', 'context',
                    'source_ref', 'complete', 'quality', 'source_budget_ml',
                    'source_remaining_ml', 'in_transit_release_ml', 'rows'}
        if not isinstance(record, dict) or set(record) != required:
            raise ValueError('finalized cycle fields mismatch')
        if record['context'] != 'SIMULASI' or record['quality'] != 'VALID' or record['complete'] is not True:
            raise ValueError('Only complete VALID SIMULASI cycles are accepted')
        index = record['cycle_index']
        if isinstance(index, bool) or not isinstance(index, int) or index < 1:
            raise ValueError('cycle_index must be a positive integer')
        start, end = time_value(record['cycle_start']), time_value(record['cycle_end'])
        if end <= start:
            raise ValueError('cycle_end must follow cycle_start')
        budget = finite_number(record['source_budget_ml'], 'source_budget_ml')
        remaining = finite_number(record['source_remaining_ml'], 'source_remaining_ml')
        transit = finite_number(record['in_transit_release_ml'], 'in_transit_release_ml')
        if transit != 0:
            raise ValueError('DATA_HOLD: reconcile transit before finalizing history')
        if remaining > budget + TOL:
            raise ValueError('remaining source exceeds initial budget')
        rows = record['rows']
        if not isinstance(rows, list) or len(rows) != len(self._zone_ids):
            raise ValueError('all registered zones required')
        normalized = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'zone_id', 'frozen_need_ml', 'source_released_ml', 'simulated_received_ml'}:
                raise ValueError('history row fields mismatch')
            zone = text(row['zone_id'], 'zone_id')
            need = finite_number(row['frozen_need_ml'], 'frozen_need_ml')
            release = finite_number(row['source_released_ml'], 'source_released_ml')
            received = finite_number(row['simulated_received_ml'], 'simulated_received_ml')
            if received > release:
                raise ValueError('isolated full-cycle receipt cannot exceed source release')
            normalized.append({'zone_id': zone, 'frozen_need_ml': need,
                               'source_released_ml': release, 'simulated_received_ml': received})
        if tuple(sorted(r['zone_id'] for r in normalized)) != self._zone_ids:
            raise ValueError('zone mismatch or duplicates')
        normalized.sort(key=lambda row: row['zone_id'])
        if abs(budget-remaining-math.fsum(r['source_released_ml'] for r in normalized)) > TOL:
            raise ValueError('source ledger does not balance')
        return {'cycle_id': text(record['cycle_id'], 'cycle_id'), 'cycle_index': index,
                'cycle_start': start.isoformat(), 'cycle_end': end.isoformat(),
                'context': 'SIMULASI', 'source_ref': text(record['source_ref'], 'source_ref'),
                'complete': True, 'quality': 'VALID', 'source_budget_ml': budget,
                'source_remaining_ml': remaining, 'in_transit_release_ml': 0., 'rows': normalized}

    def finalize_cycle(self, record):
        value = self._normalize(record)
        checksum = digest(value)
        previous = next((e for e in self._entries if e['record']['cycle_id'] == value['cycle_id']), None)
        if previous:
            if previous['record_sha256'] != checksum:
                raise ValueError('cycle_id conflict: same id with different finalized content')
            return deepcopy(previous)
        if value['cycle_index'] != len(self._entries)+1:
            raise ValueError('cycle_index must advance consecutively; do not skip unresolved cycles')
        if self._entries:
            last = self._entries[-1]['record']
            if time_value(value['cycle_start']) < time_value(last['cycle_end']):
                raise ValueError('overlapping or out-of-order cycles')
            previous_duration = (time_value(last['cycle_end'])-time_value(last['cycle_start'])).total_seconds()
            duration = (time_value(value['cycle_end'])-time_value(value['cycle_start'])).total_seconds()
            if duration != previous_duration:
                raise ValueError('mixed cycle horizons require an explicit normalization policy')
        gaps = [{'zone_id': row['zone_id'],
                 'deficit_ml': max(0., row['frozen_need_ml']-row['simulated_received_ml']),
                 'surplus_ml': max(0., row['simulated_received_ml']-row['frozen_need_ml']),
                 'simulated_fulfillment_ratio': row['simulated_received_ml']/row['frozen_need_ml'] if row['frozen_need_ml'] > 0 else None}
                for row in value['rows']]
        entry = {'record': value, 'record_sha256': checksum, 'calculated_gaps': gaps}
        self._entries.append(entry)
        return deepcopy(entry)

    def snapshot(self, as_of):
        cutoff = time_value(as_of)
        entries = [e for e in self._entries if time_value(e['record']['cycle_end']) <= cutoff][-self._window:]
        rows = []
        for zone in self._zone_ids:
            selected = [next(r for r in e['record']['rows'] if r['zone_id'] == zone) for e in entries]
            need = math.fsum(r['frozen_need_ml'] for r in selected)
            deficit = math.fsum(max(0., r['frozen_need_ml']-r['simulated_received_ml']) for r in selected)
            rows.append({'zone_id': zone, 'history_state': ('UNKNOWN' if not entries else 'NO_POSITIVE_NEED' if need == 0 else 'KNOWN'),
                         'completed_cycles_used': len(entries),
                         'history_need_ml': need if entries else None,
                         'history_deficit_ml': deficit if entries else None,
                         'history_score': deficit/need if need > 0 else None,
                         'kind': 'CALCULATED', 'context': 'SIMULASI'})
        result = {'schema_version': 'balance-history-snapshot/0.1', 'context': 'SIMULASI',
                  'as_of': cutoff.isoformat(), 'window_cycles': self._window,
                  'cycle_ids_used': [e['record']['cycle_id'] for e in entries],
                  'record_sha256_used': [e['record_sha256'] for e in entries],
                  'rows': rows, 'actual_measured': None}
        result['snapshot_sha256'] = digest(result)
        return result

    def export_state(self):
        state = {'schema_version': 'balance-history-state/0.1', 'context': 'SIMULASI',
                 'zone_ids': list(self._zone_ids), 'window_cycles': self._window,
                 'entries': deepcopy(self._entries), 'real_measurement_rows': 0,
                 'execution_allowed': False, 'actual_measured': None}
        state['state_sha256'] = digest(state)
        return state

    @classmethod
    def from_state(cls, state):
        if not isinstance(state, dict):
            raise ValueError('history state must be an object')
        body = {k: v for k, v in state.items() if k != 'state_sha256'}
        if digest(body) != state.get('state_sha256'):
            raise ValueError('history state checksum mismatch')
        if set(body) != {'schema_version','context','zone_ids','window_cycles','entries','real_measurement_rows','execution_allowed','actual_measured'}:
            raise ValueError('history state fields mismatch')
        if (body['schema_version'] != 'balance-history-state/0.1' or body['context'] != 'SIMULASI'
            or type(body['real_measurement_rows']) is not int or body['real_measurement_rows'] != 0
            or body['execution_allowed'] is not False or body['actual_measured'] is not None):
            raise ValueError('unsupported history state provenance')
        ledger = cls(body['zone_ids'], window_cycles=body['window_cycles'])
        if not isinstance(body['entries'], list):
            raise ValueError('entries must be a list')
        for entry in body['entries']:
            if not isinstance(entry, dict) or set(entry) != {'record','record_sha256','calculated_gaps'}:
                raise ValueError('history entry fields mismatch')
            regenerated = ledger.finalize_cycle(entry['record'])
            if regenerated != entry:
                raise ValueError('stored derived history does not match input record')
        if ledger.export_state() != state:
            raise ValueError('history state is not canonical')
        return ledger


def cycle_record(simulated_ledger, *, cycle_index, cycle_start, cycle_end):
    """Map a completed full-cycle replay, never LP expected values, into history."""
    if simulated_ledger['context'] != 'SIMULASI' or simulated_ledger['execution_allowed'] is not False:
        raise ValueError('Only simulation ledger supported')
    if simulated_ledger['in_transit_release_ml'] != 0 or not simulated_ledger['source_balance_ok']:
        raise ValueError('Unreconciled source ledger')
    plans = simulated_ledger['plans']
    if not plans or plans[-1]['replay_state'] != 'COMPLETED_IN_REPLAY':
        raise ValueError('Cycle must be completed before becoming history')
    rows = simulated_ledger['zones']
    if any(r['initial_simulated_received_credit_ml'] != 0 for r in rows):
        raise ValueError('Full-cycle history requires replay from cycle start, not partial checkpoint')
    return {'cycle_id': simulated_ledger['cycle_id'], 'cycle_index': cycle_index,
            'cycle_start': cycle_start, 'cycle_end': cycle_end,
            'context': 'SIMULASI', 'complete': True, 'quality': 'VALID',
            'source_ref': plans[-1]['plan_id'],
            'source_budget_ml': simulated_ledger['source_start_ml'],
            'source_remaining_ml': simulated_ledger['source_remaining_ml'],
            'in_transit_release_ml': 0.,
            'rows': [{'zone_id': r['zone_id'], 'frozen_need_ml': r['frozen_full_cycle_need_ml'],
                      'source_released_ml': r['simulated_source_released_ml'],
                      'simulated_received_ml': r['simulated_received_total_ml']} for r in rows]}

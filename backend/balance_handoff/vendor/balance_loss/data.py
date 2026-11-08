"""Paired-volume integrity checks; preserve rejected raw records."""
from collections import Counter
from datetime import datetime
import math
import numpy as np

ZONES = ('A','B','C')
FEATURES = ('is_A','is_B','is_C','nominal_flow_ml_s','upstream_head_cm')


def number(value, name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ValueError(f'{name}: finite nonnegative number required')
    return float(value)


def timestamp(value):
    result=datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('Timezone required')
    return result


def inspect_pair(row, min_release_ml=1.0):
    """Validate declared metadata; this does not authenticate a physical measurement."""
    threshold=number(min_release_ml,'min_release_ml')
    try:
        if row['schema_version']!='balance-loss-pair/0.1' or row['volume_unit']!='mL':
            raise ValueError('SCHEMA_OR_UNIT_MISMATCH')
        context=row['context']
        if context not in ('SIMULASI','MINIATUR') or row['zone_id'] not in ZONES:
            raise ValueError('UNSUPPORTED_CONTEXT_OR_ZONE')
        expected='SIMULATED' if context=='SIMULASI' else 'MEASUREMENT'
        if row['release_value_kind']!=expected or row['received_value_kind']!=expected:
            raise ValueError('COMMAND_OR_FALSE_MEASUREMENT_KIND')
        if row['quality']!='VALID' or row['collection_complete'] is not True or row['transit_reconciled'] is not True or row['isolated_pair'] is not True:
            raise ValueError('QUALITY_OR_PAIRING_NOT_READY')
        for key in ('record_id','session_id','experiment_id','pairing_ref','release_calibration_ref','received_calibration_ref','delay_ref','source_ref'):
            if not isinstance(row[key],str) or not row[key].strip():
                raise ValueError('MISSING_TRACEABILITY')
        if context=='MINIATUR' and any('SIMULATOR' in row[k].upper() for k in ('release_calibration_ref','received_calibration_ref')):
            raise ValueError('PLACEHOLDER_CALIBRATION_REFERENCE')
        release=number(row['release_volume_ml'],'release_volume_ml')
        received=number(row['received_volume_ml'],'received_volume_ml')
        delay=number(row['travel_delay_s'],'travel_delay_s')
        if release<=threshold:
            raise ValueError('RELEASE_BELOW_THRESHOLD')
        if received>release:
            raise ValueError('RECEIVED_EXCEEDS_RELEASE')
        as_of=timestamp(row['as_of'])
        start,end=timestamp(row['release_start']),timestamp(row['release_end'])
        collected_start,collected_end=timestamp(row['received_start']),timestamp(row['received_end'])
        recorded=timestamp(row['recorded_at'])
        if not (as_of<=start<end and collected_start<collected_end and recorded>=max(end,collected_end)):
            raise ValueError('INVALID_TIME_WINDOW')
        if (collected_start-start).total_seconds()>delay or (collected_end-end).total_seconds()<delay:
            raise ValueError('RECEIVING_WINDOW_MISALIGNED_OR_TRUNCATED')
        feature_ready=False
        try:
            f=row['features']
            flow=number(f['nominal_flow_ml_s'],'nominal_flow_ml_s')
            number(f['upstream_head_cm'],'upstream_head_cm')
            feature_ready=flow>0 and timestamp(row['features_available_at'])<=as_of
        except (ValueError,KeyError,TypeError):
            pass
        return {'accepted':True,'record_id':row['record_id'],'context':context,
                'label_kind':'CALCULATED_FROM_SIMULATED_PAIR' if context=='SIMULASI' else 'CALCULATED_FROM_DECLARED_MEASUREMENTS',
                'eta_label':received/release,'loss_volume_ml':release-received,
                'ml_features_ready':bool(feature_ready),'physical_measurement_authenticated':False,
                'reason':None}
    except (ValueError,KeyError,TypeError,OverflowError) as error:
        return {'accepted':False,'record_id':row.get('record_id') if isinstance(row,dict) else None,
                'eta_label':None,'loss_volume_ml':None,'ml_features_ready':False,'reason':str(error)}


def validate_pairs(rows,min_release_ml=1.0):
    if not isinstance(rows,list):
        raise ValueError('records must be an array')
    counts=Counter(r.get('record_id') for r in rows if isinstance(r,dict) and isinstance(r.get('record_id'),str))
    results=[]
    for row in rows:
        result=inspect_pair(row,min_release_ml=min_release_ml)
        if isinstance(result['record_id'],str) and counts[result['record_id']]>1:
            result.update(accepted=False,eta_label=None,loss_volume_ml=None,reason='DUPLICATE_RECORD_ID')
        results.append(result)
    return results


def matrix(rows):
    result=[]
    for row in rows:
        if row['zone_id'] not in ZONES:
            raise ValueError('Unknown branch')
        f=row['features']
        flow=number(f['nominal_flow_ml_s'],'nominal_flow_ml_s')
        head=number(f['upstream_head_cm'],'upstream_head_cm')
        if flow==0 or timestamp(row['features_available_at'])>timestamp(row['as_of']):
            raise ValueError('Unavailable/future feature')
        result.append([float(row['zone_id']==zone) for zone in ZONES]+[flow,head])
    return np.asarray(result,dtype=float)


def split_audit(rows):
    inspected=validate_pairs(rows)
    if not rows or not all(r['accepted'] and r['ml_features_ready'] for r in inspected):
        raise ValueError('Dataset contains rejected pairs or unavailable features')
    if any(r['context']!='SIMULASI' for r in rows):
        raise ValueError('Synthetic-only training; real-data training needs separate reviewed protocol')
    groups={s:[] for s in ('train','validation','test')}
    sessions={};experiments={}
    for row in rows:
        split=row['split']
        if split not in groups:
            raise ValueError('Unknown split')
        for key,seen in (('session_id',sessions),('experiment_id',experiments)):
            if seen.setdefault(row[key],split)!=split:
                raise ValueError('Session/experiment crosses split boundary')
        groups[split].append(row)
    for split,part in groups.items():
        if not part or {r['zone_id'] for r in part}!=set(ZONES):
            raise ValueError(f'{split}: all three branches required')
    for left,right in (('train','validation'),('validation','test')):
        if max(timestamp(r['recorded_at']) for r in groups[left])>=min(timestamp(r['as_of']) for r in groups[right]):
            raise ValueError('Chronological split overlap')
    return groups,inspected

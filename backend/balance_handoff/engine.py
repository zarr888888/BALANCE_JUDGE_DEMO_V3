"""Immutable model replay, explicit volume scaling, and existing LP reuse."""
import hashlib
import json
from pathlib import Path
import platform
import joblib
import numpy as np
import scipy
import sklearn
from balance_handoff.vendor.balance_ml.core import FEATURES, audit_splits, finite_nonnegative, source_budget_litres
from balance_handoff.vendor.balance_ml.training import baseline
from balance_handoff.vendor.balance_lp_v11 import Policy, Zone, optimize_fair
from .loss_runtime import LossRuntime


SCALE_FACTOR = 0.00001  # illustrative volume mapping, not hydraulic similarity
ETA = {'A': 1.0, 'B': 1.0, 'C': 0.8}  # uncalibrated simulation assumptions
POSITIONS = {'A': 'hulu', 'B': 'tengah', 'C': 'hilir'}


def to_demo_ml(volume_L):
    return finite_nonnegative(finite_nonnegative(volume_L, 'volume_L')*1000*SCALE_FACTOR, 'demo_ml')


def proportional(zones, budget):
    """Proportional source release, capacity/no-overdelivery aware; same LP inputs."""
    remaining = min(budget, sum(z.release_bound for z in zones))
    releases = {z.zone_id: 0.0 for z in zones}
    active = [z for z in zones if z.need_ml > 0 and z.release_bound > 0]
    while active and remaining > 1e-10:
        denominator = sum(z.need_ml for z in active)
        capped = [z for z in active if remaining*z.need_ml/denominator >= z.release_bound]
        if capped:
            for z in capped:
                releases[z.zone_id] = z.release_bound
                remaining = max(0.0, remaining-z.release_bound)
            active = [z for z in active if z not in capped]
        else:
            for z in active:
                releases[z.zone_id] = remaining*z.need_ml/denominator
            break
    rows = []
    ratios = []
    for z in zones:
        x = releases[z.zone_id]
        delivered = x*z.delivery_efficiency
        ratio = delivered/z.need_ml if z.need_ml > 0 else None
        if ratio is not None:
            ratios.append(ratio)
        rows.append({'zone_id': z.zone_id, 'release_ml': x, 'expected_delivered_ml': delivered,
                     'fulfillment_ratio': ratio})
    return {'method': 'CAPACITY_AWARE_PROPORTIONAL_RELEASE', 'zones': rows,
            'total_release_ml': sum(releases.values()),
            'disparity_ratio': max(ratios)-min(ratios) if ratios else None}


class DemoEngine:
    def __init__(self, run_dir, loss_run_dir):
        self.run_dir = Path(run_dir)
        self.report = json.loads((self.run_dir/'training_report.json').read_text(encoding='utf-8'))
        if self.report['schema_version'] != 'balance-ml-lab-report/0.1' or self.report['ml_status'] != 'TRAINED_SYNTHETIC_ONLY':
            raise ValueError('Only the completed synthetic ML lab run is supported')
        if self.report['data_context'] != 'SIMULASI' or self.report['real_measurement_rows'] != 0:
            raise ValueError('This demo cannot accept real-data or measurement claims')
        current = {'numpy': np.__version__, 'scipy': scipy.__version__,
                   'scikit_learn': sklearn.__version__, 'joblib': joblib.__version__}
        for name, version in current.items():
            if self.report['environment'][name] != version:
                raise ValueError(f'Versi {name} berbeda dari training; gunakan environment training yang sama')
        if '.'.join(self.report['environment']['python'].split('.')[:2]) != '.'.join(platform.python_version().split('.')[:2]):
            raise ValueError('Versi mayor/minor Python berbeda dari training')
        data = (self.run_dir/'dataset_SIMULASI.jsonl').read_bytes()
        if hashlib.sha256(data).hexdigest() != self.report['dataset_sha256']:
            raise ValueError('Dataset hash does not match the completed training report')
        self.rows = [json.loads(line) for line in data.decode('utf-8').splitlines()]
        audit_splits(self.rows)
        self.bundles = {}
        self.model_hashes = {}
        for task in FEATURES:
            path = self.run_dir/f'{task}_synthetic_model.joblib'
            # CLI startup only: local artifacts produced by the user's own training.
            # No HTTP upload/path/loading or model download endpoint exists.
            bundle = joblib.load(path)
            if bundle['task'] != task or bundle['unit'] != 'L' or tuple(bundle['feature_names']) != FEATURES[task]:
                raise ValueError('Model task/feature/unit mismatch')
            if bundle['dataset_sha256'] != self.report['dataset_sha256'] or bundle['ml_status'] != 'TRAINED_SYNTHETIC_ONLY':
                raise ValueError('Model provenance mismatch')
            if len(bundle['training_min']) != len(FEATURES[task]) or len(bundle['training_max']) != len(FEATURES[task]):
                raise ValueError('Model training bounds mismatch')
            self.bundles[task] = bundle
            self.model_hashes[task] = hashlib.sha256(path.read_bytes()).hexdigest()
        # Fixed replay of the FIRST test cycle, not selected for a good score.
        first = next(row['cycle_id'] for row in self.rows if row['split'] == 'test')
        self.scenario = sorted([r for r in self.rows if r['cycle_id'] == first],
                               key=lambda r: (r['task'], r['zone_id'] or ''))
        self.cycle_id = first
        self.loss_runtime=LossRuntime(loss_run_dir)

    def health(self):
        selected = self.report['tasks']['demand']['selected_ml']
        demand_metric = self.report['tasks']['demand']['test'][selected]['effective']
        return {'schema_version': 'balance-ai-demo/0.3', 'demo_version':'1.2', 'context': 'SIMULASI',
                'ml_status': 'TRAINED_SYNTHETIC_ONLY', 'model_run': self.run_dir.name,
                'integration_scope': 'LOCAL_REPLAY_PREDICTION_LOSS_TO_LP',
                'loss_model':self.loss_runtime.info(),'loss_integration_status':'CONNECTED_SIMULATION_ONLY',
                'proposal_targets_proven': False, 'real_measurement_rows': 0,
                'execution_allowed': False, 'model_hashes': self.model_hashes,
                'demand_selected_ml': selected,
                'demand_test_mape_pct_above_100_L': demand_metric['mape_pct_above_threshold'],
                'demand_test_mape_excluded_n': demand_metric['mape_excluded_n'],
                'demand_test_n': demand_metric['n']}

    def predict_replay(self, mode):
        output = []
        for row in self.scenario:
            task = row['task']
            bundle = self.bundles[task]
            x = np.asarray([[row['features'][key] for key in FEATURES[task]]])
            try:
                raw = float(bundle['model'].predict(x)[0])
            except (ValueError, TypeError, IndexError) as error:
                raise RuntimeError('Prediction failed; no fallback plan emitted') from error
            if not np.isfinite(raw):
                raise RuntimeError('Nonfinite model output')
            reference = 'agronomic_forecast' if task == 'demand' else 'persistence'
            reference_L = float(baseline([row], task, reference)[0])
            outside = [key for i, key in enumerate(FEATURES[task])
                       if x[0, i] < bundle['training_min'][i] or x[0, i] > bundle['training_max'][i]]
            value = max(0.0, raw) if mode == 'ml' else reference_L
            output.append({'task': task, 'zone_id': row['zone_id'],
                'selected_ml_method': self.report['tasks'][task]['selected_ml'],
                'method_used': self.report['tasks'][task]['selected_ml'] if mode == 'ml' else reference,
                'raw_ml_prediction_L': raw, 'ml_prediction_L': max(0.0, raw),
                'baseline_method': reference, 'baseline_prediction_L': reference_L,
                'value_used_L': value, 'ml_features_outside_training_range': outside,
                'preferred_method_on_validation': self.report['tasks'][task]['preferred_method_on_validation_including_baselines'],
                'provenance': {'context': 'SIMULASI', 'kind': 'PREDICTION' if mode == 'ml' else 'CALCULATED',
                    'source_ref': f"{self.run_dir.name}/{task}", 'as_of': row['as_of'], 'quality': 'UNVERIFIED'}})
        return output

    def calculate(self, options):
        # HTTP validation is duplicated by an explicit domain boundary for callers.
        required={'prediction_mode','supply_fraction','tail_capacity_ml','loss_mode'}
        if not isinstance(options,dict) or not required.issubset(options) or set(options)-required-{'loss_flow_ml_s','loss_head_cm'}:
            raise ValueError('Invalid demo options')
        options={'loss_flow_ml_s':6.0,'loss_head_cm':20.0,**options}
        if options['prediction_mode'] not in ('ml', 'baseline'):
            raise ValueError('Unsupported prediction mode')
        if options['loss_mode'] not in ('learned','assumed'):
            raise ValueError('Unsupported loss mode')
        flow=finite_nonnegative(options['loss_flow_ml_s'],'loss_flow_ml_s')
        head=finite_nonnegative(options['loss_head_cm'],'loss_head_cm')
        if not 2<=flow<=12 or not 10<=head<=30:
            raise ValueError('Loss feature settings outside configured simulation domain')
        fraction = finite_nonnegative(options['supply_fraction'], 'supply_fraction')
        tail_cap = finite_nonnegative(options['tail_capacity_ml'], 'tail_capacity_ml')
        if fraction > 1 or tail_cap > 5000:
            raise ValueError('Options exceed demo range')
        predictions = self.predict_replay(options['prediction_mode'])
        as_of=self.scenario[0]['as_of']
        learned=options['loss_mode']=='learned'
        if learned:
            loss_rows=self.loss_runtime.predict_snapshot(as_of,flow,head)
        else:
            loss_rows=[{'zone_id':z,'delivery_efficiency':eta,'eta_raw_prediction':None,
                'clipping_applied':False,'outside_training_range':[],'actual_measured_eta':None,
                'feature_snapshot':None,'provenance':{'context':'SIMULASI','kind':'ASSUMPTION',
                    'source_ref':'fixed-demo-eta-v1','as_of':as_of,'quality':'UNVERIFIED'}} for z,eta in ETA.items()]
        # Immutable local coefficients for both LP and the proportional baseline.
        # No model is evaluated inside the solver and eta never depends on x.
        frozen_eta={r['zone_id']:r['delivery_efficiency'] for r in loss_rows}
        snapshot_key=json.dumps({'cycle_id':self.cycle_id,'mode':options['loss_mode'],
            'flow':flow,'head':head,'eta':frozen_eta,'loss_model':self.loss_runtime.model_hash},sort_keys=True)
        loss_profile={'mode':options['loss_mode'],'inference_used':learned,'frozen_before_solve':True,
            'snapshot_ref':'sim-loss-'+hashlib.sha256(snapshot_key.encode()).hexdigest()[:20],
            'as_of':as_of,'feature_input_kind':'ASSUMPTION','model':self.loss_runtime.info(),
            'settings':{'nominal_flow_ml_s':flow,'upstream_head_cm':head},'zones':loss_rows,
            'context':'SIMULASI','actual_measured_eta':None,'prediction_interval':None}
        source = next(p for p in predictions if p['task'] == 'supply')
        # All storage/reserve/other-outflow assumptions are EXPLICIT zero here.
        base_budget = source_budget_litres(0, source['value_used_L'], 0, 0)
        budget_ml = to_demo_ml(base_budget)*fraction
        zones = []
        inputs = []
        warnings = ['SIMULATION_ONLY', 'DEMAND_ACCURACY_TARGET_NOT_PROVEN',
                    'LOSS_LEARNED_SYNTHETIC_ONLY' if learned else 'ETA_ASSUMED_NOT_LEARNED',
                    'LOSS_FEATURES_ASSUMED_NOT_MEASURED','CROSS_GENERATOR_COMPOSITION_UNVALIDATED',
                    'VOLUME_SCALE_NOT_HYDRAULIC_SIMILARITY',
                    'HISTORY_UNKNOWN', 'NO_CALIBRATED_PREDICTION_INTERVAL']
        if any(r['outside_training_range'] for r in loss_rows):warnings.append('LOSS_FEATURE_OUTSIDE_TRAINING_RANGE')
        if any(r['clipping_applied'] for r in loss_rows):warnings.append('LOSS_PREDICTION_CLIPPED')
        if any(p['ml_features_outside_training_range'] for p in predictions) and options['prediction_mode'] == 'ml':
            warnings.append('ML_FEATURE_OUTSIDE_TRAINING_RANGE')
        for p in predictions:
            if p['task'] != 'demand':
                continue
            zone = p['zone_id']
            need = to_demo_ml(p['value_used_L'])
            cap = tail_cap if zone == 'C' else 5000.0
            zones.append(Zone(zone, POSITIONS[zone], need, frozen_eta[zone], cap))
            inputs.append({'zone_id': zone, 'position': POSITIONS[zone], 'need_ml': need,
                           'delivery_efficiency':frozen_eta[zone],
                           'eta_kind':'PREDICTION' if learned else 'ASSUMPTION',
                           'eta_snapshot_ref':loss_profile['snapshot_ref'],'max_release_ml': cap,
                           'history_state': 'UNKNOWN'})
        result = optimize_fair(zones, budget_ml, Policy())
        if result['status'] == 'SOLVER_FAILED':
            raise RuntimeError('LP solver failed; no simulated plan is available')
        optimizer_fields = ('optimizer_version', 'policy_status', 'policy', 'status', 'total_release_ml',
            'unused_ml', 'disparity_ratio', 'min_fulfillment_ratio', 'mean_fulfillment_ratio',
            'strict_tail_max_release_ml', 'release_reduction_for_strict_tail_ml', 'history_effect',
            'zones', 'stage_objectives', 'max_primal_violation', 'solver_trace', 'runtime_s',
            'tail_feasibility_mode','objective_order')
        allocation = {key: result.get(key) for key in optimizer_fields}
        for z in allocation.get('zones', []):
            ratio = z['fulfillment_ratio']
            z['explanation_id'] = (
                f"Zona {z['zone_id']}: kebutuhan {z['need_ml']:.2f} mL; "
                f"release {z['release_ml']:.2f} mL; perkiraan diterima {z['expected_delivered_ml']:.2f} mL. "
                +(f"Pemenuhan {100*ratio:.2f}%. " if ratio is not None else 'Kebutuhan nol; rasio tidak dihitung. ')
                +f"Eta {frozen_eta[z['zone_id']]:.5f} berasal dari "+('model loss simulasi. ' if learned else 'asumsi tetap. ')
                +('Target hilir belum terpenuhi; review konflik kapasitas dan pemanfaatan air. '
                    if z['tail_guard'] and z['tail_guard']['status'] == 'UNMET' else '')
                +'Histori belum tersedia. LP mempertahankan pemanfaatan sumber dan menekan disparitas; '
                +('pengaman hilir menjadi constraint ketat karena feasible.'
                    if result.get('tail_feasibility_mode')=='HARD_GUARD_AT_MAX_RELEASE'
                    else 'target hilir tidak feasible pada pemanfaatan ini; slack tidak diprioritaskan di atas pemerataan.'))
        return {**self.health(), 'status': 'SIMULATION_FOR_REVIEW', 'options': options,
                'scenario': {'cycle_id': self.cycle_id, 'as_of': self.scenario[0]['as_of'],
                    'target_end': self.scenario[0]['target_end'], 'horizon_hours': 24,
                    'calendar_is_synthetic': True, 'replay_partition': 'test',
                    'source': 'first test cycle from existing local synthetic training run'},
                'predictions': predictions, 'prediction_unit': 'L','loss_profile':loss_profile,
                'conversion': {'formula': 'demo_mL = field_L * 1000 * scale_factor',
                    'scale_factor': SCALE_FACTOR, 'purpose': 'ILLUSTRATIVE_VOLUME_MAPPING_ONLY'},
                'source_budget': {'inflow_prediction_L': source['value_used_L'],
                    'usable_storage_assumed_L': 0, 'reserve_assumed_L': 0, 'other_outflows_assumed_L': 0,
                    'scenario_supply_fraction': fraction, 'available_demo_ml': budget_ml,
                    'kind': 'CALCULATED', 'context': 'SIMULASI'},
                'allocation_inputs': inputs, 'allocation': allocation,
                'comparison_baseline': proportional(zones, budget_ml),
                'warnings': warnings, 'actual_measured': None, 'schedule': None,
                'prediction_interval': None, 'device_connected': False}

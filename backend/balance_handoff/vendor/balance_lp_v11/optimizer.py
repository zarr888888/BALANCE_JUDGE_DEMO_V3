"""Experimental v1.1 candidate correcting a soft-tail objective pathology.

Inputs are frozen SIMULATION volumes in mL, possibly from upstream predictions.
One reservoir, independent parallel branches, fixed delivery efficiencies.
This module cannot approve plans, dispatch commands, or operate a gate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from numbers import Real
from time import perf_counter
from typing import Any

import numpy as np
import scipy
from scipy.optimize import linprog


def finite_number(value: Any, name: str, *, minimum=0.0, maximum=None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f'{name} must be a finite number, not bool/string')
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f'{name} is outside supported numeric range') from exc
    if not np.isfinite(number) or number < minimum:
        raise ValueError(f'{name} must be finite and >= {minimum}')
    if maximum is not None and number > maximum:
        raise ValueError(f'{name} must be <= {maximum}')
    return number


@dataclass(frozen=True)
class Zone:
    zone_id: str
    position: str
    need_ml: float
    delivery_efficiency: float = 1.0
    max_release_ml: float | None = None
    received_to_date_ml: float = 0.0
    history_need_ml: float = 0.0
    history_deficit_ml: float = 0.0
    history_known: bool = False
    minimum_received_ml: float = 0.0

    def __post_init__(self):
        if not isinstance(self.zone_id, str) or not self.zone_id.strip():
            raise ValueError('zone_id must be a nonempty string')
        if not isinstance(self.position, str) or self.position not in {'hulu','tengah','hilir'}:
            raise ValueError('position must be hulu/tengah/hilir')
        for field in ('need_ml','received_to_date_ml','history_need_ml',
                      'history_deficit_ml','minimum_received_ml'):
            finite_number(getattr(self, field), field)
        eta = finite_number(self.delivery_efficiency,'delivery_efficiency',maximum=1)
        if eta == 0:
            raise ValueError('delivery_efficiency must be > 0')
        if self.max_release_ml is not None:
            finite_number(self.max_release_ml,'max_release_ml')
        if self.received_to_date_ml > self.need_ml or self.minimum_received_ml > self.need_ml:
            raise ValueError('credited/minimum received cannot exceed current need')
        if not isinstance(self.history_known, bool):
            raise ValueError('history_known must be bool')
        if self.history_deficit_ml > self.history_need_ml:
            raise ValueError('history_deficit_ml cannot exceed history_need_ml')
        if not self.history_known and (self.history_need_ml or self.history_deficit_ml):
            raise ValueError('unknown history must use an explicit zero fallback')

    @property
    def history_score(self) -> float:
        return self.history_deficit_ml / self.history_need_ml if self.history_known and self.history_need_ml > 0 else 0.0

    @property
    def release_bound(self) -> float:
        bound = (self.need_ml - self.received_to_date_ml) / self.delivery_efficiency
        if not np.isfinite(bound):
            raise ValueError('unsupported derived release scale')
        return min(bound, self.max_release_ml) if self.max_release_ml is not None else bound


@dataclass(frozen=True)
class Policy:
    history_mode: str = 'STRICT_CURRENT'
    history_disparity_budget: float = 0.0
    kappa_tail: float = 0.90
    epsilon_volume: float = 1e-6
    epsilon_ratio: float = 1e-7
    solver_time_budget_s: float = 15.0

    def __post_init__(self):
        if not isinstance(self.history_mode,str) or self.history_mode not in {'STRICT_CURRENT','HISTORY_BOUNDED'}:
            raise ValueError('unknown history_mode')
        finite_number(self.history_disparity_budget,'history_disparity_budget',maximum=1)
        finite_number(self.kappa_tail,'kappa_tail',maximum=1)
        for field in ('epsilon_volume','epsilon_ratio','solver_time_budget_s'):
            if finite_number(getattr(self,field),field) == 0:
                raise ValueError(f'{field} must be > 0')
        if self.history_mode == 'STRICT_CURRENT' and self.history_disparity_budget != 0:
            raise ValueError('STRICT_CURRENT requires history_disparity_budget=0')


class SolveError(RuntimeError):
    def __init__(self, stage, status, message):
        super().__init__(message)
        self.stage, self.status = stage, status


def optimize_fair(zones: list[Zone], available_ml: float,
                  policy: Policy | None = None) -> dict[str, Any]:
    """Return a simulation decision proposal and auditable solver diagnostics.

    Maximize feasible source utilization. If hard tail guards are feasible at
    that usage, enforce them. Otherwise minimize current disparity BEFORE any
    soft-tail slack objective, so reducing the mean cannot starve another zone
    to make a relative tail gap smaller. Bounded history uses an explicit budget.
    """
    started = perf_counter()
    available = finite_number(available_ml,'available_ml')
    if not isinstance(zones,(list,tuple)) or not zones or not all(isinstance(z,Zone) for z in zones):
        raise ValueError('zones must be a nonempty sequence of Zone objects')
    if len({z.zone_id for z in zones}) != len(zones):
        raise ValueError('duplicate zone_id')
    if not any(z.position == 'hilir' for z in zones):
        raise ValueError('at least one mapped tail-end zone is required')
    if policy is None:
        policy = Policy()
    if not isinstance(policy,Policy):
        raise ValueError('policy must be a Policy object')
    # Stable order makes deterministic tie-breaking independent of input order.
    zs = sorted(zones,key=lambda z:z.zone_id)
    n = len(zs)
    active = [i for i,z in enumerate(zs) if z.need_ml > 0]
    tails = [i for i in active if zs[i].position == 'hilir']
    L, H = n, n+1
    size = n+2+len(tails)
    trace: list[dict[str,Any]] = []
    tail_mode = 'NOT_EVALUATED'
    tail_slack_reference = None
    base = {
        'mode':'SIMULASI', 'value_kind':'CALCULATED',
        'optimizer_version':'balance-lp/1.1-candidate',
        'ml_trained':False, 'execution_allowed':False,
        'policy_status':'EXPERIMENTAL_FOR_REVIEW', 'policy':asdict(policy),
        'units':'mL', 'available_ml':available,
        'solver':f'SciPy {scipy.__version__} linprog/HiGHS',
    }

    def failed(status, reason):
        return {**base,'status':status,'reasons':[reason], 'zones':[],
                'schedule':None,'solver_trace':trace,'runtime_s':perf_counter()-started}

    bounds = [(0.0,z.release_bound) for z in zs] + [(0.0,1.0),(0.0,1.0)] + [(0.0,1.0)]*len(tails)
    rows: list[np.ndarray] = []
    limits: list[float] = []
    def add(target_rows, target_limits, row, limit):
        if not np.isfinite(row).all() or not np.isfinite(limit):
            raise ValueError('unsupported numeric scale in derived LP coefficients')
        target_rows.append(row.copy()); target_limits.append(float(limit))

    release_vector = np.zeros(size); release_vector[:n] = 1
    add(rows,limits,release_vector,available)
    ratio_vectors = {}
    credits = {}
    for i in active:
        z=zs[i]; vector=np.zeros(size); vector[i]=z.delivery_efficiency/z.need_ml
        ratio_vectors[i]=vector; credits[i]=z.received_to_date_ml/z.need_ml
        low=-vector.copy();low[L]=1
        high=vector.copy();high[H]=-1
        add(rows,limits,low,credits[i]);add(rows,limits,high,-credits[i])
    low_high=np.zeros(size);low_high[L]=1;low_high[H]=-1
    add(rows,limits,low_high,0)
    for i,z in enumerate(zs):
        minimum=np.zeros(size);minimum[i]=-z.delivery_efficiency
        add(rows,limits,minimum,z.received_to_date_ml-z.minimum_received_ml)
    slack_vector=np.zeros(size)
    if active:
        mean_vector=sum((ratio_vectors[i] for i in active),start=np.zeros(size))/len(active)
        mean_credit=sum(credits.values())/len(active)
        for k,i in enumerate(tails):
            row=policy.kappa_tail*mean_vector-ratio_vectors[i]
            row[n+2+k]=-1;slack_vector[n+2+k]=1
            add(rows,limits,row,credits[i]-policy.kappa_tail*mean_credit)

    def solve(label, objective, current_rows, current_limits, current_bounds=None):
        remaining=policy.solver_time_budget_s-(perf_counter()-started)
        if remaining <= 0:
            raise SolveError(label,1,'total solver time budget exhausted')
        result=linprog(objective,A_ub=np.array(current_rows),b_ub=np.array(current_limits),
                       bounds=current_bounds or bounds,method='highs',
                       options={'time_limit':remaining,'primal_feasibility_tolerance':1e-9,
                                'dual_feasibility_tolerance':1e-9})
        trace.append({'stage':label,'success':bool(result.success),'status':int(result.status),
                      'objective':float(result.fun) if result.success else None,
                      'message':str(result.message)})
        if not result.success:
            raise SolveError(label,int(result.status),str(result.message))
        return result.x

    def ratios(solution):
        return {i:credits[i]+float(ratio_vectors[i]@solution) for i in active}

    def finish(solution,status,r_star,delta_star,floor_star,strict_max,j_reference,j_final,
               reference=None,primal_violation=0.0):
        calculated=ratios(solution)
        mean=sum(calculated.values())/len(calculated) if calculated else None
        disparity=max(calculated.values())-min(calculated.values()) if calculated else None
        history_effective=j_final>j_reference+3*policy.epsilon_ratio
        if policy.history_mode=='STRICT_CURRENT':history_effect='DISABLED_BY_POLICY'
        elif history_effective:history_effect='EFFECTIVE'
        elif not any(z.history_score>0 for z in zs):history_effect='NO_HISTORY_SIGNAL'
        else:history_effect='NO_HISTORY_HEADROOM'
        output_rows=[]
        for i,z in enumerate(zs):
            release=max(0.0,float(solution[i]))
            delivered=release*z.delivery_efficiency
            cycle_received=z.received_to_date_ml+delivered
            ratio=calculated.get(i)
            history_delta=ratio-reference[i] if reference is not None and i in reference else None
            reasons=['SIMULATION_INPUTS']
            if z.delivery_efficiency<1 and release>policy.epsilon_volume:reasons.append('LOSS_COMPENSATION')
            if z.max_release_ml is not None and z.max_release_ml < (z.need_ml-z.received_to_date_ml)/z.delivery_efficiency:
                reasons.append('CAPACITY_LIMIT')
            if not z.history_known:reasons.append('HISTORY_UNKNOWN')
            if history_effective and z.history_score>0 and history_delta is not None and history_delta>policy.epsilon_ratio:
                reasons.append('HISTORY_PRIORITY_EFFECTIVE')
            if z.position=='hilir' and i in active and mean is not None and mean>policy.epsilon_ratio:
                gap=max(0.0,policy.kappa_tail*mean-ratio)
                tail={'status':'UNMET' if gap>3*policy.epsilon_ratio else 'SATISFIED',
                      'required_ratio':policy.kappa_tail*mean,'gap_ratio':gap,
                      'tail_to_mean':ratio/mean}
                reasons.append('TAIL_TARGET_UNMET' if tail['status']=='UNMET' else 'TAIL_TARGET_SATISFIED')
            elif z.position=='hilir':
                tail={'status':'NOT_APPLICABLE_ZERO_NEED_OR_MEAN','required_ratio':None,
                      'gap_ratio':None,'tail_to_mean':None}
            else:tail=None
            output_rows.append({'zone_id':z.zone_id,'position':z.position,'need_ml':z.need_ml,
                                'release_ml':release,'expected_delivered_ml':delivered,
                                'credited_received_to_date_ml':z.received_to_date_ml,
                                'expected_cycle_received_ml':cycle_received,
                                'fulfillment_ratio':ratio,'actual_fulfillment_ratio':None,
                                'expected_deficit_ml':max(0.0,z.need_ml-cycle_received),
                                'history_score':z.history_score,'history_delta_ratio':history_delta,
                                'tail_guard':tail,'reason_codes':reasons})
        total=sum(row['release_ml'] for row in output_rows)
        return {**base,'status':status,'total_release_ml':total,
                'tail_feasibility_mode':tail_mode,
                'objective_order':'MAX_RELEASE;FEASIBLE_HARD_TAIL;MIN_DISPARITY;MAX_FLOOR;MIN_SOFT_GAP;BOUNDED_HISTORY',
                'unused_ml':max(0.0,available-total),'disparity_ratio':disparity,
                'min_fulfillment_ratio':min(calculated.values()) if calculated else None,
                'mean_fulfillment_ratio':mean,'actual_disparity_ratio':None,
                'strict_tail_max_release_ml':strict_max,
                'release_reduction_for_strict_tail_ml':max(0.0,r_star-strict_max) if strict_max is not None else None,
                'history_effect':history_effect,'zones':output_rows,
                'stage_objectives':{'max_release_ml':r_star,'min_current_disparity_ratio':delta_star,
                                    'current_floor_ratio':floor_star,'history_score_reference':j_reference,
                                    'history_score_final':j_final,'reference_tail_slack_sum':tail_slack_reference},
                'max_primal_violation':primal_violation,'schedule':None,'solver_trace':trace,
                'runtime_s':perf_counter()-started}

    if not active or all(z.need_ml==z.received_to_date_ml for z in zs):
        return finish(np.zeros(size),'NO_DEMAND',0.0,0.0,1.0 if active else 0.0,0.0,0.0,0.0)

    try:
        first=solve('1_max_release',-release_vector,rows,limits)
        r_star=float(release_vector@first)

        # R_strict is a separate physical/policy alternative, not a claim that
        # capacity-limited tail protection can always coexist with full usage.
        strict_bounds=bounds.copy()
        for k in range(len(tails)):strict_bounds[n+2+k]=(0.0,0.0)
        try:
            strict=solve('strict_tail_alternative',-release_vector,rows,limits,strict_bounds)
            strict_max=float(release_vector@strict)
        except SolveError as exc:
            if exc.status != 2: raise
            strict_max=None  # A hard SOP may also make strict-tail infeasible.

        locked_rows=list(rows);locked_limits=list(limits)
        add(locked_rows,locked_limits,-release_vector,-max(0.0,r_star-policy.epsilon_volume))
        # Test the guard at the locked maximum release. Never infer this solely
        # from a flag, and never treat timeouts/numeric errors as infeasibility.
        try:
            solve('2_hard_tail_feasibility_at_max_release',np.zeros(size),locked_rows,locked_limits,strict_bounds)
            bounds=strict_bounds
            tail_mode='HARD_GUARD_AT_MAX_RELEASE'
        except SolveError as exc:
            if exc.status != 2: raise
            tail_mode='FAIRNESS_FIRST_DEGRADED'
        delta_vector=np.zeros(size);delta_vector[H]=1;delta_vector[L]=-1
        third=solve('3_min_disparity',delta_vector,locked_rows,locked_limits)
        delta_star=max(0.0,float(delta_vector@third))
        current_rows=list(locked_rows);current_limits=list(locked_limits)
        add(current_rows,current_limits,delta_vector,delta_star+policy.epsilon_ratio)
        floor_vector=np.zeros(size);floor_vector[L]=-1
        fourth=solve('4_max_current_floor',floor_vector,current_rows,current_limits)
        floor_star=float(fourth[L])
        floor_lock=np.zeros(size);floor_lock[L]=-1
        add(current_rows,current_limits,floor_lock,-floor_star+policy.epsilon_ratio)
        fifth=solve('5_min_soft_tail_gap_after_fairness',slack_vector,current_rows,current_limits)
        tail_slack_reference=max(0.0,float(slack_vector@fifth))
        # Later history may use its reviewed disparity allowance, but cannot
        # worsen the total relative tail gap beyond this fair reference.
        add(locked_rows,locked_limits,slack_vector,tail_slack_reference+policy.epsilon_ratio)
        add(current_rows,current_limits,slack_vector,tail_slack_reference+policy.epsilon_ratio)
        reference=ratios(fifth)
        history_vector=sum((zs[i].history_score*ratio_vectors[i] for i in active),start=np.zeros(size))
        history_constant=sum(zs[i].history_score*credits[i] for i in active)
        j_reference=float(history_vector@fifth)+history_constant
        final_rows=list(current_rows);final_limits=list(current_limits)
        result=fifth
        if policy.history_mode == 'HISTORY_BOUNDED' and any(z.history_score>0 for z in zs):
            final_rows=list(locked_rows);final_limits=list(locked_limits)
            add(final_rows,final_limits,delta_vector,delta_star+policy.history_disparity_budget+policy.epsilon_ratio)
            floor=max(0.0,floor_star-policy.history_disparity_budget)
            for i in active:
                add(final_rows,final_limits,-ratio_vectors[i],credits[i]-floor)
            history_solution=solve('6_max_history_priority',-history_vector,final_rows,final_limits)
            j_linear=float(history_vector@history_solution)
            add(final_rows,final_limits,-history_vector,-j_linear+policy.epsilon_ratio)
            sixth=solve('7_min_disparity_after_history',delta_vector,final_rows,final_limits)
            add(final_rows,final_limits,delta_vector,float(delta_vector@sixth)+policy.epsilon_ratio)
            result=sixth
        else:
            floor=np.zeros(size);floor[L]=-1
            add(final_rows,final_limits,floor,-floor_star+policy.epsilon_ratio)

        # Deterministic final tie-break. Every preceding objective stays locked.
        for i,z in enumerate(zs):
            objective=np.zeros(size);objective[i]=-1
            result=solve(f'tie_{z.zone_id}',objective,final_rows,final_limits)
            add(final_rows,final_limits,objective,-float(result[i])+policy.epsilon_volume)

        calculated=ratios(result)
        max_tail_gap=max([max(0.0,policy.kappa_tail*sum(calculated.values())/len(active)-calculated[i]) for i in tails] or [0.0])
        if available == 0:
            status='NO_SUPPLY'
        elif all(z.release_bound==0 for z in zs):
            status='NO_CAPACITY'
        else:
            status='DEGRADED_TAIL' if max_tail_gap>3*policy.epsilon_ratio else 'NEEDS_APPROVAL'
        j_final=float(history_vector@result)+history_constant
        # Verify numerical primal feasibility independently of solver success.
        residual=max([float(row@result-limit) for row,limit in zip(final_rows,final_limits)]+[0.0])
        bound_violation=max([max(lo-result[i],result[i]-hi,0.0) for i,(lo,hi) in enumerate(bounds)] or [0.0])
        if max(residual,bound_violation)>max(policy.epsilon_volume,policy.epsilon_ratio):
            return failed('SOLVER_FAILED','independent primal feasibility check failed')
        return finish(result,status,r_star,delta_star,floor_star,strict_max,j_reference,j_final,
                      reference,max(residual,bound_violation))
    except SolveError as exc:
        return failed('INFEASIBLE' if exc.status==2 else 'SOLVER_FAILED',f'{exc.stage}: {exc}')

def detect_deviation(target_ml, measured_ml, threshold=0.10, *, quality='VALID', zero_flow_tolerance_ml=0.0):
    target=finite_number(target_ml,'target_ml')
    actual=finite_number(measured_ml,'measured_ml')
    threshold=finite_number(threshold,'threshold',maximum=1)
    zero_tolerance=finite_number(zero_flow_tolerance_ml,'zero_flow_tolerance_ml')
    if not isinstance(quality,str) or quality not in {'VALID','STALE','INVALID'}:
        raise ValueError('quality must be VALID/STALE/INVALID')
    if quality != 'VALID':
        return {'status':'DATA_HOLD','deviation_ratio':None,'reoptimization_required':False}
    if target == 0:
        unexpected=actual>zero_tolerance
        return {'status':'UNEXPECTED_FLOW' if unexpected else 'NO_DEVIATION',
                'deviation_ratio':None,'reoptimization_required':False,'review_required':unexpected}
    deviation=(actual-target)/target
    if not np.isfinite(deviation):
        raise ValueError('unsupported deviation numeric scale')
    return {'status':'DEVIATION' if abs(deviation)>threshold else 'NO_DEVIATION',
            'deviation_ratio':deviation,'reoptimization_required':bool(abs(deviation)>threshold)}

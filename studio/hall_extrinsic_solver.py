"""Calibrate Hall→Brush SO(3) from stationary poses in a uniform world field.

    B_world(i) = R_world_from_brush(i) @ R_brush_from_hall @ B_hall(i)

The single static 2-second mean cannot determine mounting orientation. Multiple
diverse static orientations are required. Only numpy is needed.
"""
from __future__ import annotations
import math
import numpy as np

def skew(x):
    x=np.asarray(x,dtype=float)
    return np.array([[0,-x[2],x[1]],[x[2],0,-x[0]],[-x[1],x[0],0]],float)

def exp_so3(v):
    v=np.asarray(v,float)
    t=float(np.linalg.norm(v))
    K=skew(v)
    if t < 1e-10:
        return np.eye(3)+K+0.5*K@K
    return np.eye(3)+math.sin(t)/t*K+(1-math.cos(t))/t**2*(K@K)

def project_so3(R):
    U,_,Vt=np.linalg.svd(np.asarray(R,float))
    M=U@Vt
    if np.linalg.det(M)<0:
        U[:,-1]*=-1
        M=U@Vt
    return M

def is_so3(R,atol=1e-5):
    r=np.asarray(R,float)
    return r.shape==(3,3) and bool(np.isfinite(r).all()) and bool(
        np.allclose(r.T@r,np.eye(3),atol=atol)) and abs(float(np.linalg.det(r))-1)<atol

def rotation_distance_deg(A,B):
    c=float(np.clip((np.trace(np.asarray(A).T@np.asarray(B))-1)/2,-1,1))
    return math.degrees(math.acos(c))

def field_values(rotations, hall, Q):
    R=np.asarray(rotations,float)
    H=np.asarray(hall,float)
    return np.einsum('nij,jk,nk->ni',R,Q,H)

def error_stats(rotations,hall,Q,target=None):
    w=field_values(rotations,hall,Q)
    if target is None:
        target=np.mean(w,axis=0)
    r=w-np.asarray(target,float)
    norms=np.linalg.norm(r,axis=1)
    return dict(world_mean=np.asarray(target,float), rms_mT=float(np.sqrt(np.mean(norms**2))),
                median_mT=float(np.median(norms)), max_mT=float(np.max(norms)),
                errors_mT=norms)

def _linearization(R,H,Q):
    v=(Q@H.T).T
    w=np.einsum("nij,nj->ni",R,v)
    # Local left perturbation Q' = exp([d]) @ Q.
    Ji=np.array([-Ri@skew(vi) for Ri,vi in zip(R,v)],dtype=float)
    return w,Ji

def _refine(R,H,Q0,iterations=90):
    Q=project_so3(Q0)
    damp=1e-4
    for _ in range(iterations):
        world,J=_linearization(R,H,Q)
        # Profile away unknown stationary world field vector.
        target=world.mean(axis=0)
        resid=world-target
        J-=J.mean(axis=0)[None,:,:]
        cost=float(np.mean(np.sum(resid**2,axis=1)))
        mag=np.linalg.norm(resid,axis=1)
        cutoff=max(0.015, float(np.median(mag))*2.5)
        wt=np.minimum(1.0,cutoff/np.maximum(mag,1e-10))
        A=(J*wt[:,None,None]).reshape(-1,3)
        b=(resid*wt[:,None]).reshape(-1)
        hess=A.T@A
        grad=A.T@b
        if float(np.linalg.norm(grad))<1e-13: break
        improved=False
        for _ in range(12):
            step=-np.linalg.solve(hess + damp*np.eye(3),grad)
            if float(np.linalg.norm(step))>0.7: step*=0.7/np.linalg.norm(step)
            Qnew=exp_so3(step)@Q
            cand=field_values(R,H,Qnew)
            val=float(np.mean(np.sum((cand-cand.mean(axis=0))**2,axis=1)))
            if val < cost-1e-13:
                Q=Qnew
                damp=max(damp/3,1e-9)
                improved=True
                break
            damp=min(damp*6,1e8)
        if not improved: break
        if np.linalg.norm(step)<1e-8: break
    return project_so3(Q)

def _excitation(R,H,Q):
    _,J=_linearization(R,H,Q)
    J-=J.mean(axis=0)[None,:,:]
    s=np.linalg.svd(J.reshape(-1,3),compute_uv=False)
    ratio=float(s[-1]/max(s[0],1e-12))
    max_rotation=max(rotation_distance_deg(a,b) for a in R for b in R)
    # Also report observed Hall-vector angular excitation; a weak source with
    # no Hall-vector change is not calibratable even when IMU rotates a lot.
    U=H/np.maximum(np.linalg.norm(H,axis=1,keepdims=True),1e-12)
    max_hall_angle=max(math.degrees(math.acos(float(np.clip(np.dot(a,b),-1,1))))
                       for a in U for b in U)
    return {
        "jacobian_sv":s.tolist(),
        "excitation_ratio":ratio,
        "pose_span_deg":float(max_rotation),
        "hall_vector_span_deg":float(max_hall_angle),
    }

def solve_hall_extrinsic(rotations,hall,seed=None):
    R=np.asarray(rotations,float)
    H=np.asarray(hall,float)
    if R.ndim!=3 or R.shape[1:]!=(3,3) or H.shape!=(len(R),3):
        raise ValueError("Expected N×3×3 brush rotations and N×3 Hall vectors")
    if len(R)<8: raise ValueError("至少需要 8 个不同的静态训练姿态")
    if not all(is_so3(q) for q in R): raise ValueError("PoseMapper 姿态矩阵不满足 SO(3)")
    if not np.isfinite(H).all(): raise ValueError("霍尔数据存在 NaN/Inf")
    norms=np.linalg.norm(H,axis=1)
    if float(np.mean(norms))<0.015:
        raise ValueError("环境磁场太弱，霍尔相对方向难以可靠求解")
    strength_cv=float(np.std(norms)/max(np.mean(norms),1e-12))
    if strength_cv>0.16:
        raise ValueError(f"各姿态磁场模长差异 {strength_cv:.0%} 过大；疑似磁场梯度/磁铁随笔移动/环境变化")
    seed=np.eye(3) if seed is None else project_so3(seed)
    ex_seed=_excitation(R,H,seed)
    if ex_seed["pose_span_deg"]<90:
        raise ValueError(f"姿态覆盖只有 {ex_seed['pose_span_deg']:.1f}°；需要明显的三维旋转覆盖")
    if ex_seed["hall_vector_span_deg"]<55:
        raise ValueError("霍尔方向变化太小；请包含倾斜和绕轴旋转，避免磁铁随笔固定造成的不可观测")
    # Deterministic multi-start: avoid local maxima; no scipy dependency.
    starts=[seed,np.eye(3)]
    axes=np.eye(3)
    for a in axes:
        for ang in (-math.pi, -math.pi/2, math.pi/2):
            starts.append(exp_so3(a*ang)@seed)
    best=None
    for start in starts:
        q=_refine(R,H,start)
        stat=error_stats(R,H,q)
        if best is None or stat["rms_mT"]<best[0]:
            best=(stat["rms_mT"],q,stat)
    rms,Q,stat=best
    ex=_excitation(R,H,Q)
    if ex["excitation_ratio"]<0.13:
        raise ValueError(f"求解退化：可观测性比值仅 {ex['excitation_ratio']:.3f}，需要更多方向的倾斜/翻转")
    baseline=error_stats(R,H,seed)
    result={
        "R_brush_from_hall":Q.tolist(),
        "world_field_mT":stat["world_mean"].tolist(),
        "training_rms_mT":rms,
        "training_median_mT":stat["median_mT"],
        "training_max_mT":stat["max_mT"],
        "baseline_training_rms_mT":baseline["rms_mT"],
        "field_magnitude_mean_mT":float(np.mean(norms)),
        "field_magnitude_cv":strength_cv,
        "excitation":ex,
        "training_count":int(len(R)),
        "fit_method":"so3_multi_start_lm_world_field_constant",
        "assumptions":["stationary uniform ambient field","rigid Hall+IMU mount",
                       "valid continuous relative IMU orientation","negligible Hall hard-iron offset"],
    }
    return result

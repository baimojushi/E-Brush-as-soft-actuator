from __future__ import annotations
import math, numpy as np, cv2

def _unit(v):
    n=float(np.linalg.norm(v));return v/n if n>1e-12 else v

def _angle_deg(a,b,undirected=False):
    a=_unit(a);b=_unit(b);d=float(np.clip(np.dot(a,b),-1,1))
    if undirected:d=abs(d)
    return math.degrees(math.acos(np.clip(d,-1,1)))

def _fit_line_ransac(P, threshold_mm=7.0, iterations=100, seed=12345):
    P=np.asarray(P,float)
    if len(P)<20:return None
    rng=np.random.default_rng(seed);best=None;bestn=0
    for _ in range(iterations):
        i,j=rng.integers(0,len(P),2)
        if i==j:continue
        v=P[j]-P[i];nv=np.linalg.norm(v)
        if nv<1e-6:continue
        v/=nv;d=np.linalg.norm(np.cross(P-P[i],v),axis=1);inside=d<threshold_mm
        n=int(inside.sum())
        if n>bestn:bestn=n;best=inside
    if best is None or bestn<20:return None
    Q=P[best];c=Q.mean(axis=0);_,S,Vt=np.linalg.svd(Q-c,full_matrices=False);v=_unit(Vt[0])
    dist=np.linalg.norm(np.cross(Q-c,v),axis=1)
    proj=(Q-c)@v
    return {"origin":c,"axis":v,"inliers":best,"inlier_ratio":float(bestn/len(P)),
            "line_rms_mm":float(np.sqrt(np.mean(dist**2))),"visible_length_mm":float(proj.max()-proj.min()),
            "eigen_ratio":float(S[0]/max(S[1],1e-9))}

class VisionPoseEstimator:
    def __init__(self, erode_px=1, ransac_mm=7.0, min_points=80):
        self.erode_px=int(erode_px);self.ransac_mm=float(ransac_mm);self.min_points=int(min_points)
        self.prev_axis=None

    def estimate(self, mask, camera, paper_mask=None):
        if mask is None or camera is None:return None
        m=np.asarray(mask,bool)
        if self.erode_px>0:
            k=np.ones((2*self.erode_px+1,2*self.erode_px+1),np.uint8)
            m=cv2.erode(m.astype(np.uint8),k)>0
        pc=camera.get("pointcloud")
        depth=camera.get("depth_mm")
        if pc is None:return {"ok":False,"reason":"no pointcloud"}
        pc=np.asarray(pc)
        H,W=m.shape
        if pc.ndim==2 and pc.shape[1]==3 and len(pc)==H*W:pc=pc.reshape(H,W,3)
        if pc.ndim!=3 or pc.shape[:2]!=(H,W):
            return {"ok":False,"reason":f"pointcloud shape {pc.shape} != mask {(H,W)}"}
        pts=pc[m]
        finite=np.isfinite(pts).all(axis=1)&(np.linalg.norm(pts,axis=1)>1e-6)
        pts=pts[finite]
        depth_valid_ratio=float(len(pts)/max(1,int(m.sum())))
        if len(pts)<self.min_points:return {"ok":False,"reason":"too few valid 3D points","point_count":int(len(pts)),"depth_valid_ratio":depth_valid_ratio}
        # SDK point cloud may be metres or millimetres. RANSAC threshold is specified in mm.
        scale=1000.0 if np.nanmedian(np.linalg.norm(pts,axis=1))<10 else 1.0
        P=pts*scale
        fit=_fit_line_ransac(P,self.ransac_mm)
        if fit is None:return {"ok":False,"reason":"RANSAC failed","point_count":int(len(P)),"depth_valid_ratio":depth_valid_ratio}
        axis=fit["axis"]
        # Resolve PCA/RANSAC ± sign temporally. Absolute tip/tail direction is deferred until paper-plane direction is available.
        if self.prev_axis is not None and np.dot(axis,self.prev_axis)<0:axis=-axis
        self.prev_axis=axis.copy()
        q=float(np.clip(fit["inlier_ratio"]*min(1,fit["visible_length_mm"]/100.0)*min(1,fit["eigen_ratio"]/8.0),0,1))
        result={"ok":True,"axis_camera":axis,"origin_camera_mm":fit["origin"],"point_count":int(len(P)),
                "depth_valid_ratio":depth_valid_ratio,"line_rms_mm":fit["line_rms_mm"],"inlier_ratio":fit["inlier_ratio"],
                "visible_length_mm":fit["visible_length_mm"],"eigen_ratio":fit["eigen_ratio"],"vision_quality":q,
                "mask":m}

        # 纸面平面估计（如果提供了 paper_mask）
        if paper_mask is not None and np.any(np.asarray(paper_mask,bool)):
            paper_plane=self._estimate_paper_plane(paper_mask,camera)
            if paper_plane is not None:
                result["paper_plane"]=paper_plane
                result["paper_center_camera_mm"]=[float(v) for v in paper_plane["p0"]]
                result["world_frame"]={
                    "z_axis_camera":paper_plane["n"],
                    "origin_camera_mm":[float(v) for v in paper_plane["p0"]],
                }
        return result

    def _estimate_paper_plane(self, paper_mask, camera):
        """用 paper_mask + 相机深度估计纸面平面（RANSAC）。"""
        pc=camera.get("pointcloud")
        if pc is None:return None
        pc=np.asarray(pc)
        H,W=paper_mask.shape
        if pc.ndim==2 and pc.shape[1]==3 and len(pc)==H*W:pc=pc.reshape(H,W,3)
        if pc.ndim!=3 or pc.shape[:2]!=(H,W):return None
        p_mask=np.asarray(paper_mask,bool)
        p_pts=pc[p_mask]
        finite=np.isfinite(p_pts).all(axis=1)&(np.linalg.norm(p_pts,axis=1)>1e-6)
        p_pts=p_pts[finite]
        if len(p_pts)<200:return None
        scale=1000.0 if np.nanmedian(np.linalg.norm(p_pts,axis=1))<10 else 1.0
        P=p_pts*scale  # 转为 mm

        # RANSAC 平面拟合（3 点采样）
        rng=np.random.default_rng(42)
        threshold=5.0  # mm
        best_n=None;best_count=0;best_inliers=None
        for _ in range(200):
            idx=rng.integers(0,len(P),3,replace=False)
            p0,p1,p2=P[idx[0]],P[idx[1]],P[idx[2]]
            n=np.cross(p1-p0,p2-p0)
            norm_n=float(np.linalg.norm(n))
            if norm_n<1e-9:continue
            n=n/norm_n
            d=float(np.dot(n,p0))
            dists=np.abs(P@n-d)
            inliers=dists<threshold
            count=int(np.count_nonzero(inliers))
            if count>best_count:
                best_count=count;best_n=n;best_inliers=inliers
        if best_n is None or best_count<50:return None
        p0=np.median(P[best_inliers],axis=0)
        rms=float(np.sqrt(np.mean((P[best_inliers]@best_n-p0@best_n)**2)))
        if best_n[2]<0:best_n=-best_n
        return {"n":[float(v) for v in best_n],"p0":[float(v) for v in p0],
                "inlier_ratio":float(best_count/len(P)),"rms_mm":rms,"inliers":best_count}

def compare_axes(imu_axis_board, vision_axis_camera, R_board_from_camera=None):
    """If camera->board is unknown, comparison is intentionally unavailable rather than silently mixing frames."""
    if imu_axis_board is None or vision_axis_camera is None or R_board_from_camera is None:return None
    v=_unit(np.asarray(R_board_from_camera,float)@np.asarray(vision_axis_camera,float))
    i=_unit(np.asarray(imu_axis_board,float))
    # shaft geometry is initially undirected until visual tip/tail is resolved
    return {"vision_axis_board":v,"shaft_error_deg":_angle_deg(i,v,undirected=True)}

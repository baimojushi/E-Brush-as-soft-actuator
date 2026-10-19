"""Conservative RGB-D local tracking between low-frequency SAM3 confirmations.

This is a local visual motion tracker. Every interpolated mask is marked as
PREDICTED and is never automatically promoted to ground-truth training labels.
"""
from __future__ import annotations

import math
import uuid

import cv2
import numpy as np

from .types import CameraCalibration, RGBDFrame, SegmentationMask, TrackOutput
from .geometry import analyze_depth


class RGBDLocalTracker:
    def __init__(self, calibration: CameraCalibration, max_prediction_frames: int = 12,
                 max_gap_s: float = .40, min_features: int = 6, max_motion_px: float = 40.0):
        self.calib=calibration
        self.max_prediction_frames=max_prediction_frames
        self.max_gap_ns=int(max_gap_s * 1e9)
        self.min_features=min_features
        self.max_motion_px=max_motion_px
        self.track_id=None
        self.last_mask=None
        self.last_gray=None
        self.last_frame_id=None
        self.last_time_ns=None
        self.last_seen_ns=None
        self.last_source_id=None
        self.age_frames=0
        self.confidence=0.0
        self.last_xyz=None

    def reset(self):
        self.track_id=None
        self.last_mask=None
        self.last_gray=None
        self.last_frame_id=None
        self.last_time_ns=None
        self.last_seen_ns=None
        self.last_source_id=None
        self.age_frames=0
        self.confidence=0.0
        self.last_xyz=None

    @staticmethod
    def _gray(image):
        return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)

    def accept_verified_mask(self, frame: RGBDFrame, annotation: SegmentationMask):
        """Seed only a candidate mask on exactly the same RGB-D frame."""
        if frame.camera_frame_id != annotation.camera_frame_id:
            return False
        observation=analyze_depth(frame,annotation,self.calib)
        if observation.status != "CANDIDATE" or annotation.score < .1:
            return False
        if self.last_frame_id is not None and frame.camera_frame_id < self.last_frame_id:
            # Delayed SAM3 result may update archival annotations, not current state.
            return False
        if self.track_id is None:
            self.track_id="brush-"+uuid.uuid4().hex[:12]
        self.last_gray=self._gray(frame.color_rgb)
        self.last_mask=annotation.mask.copy()
        self.last_frame_id=frame.camera_frame_id
        self.last_time_ns=frame.capture_host_ns
        self.last_seen_ns=frame.capture_host_ns
        self.last_source_id=frame.camera_frame_id
        self.age_frames=0
        self.confidence=float(min(1.0, max(.05,annotation.score)))
        self.last_xyz=observation.camera_center_m
        return True

    def _output(self, frame, state, reason):
        return TrackOutput(frame.camera_frame_id,frame.capture_host_ns,
                           self.track_id,state,round(float(self.confidence),5),
                           self.last_xyz,self.last_source_id,self.age_frames,reason)

    def update(self, frame: RGBDFrame) -> TrackOutput:
        frame.validate(self.calib)
        if self.last_frame_id is None:
            return self._output(frame,"UNKNOWN","NO_SEMANTIC_SEED")
        if frame.camera_frame_id <= self.last_frame_id:
            return self._output(frame,"LOST","NON_MONOTONIC_FRAME")
        gap=frame.capture_host_ns-self.last_time_ns
        if gap<=0 or gap>self.max_gap_ns or self.age_frames>=self.max_prediction_frames:
            self.reset()
            return self._output(frame,"LOST","TRACK_TIME_OR_AGE_LIMIT")

        new_gray=self._gray(frame.color_rgb)
        selection=(self.last_mask.astype(np.uint8)*255)
        keypts=cv2.goodFeaturesToTrack(self.last_gray,mask=selection,
                                      maxCorners=80,qualityLevel=.01,minDistance=3)
        if keypts is None or len(keypts)<self.min_features:
            self.reset()
            return self._output(frame,"LOST","INSUFFICIENT_VISUAL_FEATURES")
        new_pts, status, err=cv2.calcOpticalFlowPyrLK(
            self.last_gray,new_gray,keypts,None,winSize=(17,17),maxLevel=2)
        if new_pts is None or status is None:
            self.reset()
            return self._output(frame,"LOST","OPTICAL_FLOW_FAILED")
        ok=status.reshape(-1).astype(bool)
        if np.count_nonzero(ok)<self.min_features:
            self.reset()
            return self._output(frame,"LOST","TOO_FEW_MATCHED_FEATURES")
        delta=(new_pts-keypts).reshape(-1,2)[ok]
        shift=np.median(delta,axis=0)
        scatter=np.linalg.norm(delta-shift,axis=1)
        inliers=scatter<3.5
        if np.count_nonzero(inliers)<self.min_features or np.linalg.norm(shift)>self.max_motion_px:
            self.reset()
            return self._output(frame,"LOST","INCONSISTENT_OR_LARGE_MOTION")
        shift=np.median(delta[inliers],axis=0)
        M=np.float32([[1,0,float(shift[0])],[0,1,float(shift[1])]])
        warped=cv2.warpAffine(self.last_mask.astype(np.uint8), M,
                             (self.calib.width,self.calib.height),
                             flags=cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT)>0
        if np.count_nonzero(warped)<30:
            self.reset()
            return self._output(frame,"LOST","EMPTY_WARPED_MASK")
        pseudo=SegmentationMask(frame.camera_frame_id,0,warped,
                                score=min(.99,self.confidence),
                                source_clip_id="local_optical_flow",
                                clip_frame_index=self.age_frames+1)
        geometry=analyze_depth(frame,pseudo,self.calib)
        if geometry.status != "CANDIDATE":
            self.reset()
            return self._output(frame,"LOST","DEPTH_VALIDATION_FAILED")
        self.age_frames+=1
        self.confidence=float(max(0.0,self.confidence*.92*
                             (np.count_nonzero(inliers)/len(delta))))
        self.last_mask=warped
        self.last_gray=new_gray
        self.last_frame_id=frame.camera_frame_id
        self.last_time_ns=frame.capture_host_ns
        self.last_xyz=geometry.camera_center_m
        return self._output(frame,"PREDICTED","OPTICAL_FLOW_DEPTH_CANDIDATE")

"""Asynchronous capture/annotation coordination; camera callbacks never wait for SAM3."""
from __future__ import annotations

from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import queue
import threading
import time
from typing import Callable

from .dataset import EpisodeVisionWriter
from .sam3_client import Sam3V4BatchClient, best_unambiguous_brush
from .sync import BrushTimestampBuffer
from .tracking import RGBDLocalTracker
from .types import RGBDFrame, CameraCalibration, TrackOutput

log=logging.getLogger("ebrush.rgbd")


class RecorderBackpressure(RuntimeError):
    """The caller must stop/slow capture or log an incomplete frame sequence."""


class RGBDSemanticPipeline:
    def __init__(self, calibration: CameraCalibration,
                 writer: EpisodeVisionWriter,
                 sam3_client: Sam3V4BatchClient,
                 clip_frames: int = 5, request_interval_s: float = 5.0,
                 max_ingest_queue: int = 32, max_replay_frames: int = 15,
                 enable_sam3: bool = True, pose_mapper=None):
        if clip_frames<1 or clip_frames>sam3_client.cfg.max_clip_frames:
            raise ValueError("clip_frames must fit SAM3 client frame cap")
        if request_interval_s<=0 or max_ingest_queue<1:
            raise ValueError("Invalid request interval/ingestion capacity")
        if calibration.sha256 != writer.calibration.sha256:
            raise ValueError("Writer and pipeline calibrations differ")
        self.calib=calibration
        self.enable_sam3=bool(enable_sam3)
        self.pose_mapper=pose_mapper
        self.writer=writer
        self.sam3=sam3_client
        self.clip_frames=clip_frames
        self.request_interval_s=request_interval_s
        self.max_replay_frames=max_replay_frames
        self.brush=BrushTimestampBuffer()
        self.ingest=queue.Queue(maxsize=max_ingest_queue)
        self.tracker=RGBDLocalTracker(calibration)
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix="SAM3Meta")
        self.stop_signal=threading.Event()
        self.worker=threading.Thread(target=self._run,name="RGBDRecorder",daemon=True)
        self.recent=deque(maxlen=max(clip_frames,max_replay_frames)+5)
        self._job=None
        self._last_job_time=-1e9
        self._guard=threading.Lock()
        self._latest_track=None
        self._stats={"frames_received":0,"frames_archived":0,"frames_rejected":0,
                     "annotation_jobs_sent":0,"annotation_jobs_failed":0,
                     "annotations_written":0,"live_reacquisitions":0,
                     "last_sam3_error":None,
                     "recorder_fatal_error":None}
        self._last_accepted_id=-1
        self._last_accepted_ts=0
        self._started=False

    def start(self):
        with self._guard:
            if self._started:
                return
            self._started=True
            self.worker.start()

    def on_brush_frame(self, brush_sample:dict):
        """Directly accepts BrushSerial.frames queue dictionaries."""
        self.brush.push(brush_sample)

    def on_rgbd_frame(self, frame:RGBDFrame):
        """Nonblocking admission. Rejected frames never appear as valid training data."""
        frame.validate(self.calib)
        with self._guard:
            if not self._started or self.stop_signal.is_set():
                raise RuntimeError("Pipeline stopped or failed")
            if frame.camera_frame_id<=self._last_accepted_id:
                raise ValueError("Nonmonotonic camera frame ID")
            if frame.capture_host_ns<=self._last_accepted_ts:
                raise ValueError("Nonmonotonic camera timestamp")
            # Make a defensive copy so the camera SDK may reuse its input buffers.
            cloned=RGBDFrame(
                frame.camera_frame_id,frame.capture_host_ns,
                frame.color_rgb.copy(),frame.depth_raw.copy(),
                frame.calibration_id,frame.camera_device_ts_us,
                frame.camera_clock_synced
            )
            try:
                self.ingest.put_nowait(cloned)
            except queue.Full as exc:
                self._stats["frames_rejected"]+=1
                # Caller owns the decision to stop acquisition on recorder saturation.
                raise RecorderBackpressure(
                    "RGB-D recorder queue full. Do not silently drop training frames."
                ) from exc
            self._last_accepted_id=frame.camera_frame_id
            self._last_accepted_ts=frame.capture_host_ns
            self._stats["frames_received"]+=1

    def status(self):
        with self._guard:
            s=dict(self._stats)
            s["latest_track"]=(
                dict(vars(self._latest_track)) if self._latest_track is not None else None
            )
            s["recorder_queue_size"]=self.ingest.qsize()
            s["sam3_busy"]=bool(self._job is not None and not self._job.done())
            return s

    def _run(self):
        while not self.stop_signal.is_set() or not self.ingest.empty() or self._job is not None:
            self._drain_sam3()
            try:
                frame=self.ingest.get(timeout=.10)
            except queue.Empty:
                continue
            try:
                matched=self.brush.match(frame.capture_host_ns)
                mapped_pose=None
                if self.pose_mapper is not None and matched.synced and matched.payload:
                    try:
                        mapped_pose=self.pose_mapper.map_frame(matched.payload)
                    except (ValueError,RuntimeError,KeyError) as pose_err:
                        # Lost yaw reference or bad sensor data remains REVIEW.
                        mapped_pose={"reference_locked":False,
                                     "reason":str(pose_err)}
                self.writer.store_raw_frame(frame,matched,mapped_pose)
                track=self.tracker.update(frame)
                self.recent.append(frame)
                with self._guard:
                    self._latest_track=track
                    self._stats["frames_archived"]+=1
                self._schedule_if_due()
            except Exception as exc:
                log.exception("RGB-D worker failed for frame_id=%s",frame.camera_frame_id)
                with self._guard:
                    self._stats["recorder_fatal_error"]=f"{type(exc).__name__}: {exc}"
                # Archival errors make the episode incomplete. Stop admitting
                # new frames rather than silently presenting gaps as valid.
                self.stop_signal.set()
            finally:
                self.ingest.task_done()
        self._drain_sam3()

    def _schedule_if_due(self):
        if not self.enable_sam3 or self.stop_signal.is_set():
            return
        if len(self.recent)<self.clip_frames:
            return
        if self._job is not None:
            return
        if time.monotonic()-self._last_job_time<self.request_interval_s:
            return
        frames=list(self.recent)[-self.clip_frames:]
        self._job=self.executor.submit(self.sam3.segment_clip,frames)
        self._last_job_time=time.monotonic()
        with self._guard:
            self._stats["annotation_jobs_sent"]+=1

    def _drain_sam3(self):
        job=self._job
        if job is None or not job.done():
            return
        # Keep the future present until ALL mask files and review metadata have
        # been archived. flush() must never see an empty job while we are writing.
        try:
            result=job.result()
            best_recent=None
            for source_fid in result.camera_frame_ids:
                archived=self.writer.read_frame(source_fid)
                annotated=[]
                for mask in result.masks.get(source_fid,[]):
                    self.writer.store_mask(archived,mask,
                                           model_revision=result.backend)
                    annotated.append(mask)
                    with self._guard:
                        self._stats["annotations_written"]+=1
                best,reason=best_unambiguous_brush(annotated)
                if best is not None:
                    best_recent=(archived,best)
            if best_recent is not None:
                self._reseed_if_recent(*best_recent)
        except Exception as exc:
            with self._guard:
                self._stats["annotation_jobs_failed"]+=1
                self._stats["last_sam3_error"]=f"{type(exc).__name__}: {exc}"
            log.exception("SAM3 asynchronous annotation failure")
        finally:
            self._job=None

    def _reseed_if_recent(self, seed_frame, seed_mask):
        """Rewind a short local visual window; long-latency outputs remain archival only."""
        if not self.recent:
            return
        latest=self.recent[-1]
        lag=latest.camera_frame_id-seed_frame.camera_frame_id
        if lag<0 or lag>self.max_replay_frames:
            return
        # Require an uninterrupted copy of every source frame, with increasing timestamps.
        sequence=[f for f in self.recent if f.camera_frame_id>=seed_frame.camera_frame_id]
        if [f.camera_frame_id for f in sequence] != list(
            range(seed_frame.camera_frame_id,latest.camera_frame_id+1)
        ):
            return
        candidate=RGBDLocalTracker(self.calib)
        if not candidate.accept_verified_mask(seed_frame,seed_mask):
            return
        latest_output=TrackOutput(
            seed_frame.camera_frame_id,seed_frame.capture_host_ns,
            candidate.track_id,"OBSERVED",candidate.confidence,
            candidate.last_xyz,seed_frame.camera_frame_id,0,"SAM3_SEMANTIC_CANDIDATE"
        )
        for frame in sequence[1:]:
            latest_output=candidate.update(frame)
            if latest_output.state=="LOST":
                return
        # Preserve identity only if independent 2D/3D association supports it.
        old=self.tracker
        if old.track_id is not None and old.last_xyz is not None and candidate.last_xyz is not None:
            import numpy as np
            distance=float(np.linalg.norm(
                np.asarray(old.last_xyz)-np.asarray(candidate.last_xyz)))
            intersection=0
            union=0
            if old.last_mask is not None and candidate.last_mask is not None:
                intersection=int(np.count_nonzero(old.last_mask & candidate.last_mask))
                union=int(np.count_nonzero(old.last_mask | candidate.last_mask))
            if distance<.08 and union>0 and intersection/union>.35:
                candidate.track_id=old.track_id
            elif old.last_frame_id is not None:
                # Ambiguous association: archive new semantic result, preserve
                # current identity until a future observation resolves it.
                return
        self.tracker=candidate
        with self._guard:
            self._latest_track=latest_output
            self._stats["live_reacquisitions"]+=1

    def flush(self, timeout_s:float = 240):
        """Wait for archival processing and the one active model job."""
        deadline=time.monotonic()+timeout_s
        while time.monotonic()<deadline:
            with self._guard:
                fatal=self._stats["recorder_fatal_error"]
            if fatal:
                raise RuntimeError(f"Recorder failed; episode incomplete: {fatal}")
            if self.ingest.unfinished_tasks==0:
                # Only the recorder worker may drain a completed future.
                if self._job is None:
                    return True
            time.sleep(.05)
        return False

    def close(self, timeout_s:float = 240):
        self.stop_signal.set()
        if not self._started:
            self.executor.shutdown(wait=False,cancel_futures=True)
            return
        finished=self.flush(timeout_s)
        self.worker.join(timeout=max(0.,min(5.,timeout_s)))
        if finished:
            self.executor.shutdown(wait=True)
        else:
            # Model calls cannot be forcefully cancelled after being dispatched;
            # caller should keep process alive until HTTP timeout, or supervised shutdown.
            self.executor.shutdown(wait=False,cancel_futures=True)
            raise TimeoutError("SAM3/recorder did not drain; preserve process until worker ends")

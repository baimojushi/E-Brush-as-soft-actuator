"""E-Brush control scopes and recording lifecycle, without any hardware."""
import json
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from acquisition_session import AcquisitionSession
from agent_control import AgentControl


class Brush:
    def __init__(self):
        self.path = None
        self.active = False
        self.frames = 250
    def status(self):
        return {'streaming_data': True, 'connected': True}
    def start_recording(self, path):
        self.path = Path(path)
        self.active = True
    def recording_status(self):
        return {'active': self.active, 'connected': True, 'frames': self.frames, 'interrupted': False}
    def stop_recording(self):
        self.path.write_bytes(b'BRUSH_BRL1\n' + b'x' * 150)
        self.active = False
        return {'frames': self.frames, 'interrupted': False}


def http_call(port, endpoint, token=None, method='GET', body=None):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = json.dumps(body).encode('utf-8') if body is not None else None
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(f'http://127.0.0.1:{port}{endpoint}',
                                 headers=headers, data=data, method=method)
    with urllib.request.urlopen(req, timeout=2) as resp:
        return json.loads(resp.read())


def test_auth_capture_file_manifest_and_event(tmp_path):
    brush = Brush()
    ctl = AgentControl(tmp_path / 'runtime', port=0)
    ctl.session = AcquisitionSession(brush, tmp_path)
    ctl.start()
    try:
        port = ctl.server.server_port
        read = ctl.tokens['read_token']
        write = ctl.tokens['write_token']
        assert http_call(port, '/v1/health')['port_role'] == 'acquisition_only'
        with pytest.raises(urllib.error.HTTPError) as info:
            http_call(port, '/v1/state')
        assert info.value.code == 401
        assert http_call(port, '/v1/state', read)['meta']['schema'].endswith('v2')
        wid = '0123456789abcdef'
        with pytest.raises(urllib.error.HTTPError) as info:
            http_call(port, '/v1/acquisition/start', read, 'POST',
                      {'workflow_id':wid,'recording_index':1})
        assert info.value.code == 403
        start = http_call(port, '/v1/acquisition/start', write, 'POST',
                          {'workflow_id':wid,'recording_index':1})
        assert start['state'] == 'recording'
        assert http_call(port, '/v1/acquisition/status', read)['frames'] == 250
        completed = http_call(port, '/v1/acquisition/stop', write, 'POST', {'workflow_id':wid})
        assert completed['state'] == 'saved'
        assert Path(completed['manifest']).is_file()
        manifest = json.loads(Path(completed['manifest']).read_text())
        assert manifest['source']['size_bytes'] == Path(completed['path']).stat().st_size
        assert completed['quality_gate'] == 'unmeasured'
        event = {'workflow_id': wid, 'sequence':1, 'stage':'training',
                 'status':'running','message':'正在训练','progress':.75}
        assert http_call(port, '/v1/workflow/event', write, 'POST', event)['accepted']
        assert http_call(port, '/v1/workflow/event', write, 'POST', event)['duplicate']
        rows = http_call(port, '/v1/workflow/events?after=0', read)['events']
        assert len(rows)==1 and rows[0]['stage']=='training'
    finally:
        ctl.close()


def test_capture_quality_gate_and_bad_identifiers(tmp_path):
    ctl = AgentControl(tmp_path / 'runtime')
    brush = Brush()
    session = AcquisitionSession(brush, tmp_path,
        quality_provider=lambda: {'host_receive_hz': 192.0, 'frame_gap_percent_window': 12.0})
    with pytest.raises(ValueError):
        session.start(workflow_id='../unsafe', recording_index=1)
    session.start(workflow_id='0123456789abcdef', recording_index=1)
    result = session.stop(workflow_id='0123456789abcdef')
    assert result['state'] == 'failed'
    assert result['quality_gate'] == 'rejected'
    assert result['manifest'] is None
    assert Path(result['path']).is_file()


def test_capture_watchdog_closes_orphan_recording(tmp_path):
    import time
    brush = Brush()
    session = AcquisitionSession(brush, tmp_path)
    started = session.start(workflow_id='a' * 32, recording_index=1,
                            max_duration_seconds=2)
    assert started['max_duration_seconds'] == 2
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline and session.status().get('active'):
        time.sleep(0.04)
    result = session.status()
    assert result['state'] == 'aborted'
    assert '安全时限' in result['reason']
    assert Path(result['path']).is_file()
    assert result['manifest'] is None

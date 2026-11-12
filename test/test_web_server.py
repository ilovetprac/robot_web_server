"""
Verification test for the web server layer.

We stub out the ROS-specific modules (rclpy, sensor_msgs, cv_bridge,
ament_index_python) so we can import and exercise the pure web logic
(build_app + FrameBuffer) WITHOUT a ROS install. This proves the HTTP/REST
endpoints and MJPEG framing are correct.

Run (from repo root, inside the venv):
    python -m pytest test/test_web_server.py -v
"""

import sys
import types
from pathlib import Path

# --- Stub the ROS / native modules that aren't present off-robot ----------
for name in ['rclpy', 'rclpy.node', 'sensor_msgs', 'sensor_msgs.msg',
             'cv_bridge', 'ament_index_python', 'ament_index_python.packages']:
    sys.modules.setdefault(name, types.ModuleType(name))

# Minimal attributes the module touches at import time.
sys.modules['rclpy.node'].Node = object
sys.modules['sensor_msgs.msg'].Image = object


class _FakeBridge:
    def imgmsg_to_cv2(self, *a, **k):
        raise NotImplementedError


sys.modules['cv_bridge'].CvBridge = _FakeBridge
sys.modules['ament_index_python.packages'].get_package_share_directory = (
    lambda pkg: '/nonexistent'
)

# cv2 is needed by web_server at import; stub it too.
cv2_stub = types.ModuleType('cv2')
cv2_stub.IMWRITE_JPEG_QUALITY = 1
sys.modules.setdefault('cv2', cv2_stub)

# Make the package importable.
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient  # noqa: E402
from robot_web_server.web_server import build_app, FrameBuffer  # noqa: E402


# A minimal set of bytes that starts/ends like a JPEG. The web server treats
# frames as opaque bytes, so exact validity doesn't matter for these tests;
# we only assert the bytes round-trip through the endpoints unchanged.
TINY_JPEG = b'\xff\xd8\xff' + b'ROBOT_WEB_SERVER_TEST_FRAME' + b'\xff\xd9'


def make_client(with_frame=True):
    fb = FrameBuffer()
    if with_frame:
        fb.update(TINY_JPEG)

    class FakeNode:
        def get_name(self):
            return 'web_server'

    app = build_app(fb, FakeNode(), Path('/nonexistent/web'))
    return TestClient(app), fb


def test_index_fallback_served():
    client, _ = make_client()
    r = client.get('/')
    assert r.status_code == 200
    assert '/stream' in r.text  # fallback page references the stream


def test_api_info_lists_endpoints():
    client, _ = make_client()
    r = client.get('/api/info')
    assert r.status_code == 200
    data = r.json()
    assert 'GET /stream' in data['endpoints']
    assert 'GET /api/status' in data['endpoints']


def test_api_status_reports_camera_connected():
    client, _ = make_client(with_frame=True)
    r = client.get('/api/status')
    assert r.status_code == 200
    data = r.json()
    assert data['service'] == 'robot_web_server'
    assert data['camera_connected'] is True
    assert data['frames_received'] == 1


def test_api_status_no_camera():
    client, _ = make_client(with_frame=False)
    r = client.get('/api/status')
    assert r.status_code == 200
    assert r.json()['camera_connected'] is False


def test_snapshot_returns_jpeg():
    client, _ = make_client(with_frame=True)
    r = client.get('/api/snapshot')
    assert r.status_code == 200
    assert r.headers['content-type'] == 'image/jpeg'
    assert r.content == TINY_JPEG


def test_snapshot_503_when_no_frame():
    client, _ = make_client(with_frame=False)
    r = client.get('/api/snapshot')
    assert r.status_code == 503


def test_stream_is_multipart_mjpeg():
    client, _ = make_client(with_frame=True)
    # Stream is infinite; read just enough to confirm framing, then close.
    with client.stream('GET', '/stream') as r:
        assert r.status_code == 200
        ctype = r.headers['content-type']
        assert 'multipart/x-mixed-replace' in ctype
        assert 'boundary=frame' in ctype
        chunk = next(r.iter_bytes())
        assert b'--frame' in chunk
        assert b'Content-Type: image/jpeg' in chunk
        assert TINY_JPEG in chunk

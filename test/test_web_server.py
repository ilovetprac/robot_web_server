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
import tempfile
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
from robot_web_server.web_server import (  # noqa: E402
    build_app, FrameBuffer, LoginGuard,
)


# A minimal set of bytes that starts/ends like a JPEG. The web server treats
# frames as opaque bytes, so exact validity doesn't matter for these tests;
# we only assert the bytes round-trip through the endpoints unchanged.
TINY_JPEG = b'\xff\xd8\xff' + b'ROBOT_WEB_SERVER_TEST_FRAME' + b'\xff\xd9'


USERNAME = 'tprac'
PASSWORD = 'tprac@123'


def make_client(with_frame=True, username='', password='', max_attempts=2):
    fb = FrameBuffer()
    if with_frame:
        fb.update(TINY_JPEG)

    class FakeNode:
        def get_name(self):
            return 'web_server'

    guard = None
    if password:
        # Each client gets its own throwaway blocklist file.
        tmp = tempfile.NamedTemporaryFile(
            suffix='.json', delete=False, prefix='blocklist_'
        )
        tmp.close()
        # Start from an empty blocklist (NamedTemporaryFile is 0 bytes -> ok).
        guard = LoginGuard(
            max_attempts=max_attempts,
            blocklist_file=Path(tmp.name),
            trust_proxy_header=True,
            logger=None,
        )

    app = build_app(
        fb, FakeNode(), Path('/nonexistent/web'),
        username=username, password=password, guard=guard,
    )
    return TestClient(app), fb, guard


def test_index_fallback_served():
    client, _, _guard = make_client()
    r = client.get('/')
    assert r.status_code == 200
    assert '/stream' in r.text  # fallback page references the stream


def test_api_info_lists_endpoints():
    client, _, guard = make_client()
    r = client.get('/api/info')
    assert r.status_code == 200
    data = r.json()
    assert 'GET /stream' in data['endpoints']
    assert 'GET /api/status' in data['endpoints']


def test_api_status_reports_camera_connected():
    client, _, guard = make_client(with_frame=True)
    r = client.get('/api/status')
    assert r.status_code == 200
    data = r.json()
    assert data['service'] == 'robot_web_server'
    assert data['camera_connected'] is True
    assert data['frames_received'] == 1


def test_api_status_no_camera():
    client, _, guard = make_client(with_frame=False)
    r = client.get('/api/status')
    assert r.status_code == 200
    assert r.json()['camera_connected'] is False


def test_snapshot_returns_jpeg():
    client, _, guard = make_client(with_frame=True)
    r = client.get('/api/snapshot')
    assert r.status_code == 200
    assert r.headers['content-type'] == 'image/jpeg'
    assert r.content == TINY_JPEG


def test_snapshot_503_when_no_frame():
    client, _, guard = make_client(with_frame=False)
    r = client.get('/api/snapshot')
    assert r.status_code == 503


def test_stream_is_multipart_mjpeg():
    client, _, guard = make_client(with_frame=True)
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


# --- Login protection (HTTP Basic Auth: username + password) --------------

def test_no_password_means_open():
    """With no password configured, all routes are reachable without auth."""
    client, _, guard = make_client(with_frame=True, username='', password='')
    assert client.get('/').status_code == 200
    assert client.get('/api/status').status_code == 200


def test_protected_routes_reject_without_credentials():
    """With a password set, requests lacking credentials get 401."""
    client, _, guard = make_client(with_frame=True, username=USERNAME, password=PASSWORD)
    for path in ['/', '/api/status', '/api/snapshot', '/api/info']:
        r = client.get(path)
        assert r.status_code == 401, path
        # The WWW-Authenticate header makes the browser show the login popup.
        assert r.headers.get('www-authenticate') == 'Basic', path


def test_wrong_password_rejected():
    client, _, guard = make_client(with_frame=True, username=USERNAME, password=PASSWORD)
    r = client.get('/api/status', auth=(USERNAME, 'wrong-password'))
    assert r.status_code == 401


def test_wrong_username_rejected():
    """Correct password but wrong username must be rejected."""
    client, _, guard = make_client(with_frame=True, username=USERNAME, password=PASSWORD)
    r = client.get('/api/status', auth=('not-tprac', PASSWORD))
    assert r.status_code == 401


def test_correct_username_and_password_allowed():
    """Only the exact username AND password unlock access."""
    client, _, guard = make_client(with_frame=True, username=USERNAME, password=PASSWORD)
    r = client.get('/api/status', auth=(USERNAME, PASSWORD))
    assert r.status_code == 200
    assert r.json()['camera_connected'] is True


def test_correct_credentials_unlock_stream():
    client, _, guard = make_client(with_frame=True, username=USERNAME, password=PASSWORD)
    with client.stream('GET', '/stream', auth=(USERNAME, PASSWORD)) as r:
        assert r.status_code == 200
        assert 'multipart/x-mixed-replace' in r.headers['content-type']


# --- Brute-force protection (permanent IP block after N failures) ---------

def test_block_after_max_attempts():
    """After `max_attempts` wrong logins, the IP is blocked with 429."""
    client, _, guard = make_client(
        with_frame=True, username=USERNAME, password=PASSWORD, max_attempts=2,
    )
    # Attempt 1: wrong -> 401
    assert client.get('/api/status', auth=('x', 'bad')).status_code == 401
    # Attempt 2: wrong -> trips the limit -> 429
    assert client.get('/api/status', auth=('x', 'bad')).status_code == 429
    # Now permanently blocked.
    assert guard.is_blocked('testclient')


def test_blocked_ip_rejected_even_with_correct_password():
    """Once blocked, even the correct password is refused (429, not 200)."""
    client, _, guard = make_client(
        with_frame=True, username=USERNAME, password=PASSWORD, max_attempts=2,
    )
    client.get('/api/status', auth=('x', 'bad'))
    client.get('/api/status', auth=('x', 'bad'))  # now blocked
    r = client.get('/api/status', auth=(USERNAME, PASSWORD))
    assert r.status_code == 429


def test_success_resets_failure_counter():
    """A correct login clears prior failures so they don't accumulate."""
    client, _, guard = make_client(
        with_frame=True, username=USERNAME, password=PASSWORD, max_attempts=2,
    )
    assert client.get('/api/status', auth=('x', 'bad')).status_code == 401  # 1 fail
    assert client.get('/api/status', auth=(USERNAME, PASSWORD)).status_code == 200  # reset
    # One more failure should NOT block yet (counter was reset to 0).
    assert client.get('/api/status', auth=('x', 'bad')).status_code == 401
    assert not guard.is_blocked('testclient')


def test_block_persists_to_file():
    """The blocklist is written to disk so bans survive a restart."""
    client, _, guard = make_client(
        with_frame=True, username=USERNAME, password=PASSWORD, max_attempts=1,
    )
    # max_attempts=1 -> first wrong attempt blocks immediately.
    assert client.get('/api/status', auth=('x', 'bad')).status_code == 429
    import json
    data = json.loads(guard.blocklist_file.read_text())
    assert 'testclient' in data['blocked']


def test_unblock_clears_the_ban():
    """unblock() removes an IP so it can log in again."""
    client, _, guard = make_client(
        with_frame=True, username=USERNAME, password=PASSWORD, max_attempts=1,
    )
    assert client.get('/api/status', auth=('x', 'bad')).status_code == 429
    guard.unblock('testclient')
    assert client.get('/api/status', auth=(USERNAME, PASSWORD)).status_code == 200


def test_real_ip_from_cf_header():
    """The real client IP is read from CF-Connecting-IP behind the tunnel."""
    client, _, guard = make_client(
        with_frame=True, username=USERNAME, password=PASSWORD, max_attempts=2,
    )
    hdr = {'CF-Connecting-IP': '203.0.113.7'}
    client.get('/api/status', auth=('x', 'bad'), headers=hdr)
    client.get('/api/status', auth=('x', 'bad'), headers=hdr)
    # The attacker's REAL IP is blocked, not the tunnel's localhost address.
    assert guard.is_blocked('203.0.113.7')
    assert not guard.is_blocked('testclient')

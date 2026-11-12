#!/usr/bin/env python3
"""
web_server.py
=============

A ROS 2 node that is ALSO a web server. It subscribes to the camera topic
published by camera_publisher, and exposes over HTTP:

  GET  /                 -> HTML landing page (web/index.html) with the live view
  GET  /stream           -> MJPEG video stream  (multipart/x-mixed-replace)
  GET  /api/status       -> JSON health/status (REST endpoint)
  GET  /api/snapshot     -> single current frame as JPEG
  GET  /api/info         -> JSON describing the available endpoints (REST discovery)

ARCHITECTURE NOTES (this is the "web server" layer):
  * We use FastAPI (an ASGI framework) + uvicorn (an ASGI server).
  * A web server LISTENS on a TCP port, ACCEPTS HTTP requests, and RETURNS
    HTTP responses. Binding to host 0.0.0.0 means "all network interfaces",
    so other machines on the LAN can reach it (not just localhost).
  * MJPEG streaming works by keeping the HTTP response open and writing a
    never-ending sequence of JPEG frames separated by a boundary marker.
    The browser's <img src="/stream"> renders each frame as it arrives.

THREADING MODEL:
  rclpy (ROS) and uvicorn (web) both want to "run forever". We run ROS
  spinning on a background thread and uvicorn on the main thread. A lock
  guards the shared "latest frame".

Run it with:
    ros2 run robot_web_server web_server
    ros2 run robot_web_server web_server --ros-args -p port:=8080 -p host:=0.0.0.0
"""

import asyncio
import json
import secrets
import threading
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge

import cv2
from ament_index_python.packages import get_package_share_directory

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import uvicorn


class FrameBuffer:
    """Thread-safe holder for the most recent camera frame (as a JPEG + metadata).

    The ROS callback writes here; HTTP request handlers read from here.
    A single lock keeps reads/writes consistent across threads.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._jpeg = None            # bytes of the latest JPEG
        self._stamp = 0.0            # wall-clock time the frame was stored
        self._frame_count = 0

    def update(self, jpeg_bytes: bytes):
        with self._lock:
            self._jpeg = jpeg_bytes
            self._stamp = time.time()
            self._frame_count += 1

    def get_jpeg(self):
        with self._lock:
            return self._jpeg

    def stats(self):
        with self._lock:
            age = (time.time() - self._stamp) if self._stamp else None
            return {
                'frames_received': self._frame_count,
                'has_frame': self._jpeg is not None,
                'last_frame_age_s': round(age, 3) if age is not None else None,
            }


class WebServerNode(Node):
    """ROS node that subscribes to images and makes them available to FastAPI."""

    def __init__(self, frame_buffer: FrameBuffer):
        super().__init__('web_server')

        self.declare_parameter('topic', '/camera/image_raw')
        self.declare_parameter('jpeg_quality', 80)  # 1-100; higher = better/bigger

        topic = self.get_parameter('topic').value
        self.jpeg_quality = int(self.get_parameter('jpeg_quality').value)

        self.bridge = CvBridge()
        self.frame_buffer = frame_buffer

        self.subscription = self.create_subscription(
            Image, topic, self.on_image, 10
        )
        self.get_logger().info(f'Subscribed to image topic "{topic}"')

    def on_image(self, msg: Image):
        """ROS callback: convert the incoming Image to JPEG and store it."""
        try:
            frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        except Exception as exc:  # noqa: BLE001  (log and move on)
            self.get_logger().warn(f'Could not convert image: {exc}')
            return

        ok, buf = cv2.imencode(
            '.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
        )
        if ok:
            self.frame_buffer.update(buf.tobytes())


# --------------------------------------------------------------------------
# FastAPI application factory
# --------------------------------------------------------------------------
class LoginGuard:
    """Brute-force protection: permanently block an IP after N failed logins.

    HOW IT WORKS:
      * Every failed login increments a counter for that client's IP.
      * When the count reaches `max_attempts`, the IP is added to a blocklist
        and ALL future requests from it are rejected with 429 -- even if they
        later supply the correct password.
      * The blocklist is written to a JSON file on disk, so the ban survives
        server restarts (that's what makes it "permanent"). To un-ban an IP,
        clear it from that file (see `unblock`) and restart, or delete the file.
      * A successful login resets that IP's failure counter (so an occasional
        typo doesn't slowly accumulate toward a ban).

    IP BEHIND THE TUNNEL:
      Traffic through the Cloudflare Tunnel arrives from 127.0.0.1 (the tunnel
      itself). Cloudflare puts the real visitor IP in the `CF-Connecting-IP`
      header. When `trust_proxy_header` is True we use that header; otherwise
      we use the raw socket IP (correct for direct LAN access). If every
      visitor looked like one IP, a single attacker could lock out everyone --
      reading the real IP prevents that.
    """

    def __init__(self, max_attempts: int, blocklist_file: Path,
                 trust_proxy_header: bool = True, logger=None):
        self.max_attempts = max_attempts
        self.blocklist_file = Path(blocklist_file)
        self.trust_proxy_header = trust_proxy_header
        self._logger = logger
        self._lock = threading.Lock()
        self._failures = {}            # ip -> consecutive failure count
        self._blocked = self._load_blocklist()  # set of permanently-blocked IPs

    # --- persistence -------------------------------------------------------
    def _load_blocklist(self) -> set:
        try:
            if self.blocklist_file.exists():
                data = json.loads(self.blocklist_file.read_text(encoding='utf-8'))
                return set(data.get('blocked', []))
        except Exception as exc:  # noqa: BLE001  (corrupt file -> start empty)
            if self._logger:
                self._logger.warn(f'Could not read blocklist: {exc}')
        return set()

    def _save_blocklist(self):
        try:
            self.blocklist_file.parent.mkdir(parents=True, exist_ok=True)
            self.blocklist_file.write_text(
                json.dumps({'blocked': sorted(self._blocked)}, indent=2),
                encoding='utf-8',
            )
        except Exception as exc:  # noqa: BLE001
            if self._logger:
                self._logger.warn(f'Could not write blocklist: {exc}')

    # --- IP extraction -----------------------------------------------------
    def client_ip(self, request: Request) -> str:
        if self.trust_proxy_header:
            # Cloudflare sets CF-Connecting-IP to the true client IP.
            cf_ip = request.headers.get('cf-connecting-ip')
            if cf_ip:
                return cf_ip.strip()
            # Generic reverse-proxy header; take the first (original) hop.
            xff = request.headers.get('x-forwarded-for')
            if xff:
                return xff.split(',')[0].strip()
        return request.client.host if request.client else 'unknown'

    # --- decisions ---------------------------------------------------------
    def is_blocked(self, ip: str) -> bool:
        with self._lock:
            return ip in self._blocked

    def record_failure(self, ip: str) -> bool:
        """Count a failed attempt. Returns True if this IP is now blocked."""
        with self._lock:
            if ip in self._blocked:
                return True
            self._failures[ip] = self._failures.get(ip, 0) + 1
            if self._failures[ip] >= self.max_attempts:
                self._blocked.add(ip)
                self._save_blocklist()
                if self._logger:
                    self._logger.warn(
                        f'IP {ip} permanently blocked after '
                        f'{self._failures[ip]} failed login attempts.'
                    )
                return True
            if self._logger:
                remaining = self.max_attempts - self._failures[ip]
                self._logger.warn(
                    f'Failed login from {ip} '
                    f'({self._failures[ip]}/{self.max_attempts}); '
                    f'{remaining} attempt(s) before permanent block.'
                )
            return False

    def record_success(self, ip: str):
        """A correct login clears that IP's failure counter."""
        with self._lock:
            self._failures.pop(ip, None)

    def unblock(self, ip: str):
        """Manually remove an IP from the blocklist (recovery helper)."""
        with self._lock:
            self._blocked.discard(ip)
            self._failures.pop(ip, None)
            self._save_blocklist()


def build_app(
    frame_buffer: FrameBuffer,
    node: Node,
    web_dir: Path,
    username: str = '',
    password: str = '',
    guard: 'LoginGuard | None' = None,
) -> FastAPI:
    app = FastAPI(title='Robot Web Server', version='0.1.0')

    # MJPEG uses a boundary string to separate frames in one HTTP response.
    BOUNDARY = 'frame'

    # ------------------------------------------------------------------
    # Password protection (HTTP Basic Auth).
    #
    # The browser shows a native login popup. The viewer must enter BOTH the
    # correct username AND the correct password. If `password` is empty
    # (the default for that param), auth is disabled and the server is open.
    #
    # Basic Auth sends credentials base64-encoded (not encrypted), so it is
    # only truly private over HTTPS. Your public access via the Cloudflare
    # Tunnel is HTTPS, so this is safe there.
    # ------------------------------------------------------------------
    basic = HTTPBasic(auto_error=False)

    def require_login(
        request: Request,
        creds: HTTPBasicCredentials = Depends(basic),
    ):
        # No password configured -> auth disabled, allow everyone.
        if not password:
            return

        ip = guard.client_ip(request) if guard else 'unknown'

        # 1) If this IP is already permanently blocked, reject outright --
        #    even correct credentials cannot get in. 429 = Too Many Requests.
        if guard and guard.is_blocked(ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail='Too many failed login attempts; this address is blocked.',
            )

        # 2) If NO credentials were supplied, this is just the browser's first
        #    request -- respond 401 so it shows the login popup. We do NOT
        #    count this as a failed attempt (otherwise simply opening the page
        #    would burn attempts and could block a legitimate user instantly).
        if creds is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail='Authentication required',
                headers={'WWW-Authenticate': 'Basic'},
            )

        # 3) Credentials WERE supplied -> verify them.
        # secrets.compare_digest avoids leaking timing information. Both the
        # username and the password must match. We AND the two checks (and
        # evaluate both, rather than short-circuiting) to keep timing uniform.
        user_ok = secrets.compare_digest(creds.username, username)
        pass_ok = secrets.compare_digest(creds.password, password)

        if user_ok and pass_ok:
            # Correct login -> clear this IP's failure counter.
            if guard:
                guard.record_success(ip)
            return

        # 4) Wrong credentials -> count the failure. If that trips the limit,
        #    the IP is now blocked; tell them so with a 429, else a 401.
        now_blocked = guard.record_failure(ip) if guard else False
        if now_blocked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail='Too many failed login attempts; this address is blocked.',
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Incorrect username or password',
            headers={'WWW-Authenticate': 'Basic'},
        )

    @app.get('/', response_class=HTMLResponse)
    async def index(_=Depends(require_login)):
        """Serve the landing page with the embedded live view."""
        index_file = web_dir / 'index.html'
        if index_file.exists():
            return HTMLResponse(index_file.read_text(encoding='utf-8'))
        # Fallback minimal page if the static asset is missing.
        return HTMLResponse(
            '<h1>Robot Web Server</h1>'
            '<img src="/stream" alt="camera stream">'
        )

    async def mjpeg_generator():
        """Yield an endless multipart stream of JPEG frames.

        This is the core of MJPEG streaming: we never 'return', we keep
        yielding chunks. Each chunk is one JPEG framed by the boundary.
        """
        while True:
            jpeg = frame_buffer.get_jpeg()
            if jpeg is not None:
                yield (
                    b'--' + BOUNDARY.encode() + b'\r\n'
                    b'Content-Type: image/jpeg\r\n'
                    b'Content-Length: ' + str(len(jpeg)).encode() + b'\r\n\r\n'
                    + jpeg + b'\r\n'
                )
            # ~30 fps cap on the stream; also prevents a busy-loop when no frames.
            await asyncio.sleep(1.0 / 30.0)

    @app.get('/stream')
    async def stream(_=Depends(require_login)):
        """MJPEG stream endpoint. Point an <img> tag at this URL."""
        return StreamingResponse(
            mjpeg_generator(),
            media_type=f'multipart/x-mixed-replace; boundary={BOUNDARY}',
        )

    @app.get('/api/snapshot')
    async def snapshot(_=Depends(require_login)):
        """Return the single most recent frame as a JPEG (REST resource)."""
        jpeg = frame_buffer.get_jpeg()
        if jpeg is None:
            return JSONResponse(
                status_code=503,
                content={'error': 'no frame available yet'},
            )
        return Response(content=jpeg, media_type='image/jpeg')

    @app.get('/api/status')
    async def status_endpoint(_=Depends(require_login)):
        """Health/status as JSON. This is a REST endpoint returning resource state."""
        stats = frame_buffer.stats()
        return {
            'service': 'robot_web_server',
            'ros_node': node.get_name(),
            'camera_connected': stats['has_frame'],
            'frames_received': stats['frames_received'],
            'last_frame_age_s': stats['last_frame_age_s'],
            'server_time': time.time(),
        }

    @app.get('/api/info')
    async def info(_=Depends(require_login)):
        """Self-describing list of endpoints (handy for REST discovery/learning)."""
        return {
            'endpoints': {
                'GET /': 'HTML landing page with live view',
                'GET /stream': 'MJPEG multipart video stream',
                'GET /api/snapshot': 'Single latest frame as JPEG',
                'GET /api/status': 'JSON health/status',
                'GET /api/info': 'This document',
            }
        }

    return app


def main(args=None):
    rclpy.init(args=args)

    frame_buffer = FrameBuffer()
    node = WebServerNode(frame_buffer)

    # Read web server params (host/port/password) from the node's parameters.
    node.declare_parameter('host', '0.0.0.0')
    node.declare_parameter('port', 8080)
    # Username + password required to view the site. Viewers must enter BOTH.
    # Set password to '' to disable auth entirely.
    # Override at launch:  --ros-args -p username:='tprac' -p password:='my-secret'
    node.declare_parameter('username', 'tprac')
    node.declare_parameter('password', 'tprac@123')
    # Brute-force protection: permanently block an IP after this many failed
    # logins. Default 2. The block persists in `blocklist_file`.
    node.declare_parameter('max_attempts', 2)
    node.declare_parameter(
        'blocklist_file',
        str(Path.home() / '.robot_web_server_blocklist.json'),
    )
    # Trust Cloudflare/proxy headers for the real client IP. Keep True when
    # behind the Cloudflare Tunnel; set False for direct LAN-only access.
    node.declare_parameter('trust_proxy_header', True)
    host = node.get_parameter('host').value
    port = int(node.get_parameter('port').value)
    username = node.get_parameter('username').value
    password = node.get_parameter('password').value
    max_attempts = int(node.get_parameter('max_attempts').value)
    blocklist_file = node.get_parameter('blocklist_file').value
    trust_proxy_header = bool(node.get_parameter('trust_proxy_header').value)

    # Locate installed static web assets.
    try:
        web_dir = Path(get_package_share_directory('robot_web_server')) / 'web'
    except Exception:
        web_dir = Path(__file__).resolve().parent.parent / 'web'

    guard = LoginGuard(
        max_attempts=max_attempts,
        blocklist_file=blocklist_file,
        trust_proxy_header=trust_proxy_header,
        logger=node.get_logger(),
    )

    app = build_app(
        frame_buffer, node, web_dir,
        username=username, password=password, guard=guard,
    )

    # ------------------------------------------------------------------
    # Run ROS spinning on a background thread so the main thread can run
    # the (blocking) uvicorn event loop.
    # ------------------------------------------------------------------
    def ros_spin():
        try:
            rclpy.spin(node)
        except Exception:
            pass

    spin_thread = threading.Thread(target=ros_spin, daemon=True)
    spin_thread.start()

    node.get_logger().info(f'Starting web server on http://{host}:{port}')
    node.get_logger().info(f'  Local view:  http://localhost:{port}/')
    node.get_logger().info(f'  LAN view:    http://<this-pc-ip>:{port}/')
    if password:
        node.get_logger().info(
            f'  Login protection: ENABLED (Basic Auth, username "{username}")'
        )
        node.get_logger().info(
            f'  Brute-force block: after {max_attempts} failed attempts per IP '
            f'(blocklist: {blocklist_file})'
        )
        if guard._blocked:
            node.get_logger().info(
                f'  Currently blocked IPs: {len(guard._blocked)} '
                '(clear the blocklist file to reset)'
            )
    else:
        node.get_logger().warn('  Login protection: DISABLED (open to anyone)')

    config = uvicorn.Config(app, host=host, port=port, log_level='info')
    server = uvicorn.Server(config)

    try:
        server.run()  # blocks until Ctrl-C
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

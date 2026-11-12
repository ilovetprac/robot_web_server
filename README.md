# robot_web_server

A **from-scratch web server for a robot** that streams a USB camera (your
Microsoft camera) to any web browser, with a REST API — built as a ROS 2
package for **ROS 2 Jazzy on Ubuntu 24.04**.

This repo is a learning project covering the three required areas:

| Concept | Where you see it |
|---|---|
| **Web server architecture** | `web_server.py` — a program that binds a TCP port, accepts HTTP requests, returns responses (HTML, JPEG, JSON, and an endless MJPEG stream). |
| **HTTP / REST API** | `/api/status`, `/api/snapshot`, `/api/info` — stateless HTTP endpoints returning JSON/JPEG resources. |
| **Basic networking** | Binding `0.0.0.0` vs `localhost`, LAN IPs, ports, DNS, port forwarding, and public tunnels (see *Going public* below). |

---

## 1. The architecture

```
┌──────────────┐  USB   ┌────────────────────┐  ROS topic        ┌──────────────────────┐  HTTP   ┌─────────┐
│ MS USB Camera │──────▶│ camera_publisher    │─────────────────▶│ web_server           │────────▶│ Browser │
│ /dev/video0   │        │ (OpenCV reads cam, │ /camera/image_raw │ (FastAPI+uvicorn:    │ MJPEG   │ (phone, │
│               │        │  publishes Image)   │                   │  MJPEG + REST API)   │ + JSON  │  laptop)│
└──────────────┘        └────────────────────┘                   └──────────────────────┘         └─────────┘
```

Why split into two nodes connected by a ROS topic? Decoupling means other ROS
nodes (recording, object detection, etc.) can subscribe to the **same** camera
frames, and the web server doesn't care *how* frames are produced.

### HTTP endpoints exposed

| Method & path | Returns | Purpose |
|---|---|---|
| `GET /` | `text/html` | Landing page with the embedded live view |
| `GET /stream` | `multipart/x-mixed-replace` | Endless MJPEG video stream |
| `GET /api/snapshot` | `image/jpeg` | The single most recent frame |
| `GET /api/status` | `application/json` | Health: camera connected?, frame count, frame age |
| `GET /api/info` | `application/json` | Self-describing list of endpoints |

### How MJPEG streaming works (the key idea)

A normal HTTP response is one body, then the connection closes. For MJPEG the
server **keeps the response open** and writes JPEG frames forever, each
separated by a boundary marker (`multipart/x-mixed-replace`). The browser's
`<img src="/stream">` paints each new JPEG as it arrives — that's your "live
video" with zero client-side JavaScript. Simple, robust on a LAN, and works in
every browser.

---

## 2. Prerequisites (on the mini PC)

ROS 2 Jazzy already installed. Then:

```bash
# ROS packages used by the nodes
sudo apt update
sudo apt install ros-jazzy-cv-bridge ros-jazzy-sensor-msgs v4l-utils

# Python web libraries (into the system/ROS python env)
pip3 install --user fastapi "uvicorn[standard]"
# opencv usually comes with ROS; if not:
#   pip3 install --user opencv-python
```

Verify the camera is detected:

```bash
v4l2-ctl --list-devices     # shows your Microsoft camera and its /dev/videoN
ls /dev/video*
```

Note the device number (usually `/dev/video0`).

---

## 3. Build and run (the from-scratch version)

```bash
# Put this package in a ROS 2 workspace
mkdir -p ~/ros2_ws/src
cp -r robot_web_server ~/ros2_ws/src/

cd ~/ros2_ws
# Resolve system dependencies declared in package.xml
rosdep install --from-paths src --ignore-src -r -y

# Build
colcon build --packages-select robot_web_server
source install/setup.bash

# Launch both nodes together
ros2 launch robot_web_server robot_web_server.launch.py device_id:=0 port:=8080
```

Open a browser on the **same machine**: `http://localhost:8080/`

### Running the two nodes separately (useful for learning/debugging)

```bash
# Terminal 1
source ~/ros2_ws/install/setup.bash
ros2 run robot_web_server camera_publisher --ros-args -p device_id:=0

# Terminal 2
source ~/ros2_ws/install/setup.bash
ros2 run robot_web_server web_server --ros-args -p port:=8080

# Terminal 3 — inspect the ROS topic directly
ros2 topic list
ros2 topic hz /camera/image_raw      # confirms frames are flowing
```

---

## 4. Viewing from another device on your LAN

1. Find the mini PC's LAN IP:
   ```bash
   hostname -I        # e.g. 192.168.1.50
   ```
2. On your phone/laptop (same Wi-Fi/LAN), open:
   `http://192.168.1.50:8080/`

This works because the server binds `0.0.0.0` (all network interfaces), not
`127.0.0.1` (localhost only). If it doesn't load, check the firewall:

```bash
sudo ufw allow 8080/tcp     # only if ufw is active
```

### Nice local name instead of an IP (`robot.local`)

Ubuntu ships Avahi (mDNS). If the PC's hostname is `robot`, then
`http://robot.local:8080/` resolves on the LAN automatically — no DNS server
needed. Set the hostname with `sudo hostnamectl set-hostname robot`.

---

## 5. The production path (standard packages)

Instead of the custom nodes you can use the community packages:

```bash
sudo apt install ros-jazzy-usb-cam ros-jazzy-web-video-server
ros2 launch robot_web_server production_stack.launch.py
# then: http://<pc-ip>:8080/stream?topic=/image_raw
```

`web_video_server` gives you the MJPEG stream for free but no custom page or
REST API. You can run our `web_server` node alongside it for the `/api/*`
endpoints. Start with the from-scratch version to learn; reach for these when
you want the hardened, maintained option.

---

## 6. Going public — hosting on the internet with a domain

"View the feed from anywhere" means exposing the server beyond your LAN. There
are three common approaches, easiest → most involved.

> ⚠️ **Security first.** The moment your feed is reachable from the internet,
> anyone who finds the URL can watch your camera. Before exposing it, add
> authentication (see *Securing it* below) and prefer HTTPS. Never port-forward
> a camera feed with no auth.

### Option A — Cloudflare Tunnel (recommended; free, no port forwarding, real HTTPS domain)

A tunnel runs an agent on the mini PC that dials **out** to Cloudflare, so you
never open a port on your router. You get a public HTTPS URL, and you can map
it to your own domain.

```bash
# Install cloudflared (amd64 — your mini PC is x86_64)
curl -L -o cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb

# Quick test — gives a temporary https://<random>.trycloudflare.com URL:
cloudflared tunnel --url http://localhost:8080

# Permanent, with your own domain (domain must be on a Cloudflare account):
cloudflared tunnel login
cloudflared tunnel create robot-cam
# Route your domain/subdomain to the tunnel:
cloudflared tunnel route dns robot-cam cam.yourdomain.com
# Run it, pointing at the local server:
cloudflared tunnel run --url http://localhost:8080 robot-cam
```

Then browse to `https://cam.yourdomain.com` from anywhere. Make it a service so
it starts on boot: `sudo cloudflared service install`.

### Option B — ngrok (fastest to a public URL; free tier is ephemeral)

```bash
# amd64 build for your mini PC
curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | \
  sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | \
  sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update && sudo apt install ngrok

ngrok config add-authtoken <YOUR_TOKEN>   # from the ngrok dashboard
ngrok http 8080
```

ngrok prints a public `https://<id>.ngrok-free.app` URL. Good for demos; on the
free plan the URL changes each run and there's a bandwidth cap.

### Option C — Your own domain + router port forwarding (classic, full control)

This is the "host a domain server" path in the traditional sense:

1. **Buy a domain** (Namecheap, Cloudflare Registrar, etc.), e.g. `yourrobot.com`.
2. **Static-ish public IP.** Home IPs change; use a **Dynamic DNS** client
   (e.g. `ddclient`) to keep an `A` record pointed at your current public IP.
   ```bash
   sudo apt install ddclient   # configure with your DNS provider's API
   ```
3. **DNS record.** In your domain's DNS, add an `A` record
   `cam.yourrobot.com -> <your public IP>`.
4. **Port forward** on your router: external `80`/`443` → mini PC's
   `192.168.1.50:8080`.
5. **TLS/HTTPS.** Put a reverse proxy in front and get a free cert:
   ```bash
   sudo apt install nginx certbot python3-certbot-nginx
   # reverse-proxy localhost:8080, then:
   sudo certbot --nginx -d cam.yourrobot.com
   ```

Example nginx reverse-proxy site (`/etc/nginx/sites-available/robotcam`):

```nginx
server {
    server_name cam.yourrobot.com;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # MJPEG needs buffering OFF so frames flush immediately:
        proxy_buffering off;
    }
}
```

> The `proxy_buffering off;` line matters: without it nginx buffers the endless
> MJPEG response and the live view stalls.

**Which to pick?** Start with **Option A (Cloudflare Tunnel)** — it gives you a
real HTTPS domain, requires no router changes, and hides your home IP. Move to
Option C only if you specifically need to run your own DNS/port setup.

---

## 7. Password protection (built in)

The web server has HTTP Basic Auth built in and **enabled by default**. When a
password is set, every route (page, stream, snapshot, API) requires it — the
browser shows a native login popup. Only the **password** is checked, so the
user can type anything as the username.

Default password: `tprac@123`. Change it at launch (no code edit needed):

```bash
# set your own password
ros2 launch robot_web_server robot_web_server.launch.py password:='my-secret'

# or on the web_server node directly
ros2 run robot_web_server web_server --ros-args -p password:='my-secret'

# disable auth entirely (open to anyone) — only safe on a trusted LAN
ros2 launch robot_web_server robot_web_server.launch.py password:=''
```

> **HTTPS matters.** Basic Auth sends the password base64-encoded, *not*
> encrypted. Over plain `http://` on your LAN that's fine for casual use, but
> anyone sniffing the network could read it. When you expose the feed publicly
> via the **Cloudflare Tunnel** (§6 Option A), the connection is HTTPS, so the
> password is protected in transit. Always use the HTTPS tunnel URL for remote
> access, never a raw public `http://` port.

For stronger protection (per-user logins, no shared password), put **Cloudflare
Access** in front of the tunnel — a free login page with zero extra code.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not open camera /dev/video0` | Wrong index or camera busy. `v4l2-ctl --list-devices`, try `device_id:=1`. Make sure nothing else holds the camera. |
| Page loads but no video | Camera node not publishing. Check `ros2 topic hz /camera/image_raw`. |
| Works on localhost, not from other devices | Server bound to localhost, or firewall. Confirm `host:=0.0.0.0` and `sudo ufw allow 8080/tcp`. |
| Stream freezes behind nginx/tunnel | Disable response buffering (`proxy_buffering off;` in nginx). |
| High CPU / lag | Lower `fps`, `frame_width/height`, or `jpeg_quality`. MJPEG is bandwidth-heavy; for low-bandwidth/remote use, consider WebRTC later. |
| `ModuleNotFoundError: fastapi` | `pip3 install --user fastapi "uvicorn[standard]"` in the same Python ROS uses. |

---

## 9. Where to go next

- **WebRTC** (e.g. `aiortc`) for much lower bandwidth/latency over the internet.
- **Add robot telemetry** to `/api/status` (battery, pose) — more REST practice.
- **Multiple cameras** — one `camera_publisher` per device, selectable topic in the stream URL.

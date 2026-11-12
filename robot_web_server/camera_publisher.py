#!/usr/bin/env python3
"""
camera_publisher.py
===================

A ROS 2 node that reads frames from a USB camera (your Microsoft camera)
using OpenCV and publishes them on a ROS topic as sensor_msgs/Image.

WHY THIS EXISTS (the "ROS" layer of the architecture):
    Camera (USB)  ->  THIS NODE  ->  /camera/image_raw topic  ->  web_server node

Decoupling the camera from the web server through a ROS topic means:
  * Any number of other ROS nodes (recording, detection, etc.) can also
    subscribe to the same frames.
  * The web server does not need to know *how* frames are produced.

Run it with:
    ros2 run robot_web_server camera_publisher
    ros2 run robot_web_server camera_publisher --ros-args -p device_id:=0 -p fps:=15.0
"""

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge

import cv2


class CameraPublisher(Node):
    """Reads a USB camera with OpenCV and publishes frames as ROS Images."""

    def __init__(self):
        super().__init__('camera_publisher')

        # ------------------------------------------------------------------
        # Parameters: these are ROS 2 parameters, overridable at launch with
        #   --ros-args -p device_id:=1
        # so you don't have to edit code to point at a different camera.
        # ------------------------------------------------------------------
        self.declare_parameter('device_id', 0)        # /dev/video0 by default
        self.declare_parameter('frame_width', 640)
        self.declare_parameter('frame_height', 480)
        self.declare_parameter('fps', 15.0)
        self.declare_parameter('topic', '/camera/image_raw')
        # Camera capture format (FOURCC). 'MJPG' lets the camera compress on
        # board, which is REQUIRED for high resolution/fps on most USB webcams
        # (uncompressed 'YUYV' is capped to low fps at high res). Use 'MJPG'
        # for anything above 640x480. Leave '' to let the driver choose.
        self.declare_parameter('pixel_format', 'MJPG')

        device_id = self.get_parameter('device_id').value
        width = self.get_parameter('frame_width').value
        height = self.get_parameter('frame_height').value
        fps = self.get_parameter('fps').value
        topic = self.get_parameter('topic').value
        pixel_format = self.get_parameter('pixel_format').value

        # ------------------------------------------------------------------
        # Open the camera. On Linux, OpenCV's V4L2 backend talks to
        # /dev/video<device_id>. cv2.CAP_V4L2 makes the backend explicit.
        # ------------------------------------------------------------------
        self.get_logger().info(f'Opening camera device /dev/video{device_id} ...')
        self.cap = cv2.VideoCapture(device_id, cv2.CAP_V4L2)

        if not self.cap.isOpened():
            self.get_logger().error(
                f'Could not open camera /dev/video{device_id}. '
                'Is the camera plugged in? Try `ls /dev/video*` and `v4l2-ctl --list-devices`.'
            )
            raise RuntimeError('Camera open failed')

        # IMPORTANT: set the capture format (FOURCC) BEFORE resolution/fps.
        # V4L2 only honors a high-res/high-fps request if the pixel format
        # supports it. 'MJPG' = on-camera JPEG compression (fast, high res);
        # 'YUYV' = raw (limited to low fps at high res on this camera).
        if pixel_format:
            fourcc = cv2.VideoWriter_fourcc(*pixel_format)
            self.cap.set(cv2.CAP_PROP_FOURCC, fourcc)

        # Request a resolution. The camera may pick the closest it supports.
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        # Read back the format the driver actually selected (decode FOURCC int).
        fourcc_int = int(self.cap.get(cv2.CAP_PROP_FOURCC))
        actual_fmt = ''.join(
            [chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)]
        ).strip()
        self.get_logger().info(
            f'Camera opened at {actual_w}x{actual_h} @ ~{actual_fps:.0f} fps '
            f'(format: {actual_fmt or "unknown"})'
        )
        if actual_w != width or actual_h != height:
            self.get_logger().warn(
                f'Requested {width}x{height} but camera gave {actual_w}x{actual_h}. '
                'Check `v4l2-ctl --list-formats-ext` for supported combos.'
            )

        # cv_bridge converts OpenCV numpy images <-> ROS Image messages.
        self.bridge = CvBridge()

        # Publisher: queue depth 10 is a reasonable default for images.
        self.publisher = self.create_publisher(Image, topic, 10)

        # Timer fires at the target FPS and triggers one frame capture+publish.
        period = 1.0 / float(fps) if fps > 0 else 1.0 / 15.0
        self.timer = self.create_timer(period, self.capture_and_publish)
        self.get_logger().info(f'Publishing frames on "{topic}" at {fps} FPS')

    def capture_and_publish(self):
        """Grab one frame and publish it. Called by the ROS timer."""
        ok, frame = self.cap.read()
        if not ok:
            self.get_logger().warn('Frame capture failed; skipping.')
            return

        # OpenCV frames are BGR. Convert to a ROS Image with encoding 'bgr8'.
        msg = self.bridge.cv2_to_imgmsg(frame, encoding='bgr8')
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = 'camera'
        self.publisher.publish(msg)

    def destroy_node(self):
        """Release the camera cleanly on shutdown."""
        if hasattr(self, 'cap') and self.cap is not None:
            self.cap.release()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = CameraPublisher()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        # Camera failed to open; message already logged.
        print(f'camera_publisher exiting: {exc}')
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()

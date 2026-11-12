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

        device_id = self.get_parameter('device_id').value
        width = self.get_parameter('frame_width').value
        height = self.get_parameter('frame_height').value
        fps = self.get_parameter('fps').value
        topic = self.get_parameter('topic').value

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

        # Request a resolution. The camera may pick the closest it supports.
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, fps)

        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.get_logger().info(f'Camera opened at {actual_w}x{actual_h}')

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

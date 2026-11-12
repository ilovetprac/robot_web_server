#!/usr/bin/env python3
"""
production_stack.launch.py
==========================

The "use the standard packages" path (option b from planning). Instead of our
custom nodes, this launches the two battle-tested community packages:

  * usb_cam           -> reads the USB camera, publishes /image_raw
  * web_video_server  -> serves ANY image topic as MJPEG/HTTP on :8080

Install them first (Ubuntu 24.04 / ROS 2 Jazzy):
    sudo apt update
    sudo apt install ros-jazzy-usb-cam ros-jazzy-web-video-server

Run:
    ros2 launch robot_web_server production_stack.launch.py

Then open:   http://<pc-ip>:8080/stream?topic=/image_raw
web_video_server also serves an index at http://<pc-ip>:8080/ listing topics.

NOTE: web_video_server gives you the MJPEG stream out of the box, but NOT a
custom landing page or REST API. You can still run our custom `web_server`
node alongside it if you want the /api/* endpoints — just point both at the
same image topic.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    video_device = LaunchConfiguration('video_device')
    port = LaunchConfiguration('port')

    return LaunchDescription([
        DeclareLaunchArgument('video_device', default_value='/dev/video0'),
        DeclareLaunchArgument('port', default_value='8080'),

        # usb_cam: publishes sensor_msgs/Image on /image_raw
        Node(
            package='usb_cam',
            executable='usb_cam_node_exe',
            name='usb_cam',
            output='screen',
            parameters=[{
                'video_device': video_device,
                'image_width': 640,
                'image_height': 480,
                'framerate': 15.0,
                'pixel_format': 'mjpeg2rgb',  # most MS/UVC cams support MJPEG
            }],
        ),

        # web_video_server: exposes any image topic over HTTP as MJPEG
        Node(
            package='web_video_server',
            executable='web_video_server',
            name='web_video_server',
            output='screen',
            parameters=[{
                'port': port,
                'address': '0.0.0.0',
            }],
        ),
    ])

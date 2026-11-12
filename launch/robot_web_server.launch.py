#!/usr/bin/env python3
"""
robot_web_server.launch.py
==========================

Launches BOTH custom nodes together:
  * camera_publisher  -> reads the USB camera, publishes /camera/image_raw
  * web_server        -> serves the MJPEG stream + REST API on :8080

Usage:
    ros2 launch robot_web_server robot_web_server.launch.py
    ros2 launch robot_web_server robot_web_server.launch.py device_id:=0 port:=8080
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    # Launch arguments (override on the command line).
    device_id = LaunchConfiguration('device_id')
    width = LaunchConfiguration('frame_width')
    height = LaunchConfiguration('frame_height')
    fps = LaunchConfiguration('fps')
    pixel_format = LaunchConfiguration('pixel_format')
    host = LaunchConfiguration('host')
    port = LaunchConfiguration('port')
    topic = LaunchConfiguration('topic')
    username = LaunchConfiguration('username')
    password = LaunchConfiguration('password')
    jpeg_quality = LaunchConfiguration('jpeg_quality')

    return LaunchDescription([
        DeclareLaunchArgument('device_id', default_value='0',
                              description='USB camera index (/dev/videoN)'),
        DeclareLaunchArgument('frame_width', default_value='1280'),
        DeclareLaunchArgument('frame_height', default_value='720'),
        DeclareLaunchArgument('fps', default_value='30.0'),
        DeclareLaunchArgument('pixel_format', default_value='MJPG',
                              description="Camera capture format; 'MJPG' is "
                                          "required for high res/fps"),
        DeclareLaunchArgument('host', default_value='0.0.0.0',
                              description='Bind address; 0.0.0.0 = all interfaces'),
        DeclareLaunchArgument('port', default_value='8080'),
        DeclareLaunchArgument('topic', default_value='/camera/image_raw'),
        DeclareLaunchArgument('jpeg_quality', default_value='80',
                              description='Stream JPEG quality 1-100; lower = '
                                          'smaller/smoother over the internet'),
        DeclareLaunchArgument('username', default_value='tprac',
                              description='Username required to view the site'),
        DeclareLaunchArgument('password', default_value='tprac@123',
                              description="Password required to view the site; "
                                          "set to '' to disable auth"),

        Node(
            package='robot_web_server',
            executable='camera_publisher',
            name='camera_publisher',
            output='screen',
            parameters=[{
                'device_id': device_id,
                'frame_width': width,
                'frame_height': height,
                'fps': fps,
                'pixel_format': pixel_format,
                'topic': topic,
            }],
        ),

        Node(
            package='robot_web_server',
            executable='web_server',
            name='web_server',
            output='screen',
            parameters=[{
                'host': host,
                'port': port,
                'topic': topic,
                'username': username,
                'password': password,
                'jpeg_quality': jpeg_quality,
            }],
        ),
    ])

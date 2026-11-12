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
    host = LaunchConfiguration('host')
    port = LaunchConfiguration('port')
    topic = LaunchConfiguration('topic')
    password = LaunchConfiguration('password')

    return LaunchDescription([
        DeclareLaunchArgument('device_id', default_value='0',
                              description='USB camera index (/dev/videoN)'),
        DeclareLaunchArgument('frame_width', default_value='640'),
        DeclareLaunchArgument('frame_height', default_value='480'),
        DeclareLaunchArgument('fps', default_value='15.0'),
        DeclareLaunchArgument('host', default_value='0.0.0.0',
                              description='Bind address; 0.0.0.0 = all interfaces'),
        DeclareLaunchArgument('port', default_value='8080'),
        DeclareLaunchArgument('topic', default_value='/camera/image_raw'),
        DeclareLaunchArgument('password', default_value='tprac@123',
                              description="Shared password to view the site; "
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
                'password': password,
            }],
        ),
    ])

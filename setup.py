from setuptools import find_packages, setup
import os
from glob import glob

package_name = 'robot_web_server'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        # Register the package with the ament resource index
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        # Install package.xml
        ('share/' + package_name, ['package.xml']),
        # Install launch files
        (os.path.join('share', package_name, 'launch'),
            glob('launch/*.launch.py')),
        # Install the static web assets (HTML/CSS/JS) served by the web node
        (os.path.join('share', package_name, 'web'),
            glob('web/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='you',
    maintainer_email='you@example.com',
    description='A from-scratch robot web server: MJPEG camera stream + REST API on ROS 2 Jazzy.',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            # `ros2 run robot_web_server camera_publisher`
            'camera_publisher = robot_web_server.camera_publisher:main',
            # `ros2 run robot_web_server web_server`
            'web_server = robot_web_server.web_server:main',
        ],
    },
)

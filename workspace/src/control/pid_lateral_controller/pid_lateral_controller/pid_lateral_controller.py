#
# BSD 3-Clause License
#
# Copyright (c) 2022 University of Wisconsin - Madison
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holder nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.#
import rclpy
from rclpy.node import Node
from art_msgs.msg import VehicleState
from chrono_ros_interfaces.msg import DriverInputs as VehicleInput
from nav_msgs.msg import Path
from ament_index_python.packages import get_package_share_directory
import numpy as np
import os

from rclpy.qos import QoSHistoryPolicy
from rclpy.qos import QoSProfile


class PIDLateralControllerNode(Node):
    """A PID controller.

    This node subscribes to a path which is the data type published by the Cone Path Planner node, and publishes vehicle inputs to follow the path.

    Attributes:
        mode: The control mode to be used (currently only PID)
        file: Where to read predifined inputs from
        recorded_inputs: The inputs read from the file
        steering_gain: The gain for the steering input
        throttle_gain: The gain for the throttle input
    """

    def __init__(self):
        super().__init__("pid_lateral_controller_node")

        # DEFAULT SETTINGS

        # pid_lateral_controller node mode
        self.mode = "PID"  # "PID", "File"
        self.file = ""
        self.recorded_inputs = np.array([])

        # update frequency of this node
        self.freq = 10.0

        self.t_start = self.get_clock().now().nanoseconds / 1e9

        # READ IN SHARE DIRECTORY LOCATION
        package_share_directory = get_package_share_directory("pid_lateral_controller")

        # ROS PARAMETERS
        self.declare_parameter("control_mode", "PID")
        self.mode = (
            self.get_parameter("control_mode").get_parameter_value().string_value
        )
        self.declare_parameter("control_file", "")
        self.file = (
            self.get_parameter("control_file").get_parameter_value().string_value
        )

        self.declare_parameter("steering_gain", 1.0)
        self.steering_gain = (
            self.get_parameter("steering_gain").get_parameter_value().double_value
        )
        self.declare_parameter("throttle_gain", 1.0)
        self.throttle_gain = (
            self.get_parameter("throttle_gain").get_parameter_value().double_value
        )

        if self.file == "":
            self.mode = "PID"
        else:
            file_path = os.path.join(package_share_directory, self.file)
            self.recorded_inputs = np.loadtxt(file_path, delimiter=",")

        self.steering = 0.0
        self.throttle = 0.0
        self.braking = 0.0

        # data that will be used by this class
        self.state = ""
        self.path = Path()
        self.vehicle_cmd = VehicleInput()

        # waits for first path if using PID, otherwise runs right away
        self.go = self.mode == "File"

        # publishers and subscribers
        qos_profile = QoSProfile(depth=1)
        qos_profile.history = QoSHistoryPolicy.KEEP_LAST
        self.sub_path = self.create_subscription(
            Path, "/input/path", self.path_callback, qos_profile
        )
        # self.sub_state = self.create_subscription(VehicleState, '~/input/vehicle_state', self.state_callback, qos_profile)
        self.pub_vehicle_cmd = self.create_publisher(
            VehicleInput, "/output/vehicle_inputs", 10
        )
        self.timer = self.create_timer(1 / self.freq, self.pub_callback)

    # function to process data this class subscribes to
    def state_callback(self, msg):
        """Callback for the vehicle state subscriber.

        Read the state of the vehicle from the topic.

        Args:
            msg: The message received from the topic
        """
        # self.get_logger().info("Received '%s'" % msg)
        self.state = msg

    def path_callback(self, msg):
        """Callback for the path subscriber.

        Read the path from the topic.

        Args:
            msg: The message received from the topic
        """
        self.go = True
        # self.get_logger().info("Received '%s'" % msg)
        self.path = msg

    # callback to run a loop and publish data this class generates
    def pub_callback(self):
        """Callback for the publisher.

        Publish the vehicle inputs to follow the path. If we are using control inputs from a file, then calculate what the control inputs should be. If the PID controller is being used, multiply the ratio of y/x reference coordinatesby the steering gain, and set constant throttle.
        """
        if not self.go:
            return

        msg = VehicleInput()

        if self.mode == "File":
            self.calc_inputs_from_file()
        elif self.mode == "PID" and len(self.path.poses) > 0:
            pt = [
                self.path.poses[0].pose.position.x,
                self.path.poses[0].pose.position.y,
            ]

            ratio = pt[1] / pt[0]
            steering = self.steering_gain * ratio
            # ensure steering can't change too much between timesteps, smooth transition
            delta_steering = steering - self.steering
            if abs(delta_steering) > 0.1:
                self.steering = self.steering + 0.1 * delta_steering / abs(
                    delta_steering
                )
            else:
                self.steering = steering
            # self.get_logger().info('Target steering = %s' % self.steering)

        # for circle
        # self.steering = -0.5

        # TODO: remove after debugging
        # self.steering = 0.0
        self.throttle = self.throttle_gain * 0.55  # only doing lateral conmtrol for now
        # self.braking = 0.0

        msg.steering = np.clip(self.steering, -1, 1)
        msg.throttle = np.clip(self.throttle, 0, 1)
        msg.braking = np.clip(self.braking, 0, 1)

        msg.header.stamp = self.get_clock().now().to_msg()

        self.pub_vehicle_cmd.publish(msg)

    def calc_inputs_from_file(self):
        """Calculate the inputs from a given file.

        Defines steering, throttle, and braking based on inputs that are recorded in a given file.
        """
        t = self.get_clock().now().nanoseconds / 1e9 - self.t_start

        self.throttle = np.interp(
            t, self.recorded_inputs[:, 0], self.recorded_inputs[:, 1]
        )
        self.braking = np.interp(
            t, self.recorded_inputs[:, 0], self.recorded_inputs[:, 2]
        )
        self.steering = np.interp(
            t, self.recorded_inputs[:, 0], self.recorded_inputs[:, 3]
        )

        # self.get_logger().info('Inputs %s' % self.recorded_inputs[0,:])

        # self.get_logger().info('Inputs from file: (t=%s, (%s,%s,%s)),' % (t,self.throttle,self.braking,self.steering))


def main(args=None):
    rclpy.init(args=args)
    control = PIDLateralControllerNode()
    rclpy.spin(control)

    control.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()

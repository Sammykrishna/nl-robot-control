# core/nav2_client.py
# Sends NavigateToPose goals to Nav2 and waits for the result.
# Uses rclpy directly (not rosbridge) because Nav2 actions need
# the full DDS stack — WebSocket is too slow for action feedback.

import json
import math
import os

import rclpy # type: ignore
from rclpy.node import Node # type: ignore
from rclpy.action import ActionClient # type: ignore
from action_msgs.msg import GoalStatus # type: ignore
from nav2_msgs.action import NavigateToPose # type: ignore
from geometry_msgs.msg import PoseStamped # pyright: ignore[reportMissingImports]


LOCATIONS_FILE = os.path.join(os.path.dirname(__file__), 'locations.json')


def yaw_to_quaternion(yaw: float) -> dict:
    """
    Converts a yaw angle (radians) to a quaternion.
    For 2D navigation you only need to rotate around the Z axis,
    so roll=0 and pitch=0. The math simplifies to:
      w = cos(yaw/2)
      z = sin(yaw/2)
    """
    return {
        'x': 0.0,
        'y': 0.0,
        'z': math.sin(yaw / 2.0),
        'w': math.cos(yaw / 2.0),
    }


class Nav2Client(Node):

    def __init__(self):
        super().__init__('nl_robot_nav2_client')

        # Action client connects to Nav2's /navigate_to_pose server
        self._action_client = ActionClient(
            self,
            NavigateToPose,
            'navigate_to_pose'
        )

        # Load named locations from JSON
        if not os.path.exists(LOCATIONS_FILE):
            self.get_logger().warning(
                f"locations.json not found at {LOCATIONS_FILE}. "
                "Navigation to named locations will not work."
            )
            self.locations = {}
        else:
            with open(LOCATIONS_FILE) as f:
                self.locations = json.load(f)
            self.get_logger().info(
                f"Loaded {len(self.locations)} named locations: "
                f"{list(self.locations.keys())}"
            )

    def go_to_location(self, name: str) -> str:
        """
        Navigate to a named location.
        Blocks until Nav2 reports success or failure.
        Returns a human-readable result string.
        """
        # Normalise the name — handle "the kitchen" → "kitchen"
        name = name.lower().strip()
        name = name.replace('the ', '').replace(' ', '_')

        if name not in self.locations:
            available = list(self.locations.keys())
            return (
                f"I don't know the location '{name}'. "
                f"Available locations: {', '.join(available)}."
            )

        loc = self.locations[name]
        self.get_logger().info(
            f"Navigating to '{name}' at "
            f"x={loc['x']}, y={loc['y']}, yaw={loc['yaw']}"
        )

        # Build the goal message
        goal_msg = NavigateToPose.Goal()
        pose = PoseStamped()
        pose.header.frame_id = 'map'     # always 'map' for Nav2
        pose.header.stamp    = self.get_clock().now().to_msg()

        pose.pose.position.x = float(loc['x'])
        pose.pose.position.y = float(loc['y'])
        pose.pose.position.z = 0.0

        q = yaw_to_quaternion(float(loc['yaw']))
        pose.pose.orientation.x = q['x']
        pose.pose.orientation.y = q['y']
        pose.pose.orientation.z = q['z']
        pose.pose.orientation.w = q['w']

        goal_msg.pose = pose

        # Wait for Nav2 action server to be ready (timeout 5s)
        self.get_logger().info("Waiting for Nav2 action server...")
        server_ready = self._action_client.wait_for_server(timeout_sec=5.0)

        if not server_ready:
            return (
                "Nav2 action server is not available. "
                "Make sure nav2_bringup is running."
            )

        # Send the goal — this returns a future
        send_future = self._action_client.send_goal_async(goal_msg)
        rclpy.spin_until_future_complete(self, send_future)
        goal_handle = send_future.result()

        if not goal_handle.accepted:
            return f"Nav2 rejected the goal for '{name}'. Check map and AMCL."

        self.get_logger().info(f"Goal accepted. Navigating to '{name}'...")

        # Wait for navigation to complete
        result_future = goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self, result_future)
        result = result_future.result()

        if result.status == GoalStatus.STATUS_SUCCEEDED:
            desc = loc.get('description', name)
            return f"Successfully arrived at {name} ({desc})."
        else:
            return (
                f"Navigation to '{name}' failed with status {result.status}. "
                "The path may be blocked or the goal unreachable."
            )

    def go_to_pose(self, x: float, y: float, yaw: float = 0.0) -> str:
        """
        Navigate to an arbitrary (x, y, yaw) pose.
        Used when the user gives explicit coordinates.
        """
        self.get_logger().info(
            f"Navigating to pose: x={x}, y={y}, yaw={yaw}"
        )

        goal_msg  = NavigateToPose.Goal()
        pose      = PoseStamped()
        pose.header.frame_id = 'map'
        pose.header.stamp    = self.get_clock().now().to_msg()

        pose.pose.position.x = float(x)
        pose.pose.position.y = float(y)
        pose.pose.position.z = 0.0

        q = yaw_to_quaternion(float(yaw))
        pose.pose.orientation.x = q['x']
        pose.pose.orientation.y = q['y']
        pose.pose.orientation.z = q['z']
        pose.pose.orientation.w = q['w']

        goal_msg.pose = pose

        if not self._action_client.wait_for_server(timeout_sec=5.0):
            return "Nav2 action server not available."

        send_future = self._action_client.send_goal_async(goal_msg)
        rclpy.spin_until_future_complete(self, send_future)
        goal_handle = send_future.result()

        if not goal_handle.accepted:
            return "Nav2 rejected the goal."

        result_future = goal_handle.get_result_async()
        rclpy.spin_until_future_complete(self, result_future)
        result = result_future.result()

        if result.status == GoalStatus.STATUS_SUCCEEDED:
            return f"Arrived at ({x:.2f}, {y:.2f})."
        else:
            return f"Navigation failed with status {result.status}."

    def get_location_names(self) -> list:
        return list(self.locations.keys())

    def get_locations_summary(self) -> str:
        """Returns a formatted string for the LLM system prompt."""
        if not self.locations:
            return "No named locations configured."
        lines = []
        for name, loc in self.locations.items():
            lines.append(
                f"  - {name}: {loc.get('description', '')} "
                f"(x={loc['x']}, y={loc['y']})"
            )
        return "\n".join(lines)
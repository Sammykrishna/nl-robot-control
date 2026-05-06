import asyncio
import time
import roslibpy


class BridgeClient:
    def __init__(self, host: str = 'localhost', port: int = 9090):
        self.client = roslibpy.Ros(host=host, port=port)
        self.client.run()
        self.robot_state = {'x': 0.0, 'y': 0.0, 'heading': 0.0}
        self._setup_state_subscribers()

    def _setup_state_subscribers(self):
        odom = roslibpy.Topic(self.client, '/odom', 'nav_msgs/Odometry')
        odom.subscribe(self._on_odom)

    def _on_odom(self, msg):
        pos = msg['pose']['pose']['position']
        ori = msg['pose']['pose']['orientation']
        self.robot_state['x']       = round(pos['x'], 3)
        self.robot_state['y']       = round(pos['y'], 3)
        self.robot_state['heading'] = round(ori['z'], 3)

    def get_topics(self):
        result = []
        done   = False

        def on_topics(topics):
            nonlocal done
            for name, msg_type in zip(topics['topics'], topics['types']):
                result.append({'topic': name, 'type': msg_type})
                print(f"  {name} → {msg_type}")
            done = True

        self.client.get_topics(on_topics)

        timeout = 3.0
        while not done and timeout > 0:
            time.sleep(0.1)
            timeout -= 0.1

        return result

    def _make_twist_stamped(self, linear_x: float, angular_z: float) -> roslibpy.Message:
        """
        Builds a TwistStamped message.
        The header timestamp can be zeroed — Gazebo accepts it fine.
        frame_id 'base_link' tells ROS which frame the velocity is in.
        """
        return roslibpy.Message({
            'header': {
                'stamp':    {'sec': 0, 'nanosec': 0},
                'frame_id': 'base_link'
            },
            'twist': {
                'linear':  {'x': linear_x, 'y': 0.0, 'z': 0.0},
                'angular': {'x': 0.0,      'y': 0.0, 'z': angular_z}
            }
        })

    def publish_twist(self, linear_x: float, angular_z: float, duration: float):
        """Blocking version — fine for scripts, not for async web servers."""
        linear_x  = max(-0.5, min(0.5,  linear_x))
        angular_z = max(-1.0, min(1.0, angular_z))

        # NOTE: type is now TwistStamped, not Twist
        pub = roslibpy.Topic(self.client, '/cmd_vel', 'geometry_msgs/TwistStamped')

        pub.publish(self._make_twist_stamped(linear_x, angular_z))
        time.sleep(duration)

        # Zero-velocity stop
        pub.publish(self._make_twist_stamped(0.0, 0.0))
        pub.unadvertise()

    async def publish_twist_async(self, linear_x: float, angular_z: float, duration: float):
        """Non-blocking version for FastAPI."""
        linear_x  = max(-0.5, min(0.5,  linear_x))
        angular_z = max(-1.0, min(1.0, angular_z))

        pub = roslibpy.Topic(self.client, '/cmd_vel', 'geometry_msgs/TwistStamped')
        pub.publish(self._make_twist_stamped(linear_x, angular_z))

        await asyncio.sleep(duration)

        pub.publish(self._make_twist_stamped(0.0, 0.0))
        pub.unadvertise()

    def get_state(self) -> dict:
        return self.robot_state

    def close(self):
        self.client.terminate()


if __name__ == '__main__':
    bridge = BridgeClient()

    print("=== Topic Manifest ===")
    bridge.get_topics()

    print("\n=== Robot state before move ===")
    time.sleep(0.5)   # let odom subscriber collect one reading
    print(bridge.get_state())

    print("\n=== Moving forward 0.3 m/s for 2s ===")
    bridge.publish_twist(linear_x=0.3, angular_z=0.0, duration=2.0)

    print("\n=== Robot state after move ===")
    print(bridge.get_state())

    bridge.close()
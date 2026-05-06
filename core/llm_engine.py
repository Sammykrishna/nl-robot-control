# core/llm_engine.py  — updated for Phase 4

import os
from dotenv import load_dotenv
import anthropic

from safety import validate_command

load_dotenv()


# Tool 1 — velocity commands (unchanged from Phase 2-3)
VELOCITY_TOOL = {
    "name": "publish_command",
    "description": (
        "Publish a velocity command to move the robot directly. "
        "Use for: 'move forward X meters', 'turn left/right N degrees', "
        "'go backward', 'spin'. "
        "Do NOT use for named locations like 'go to kitchen' — "
        "use navigate_to_location for those."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "enum": ["/cmd_vel"],
                "description": "Always /cmd_vel"
            },
            "linear_x": {
                "type": "number",
                "description": (
                    "Forward/backward speed in m/s. "
                    "Positive=forward, negative=backward. Max: 0.20 m/s."
                )
            },
            "angular_z": {
                "type": "number",
                "description": (
                    "Rotation speed in rad/s. "
                    "Positive=left, negative=right. Max: 1.0 rad/s."
                )
            },
            "duration": {
                "type": "number",
                "description": (
                    "Seconds to apply the command. "
                    "For distance: duration = distance / speed. "
                    "For rotation: duration = radians / angular_vel. "
                    "Max: 10.0 seconds."
                )
            },
            "explanation": {
                "type": "string",
                "description": "Show your working — how you computed the values."
            }
        },
        "required": ["topic", "linear_x", "angular_z", "duration", "explanation"]
    }
}


# Tool 2 — Nav2 semantic navigation (new in Phase 4)
NAV2_TOOL = {
    "name": "navigate_to_location",
    "description": (
        "Navigate the robot to a named location using Nav2 autonomous navigation. "
        "Nav2 handles path planning and obstacle avoidance automatically. "
        "Use for: 'go to kitchen', 'return home', 'navigate to charging station'. "
        "Do NOT use for simple distance commands — use publish_command for those."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "location_name": {
                "type": "string",
                "description": (
                    "The name of the destination. "
                    "Must match one of the available named locations."
                )
            },
            "explanation": {
                "type": "string",
                "description": "Why you chose this location based on the user's command."
            }
        },
        "required": ["location_name", "explanation"]
    }
}


def build_system_prompt(topic_manifest: list,
                         robot_state: dict,
                         location_summary: str) -> str:
    topic_lines = "\n".join(
        f"  - {t['topic']}  ({t['type']})"
        for t in topic_manifest
    )

    return f"""You are a ROS2 robot controller for a TurtleBot3 Burger running ROS2 Jazzy.
Interpret natural language commands and translate them into robot actions.

ROBOT SPECS:
- Max linear velocity:  0.20 m/s
- Max angular velocity: 1.0 rad/s
- Navigation: Nav2 with AMCL localisation

CURRENT ROBOT STATE:
- Position x: {robot_state.get('x', 0.0):.3f} m
- Position y: {robot_state.get('y', 0.0):.3f} m
- Heading:    {robot_state.get('heading', 0.0):.3f} rad

NAMED LOCATIONS (use navigate_to_location for these):
{location_summary}

AVAILABLE ROS2 TOPICS:
{topic_lines}

TOOL SELECTION RULES:
- Named place ("go to kitchen", "return home") → navigate_to_location
- Distance/angle ("move 0.5m", "turn 90°", "spin")  → publish_command
- Ambiguous ("go forward a bit") → publish_command with conservative values

CALCULATION RULES (for publish_command):
- Distance to duration: duration = distance / speed  (default speed: 0.15 m/s)
- Degrees to duration:  duration = (degrees × π/180) / angular_vel  (default: 0.5 rad/s)
- Turn left  = positive angular_z
- Turn right = negative angular_z
- Backward   = negative linear_x

Always use a tool for movement requests. For questions, respond in plain text.
"""


class LLMEngine:

    def __init__(self, bridge_client, nav2_client=None):
        self.client         = anthropic.Anthropic(
                                  api_key=os.getenv("ANTHROPIC_API_KEY")
                              )
        self.bridge         = bridge_client
        self.nav2           = nav2_client   # None if Nav2 not running
        self.history        = []
        self.topic_manifest = []

    def refresh_topics(self):
        self.topic_manifest = self.bridge.get_topics()
        print(f"LLMEngine: loaded {len(self.topic_manifest)} topics")

    def chat(self, user_message: str) -> str:
        self.history.append({"role": "user", "content": user_message})

        # Include Nav2 tool only if nav2_client is available
        tools = [VELOCITY_TOOL]
        if self.nav2 is not None:
            tools.append(NAV2_TOOL)

        # Get location summary for system prompt
        location_summary = (
            self.nav2.get_locations_summary()
            if self.nav2 else "Nav2 not running — named locations unavailable."
        )

        system_prompt = build_system_prompt(
            topic_manifest=self.topic_manifest,
            robot_state=self.bridge.get_state(),
            location_summary=location_summary
        )

        response = self.client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=system_prompt,
            tools=tools, # type: ignore
            messages=self.history
        )

        reply = self._handle_response(response)

        self.history.append({"role": "assistant", "content": reply})
        return reply

    def _handle_response(self, response) -> str:
        for block in response.content:

            # Velocity command
            if block.type == "tool_use" and block.name == "publish_command":
                return self._execute_velocity(block.input)

            # Nav2 navigation goal
            if block.type == "tool_use" and block.name == "navigate_to_location":
                return self._execute_navigation(block.input)

            # Plain text response
            if block.type == "text" and block.text.strip():
                return block.text

        return "I received your message but could not determine an action."

    def _execute_velocity(self, tool_input: dict) -> str:
        topic       = tool_input.get("topic", "/cmd_vel")
        linear_x    = float(tool_input.get("linear_x", 0.0))
        angular_z   = float(tool_input.get("angular_z", 0.0))
        duration    = float(tool_input.get("duration", 0.0))
        explanation = tool_input.get("explanation", "")

        print(f"\n[LLM→velocity] linear_x={linear_x}, "
              f"angular_z={angular_z}, duration={duration}s")
        print(f"  reasoning: {explanation}")

        result = validate_command(topic, linear_x, angular_z, duration)

        if not result.approved:
            return f"Safety system rejected this command: {result.reject_reason}"

        self.bridge.publish_twist(
            linear_x=result.linear_x,
            angular_z=result.angular_z,
            duration=result.duration
        )

        return (
            f"{explanation} "
            f"(speed={result.linear_x} m/s, "
            f"rotation={result.angular_z} rad/s, "
            f"duration={result.duration}s)"
        )

    def _execute_navigation(self, tool_input: dict) -> str:
        location_name = tool_input.get("location_name", "")
        explanation   = tool_input.get("explanation", "")

        print(f"\n[LLM→nav2] destination='{location_name}'")
        print(f"  reasoning: {explanation}")

        if self.nav2 is None:
            return (
                "Nav2 is not running. "
                "Start nav2_bringup to use named location navigation."
            )

        # This blocks until Nav2 reports arrival or failure
        result = self.nav2.go_to_location(location_name)
        return result

    def reset_history(self):
        self.history = []
        print("Conversation history cleared.")
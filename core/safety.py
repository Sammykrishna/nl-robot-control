# safety.py
# This layer sits between the LLM output and the robot.
# The LLM might hallucinate large velocities, wrong topic names,
# or nonsensical durations. This catches all of that.

from dataclasses import dataclass
from typing import Optional

# Hard limits — tune these for your TurtleBot3 Burger
MAX_LINEAR_VEL  =  0.20   # m/s  (Burger's safe max is ~0.22)
MIN_LINEAR_VEL  = -0.20   # m/s
MAX_ANGULAR_VEL =  1.0    # rad/s
MIN_ANGULAR_VEL = -1.0    # rad/s
MAX_DURATION    = 10.0    # seconds — never let LLM run motor for >10s
MIN_DURATION    =  0.1    # seconds

# Only these topics are allowed — prevents LLM from publishing
# to /robot_description or other sensitive topics
ALLOWED_TOPICS = {
    '/cmd_vel',
}

@dataclass
class CommandResult:
    approved:      bool
    linear_x:      float = 0.0
    angular_z:     float = 0.0
    duration:      float = 0.0
    topic:         str   = '/cmd_vel'
    reject_reason: Optional[str] = None


def validate_command(topic: str,
                     linear_x: float,
                     angular_z: float,
                     duration: float) -> CommandResult:
    """
    Validates LLM-generated command before it reaches the robot.
    Returns a CommandResult — always check .approved before executing.
    """

    # 1. Topic whitelist check
    if topic not in ALLOWED_TOPICS:
        return CommandResult(
            approved=False,
            reject_reason=f"Topic '{topic}' is not in the allowed list: {ALLOWED_TOPICS}"
        )

    # 2. Clamp velocities with a warning if they were out of range
    original_linear  = linear_x
    original_angular = angular_z
    linear_x  = max(MIN_LINEAR_VEL,  min(MAX_LINEAR_VEL,  linear_x))
    angular_z = max(MIN_ANGULAR_VEL, min(MAX_ANGULAR_VEL, angular_z))

    was_clamped = (linear_x != original_linear or angular_z != original_angular)

    # 3. Duration check
    if duration < MIN_DURATION:
        duration = MIN_DURATION
    if duration > MAX_DURATION:
        return CommandResult(
            approved=False,
            reject_reason=f"Duration {duration}s exceeds maximum allowed {MAX_DURATION}s"
        )

    return CommandResult(
        approved=True,
        linear_x=round(linear_x, 3),
        angular_z=round(angular_z, 3),
        duration=round(duration, 2),
        topic=topic
    )


def compute_duration_for_distance(distance_m: float,
                                   speed_mps: float = 0.15) -> float:
    """
    Helper: convert 'move 1 meter' into a duration.
    The LLM will use this logic implicitly via the system prompt,
    but you can also call it explicitly in your server.
    speed default is conservative 0.15 m/s for Burger on flat ground.
    """
    if speed_mps <= 0:
        speed_mps = 0.15
    return round(abs(distance_m) / speed_mps, 2)


def compute_duration_for_rotation(degrees: float,
                                   angular_vel: float = 0.5) -> float:
    """
    Helper: convert 'turn 90 degrees' into a duration.
    """
    import math
    radians = abs(math.radians(degrees))
    return round(radians / angular_vel, 2)
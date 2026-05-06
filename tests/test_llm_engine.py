# Quick CLI test — run this before wiring up the web server.
# Lets you verify the LLM → safety → robot pipeline works
# without any web complexity.

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'core'))

from bridge_client import BridgeClient
from llm_engine import LLMEngine
import time

def main():
    print("Connecting to rosbridge...")
    bridge = BridgeClient(host='localhost', port=9090)
    time.sleep(0.5)   # let odom subscriber settle

    print("Starting LLM engine...")
    engine = LLMEngine(bridge_client=bridge)
    engine.refresh_topics()

    print("\n=== NL Robot Control CLI ===")
    print("Type a command in plain English. Examples:")
    print("  'move forward 0.5 meters'")
    print("  'turn left 90 degrees'")
    print("  'go backward slowly'")
    print("  'spin in a circle'")
    print("Type 'quit' to exit, 'reset' to clear history.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not user_input:
            continue
        if user_input.lower() == 'quit':
            break
        if user_input.lower() == 'reset':
            engine.reset_history()
            print("History cleared.")
            continue

        print("Robot: ", end="", flush=True)
        response = engine.chat(user_input)
        print(response)
        print()

    bridge.close()
    print("Goodbye.")

if __name__ == '__main__':
    main()
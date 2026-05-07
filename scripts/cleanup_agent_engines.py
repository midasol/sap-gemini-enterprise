
import os
import time
import vertexai
from vertexai import agent_engines

# Configuration
PROJECT_ID = os.getenv("PROJECT_ID", "")
LOCATION = os.getenv("REGION", "us-central1")

def cleanup_all_engines():
    print(f"Initializing Vertex AI for project '{PROJECT_ID}' in '{LOCATION}'...")
    vertexai.init(project=PROJECT_ID, location=LOCATION)

    print("Listing all Reasoning Engines...")
    try:
        # List all agent engines in the location
        engines = list(agent_engines.list())
        
        if not engines:
            print("No Agent Engines found to delete.")
            return

        print(f"Found {len(engines)} Agent Engines. Starting cleanup...")

        for engine in engines:
            resource_name = engine.resource_name
            print(f"\nTargeting: {resource_name}")
            print(f"  - Display Name: {engine.display_name}")
            
            try:
                print(f"  - Deleting with force=True...")
                # The 'force=True' argument is key to deleting engines with active sessions
                engine.delete(force=True)
                print(f"  - Successfully deleted: {resource_name}")
                print("  - Waiting 10 seconds to respect rate limits...")
                time.sleep(10)
            except Exception as e:
                print(f"  - Failed to delete {resource_name}: {e}")
                if "RATE_LIMIT_EXCEEDED" in str(e):
                    print("  - Rate limit hit. Waiting 60 seconds before continuing...")
                    time.sleep(60)

    except Exception as e:
        print(f"An error occurred during listing or cleanup: {e}")

if __name__ == "__main__":
    cleanup_all_engines()

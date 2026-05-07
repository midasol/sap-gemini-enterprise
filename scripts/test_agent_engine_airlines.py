
import os
import asyncio
from vertexai import agent_engines
import vertexai
from google.genai import types as genai_types

# Configuration
PROJECT_ID = os.getenv("PROJECT_ID", "")
LOCATION = os.getenv("REGION", "us-central1")
STAGING_BUCKET = os.getenv("STAGING_BUCKET", os.getenv("GCS_STAGING_BUCKET", ""))
RESOURCE_NAME = os.getenv("AGENT_ENGINE_RESOURCE_NAME", "")

print(f"Initializing Vertex AI SDK...")
vertexai.init(
    project=PROJECT_ID,
    location=LOCATION,
    staging_bucket=STAGING_BUCKET,
)

async def main():
    print(f"Connecting to Agent Engine: {RESOURCE_NAME}")
    try:
        remote_app = agent_engines.get(RESOURCE_NAME)
        
        print("Creating session...")
        session = await remote_app.async_create_session(user_id="test_user")
        print(f"Session created: {session}")

        query = "Retrieve all airlines from the Z_TRAVEL_RECO_SRV service and the AirlineSet entity set."
        print(f"\nSending query: '{query}'")
        
        async for event in remote_app.async_stream_query(
            user_id="test_user",
            session_id=session["id"], 
            message=query,
        ):
            print(event)
            
    except Exception as e:
        print(f"Test failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())


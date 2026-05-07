import os
import asyncio
from vertexai import agent_engines
import vertexai

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
        
        # Inspect the object
        print(f"Type of remote_app: {type(remote_app)}")
        # print(f"Attributes: {dir(remote_app)}")

        query_text = "What OData services are available in the SAP system?"
        print(f"\nSending query: '{query_text}'")
        
        # response = remote_app.query(query=query_text)
        # print("\nResponse:")
        # print(response)
        
        async for chunk in remote_app.async_stream_query(query=query_text):
            print(chunk)
            
    except Exception as e:
        print(f"Test failed: {e}")
            
    except Exception as e:
        print(f"Test failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())

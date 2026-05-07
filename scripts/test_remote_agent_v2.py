
import asyncio
import os
import vertexai
from vertexai import agent_engines
from google.genai import types
from google.adk.events import Event

# New Agent Engine Resource Name
AGENT_ENGINE_RESOURCE_NAME = os.getenv("AGENT_ENGINE_RESOURCE_NAME", "")
PROJECT_ID = os.getenv("PROJECT_ID", "")
LOCATION = os.getenv("REGION", "us-central1")

async def test_remote_agent():
    print(f"Connecting to remote Agent Engine: {AGENT_ENGINE_RESOURCE_NAME}")
    print(f"Project: {PROJECT_ID}, Location: {LOCATION}")

    vertexai.init(
        project=PROJECT_ID,
        location=LOCATION,
    )

    try:
        remote_app = agent_engines.get(AGENT_ENGINE_RESOURCE_NAME)
        print("Successfully connected to remote_app.")

        print("Creating a remote session...")
        remote_session = await remote_app.async_create_session(user_id="test_user_remote_v2")
        session_id = remote_session["id"]
        print(f"Remote session created with ID: {session_id}")

        query = "SAP 서비스 목록을 조회해줘"
        print(f"Sending query: '{query}'")

        events = []
        async for event in remote_app.async_stream_query(
            user_id="test_user_remote_v2",
            session_id=session_id,
            message=query,
        ):
            events.append(event)
            # Print minimal info to reduce noise, full object printed if needed
            if isinstance(event, Event):
                 if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                             print(f"  -> Response Part: {part.text[:50]}..." if len(part.text) > 50 else f"  -> Response Part: {part.text}")
            elif isinstance(event, dict) and "message" in event:
                 print(f"  -> Error Event: {event['message']}")

        print("\n--- Final Response ---")
        final_text_responses = [
            e for e in events
            if isinstance(e, Event) and e.is_final_response()
        ]
        if final_text_responses and final_text_responses[0].content and final_text_responses[0].content.parts:
            print(f"Final text: {final_text_responses[0].content.parts[0].text}")
        else:
            print("No final text response found or final response is not text.")

    except Exception as e:
        print(f"Error during remote agent test: {e}")

if __name__ == "__main__":
    asyncio.run(test_remote_agent())

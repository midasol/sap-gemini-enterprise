"""Run ADK web server with HTTPS (self-signed cert for development)."""

import uvicorn
from google.adk.cli.cli_tools_click import get_fast_api_app

app = get_fast_api_app(
    agents_dir=".",
    web=True,
    allow_origins=["https://localhost:8000"],
)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ssl_keyfile="certs/key.pem",
        ssl_certfile="certs/cert.pem",
    )

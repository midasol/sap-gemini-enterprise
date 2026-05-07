"""Stdio-based Gateway Server implementation"""

import sys
# Debug print at the very top level
print("[DEBUG] sap_gw_connector.transports.stdio module loaded", file=sys.stderr)

import asyncio
import logging
from pathlib import Path
import argparse

from dotenv import load_dotenv
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from sap_agent.sap_gw_connector.tools import tool_registry
from sap_agent.sap_gw_connector.protocol.schemas import ToolCallRequest
from sap_agent.sap_gw_connector.config.settings import SAPConnectionConfig, GWServerConfig, SecurityConfig, AppConfig

logger = logging.getLogger(__name__)


def find_env_file() -> Path | None:
    """Find .env.server file in multiple possible locations"""
    try:
        # Calculate project root from file location
        project_root = Path(__file__).parent.parent.parent.parent.parent.parent
        sys.stderr.write(f"[DEBUG] Calculated project root: {project_root}\n")

        env_paths = [
            Path.cwd() / ".env.server",              # Current working directory
            project_root / ".env.server",            # Project root
            Path.home() / ".env.server",             # User home directory
        ]

        for path in env_paths:
            sys.stderr.write(f"[DEBUG] Checking env path: {path}\n")
            if path.exists():
                logger.info(f"Found .env.server at: {path}")
                sys.stderr.write(f"[DEBUG] Found .env.server at: {path}\n")
                return path
        return None
    except Exception as e:
        sys.stderr.write(f"[DEBUG] Error in find_env_file: {e}\n")
        return None


async def main(sap_connection_args: dict) -> None:
    """Main entry point for stdio MCP server"""
    sys.stderr.write("[DEBUG] Entering async main...\n")

    # Load environment variables (still useful for non-SAP Gateway config)
    env_path = find_env_file()
    if env_path:
        load_dotenv(dotenv_path=env_path)
        logger.info(f"Loaded server environment variables from {env_path}")
    else:
        logger.warning("Starting server without environment file")

    # Create SAPConnectionConfig from provided arguments
    # Bypass environment variable lookup for SAP credentials
    sap_config = SAPConnectionConfig(**sap_connection_args)
    
    # Manually initialize the global config instance for Gateway Server
    # This part should ideally be refactored into a proper config loading utility
    # but for now, we ensure the Gateway server's internal config is set up.
    # We create a dummy AppConfig to hold the SAP config
    try:
        global_app_config = AppConfig(
            sap=sap_config,
            server=GWServerConfig(), # Load server config from env vars
            security=SecurityConfig(), # Load security config from env vars
        )
        # Override the global config instance in settings.py
        from sap_agent.sap_gw_connector.config import settings
        settings.config = global_app_config
        sys.stderr.write("[DEBUG] AppConfig initialized successfully.\n")
    except Exception as e:
        sys.stderr.write(f"[DEBUG] Error initializing AppConfig: {e}\n")
        raise

    # Create Gateway server
    server = Server("sap-gw")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        """List all available tools"""
        try:
            tool_list = tool_registry.list_tools()
            return [
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    inputSchema=tool.inputSchema,
                )
                for tool in tool_list
            ]
        except Exception as e:
            sys.stderr.write(f"[DEBUG] Error listing tools: {e}\n")
            raise

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        """Call a tool with the given arguments"""
        try:
            sys.stderr.write(f"[DEBUG] Calling tool: {name}\n")
            # Create tool call request
            request = ToolCallRequest(name=name, arguments=arguments)

            # Call the tool
            result = await tool_registry.call_tool(request)

            # Return result as text content
            return [types.TextContent(type="text", text=str(result.content))]
        except Exception as e:
            logger.error(f"Tool call failed: {e}", exc_info=True)
            sys.stderr.write(f"[DEBUG] Tool call failed: {e}\n")
            return [types.TextContent(type="text", text=f"Error: {str(e)}")]

    # Run the server
    logger.info("Starting SAP Gateway stdio server...")
    sys.stderr.write("[DEBUG] Starting stdio server run loop...\n")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream, write_stream, server.create_initialization_options()
        )


def cli_main() -> None:
    """CLI entry point for console_scripts"""
    sys.stderr.write("[DEBUG] Starting sap-gw-server-stdio CLI main...\n")
    
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG, # Changed to DEBUG
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    parser = argparse.ArgumentParser(description="SAP Gateway Stdio Server")
    parser.add_argument("--sap-host", required=True, help="SAP server hostname")
    parser.add_argument("--sap-port", type=int, default=44300, help="SAP server port")
    parser.add_argument("--sap-client", default="100", help="SAP client number")
    parser.add_argument("--sap-username", required=True, help="SAP username")
    parser.add_argument("--sap-password", required=True, help="SAP password")
    parser.add_argument("--sap-verify-ssl", type=bool, default=False, help="Verify SSL certificates")
    parser.add_argument("--sap-timeout", type=int, default=30, help="Request timeout in seconds")
    parser.add_argument("--sap-retry-attempts", type=int, default=3, help="Number of retry attempts")
    
    try:
        args = parser.parse_args()
        sys.stderr.write(f"[DEBUG] Arguments parsed successfully. Host: {args.sap_host}\n")
    except SystemExit as e:
        sys.stderr.write(f"[DEBUG] Argument parsing failed: {e}\n")
        # Print help to stderr if possible
        parser.print_help(sys.stderr)
        raise

    sap_connection_args = {
        "host": args.sap_host,
        "port": args.sap_port,
        "client": args.sap_client,
        "username": args.sap_username,
        "password": args.sap_password,
        "verify_ssl": args.sap_verify_ssl,
        "timeout": args.sap_timeout,
        "retry_attempts": args.sap_retry_attempts,
    }

    logger.debug(f"Gateway Server received SAP connection arguments: {sap_connection_args['host']}:{sap_connection_args['port']} client {sap_connection_args['client']}")

    # Run async main
    try:
        asyncio.run(main(sap_connection_args))
    except Exception as e:
        sys.stderr.write(f"[DEBUG] Uncaught exception in main loop: {e}\n")
        raise


if __name__ == "__main__":
    cli_main()

"""SAP Authentication Tool"""

import logging
from typing import Any, Dict

from sap_agent.sap_gw_connector.tools.base import SAPTool
from sap_agent.sap_gw_connector.core.sap_client import SAPClient

logger = logging.getLogger(__name__)


class SAPAuthenticateTool(SAPTool):
    """Tool for authenticating with SAP Gateway"""

    @property
    def name(self) -> str:
        return "sap_authenticate"

    @property
    def description(self) -> str:
        return "Authenticate with SAP Gateway using SAP OAuth 2.0 Authorization Code flow"

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute authentication"""
        try:
            from sap_agent.sap_gw_connector.config.settings import get_config

            config = get_config(require_sap=True)

            async with SAPClient(config.sap) as client:
                success = await client.authenticate()

            if success:
                return {
                    "success": True,
                    "message": "Successfully authenticated with SAP Gateway",
                    "auth_type": config.sap.auth_type,
                    "host": config.sap.host,
                    "client": config.sap.client,
                }
            else:
                return {"success": False, "error": "Authentication failed"}

        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return {"success": False, "error": str(e)}

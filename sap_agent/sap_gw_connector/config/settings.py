"""Configuration settings for SAP Gateway Connector"""

import os
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class SAPConnectionConfig(BaseSettings):
    """SAP Gateway connection configuration"""

    host: str = Field(..., description="SAP server hostname")
    port: int = Field(44300, description="SAP server port")
    client: str = Field("100", description="SAP client number")
    auth_type: str = Field("sap_oauth", description="Authentication type (only 'sap_oauth' supported)")
    oauth_client_id: Optional[str] = Field(None, description="OAuth 2.0 client ID")
    oauth_client_secret: Optional[str] = Field(None, description="OAuth 2.0 client secret", repr=False)
    oauth_token_url: Optional[str] = Field(None, description="OAuth 2.0 token endpoint URL")
    oauth_scope: Optional[str] = Field(None, description="OAuth 2.0 scope (optional)")
    oauth_csrf_for_writes: bool = Field(
        False, description="Fetch CSRF token for write operations when using OAuth"
    )
    oauth_authorize_url: Optional[str] = Field(
        None,
        description="SAP OAuth 2.0 authorization endpoint URL (for Authorization Code flow)",
    )
    oauth_redirect_uri: Optional[str] = Field(
        None,
        description="OAuth redirect URI for Authorization Code flow callback",
    )
    verify_ssl: bool = Field(
        False, description="Verify SSL certificates (set True for production)"
    )
    timeout: int = Field(30, description="Request timeout in seconds")
    retry_attempts: int = Field(3, description="Number of retry attempts")

    model_config = {"env_prefix": "SAP_"}

    @field_validator("host")
    @classmethod
    def validate_host(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("SAP host cannot be empty")
        return v.strip()

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v

    @field_validator("auth_type")
    @classmethod
    def validate_auth_type(cls, v: str) -> str:
        if v.lower() != "sap_oauth":
            raise ValueError("Only auth_type 'sap_oauth' is supported")
        return v.lower()

    @model_validator(mode="after")
    def validate_credentials(self) -> "SAPConnectionConfig":
        if (
            not self.oauth_client_id
            or not self.oauth_client_secret
            or not self.oauth_token_url
        ):
            raise ValueError(
                "oauth_client_id, oauth_client_secret, and oauth_token_url "
                "are required for sap_oauth authentication"
            )
        if not self.oauth_authorize_url:
            raise ValueError(
                "oauth_authorize_url is required for sap_oauth authentication "
                "(SAP OAuth Authorization endpoint)"
            )
        return self


class GWServerConfig(BaseSettings):
    """Gateway server configuration"""

    host: str = Field("0.0.0.0", description="Server bind address")
    port: int = Field(8000, description="Server port")
    log_level: str = Field("INFO", description="Logging level")
    max_workers: int = Field(1, description="Maximum worker threads")
    debug: bool = Field(False, description="Enable debug mode")
    reload: bool = Field(False, description="Enable auto-reload")
    services_config_path: Optional[str] = Field(
        None, description="Path to services YAML configuration file"
    )

    model_config = {"env_prefix": "SAP_GW_"}

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Log level must be one of: {valid_levels}")
        return v.upper()


class SecurityConfig(BaseSettings):
    """Security configuration"""

    session_timeout: int = Field(3600, description="Session timeout in seconds")
    max_concurrent_sessions: int = Field(100, description="Maximum concurrent sessions")
    rate_limit_per_minute: int = Field(60, description="Rate limit per minute")
    encryption_key: Optional[str] = Field(
        None, description="Encryption key for sensitive data", repr=False
    )

    model_config = {"env_prefix": "SECURITY_"}

    @field_validator("session_timeout")
    @classmethod
    def validate_session_timeout(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Session timeout must be positive")
        return v


class AppConfig(BaseSettings):
    """Main application configuration"""

    # Core configurations
    sap: SAPConnectionConfig
    server: GWServerConfig
    security: SecurityConfig

    model_config = {
        "env_file": ".env",
        "env_nested_delimiter": "__",
        "case_sensitive": False,
        "extra": "allow",
    }

    @classmethod
    def load_from_env(cls, require_sap: bool = True) -> "AppConfig":
        """Load configuration from environment variables"""
        # Try to load SAP config, use defaults if not available and not required
        try:
            sap_config = SAPConnectionConfig()  # type: ignore[call-arg]
        except Exception as e:
            if require_sap:
                raise e
            sap_config = SAPConnectionConfig(  # type: ignore[call-arg]
                host="localhost",
                auth_type="sap_oauth",
                oauth_client_id="test",
                oauth_client_secret="test",
                oauth_token_url="https://localhost/token",
                oauth_authorize_url="https://localhost/authorize",
            )

        return cls(
            sap=sap_config,
            server=GWServerConfig(),  # type: ignore[call-arg]
            security=SecurityConfig(),  # type: ignore[call-arg]
        )

    def validate_required_env_vars(self) -> None:
        """Validate that all required environment variables are set"""
        required_vars = [
            "SAP_HOST",
            "SAP_OAUTH_CLIENT_ID",
            "SAP_OAUTH_CLIENT_SECRET",
            "SAP_OAUTH_TOKEN_URL",
            "SAP_OAUTH_AUTHORIZE_URL",
        ]
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"Missing required environment variables: {missing_vars}")


# Global configuration instance
config: Optional[AppConfig] = None


def get_config(require_sap: bool = False) -> AppConfig:
    """Get the global configuration instance"""
    global config
    if config is None:
        config = AppConfig.load_from_env(require_sap=require_sap)
        if require_sap:
            config.validate_required_env_vars()
    return config


def reload_config() -> AppConfig:
    """Reload configuration from environment"""
    global config
    config = None
    return get_config()


def get_services_config_path() -> Optional[Path]:
    """Get the path to services configuration file from environment or config"""
    # Check environment variable first
    env_path = os.getenv("SAP_SERVICES_CONFIG_PATH")
    if env_path:
        return Path(env_path)

    # Check server config
    cfg = get_config(require_sap=False)
    if cfg.server.services_config_path:
        return Path(cfg.server.services_config_path)

    return None

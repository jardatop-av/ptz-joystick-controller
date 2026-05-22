from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StaticNetworkConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    address: str | None = None
    netmask: str | None = None
    gateway: str | None = None
    dns: list[str] = Field(default_factory=list)


class SafeRollbackConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    timeout_seconds: int = Field(default=120, ge=10)


class NetworkConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    management_enabled: bool = True
    interface: str = "eth0"
    mode: str = "dhcp"
    static: StaticNetworkConfig = Field(default_factory=StaticNetworkConfig)
    safe_rollback: SafeRollbackConfig = Field(default_factory=SafeRollbackConfig)

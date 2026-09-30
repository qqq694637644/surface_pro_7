from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ActuatorError(RuntimeError):
    pass


class ProfileActuator(ABC):
    name = "base"

    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def inspect(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def apply_profile(self, backend_profile: str) -> dict[str, Any]:
        raise NotImplementedError

    def reset_override(self) -> dict[str, Any]:
        raise ActuatorError(f"{self.name} does not support override reset")


class ParameterActuator(ABC):
    @abstractmethod
    def snapshot(self, parameter: str) -> Any:
        raise NotImplementedError

    @abstractmethod
    def apply(self, parameter: str, value: Any) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def restore(self, parameter: str, value: Any) -> dict[str, Any]:
        raise NotImplementedError

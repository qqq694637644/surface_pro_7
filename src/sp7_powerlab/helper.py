from __future__ import annotations

import json
import os
import signal
import socket
from pathlib import Path
from typing import Any

from .actuators.base import ActuatorError, ParameterActuator
from .actuators.sysfs import SysfsParameterActuator
from .safety import PARAMETER_RULES, validate_parameter


class RootHelperProtocolError(RuntimeError):
    pass


class RootHelperClient(ParameterActuator):
    def __init__(self, socket_path: Path, timeout: float = 3.0):
        self.socket_path = socket_path
        self.timeout = timeout

    def available(self) -> bool:
        return self.socket_path.exists()

    def _request(self, action: str, parameter: str, value: Any = None) -> Any:
        payload = {"action": action, "parameter": parameter}
        if action in {"apply", "restore"}:
            payload["value"] = value
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(str(self.socket_path))
                sock.sendall(raw)
                chunks = []
                while True:
                    part = sock.recv(65536)
                    if not part:
                        break
                    chunks.append(part)
                    if b"\n" in part:
                        break
        except OSError as exc:
            raise ActuatorError(f"root helper unavailable: {exc}") from exc
        try:
            response = json.loads(b"".join(chunks).decode().strip())
        except json.JSONDecodeError as exc:
            raise ActuatorError("invalid root helper response") from exc
        if not response.get("ok"):
            raise ActuatorError(str(response.get("error") or "root helper failed"))
        return response.get("result")

    def snapshot(self, parameter: str) -> Any:
        return self._request("snapshot", parameter)

    def apply(self, parameter: str, value: Any) -> dict[str, Any]:
        return self._request("apply", parameter, value)

    def restore(self, parameter: str, value: Any) -> dict[str, Any]:
        return self._request("restore", parameter, value)


class RootHelperServer:
    def __init__(self, socket_path: Path, allow_user: str):
        self.socket_path = socket_path
        self.allow_user = allow_user
        self.actuator = SysfsParameterActuator()
        self.stop = False

    def _install_signals(self) -> None:
        def stop(*_args):
            self.stop = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

    def _authorize_parameter(self, parameter: str) -> None:
        if parameter not in PARAMETER_RULES:
            raise RootHelperProtocolError(f"parameter not allowed by root helper: {parameter}")

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        parameter = str(request.get("parameter") or "")
        self._authorize_parameter(parameter)
        if action == "snapshot":
            return {"ok": True, "result": self.actuator.snapshot(parameter)}
        if action == "apply":
            errors = validate_parameter(parameter, request.get("value"), for_auto_trial=False)
            if errors:
                raise RootHelperProtocolError("; ".join(errors))
            return {
                "ok": True,
                "result": self.actuator.apply(parameter, request.get("value")),
            }
        if action == "restore":
            return {
                "ok": True,
                "result": self.actuator.restore(parameter, request.get("value")),
            }
        raise RootHelperProtocolError(f"unknown helper action: {action}")

    def run(self) -> None:
        if os.name != "posix":
            raise SystemExit("root-helper is only supported on POSIX/Linux")
        import pwd

        if not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise SystemExit("root-helper must run as root")
        user = pwd.getpwnam(self.allow_user)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        self._install_signals()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.socket_path))
            os.chown(self.socket_path, user.pw_uid, user.pw_gid)
            os.chmod(self.socket_path, 0o600)
            server.listen(8)
            server.settimeout(1.0)
            while not self.stop:
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                with conn:
                    try:
                        raw = b""
                        while b"\n" not in raw and len(raw) < 1024 * 1024:
                            part = conn.recv(65536)
                            if not part:
                                break
                            raw += part
                        request = json.loads(raw.decode().strip())
                        if not isinstance(request, dict):
                            raise RootHelperProtocolError("request must be an object")
                        response = self.handle(request)
                    except Exception as exc:
                        response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    conn.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode())
        self.socket_path.unlink(missing_ok=True)

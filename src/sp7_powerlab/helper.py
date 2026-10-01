from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import time
from pathlib import Path
from typing import Any

from .actuators.hwp import HWPActuator

ROOT_HELPER_PROTOCOL_VERSION = 2


def helper_implementation_identity() -> str:
    package_root = Path(__file__).resolve().parent
    paths = (
        package_root / "helper.py",
        package_root / "actuators" / "hwp.py",
    )
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(package_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class HelperProtocolError(RuntimeError):
    pass


def _recv_line(conn: socket.socket, limit: int = 65536) -> bytes:
    data = bytearray()
    while len(data) < limit:
        chunk = conn.recv(4096)
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in chunk:
            break
    if len(data) >= limit:
        raise HelperProtocolError("request too large")
    return bytes(data).split(b"\n", 1)[0]


class RootHelperClient:
    def __init__(self, socket_path: Path):
        self.socket_path = socket_path

    def available(self) -> bool:
        return self.socket_path.exists()

    def request(self, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        request = {"action": action, "payload": payload or {}}
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(3.0)
                client.connect(str(self.socket_path))
                client.sendall(json.dumps(request).encode("utf-8") + b"\n")
                raw = _recv_line(client)
        except OSError as exc:
            raise HelperProtocolError(str(exc)) from exc
        response = json.loads(raw.decode("utf-8"))
        if not response.get("ok"):
            raise HelperProtocolError(str(response.get("error") or "helper error"))
        return response["result"]

    def inspect(self) -> dict[str, Any]:
        return self.request("inspect")

    def snapshot(self) -> dict[str, Any]:
        return self.request("snapshot")

    def apply_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "epp": envelope["epp"],
            "max_perf_pct": int(envelope["max_perf_pct"]),
            "turbo": bool(envelope["turbo"]),
        }
        return self.request("apply", payload)

    def restore(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        return self.request("restore", {"snapshot": snapshot})


class RootHelperServer:
    def __init__(
        self,
        socket_path: Path,
        allow_uid: int,
        actuator: HWPActuator | None = None,
    ):
        self.socket_path = socket_path
        self.allow_uid = allow_uid
        self.actuator = actuator or HWPActuator()

    def _peer_uid(self, conn: socket.socket) -> int | None:
        if not hasattr(socket, "SO_PEERCRED"):
            return None
        raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
        return uid

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        payload = request.get("payload") or {}
        if action == "inspect":
            return {
                **self.actuator.inspect(),
                "protocol_version": ROOT_HELPER_PROTOCOL_VERSION,
                "implementation_identity": helper_implementation_identity(),
            }
        if action == "snapshot":
            return self.actuator.snapshot()
        if action == "apply":
            allowed = {"epp", "max_perf_pct", "turbo"}
            if set(payload) != allowed:
                raise HelperProtocolError("apply payload must contain only epp/max_perf_pct/turbo")
            return self.actuator.apply_values(
                epp=str(payload["epp"]),
                max_perf_pct=int(payload["max_perf_pct"]),
                turbo=payload["turbo"],
            )
        if action == "restore":
            if set(payload) != {"snapshot"} or not isinstance(payload["snapshot"], dict):
                raise HelperProtocolError("restore requires one snapshot object")
            return self.actuator.restore(payload["snapshot"])
        raise HelperProtocolError(f"unsupported action: {action}")

    def serve_forever(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(self.socket_path))
            if hasattr(os, "chown"):
                os.chown(self.socket_path, self.allow_uid, -1)
            os.chmod(self.socket_path, 0o600)
            server.listen(16)
            while True:
                conn, _ = server.accept()
                with conn:
                    uid = None
                    request: dict[str, Any] = {}
                    try:
                        uid = self._peer_uid(conn)
                        if uid is not None and uid != self.allow_uid:
                            raise HelperProtocolError(f"uid {uid} is not allowed")
                        raw = _recv_line(conn)
                        request = json.loads(raw.decode("utf-8"))
                        result = self.dispatch(request)
                        response = {"ok": True, "result": result}
                        audit = {
                            "ts": time.time(),
                            "uid": uid,
                            "action": request.get("action"),
                            "success": True,
                        }
                    except Exception as exc:  # helper boundary
                        response = {"ok": False, "error": str(exc)}
                        audit = {
                            "ts": time.time(),
                            "uid": uid,
                            "action": request.get("action"),
                            "success": False,
                            "error": str(exc),
                        }
                    print(
                        json.dumps(audit, ensure_ascii=False, sort_keys=True),
                        flush=True,
                    )
                    conn.sendall(json.dumps(response).encode("utf-8") + b"\n")

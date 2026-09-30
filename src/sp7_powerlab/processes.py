from __future__ import annotations

import fnmatch
from typing import Any

import psutil


PROCESS_CLASSES: dict[str, tuple[str, ...]] = {
    "compile": (
        "gcc", "g++", "cc1", "cc1plus", "clang", "clang++", "rustc", "cargo",
        "make", "ninja", "cmake", "go", "javac", "mvn", "gradle",
    ),
    "language_server": (
        "*language-server*", "*lsp*", "rust-analyzer", "clangd", "pyright*",
        "typescript-language-server", "jdtls", "gopls",
    ),
    "browser": (
        "firefox*", "chrome*", "chromium*", "brave*", "vivaldi*", "microsoft-edge*",
    ),
    "editor": (
        "code", "code-*", "codium", "zed", "nvim", "vim", "emacs*", "idea*", "pycharm*",
    ),
    "office": (
        "libreoffice*", "soffice*", "onlyoffice*", "evince", "okular", "zathura",
    ),
    "video_call": (
        "zoom*", "teams*", "slack*", "discord*", "webex*", "skype*",
    ),
    "remote": (
        "remmina*", "rustdesk*", "anydesk*", "parsec*", "xfreerdp*", "rdesktop*",
    ),
    "media": (
        "mpv", "vlc", "totem", "celluloid", "rhythmbox*", "spotify*",
    ),
}


def classify_process(name: str | None, executable: str | None = None) -> str:
    values = [str(name or "").lower(), str(executable or "").split("/")[-1].lower()]
    for cls, patterns in PROCESS_CLASSES.items():
        for pattern in patterns:
            if any(fnmatch.fnmatch(value, pattern.lower()) for value in values if value):
                return cls
    return "other"


class ProcessSampler:
    def __init__(
        self,
        top_n: int = 12,
        *,
        include_tree: bool = True,
        store_executable: bool = True,
    ):
        self.top_n = top_n
        self.include_tree = include_tree
        self.store_executable = store_executable
        self._primed = False

    def sample(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        attrs = ["pid", "name", "create_time", "memory_info", "io_counters"]
        if self.store_executable:
            attrs.append("exe")
        if self.include_tree:
            attrs.append("ppid")
        for proc in psutil.process_iter(attrs):
            try:
                cpu = proc.cpu_percent(interval=None)
                info = proc.info
                mem = info.get("memory_info")
                io = info.get("io_counters")
                entry = {
                    "pid": int(info["pid"]),
                    "ppid": int(info.get("ppid") or 0) if self.include_tree else None,
                    "start_time": float(info.get("create_time") or 0.0),
                    "name": info.get("name"),
                    "executable": info.get("exe") if self.store_executable else None,
                    "cpu_percent": float(cpu),
                    "rss_bytes": int(mem.rss) if mem else None,
                    "read_bytes": int(io.read_bytes) if io else None,
                    "write_bytes": int(io.write_bytes) if io else None,
                }
                entry["class"] = classify_process(entry["name"], entry["executable"])
                entries.append(entry)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
                continue
        entries.sort(key=lambda item: item.get("cpu_percent") or 0.0, reverse=True)
        self._primed = True
        return entries[: self.top_n]


def summarize_processes(processes: list[dict[str, Any]]) -> dict[str, Any]:
    class_cpu: dict[str, float] = {}
    top = []
    for proc in processes:
        cls = str(proc.get("class") or "other")
        class_cpu[cls] = class_cpu.get(cls, 0.0) + float(proc.get("cpu_percent") or 0.0)
        top.append(
            {
                "pid": proc.get("pid"),
                "ppid": proc.get("ppid"),
                "name": proc.get("name"),
                "class": cls,
                "cpu_percent": proc.get("cpu_percent"),
            }
        )
    dominant = max(class_cpu.items(), key=lambda x: x[1])[0] if class_cpu else None
    return {
        "class_cpu_percent": class_cpu,
        "dominant_class": dominant,
        "top": top[:8],
        "background_cpu_percent": sum(class_cpu.values()),
    }

from __future__ import annotations

import os
import queue
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .backends import ExecutionBackend, NativeLinuxBackend, WindowsBackend, WslBackend
from .process import AdaptiveStreamDecoder
from .process_tree import process_options, terminate_tree


@dataclass(frozen=True, slots=True)
class ManagedProcessSnapshot:
    process_id: str
    state: str
    pid: int | None
    exit_code: int | None
    started_at: float | None


@dataclass(slots=True)
class _ManagedProcess:
    process_id: str
    proc: subprocess.Popen[bytes]
    started_at: float
    stop_requested: bool = False
    windows_tree: bool = False


class ProcessSupervisor:
    """Own long-running development processes started by the Control Panel.

    One-shot commands continue to use ProcessRunner. This supervisor is deliberately
    separate so a dev server does not hold the global command runner busy forever.
    """

    def __init__(self, log: Callable[[str], None]) -> None:
        self.log = log
        self._items: dict[str, _ManagedProcess] = {}
        self._last_exit: dict[str, int] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _no_window_flags() -> int:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)

    @staticmethod
    def shell_argv(backend: ExecutionBackend, root: str, command: str, environment: dict[str, str] | None = None) -> list[str]:
        env = dict(environment or {})
        assignments = " ".join(f"export {key}={shlex.quote(str(value))};" for key, value in env.items())
        body = f'export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"; {assignments} cd {shlex.quote(root)} && exec {command}'
        if isinstance(backend, WindowsBackend):
            def ps_quote(value):
                return "'" + value.replace("'", "''") + "'"
            assignments = " ".join(f"$env:{key}={ps_quote(str(value))};" for key, value in env.items())
            script = f"{assignments} Set-Location -LiteralPath {ps_quote(root)}; {command}"
            return backend.powershell_argv(script)
        if isinstance(backend, WslBackend):
            return ["wsl.exe", "-d", backend.distro, "--", backend.shell, "-lc", body]
        if isinstance(backend, NativeLinuxBackend):
            return [backend.shell, "-lc", body]
        raise RuntimeError(f"Persistent processes are not supported by backend {type(backend).__name__}")

    def _pump_output(self, item: _ManagedProcess) -> None:
        proc = item.proc
        if proc.stdout is None:
            return
        decoder = AdaptiveStreamDecoder()
        output_queue: queue.Queue[bytes | None] = queue.Queue()

        def reader() -> None:
            try:
                while True:
                    chunk = proc.stdout.read1(4096)
                    if not chunk:
                        break
                    output_queue.put(chunk)
            finally:
                proc.stdout.close()
                output_queue.put(None)

        threading.Thread(target=reader, daemon=True, name=f"koali-supervisor-reader-{item.process_id}").start()
        text_buffer = ""
        done = False
        while not done:
            chunk = output_queue.get()
            if chunk is None:
                done = True
                text = decoder.feed(b"", final=True)
            else:
                text = decoder.feed(chunk)
            text_buffer += text
            while "\n" in text_buffer:
                line, text_buffer = text_buffer.split("\n", 1)
                self.log(f"[{item.process_id}] {line.rstrip(chr(13))}")
        if text_buffer:
            self.log(f"[{item.process_id}] {text_buffer.rstrip(chr(13))}")
        code = int(proc.wait())
        with self._lock:
            self._last_exit[item.process_id] = code
            current = self._items.get(item.process_id)
            if current is item:
                self._items.pop(item.process_id, None)
        label = "stopped" if item.stop_requested else "exited"
        self.log(f"[{item.process_id}] {label} with exit {code}")

    def start(
        self,
        process_id: str,
        argv: list[str],
        *,
        cwd: str | None = None,
        windows_tree: bool = False,
    ) -> bool:
        with self._lock:
            current = self._items.get(process_id)
            if current and current.proc.poll() is None:
                self.log(f"[{process_id}] already running (pid {current.proc.pid})")
                return True
        self.log(f"> Start {process_id}")
        self.log("  " + subprocess.list2cmdline(argv))
        try:
            flags = self._no_window_flags()
            if windows_tree:
                flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            proc = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **process_options(),
            )
        except OSError as exc:
            self.log(f"ERROR [{process_id}] cannot start: {exc}")
            return False
        item = _ManagedProcess(process_id=process_id, proc=proc, started_at=time.time(), windows_tree=windows_tree)
        with self._lock:
            self._items[process_id] = item
            self._last_exit.pop(process_id, None)
        threading.Thread(target=self._pump_output, args=(item,), daemon=True, name=f"koali-supervisor-{process_id}").start()
        self.log(f"[{process_id}] STARTING pid={proc.pid}")
        return True

    def start_shell(
        self,
        process_id: str,
        backend: ExecutionBackend,
        root: str,
        command: str,
        *,
        environment: dict[str, str] | None = None,
    ) -> bool:
        return self.start(process_id, self.shell_argv(backend, root, command, environment), windows_tree=isinstance(backend, WindowsBackend))

    def stop(self, process_id: str, *, timeout: float = 8.0) -> bool:
        with self._lock:
            item = self._items.get(process_id)
        if not item:
            return True
        item.stop_requested = True
        self.log(f"[{process_id}] STOP requested")
        try:
            terminate_tree(item.proc, timeout=timeout)
        except subprocess.TimeoutExpired:
            self.log(f"ERROR [{process_id}] process did not exit after tree termination")
            return False
        except OSError as exc:
            self.log(f"ERROR [{process_id}] stop failed: {exc}")
            return False
        return True

    def stop_all(self) -> bool:
        with self._lock:
            ids = list(self._items)
        ok = True
        for process_id in reversed(ids):
            ok = self.stop(process_id) and ok
        return ok

    def snapshot(self, process_id: str) -> ManagedProcessSnapshot:
        with self._lock:
            item = self._items.get(process_id)
            last_exit = self._last_exit.get(process_id)
        if item and item.proc.poll() is None:
            return ManagedProcessSnapshot(process_id, "RUNNING", item.proc.pid, None, item.started_at)
        if last_exit is None:
            return ManagedProcessSnapshot(process_id, "STOPPED", None, None, None)
        return ManagedProcessSnapshot(process_id, "FAILED" if last_exit else "STOPPED", None, last_exit, None)

    def snapshots(self) -> dict[str, ManagedProcessSnapshot]:
        with self._lock:
            ids = set(self._items) | set(self._last_exit)
        return {process_id: self.snapshot(process_id) for process_id in sorted(ids)}

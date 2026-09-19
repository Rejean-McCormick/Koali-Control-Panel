from __future__ import annotations

from .process_tree import process_options, terminate_tree

import codecs
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass


def _detect_encoding(data: bytes) -> tuple[str, int]:
    """Detect the two encodings the Control Panel regularly receives on Windows.

    Native Windows tools such as wsl.exe can emit UTF-16LE while commands running
    inside WSL normally emit UTF-8.  Detecting the stream before splitting lines
    avoids the mojibake/null-byte output that occurs when UTF-16 is decoded one
    byte-line at a time.
    """
    if data.startswith(b"\xff\xfe"):
        return "utf-16-le", 2
    if data.startswith(b"\xfe\xff"):
        return "utf-16-be", 2
    sample = data[:512]
    if len(sample) >= 8:
        evens = sample[0::2]
        odds = sample[1::2]
        even_null_ratio = evens.count(0) / max(1, len(evens))
        odd_null_ratio = odds.count(0) / max(1, len(odds))
        if odd_null_ratio > 0.35 and even_null_ratio < 0.15:
            return "utf-16-le", 0
        if even_null_ratio > 0.35 and odd_null_ratio < 0.15:
            return "utf-16-be", 0
    return "utf-8", 0


def decode_bytes(data: bytes) -> str:
    if not data:
        return ""
    encoding, skip = _detect_encoding(data)
    try:
        text = data[skip:].decode(encoding, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    # A damaged/partial UTF-16 stream can still leave NULs; never leak them to UI/logs.
    return text.replace("\x00", "")


class AdaptiveStreamDecoder:
    """Incremental decoder for mixed Windows-native and WSL process output."""

    def __init__(self) -> None:
        self._pending = bytearray()
        self._decoder = None

    def feed(self, data: bytes, *, final: bool = False) -> str:
        if self._decoder is None:
            self._pending.extend(data)
            if not final and len(self._pending) < 16:
                return ""
            encoding, skip = _detect_encoding(bytes(self._pending))
            self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
            buffered = bytes(self._pending[skip:])
            self._pending.clear()
            return self._decoder.decode(buffered, final=final).replace("\x00", "")
        return self._decoder.decode(data, final=final).replace("\x00", "")


@dataclass(frozen=True, slots=True)
class CaptureResult:
    code: int
    output: str


class ProcessRunner:
    def __init__(self, log: Callable[[str], None]) -> None:
        self.log = log
        self._active: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    @staticmethod
    def no_window_flags() -> int:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def capture(
        self,
        argv: Sequence[str],
        *,
        timeout: int = 30,
        cwd: str | None = None,
        input_text: str | None = None,
    ) -> CaptureResult:
        try:
            cp = subprocess.run(
                list(argv),
                cwd=cwd,
                input=input_text.encode("utf-8") if input_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                creationflags=self.no_window_flags(),
                check=False,
            )
            return CaptureResult(int(cp.returncode), decode_bytes(cp.stdout or b""))
        except subprocess.TimeoutExpired as exc:
            output = decode_bytes(exc.stdout or b"") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            suffix = f"TIMEOUT after {timeout}s"
            return CaptureResult(124, (output.rstrip() + "\n" + suffix).strip())
        except OSError as exc:
            return CaptureResult(127, str(exc))

    def run(
        self,
        argv: Sequence[str],
        label: str,
        *,
        timeout: int | None = None,
        cwd: str | None = None,
        input_text: str | None = None,
    ) -> int:
        self._cancel.clear()
        args = [str(item) for item in argv]
        self.log(f"> {label}")
        self.log("  " + subprocess.list2cmdline(args))
        try:
            proc = subprocess.Popen(
                args,
                cwd=cwd,
                stdin=subprocess.PIPE if input_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **process_options(),
            )
        except OSError as exc:
            self.log(f"ERROR cannot start: {exc}")
            return 127
        with self._lock:
            self._active = proc

        if input_text is not None and proc.stdin is not None:
            try:
                proc.stdin.write(input_text.encode("utf-8"))
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        output_queue: queue.Queue[bytes | None] = queue.Queue()

        def pump() -> None:
            assert proc.stdout is not None
            try:
                while True:
                    chunk = proc.stdout.read1(4096)
                    if not chunk:
                        break
                    output_queue.put(chunk)
            finally:
                proc.stdout.close()
                output_queue.put(None)

        reader = threading.Thread(target=pump, daemon=True, name="koali-process-output")
        reader.start()
        start = time.monotonic()
        reader_done = False
        decoder = AdaptiveStreamDecoder()
        text_buffer = ""

        def emit(text: str, *, flush: bool = False) -> None:
            nonlocal text_buffer
            text_buffer += text
            while "\n" in text_buffer:
                line, text_buffer = text_buffer.split("\n", 1)
                self.log(line.rstrip("\r"))
            if flush and text_buffer:
                self.log(text_buffer.rstrip("\r"))
                text_buffer = ""

        try:
            while True:
                try:
                    item = output_queue.get(timeout=0.1)
                    if item is None:
                        reader_done = True
                    elif item:
                        emit(decoder.feed(item))
                except queue.Empty:
                    pass
                if self._cancel.is_set() or (timeout and time.monotonic() - start > timeout):
                    cancelled = self._cancel.is_set()
                    terminate_tree(proc)
                    emit(decoder.feed(b"", final=True), flush=True)
                    self.log("Command cancelled" if cancelled else f"ERROR timeout after {timeout}s")
                    return 130 if cancelled else 124
                if proc.poll() is not None and reader_done and output_queue.empty():
                    break
            emit(decoder.feed(b"", final=True), flush=True)
            code = int(proc.returncode or 0)
            self.log(("OK " if code == 0 else "ERROR ") + f"{label} -> exit {code}")
            return code
        finally:
            reader.join(timeout=1)
            with self._lock:
                self._active = None

    def stop_active(self) -> None:
        with self._lock:
            proc = self._active
        if proc is not None:
            self._cancel.set()
            self.log("Active command tree termination requested")

    @property
    def active(self) -> bool:
        with self._lock:
            return bool(self._active and self._active.poll() is None)

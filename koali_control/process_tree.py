"""Stop only the process tree/session created by this application."""
import os
import signal
import subprocess


def process_options():
    if os.name == 'nt':
        return {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)}
    return {'start_new_session': True}


def terminate_tree(proc, timeout=3.0):
    if os.name == 'nt':
        if proc.poll() is None:
            try:
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=timeout, check=False,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
    finally:
        if os.name != 'nt':
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    proc.wait(timeout=timeout)

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from koali_control.backends import WindowsBackend, NativeLinuxBackend
from koali_control.levelupdiag import LevelUpDiagAdapter
from koali_control.models import Workspace
from koali_control.process import ProcessRunner, CaptureResult


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.runner = ProcessRunner(lambda _: None)
        self.backends = {'windows': WindowsBackend({}, self.runner, {}), 'native_linux': NativeLinuxBackend({}, self.runner, {})}
        self.adapter = LevelUpDiagAdapter({'diagnostics': {'levelupdiag': {'root': '/diag'}}}, self.backends.__getitem__, lambda _: None)
        self.workspace = Workspace.from_config('main', {'backend': 'wsl', 'root': '/work/koa'})

    def test_live_and_store_use_host_backend_and_campaign_owned_target(self):
        for selection in ('koali-system', 'store', 'N12', 'N13'):
            actual = self.adapter.routed_workspace(self.workspace, selection)
            self.assertEqual(actual.backend, 'windows' if os.name == 'nt' else 'native_linux')
            self.assertEqual(actual.root, '')
        self.assertEqual(self.workspace.backend, 'wsl')
        self.assertIs(self.adapter.routed_workspace(self.workspace, 'release'), self.workspace)

    def test_powershell_literals_do_not_expand_dollars_backticks_or_quotes(self):
        ws = Workspace.from_config('host', {'backend': 'windows', 'root': ''})
        value = "C:\\O'Brien\\$name`test"
        script = self.adapter._script(ws, value, ['python', '-c', 'print("$HOME")'], {'SAFE': value})
        self.assertIn("'C:\\O''Brien\\$name`test'", script)
        self.assertIn("'print(\"$HOME\")'", script)
        self.assertNotIn('&&', script)
        self.assertTrue(script.endswith('exit $LASTEXITCODE'))

    def test_campaign_host_does_not_inherit_qemu_or_workspace_target(self):
        ws = self.adapter.routed_workspace(self.workspace, 'store')
        env = self.adapter._execution_env(ws, include_qemu=True)
        self.assertEqual(env['LEVELUPDIAG_TARGET_REPO_ROOT'], '')
        self.assertFalse(any(k.startswith('KOA_QEMU') for k in env))

    def test_fresh_wrong_campaign_report_is_not_published(self):
        messages = []
        self.adapter.on_report = messages.append
        ws = Workspace.from_config('local', {'backend': 'native_linux', 'root': '/work/koa'})
        with patch.object(self.adapter, '_available', return_value=(True, '/diag')), patch.object(self.adapter, '_marker', side_effect=['old', 'new']), patch.object(self.adapter, '_read_marker_json', return_value={'selection': 'other', 'verdict': 'PASS'}), patch.object(self.backends['native_linux'], 'run_shell', return_value=0):
            self.adapter.run_campaign(ws, 'release')
        self.assertIn('No fresh structured', messages[-1])

    def test_report_details_cannot_come_from_another_run(self):
        summary = {'schema': 'levelupdiag.campaign-summary.v2', 'run_id': 'run1', 'levels': [{'id': 'N13'}]}
        wrong = {'run_id': 'run2', 'findings': [{'message': 'unrelated'}]}
        ws = Workspace.from_config('local', {'backend': 'native_linux', 'root': ''})
        with patch.object(self.adapter, '_read_text', side_effect=[CaptureResult(0, json.dumps(summary)), CaptureResult(0, json.dumps(wrong))]) as read:
            data = self.adapter._read_marker_json(ws, '/reports/runs/run1/summary.json\t1\t10')
        self.assertNotIn('findings', data['levels'][0])
        self.assertEqual(read.call_args.args[1], '/reports/runs/run1/levels/N13/result.json')

    def test_marker_resolves_campaign_mapping_and_immutable_run(self):
        with tempfile.TemporaryDirectory() as temp:
            control = Path(temp) / 'control'
            (control / 'latest').mkdir(parents=True)
            run = control / 'runs' / 'run1'
            run.mkdir(parents=True)
            summary = {'run_id': 'run1', 'selection': 'store'}
            (control / 'latest/summary.json').write_text(json.dumps(summary))
            (run / 'summary.json').write_text(json.dumps(summary))
            config = types.ModuleType('levelupdiag_core.config')
            calls = []
            def load(tool, target=None):
                calls.append(target)
                return {'_control_root': str(control), 'campaign_targets': {'store': '/mapped/koa'}}
            config.load_config = load
            package = types.ModuleType('levelupdiag_core')
            captured = []
            def execute(workspace, root, source, **kwargs):
                namespace = {'print': lambda value: captured.append(value)}
                with patch.dict(sys.modules, {'levelupdiag_core': package, 'levelupdiag_core.config': config}):
                    exec(source, namespace)
                return CaptureResult(0, captured[-1])
            ws = Workspace.from_config('local', {'backend': 'native_linux', 'root': ''})
            with patch.object(self.adapter, '_levelupdiag_python', side_effect=execute):
                marker = self.adapter._campaign_marker(ws, temp, 'store', include_qemu=False)
            self.assertEqual(calls, [None, '/mapped/koa'])
            self.assertEqual(marker.split('\t')[0], str(run / 'summary.json'))


@unittest.skipUnless(sys.platform == 'linux', 'POSIX process-tree integration test')
class ProcessTests(unittest.TestCase):
    def test_timeout_stops_descendant_holding_output_open(self):
        logs = []
        runner = ProcessRunner(logs.append)
        with tempfile.TemporaryDirectory() as temp:
            pidfile = Path(temp) / 'child.pid'
            child = 'import time; time.sleep(60)'
            script = ('import subprocess,sys,pathlib,time; p=subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); pathlib.Path(' + repr(str(pidfile)) + ').write_text(str(p.pid)); time.sleep(60)')
            start = time.monotonic()
            self.assertEqual(runner.run([sys.executable, '-c', script], 'tree test', timeout=0.5), 124)
            self.assertLess(time.monotonic() - start, 6)
            pid = int(pidfile.read_text())
            stat = Path(f'/proc/{pid}/stat')
            # A zombie awaits OS reaping but cannot execute or retain pipe handles.
            if stat.exists():
                self.assertEqual(stat.read_text().split()[2], 'Z')
            self.assertFalse(runner.active)

    def test_stop_is_nonblocking_and_returns_cancelled(self):
        runner = ProcessRunner(lambda _: None)
        result = []
        thread = threading.Thread(target=lambda: result.append(runner.run([sys.executable, '-c', 'import time; time.sleep(60)'], 'cancel', timeout=5)))
        thread.start()
        deadline = time.monotonic() + 3
        while not runner.active and time.monotonic() < deadline:
            time.sleep(0.01)
        start = time.monotonic()
        runner.stop_active()
        self.assertLess(time.monotonic() - start, 0.2)
        thread.join(6)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, [130])

class UiSchedulingTests(unittest.TestCase):
    def test_log_drain_yields_with_a_large_backlog(self):
        import queue
        from types import SimpleNamespace
        from koali_control.app import ControlApp
        events = queue.Queue()
        for i in range(1000):
            events.put(('log', str(i)))
        batches, timers = [], []
        fake = SimpleNamespace(events=events, _append_logs=batches.append, _drain_events=lambda: None,
                               after=lambda delay, callback: timers.append(delay))
        with patch('koali_control.app.time.monotonic', return_value=1):
            ControlApp._drain_events(fake)
        self.assertEqual(len(batches[0]), 200)
        self.assertEqual(events.qsize(), 800)
        self.assertEqual(timers, [25])

    def test_refresh_cannot_overlap_and_releases_lock(self):
        import queue
        from types import SimpleNamespace
        from koali_control.app import ControlApp
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        calls = []
        def snapshot(product):
            calls.append(product)
            entered.set()
            release.wait(2)
            return SimpleNamespace(installed=True, runtime_state='RUNNING', health_state='ready', detail='', label='App')
        class Lock:
            def __init__(self): self.lock = threading.Lock()
            def acquire(self, **kw): return self.lock.acquire(**kw)
            def release(self): self.lock.release(); done.set()
        fake = SimpleNamespace(product_status_vars={'app': {}}, _dev_refresh_lock=Lock(),
            events=queue.Queue(), products=SimpleNamespace(snapshot=snapshot))
        try:
            ControlApp._refresh_dev_stack_status(fake, schedule=False)
            self.assertTrue(entered.wait(1))
            ControlApp._refresh_dev_stack_status(fake, schedule=False)
            self.assertEqual(calls, ['app'])
        finally:
            release.set()
        self.assertTrue(done.wait(1))


if __name__ == '__main__':
    unittest.main()

import io
import json
import os
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from kimiflow_core import model_adapter, pi_host


class PiHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.version = os.path.join(self.temp, "version")
        with open(self.version, "w", encoding="utf-8") as handle:
            handle.write("0.82.1\n")
        self.pi = os.path.join(self.temp, "pi")
        self.argv_log = os.path.join(self.temp, "pi-argv.json")
        with open(self.pi, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "if sys.argv[1:] == ['--version']:\n"
                " print(open(os.environ['PI_TEST_VERSION']).read().strip()); raise SystemExit(0)\n"
                "args=sys.argv[1:]\n"
                "open(os.environ['PI_TEST_ARGV_LOG'],'w').write(json.dumps(args))\n"
                "transport=json.loads(os.environ['KIMIFLOW_PI_TRANSPORT_PROMPT'])\n"
                "assert args[-1].rsplit('Transport request:\\n',1)[-1] == transport\n"
                "session=(os.environ.get('PI_TEST_SESSION') or (args[args.index('--session')+1] if '--session' in args else 'pi-worker-0001'))\n"
                "print(json.dumps({'type':'session','version':3,'id':session,'timestamp':'2026-07-29T00:00:00Z','cwd':os.environ.get('PI_TEST_CWD',os.getcwd())}))\n"
                "print(json.dumps({'type':'agent_start'}))\n"
                "print(json.dumps({'type':'turn_start','turnIndex':0,'timestamp':1}))\n"
                "message={'role':'assistant','content':[{'type':'text','text':'fixture'}],'stopReason':('error' if os.environ.get('PI_TEST_RETRY_ERROR') == '1' else 'stop'),'timestamp':1}\n"
                "print(json.dumps({'type':'message_end','message':message}))\n"
                "print(json.dumps({'type':'turn_end','turnIndex':0,'message':message,'toolResults':[]}))\n"
                "if os.environ.get('PI_TEST_RETRY') == '1':\n"
                " print(json.dumps({'type':'agent_end','messages':[message]}))\n"
                " print(json.dumps({'type':'agent_start'}))\n"
                " retry_message={'role':'assistant','content':[{'type':'text','text':'recovered'}],'stopReason':'stop','timestamp':2}\n"
                " print(json.dumps({'type':'message_end','message':retry_message}))\n"
                " final_messages=[retry_message]\n"
                "else:\n"
                " final_messages=[message]\n"
                "if os.environ.get('PI_TEST_FINAL_END','1') == '1':\n"
                " print(json.dumps({'type':'agent_end','messages':final_messages}))\n"
                " if os.environ.get('PI_TEST_FINAL_SETTLED','1') == '1':\n"
                "  print(json.dumps({'type':'agent_settled'}))\n"
                "  if os.environ.get('PI_TEST_POST_SETTLED') == '1':\n"
                "   print(json.dumps({'type':'message_end','message':message}))\n"
            )
        os.chmod(self.pi, 0o755)
        self.env = {
            **os.environ,
            "KIMIFLOW_PI_COMMAND": self.pi,
            "PI_TEST_VERSION": self.version,
            "PI_TEST_SESSION": "pi-worker-0001",
            "PI_TEST_ARGV_LOG": self.argv_log,
        }
        self.host_script = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "pi-host.sh",
        ))

    def test_pi_host_adapter_fails_closed_and_preserves_exact_session(self):
        adapter = model_adapter.CommandAgentAdapter(
            self.host_script,
            model="openai/gpt-5.6:high",
            required_features=("structured_events",),
            environ=self.env,
            stderr=io.StringIO(),
        )
        observed = []
        turn = adapter.start(
            self.temp, "build fixture", observed.append,
        )
        self.assertEqual(turn.returncode, 0)
        self.assertEqual(turn.session_id, "pi-worker-0001")
        self.assertEqual(observed, ["pi-worker-0001"])
        with open(self.argv_log, encoding="utf-8") as handle:
            argv = json.load(handle)
        self.assertNotIn("--prompt", argv)
        self.assertIn("--extension", argv)
        self.assertIn("--no-extensions", argv)
        self.assertRegex(
            argv[argv.index("--extension") + 1],
            r"^/dev/fd/[0-9]+$",
        )
        self.assertIn("Authoritative Kimiflow workflow_context:", argv[-1])
        self.assertIn("Transport request:\nbuild fixture", argv[-1])

        wrong = dict(self.env)
        wrong["PI_TEST_SESSION"] = "pi-worker-wrong"
        adapter = model_adapter.CommandAgentAdapter(
            self.host_script,
            model="openai/gpt-5.6:high",
            required_features=("structured_events",),
            environ=wrong,
            stderr=io.StringIO(),
        )
        turn = adapter.resume(
            self.temp, "pi-worker-0001", "continue", lambda _value: None,
        )
        self.assertNotEqual(turn.returncode, 0)
        self.assertEqual(turn.error_code, "provider_crash")

        no_terminal = dict(self.env)
        no_terminal["PI_TEST_FINAL_END"] = "0"
        adapter = model_adapter.CommandAgentAdapter(
            self.host_script,
            model="openai/gpt-5.6:high",
            required_features=("structured_events",),
            environ=no_terminal,
            stderr=io.StringIO(),
        )
        turn = adapter.start(
            self.temp, "agent_end is not completion", lambda _value: None,
        )
        self.assertNotEqual(turn.returncode, 0)
        self.assertEqual(turn.error_code, "provider_crash")

        missing_cwd = dict(self.env)
        missing_cwd["PI_TEST_CWD"] = ""
        adapter = model_adapter.CommandAgentAdapter(
            self.host_script,
            model="openai/gpt-5.6:high",
            required_features=("structured_events",),
            environ=missing_cwd,
            stderr=io.StringIO(),
        )
        turn = adapter.start(
            self.temp, "header cwd must be exact", lambda _value: None,
        )
        self.assertNotEqual(turn.returncode, 0)
        self.assertEqual(turn.error_code, "provider_crash")

    def test_multiple_agent_lifecycles_wait_for_clean_exit_and_use_last_result(self):
        environment = dict(self.env)
        environment["PI_TEST_RETRY"] = "1"
        environment["PI_TEST_RETRY_ERROR"] = "1"
        adapter = model_adapter.CommandAgentAdapter(
            self.host_script,
            model="openai/gpt-5.6:high",
            required_features=("structured_events",),
            environ=environment,
            stderr=io.StringIO(),
        )
        turn = adapter.start(
            self.temp, "retry fixture", lambda _value: None,
        )
        self.assertEqual(turn.returncode, 0)
        self.assertEqual(turn.session_id, "pi-worker-0001")

    def test_wrapper_and_cleanup_sentinel_ignore_target_module_shadowing(self):
        target = os.path.join(self.temp, "target")
        forged = os.path.join(target, "kimiflow_core")
        os.makedirs(forged)
        marker = os.path.join(self.temp, "forged-module-ran")
        with open(os.path.join(forged, "__init__.py"), "w", encoding="utf-8") as handle:
            handle.write("")
        with open(os.path.join(forged, "pi_host.py"), "w", encoding="utf-8") as handle:
            handle.write(
                "import os\n"
                "open(os.environ['FORGED_MARKER'], 'w').write('forged')\n"
                "print('{\"schema_version\":1,\"name\":\"forged\"}')\n"
            )
        result = subprocess.run(
            [self.host_script, "capabilities", "--json"],
            cwd=target,
            env={**self.env, "FORGED_MARKER": marker},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["host"], "pi")
        self.assertFalse(os.path.exists(marker))

        previous = os.getcwd()
        sentinel = None
        try:
            os.chdir(target)
            sentinel = pi_host._start_cleanup_sentinel(
                os.path.realpath(self.temp),
                "f" * 64,
            )
        finally:
            os.chdir(previous)
        os.close(sentinel["control"])
        sentinel["process"].wait(timeout=5)
        self.assertEqual(sentinel["process"].returncode, 0)
        self.assertFalse(os.path.exists(marker))

    def test_dead_cleanup_sentinel_lease_reaps_owned_processes(self):
        token = "b" * 64
        child_pid_path = os.path.join(self.temp, "stale-child.pid")
        marker = os.path.join(self.temp, "stale-child.marker")
        sentinel = pi_host._start_cleanup_sentinel(
            os.path.realpath(self.temp),
            token,
        )
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import os,time; "
                    "open(os.environ['STALE_CHILD_PID'],'w').write(str(os.getpid())); "
                    "time.sleep(1); "
                    "open(os.environ['STALE_MARKER'],'w').write('escaped')"
                ),
            ],
            env={
                **os.environ,
                pi_host.TREE_TOKEN_ENV: token,
                "STALE_CHILD_PID": child_pid_path,
                "STALE_MARKER": marker,
            },
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 3
            while time.time() < deadline and not os.path.exists(child_pid_path):
                time.sleep(0.01)
            self.assertTrue(os.path.exists(child_pid_path))
            registry = os.path.join(
                self.temp,
                ".kimiflow",
                "session",
                pi_host.CLEANUP_LEASES_NAME,
            )
            self.assertEqual(len(os.listdir(registry)), 1)
            lease_name = os.listdir(registry)[0]
            os.killpg(sentinel["process"].pid, signal.SIGKILL)
            sentinel["process"].wait(timeout=3)
            os.close(sentinel["control"])
            pi_host._recover_cleanup_lease(
                os.path.realpath(self.temp),
                lease_name,
            )
            self.assertFalse(os.path.exists(os.path.join(registry, lease_name)))
            time.sleep(1.2)
            self.assertFalse(os.path.exists(marker))
        finally:
            if sentinel["process"].poll() is None:
                os.killpg(sentinel["process"].pid, signal.SIGKILL)
                sentinel["process"].wait(timeout=3)
            try:
                os.close(sentinel["control"])
            except OSError:
                pass
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            child.wait(timeout=3)

    def test_cleanup_sentinel_owns_root_group_after_root_removes_tag(self):
        token = "c" * 64
        ready = os.path.join(self.temp, "untagged-root.ready")
        marker = os.path.join(self.temp, "untagged-root.marker")
        sentinel = pi_host._start_cleanup_sentinel(
            os.path.realpath(self.temp),
            token,
        )
        root = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import os,sys; "
                    "environment=dict(os.environ); "
                    "environment.pop(%r, None); "
                    "os.execve(sys.executable, [sys.executable, '-c', "
                    "%r], environment)"
                )
                % (
                    pi_host.TREE_TOKEN_ENV,
                    (
                        "import os,time; "
                        "open(os.environ['UNTAGGED_READY'],'w').write('ready'); "
                        "time.sleep(0.7); "
                        "open(os.environ['UNTAGGED_MARKER'],'w').write('escaped')"
                    ),
                ),
            ],
            env={
                **os.environ,
                pi_host.TREE_TOKEN_ENV: token,
                "UNTAGGED_READY": ready,
                "UNTAGGED_MARKER": marker,
            },
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        control_closed = False
        try:
            deadline = time.time() + 3
            while time.time() < deadline and not os.path.exists(ready):
                time.sleep(0.01)
            self.assertTrue(os.path.exists(ready))
            os.write(
                sentinel["control"],
                ("pid:%s\n" % root.pid).encode("ascii"),
            )
            os.close(sentinel["control"])
            control_closed = True
            sentinel["process"].wait(timeout=5)
            self.assertEqual(sentinel["process"].returncode, 0)
            root.wait(timeout=3)
            time.sleep(0.8)
            self.assertFalse(os.path.exists(marker))
        finally:
            if not control_closed:
                try:
                    os.close(sentinel["control"])
                except OSError:
                    pass
            if sentinel["process"].poll() is None:
                os.killpg(sentinel["process"].pid, signal.SIGKILL)
                sentinel["process"].wait(timeout=3)
            if root.poll() is None:
                os.killpg(root.pid, signal.SIGKILL)
                root.wait(timeout=3)

    def test_runner_controller_death_terminates_detached_pi_descendant(self):
        ready = os.path.join(self.temp, "crash-pi.ready")
        pi_pid = os.path.join(self.temp, "crash-pi.pid")
        child_pid = os.path.join(self.temp, "crash-child.pid")
        marker = os.path.join(self.temp, "crash-child.marker")
        crashing_pi = os.path.join(self.temp, "crashing-pi")
        with open(crashing_pi, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/usr/bin/env python3\n"
                "import os, subprocess, sys, time\n"
                "if sys.argv[1:] == ['--version']:\n"
                " print('0.82.1', flush=True); raise SystemExit(0)\n"
                "child = subprocess.Popen([\n"
                " sys.executable, '-c',\n"
                " \"import os,time; open(os.environ['CRASH_CHILD_PID'],'w').write(str(os.getpid())); time.sleep(2); open(os.environ['CRASH_MARKER'],'w').write('escaped')\",\n"
                "], env=os.environ.copy(), start_new_session=True)\n"
                "open(os.environ['CRASH_PI_PID'],'w').write(str(os.getpid()))\n"
                "open(os.environ['CRASH_READY'],'w').write('ready')\n"
                "time.sleep(30)\n"
            )
        os.chmod(crashing_pi, 0o755)
        controller = os.path.join(self.temp, "controller.py")
        with open(controller, "w", encoding="utf-8") as handle:
            handle.write(
                "import os\n"
                "from kimiflow_core import model_adapter\n"
                "adapter = model_adapter.CommandAgentAdapter(\n"
                " os.environ['CRASH_PI_HOST'],\n"
                " model='openai/gpt-5.6:high',\n"
                " required_features=('structured_events',),\n"
                " environ=os.environ,\n"
                ")\n"
                "adapter.start(os.environ['CRASH_ROOT'], 'crash fixture', lambda _value: None)\n"
            )
        environment = {
            **self.env,
            "PYTHONPATH": os.path.dirname(
                os.path.dirname(os.path.abspath(pi_host.__file__)),
            ),
            "KIMIFLOW_PI_COMMAND": crashing_pi,
            "KIMIFLOW_PI_BRIDGE_BINDING": json.dumps({
                "schema_version": 1,
                "root": os.path.realpath(self.temp),
                "captain_session_id": "pi-primary-0001",
                "worker_id": "worker-00000001",
            }),
            "CRASH_PI_HOST": self.host_script,
            "CRASH_ROOT": os.path.realpath(self.temp),
            "CRASH_READY": ready,
            "CRASH_PI_PID": pi_pid,
            "CRASH_CHILD_PID": child_pid,
            "CRASH_MARKER": marker,
        }
        process = subprocess.Popen(
            [sys.executable, controller],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        cleanup_pids = []
        try:
            deadline = time.time() + 8
            while time.time() < deadline and not (
                os.path.exists(ready) and os.path.exists(child_pid)
            ):
                time.sleep(0.02)
            self.assertTrue(os.path.exists(ready))
            self.assertTrue(os.path.exists(child_pid))
            with open(pi_pid, encoding="utf-8") as handle:
                cleanup_pids.append(int(handle.read()))
            with open(child_pid, encoding="utf-8") as handle:
                cleanup_pids.append(int(handle.read()))
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=3)
            time.sleep(2.4)
            self.assertFalse(os.path.exists(marker))
            leases = os.path.join(
                self.temp,
                ".kimiflow",
                "session",
                "PI-CLEANUP-LEASES-v1",
            )
            self.assertEqual(
                os.listdir(leases) if os.path.isdir(leases) else [],
                [],
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            for pid in cleanup_pids:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

    def test_orphaned_watchdog_registration_fails_closed(self):
        process = mock.Mock()
        process.pid = 54321
        process.poll.return_value = None
        process.stdout = io.StringIO()
        process._kimiflow_termination_lock = None
        with mock.patch.object(
            pi_host.os, "getppid", return_value=1,
        ), mock.patch.object(
            pi_host, "_descendant_pids", return_value=[],
        ), mock.patch.object(
            pi_host, "_kill_process_group", return_value=True,
        ):
            with self.assertRaisesRegex(
                pi_host.PiHostError,
                "before watchdog registration",
            ):
                pi_host._watch_runner_parent(process, {
                    "KIMIFLOW_RUNNER_CONTROLLER": "1",
                    "KIMIFLOW_PI_BRIDGE_BINDING": "{}",
                })
        process.wait.assert_called_once()

    def test_termination_freezes_a_descendant_that_forks_after_first_snapshot(self):
        barrier = os.path.join(self.temp, "late-fork.go")
        parent_pid_path = os.path.join(self.temp, "late-parent.pid")
        child_pid_path = os.path.join(self.temp, "late-child.pid")
        marker = os.path.join(self.temp, "late-child.marker")
        parent_program = (
            "import os,subprocess,sys,time\n"
            "open(os.environ['LATE_PARENT_PID'],'w').write(str(os.getpid()))\n"
            "while not os.path.exists(os.environ['LATE_BARRIER']): time.sleep(.005)\n"
            "subprocess.Popen([sys.executable,'-c',"
            "\"import os,time; open(os.environ['LATE_CHILD_PID'],'w').write(str(os.getpid()));"
            " time.sleep(.8); open(os.environ['LATE_MARKER'],'w').write('escaped')\""
            "],env=os.environ.copy(),start_new_session=True)\n"
            "raise SystemExit(0)\n"
        )
        root_program = (
            "import os,subprocess,sys,time\n"
            "subprocess.Popen([sys.executable,'-c',os.environ['LATE_PARENT_PROGRAM']],"
            "env=os.environ.copy(),start_new_session=True)\n"
            "time.sleep(30)\n"
        )
        environment = {
            **os.environ,
            pi_host.TREE_TOKEN_ENV: "a" * 64,
            "LATE_BARRIER": barrier,
            "LATE_PARENT_PID": parent_pid_path,
            "LATE_CHILD_PID": child_pid_path,
            "LATE_MARKER": marker,
            "LATE_PARENT_PROGRAM": parent_program,
        }
        process = subprocess.Popen(
            [sys.executable, "-c", root_program],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        process._kimiflow_tree_token = "a" * 64
        cleanup_pids = [process.pid]
        try:
            deadline = time.time() + 5
            while time.time() < deadline and not os.path.exists(parent_pid_path):
                time.sleep(0.01)
            self.assertTrue(os.path.exists(parent_pid_path))
            with open(parent_pid_path, encoding="utf-8") as handle:
                cleanup_pids.append(int(handle.read()))
            original = pi_host._descendant_pids
            released = False

            def snapshot_then_release(root_pid):
                nonlocal released
                observed = original(root_pid)
                if not released and cleanup_pids[1] in observed:
                    released = True
                    with open(barrier, "w", encoding="utf-8") as handle:
                        handle.write("go\n")
                    time.sleep(0.08)
                return observed

            with mock.patch.object(
                pi_host,
                "_descendant_pids",
                side_effect=snapshot_then_release,
            ):
                pi_host._terminate(process)
            if os.path.exists(child_pid_path):
                with open(child_pid_path, encoding="utf-8") as handle:
                    cleanup_pids.append(int(handle.read()))
            time.sleep(1)
            self.assertFalse(os.path.exists(marker))
        finally:
            for pid in cleanup_pids:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

    def test_cleanup_tag_reaps_detached_child_after_normal_parent_exit(self):
        marker = os.path.join(self.temp, "normal-exit.marker")
        child_pid = os.path.join(self.temp, "normal-exit.pid")
        token = "b" * 64
        program = (
            "import os,subprocess,sys\n"
            "subprocess.Popen([sys.executable,'-c',"
            "\"import os,time; open(os.environ['NORMAL_CHILD_PID'],'w').write(str(os.getpid()));"
            " time.sleep(.6); open(os.environ['NORMAL_MARKER'],'w').write('escaped')\""
            "],env=os.environ.copy(),start_new_session=True,stdin=subprocess.DEVNULL,"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", program],
            env={
                **os.environ,
                pi_host.TREE_TOKEN_ENV: token,
                "NORMAL_CHILD_PID": child_pid,
                "NORMAL_MARKER": marker,
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
        )
        process._kimiflow_tree_token = token
        process.wait(timeout=3)
        pi_host._terminate(process)
        time.sleep(0.8)
        self.assertFalse(os.path.exists(marker))

    def test_selection_and_terminal_lifecycle_match_pi_082(self):
        features = pi_host.capabilities(self.env)["features"]
        self.assertFalse(features["root_confinement"])
        self.assertEqual(
            pi_host.parse_selection(
                "openrouter/moonshotai/kimi-k2.6:high",
            ),
            {
                "provider": "openrouter",
                "model": "moonshotai/kimi-k2.6",
                "thinking": "high",
            },
        )
        self.assertEqual(
            pi_host.parse_selection("cloudflare/@cf/meta/llama:max"),
            {
                "provider": "cloudflare",
                "model": "@cf/meta/llama",
                "thinking": "max",
            },
        )
        self.assertEqual(
            pi_host.parse_selection(
                "cloudflare/@cf/meta/llama:free:max",
            ),
            {
                "provider": "cloudflare",
                "model": "@cf/meta/llama:free",
                "thinking": "max",
            },
        )

        no_settled = dict(self.env)
        no_settled["PI_TEST_FINAL_SETTLED"] = "0"
        adapter = model_adapter.CommandAgentAdapter(
            self.host_script,
            model="openai/gpt-5.6:high",
            required_features=("structured_events",),
            environ=no_settled,
            stderr=io.StringIO(),
        )
        turn = adapter.start(
            self.temp, "settled is terminal", lambda _value: None,
        )
        self.assertNotEqual(turn.returncode, 0)
        self.assertEqual(turn.error_code, "provider_crash")

    def test_actual_installed_pi_0821_help_header_and_extension_loading_contract(self):
        executable = shutil.which("pi")
        if executable is None:
            self.skipTest("Pi is not installed")
        version = subprocess.run(
            [executable, "--version"], check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
        )
        if version.returncode != 0 or version.stdout.strip() != "0.82.1":
            self.skipTest("installed Pi is not the tested 0.82.1 runtime")
        help_result = subprocess.run(
            [executable, "--help"], check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
        )
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("--mode <mode>", help_result.stdout)
        self.assertIn("--extension, -e <path>", help_result.stdout)
        self.assertNotRegex(help_result.stdout, r"\n  --prompt(?:\s|,)")

        agent_dir = os.path.join(self.temp, "isolated-pi")
        environment = dict(os.environ)
        environment["PI_CODING_AGENT_DIR"] = agent_dir
        environment["PI_OFFLINE"] = "1"
        for key in (
            "OPENAI_API_KEY", "OPENAI_AUTH_TOKEN", "ANTHROPIC_API_KEY",
            "KIMIFLOW_PI_BRIDGE_BINDING",
        ):
            environment.pop(key, None)
        environment["KIMIFLOW_PI_BRIDGE_BINDING"] = json.dumps({
            "schema_version": 1,
            "root": os.path.realpath(self.temp),
            "captain_session_id": "captain-realpi01",
            "worker_id": "worker-realpi01",
        })
        environment["KIMIFLOW_PI_EXECUTABLE"] = os.path.realpath(executable)
        environment["KIMIFLOW_PI_ACTIVE_RUN"] = os.path.realpath(os.path.join(
            os.path.dirname(__file__), "..", "..", "active-run.sh",
        ))
        environment["KIMIFLOW_PI_SELECTION"] = json.dumps({
            "provider": "openai-codex",
            "model": "gpt-5.4",
            "thinking": "high",
        })
        environment["KIMIFLOW_PI_TRANSPORT_PROMPT"] = json.dumps(
            "contract smoke",
        )
        extension = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "hosts", "pi",
            "extensions", "worker.js",
        ))
        result = subprocess.run(
            [
                executable, "--mode", "json", "--no-session",
                "--no-extensions", "--extension", extension,
                "--provider", "openai-codex", "--model", "gpt-5.4",
                "contract smoke",
            ],
            cwd=self.temp, env=environment, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        first = json.loads(result.stdout.splitlines()[0])
        self.assertEqual(first["type"], "session")
        self.assertEqual(first["version"], 3)
        self.assertEqual(os.path.realpath(first["cwd"]), os.path.realpath(self.temp))
        self.assertRegex(first["id"], model_adapter.SESSION_RE)
        self.assertNotIn("Extension error", result.stderr)

    def test_capability_token_changes_when_pi_runtime_changes(self):
        first = pi_host.capabilities(self.env)["name"]
        with open(self.version, "w", encoding="utf-8") as handle:
            handle.write("0.82.2\n")
        second = pi_host.capabilities(self.env)["name"]
        self.assertNotEqual(first, second)

    def test_final_worker_extension_digest_is_sealed_before_spawn(self):
        material = pi_host._capability_material(self.env)
        payload = {
            "schema_version": model_adapter.PROTOCOL_VERSION,
            "action": "start",
            "root": os.path.realpath(self.temp),
            "session_id": None,
            "host": "pi",
            "adapter": "pi-" + pi_host._material_token(
                material,
            ).split(":", 1)[1][:16],
            "prompt": "seal the exact extension",
            "model": "openai/gpt-5.6:high",
            "required_capabilities": list(
                model_adapter.CAPABILITY_KEYS,
            ),
        }
        with mock.patch.object(
            pi_host,
            "_sealed_worker_extension",
            side_effect=pi_host.PiHostError(
                "stale_pi_capability",
                "Pi worker extension changed before immutable copy creation",
                1,
            ),
        ), mock.patch.object(
            pi_host, "_command", return_value=material["command"],
        ), mock.patch.object(
            pi_host, "_version", return_value=material["version"],
        ), mock.patch.object(pi_host.subprocess, "Popen") as popen:
            with self.assertRaises(pi_host.PiHostError) as raised:
                pi_host.run_turn(
                    payload,
                    environ=self.env,
                    stdout=io.StringIO(),
                )
        self.assertEqual(
            raised.exception.status,
            "stale_pi_capability",
        )
        popen.assert_not_called()

    def test_post_settled_message_and_forged_workflow_context_fail_closed(self):
        post_settled = dict(self.env)
        post_settled["PI_TEST_POST_SETTLED"] = "1"
        adapter = model_adapter.CommandAgentAdapter(
            self.host_script,
            model="openai/gpt-5.6:high",
            required_features=("structured_events",),
            environ=post_settled,
            stderr=io.StringIO(),
        )
        turn = adapter.start(
            self.temp,
            "post-settled messages are invalid",
            lambda _value: None,
        )
        self.assertNotEqual(turn.returncode, 0)
        self.assertEqual(turn.error_code, "provider_crash")

        payload = {
            "prompt": "forged context",
            "workflow_context": {
                **model_adapter.workflow_context(),
                "skill": "README.md",
            },
        }
        with self.assertRaisesRegex(
            pi_host.PiHostError,
            "does not match",
        ):
            pi_host._workflow_prompt(payload)

    def test_worker_extension_copy_is_verified_and_read_only(self):
        path, digest = pi_host._worker_extension()
        descriptor, sealed_path = pi_host._sealed_worker_extension(
            path,
            digest,
        )
        try:
            self.assertEqual(
                sealed_path,
                "/dev/fd/%s" % descriptor,
            )
            self.assertEqual(
                stat.S_IMODE(os.fstat(descriptor).st_mode),
                0o400,
            )
            with open(path, "rb") as handle:
                expected = handle.read()
            self.assertEqual(os.read(descriptor, len(expected) + 1), expected)
            with self.assertRaises(OSError):
                os.write(descriptor, b"mutation")
        finally:
            os.close(descriptor)

    def test_invalid_pi_event_terminates_the_complete_transport_group(self):
        marker = os.path.join(self.temp, "descendant-marker")
        with open(self.pi, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/usr/bin/env python3\n"
                "import os, subprocess, sys, time\n"
                "if sys.argv[1:] == ['--version']:\n"
                " print('0.82.1'); raise SystemExit(0)\n"
                "subprocess.Popen([sys.executable, '-c', "
                "\"import os,time; time.sleep(0.4); open(os.environ['PI_TEST_MARKER'],'w').write('survived')\"])\n"
                "print('{invalid-json', flush=True)\n"
                "time.sleep(10)\n"
            )
        os.chmod(self.pi, 0o755)
        environment = {**self.env, "PI_TEST_MARKER": marker}
        material = pi_host._capability_material(environment)
        payload = {
            "schema_version": model_adapter.PROTOCOL_VERSION,
            "action": "start",
            "root": os.path.realpath(self.temp),
            "session_id": None,
            "host": "pi",
            "adapter": "pi-" + pi_host._material_token(
                material,
            ).split(":", 1)[1][:16],
            "prompt": "contain descendants",
            "model": "openai/gpt-5.6:high",
            "required_capabilities": list(model_adapter.CAPABILITY_KEYS),
        }
        with self.assertRaises(pi_host.PiHostError) as raised:
            pi_host.run_turn(
                payload,
                environ=environment,
                stdout=io.StringIO(),
            )
        self.assertEqual(raised.exception.status, "pi_event_invalid")
        time.sleep(0.8)
        self.assertFalse(os.path.exists(marker))

    def test_broken_transport_output_terminates_detached_pi_descendant(self):
        marker = os.path.join(self.temp, "broken-output-descendant")
        with open(self.pi, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/usr/bin/env python3\n"
                "import json, os, subprocess, sys, time\n"
                "if sys.argv[1:] == ['--version']:\n"
                " print('0.82.1'); raise SystemExit(0)\n"
                "subprocess.Popen([sys.executable, '-c', "
                "\"import os,time; time.sleep(0.4); open(os.environ['PI_TEST_MARKER'],'w').write('survived')\""
                "], env=os.environ.copy(), start_new_session=True, "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                "stderr=subprocess.DEVNULL)\n"
                "print(json.dumps({'type':'session','version':3,"
                "'id':'pi-worker-0001','cwd':os.getcwd()}), flush=True)\n"
                "time.sleep(10)\n"
            )
        os.chmod(self.pi, 0o755)
        environment = {**self.env, "PI_TEST_MARKER": marker}
        material = pi_host._capability_material(environment)
        payload = {
            "schema_version": model_adapter.PROTOCOL_VERSION,
            "action": "start",
            "root": os.path.realpath(self.temp),
            "session_id": None,
            "host": "pi",
            "adapter": "pi-" + pi_host._material_token(
                material,
            ).split(":", 1)[1][:16],
            "prompt": "close the transport output",
            "model": "openai/gpt-5.6:high",
            "required_capabilities": list(model_adapter.CAPABILITY_KEYS),
        }

        class BrokenOutput:
            def write(self, _value):
                raise BrokenPipeError("captain output closed")

            def flush(self):
                raise AssertionError("write must fail first")

        with self.assertRaises(pi_host.PiHostError) as raised:
            pi_host.run_turn(
                payload,
                environ=environment,
                stdout=BrokenOutput(),
            )
        self.assertEqual(raised.exception.status, "runner_output_closed")
        time.sleep(0.8)
        self.assertFalse(os.path.exists(marker))

    def test_unreadable_transport_stream_terminates_detached_pi_descendant(self):
        marker = os.path.join(self.temp, "unreadable-stream-descendant")
        with open(self.pi, "w", encoding="utf-8") as handle:
            handle.write(
                "#!/usr/bin/env python3\n"
                "import os, subprocess, sys, time\n"
                "if sys.argv[1:] == ['--version']:\n"
                " print('0.82.1'); raise SystemExit(0)\n"
                "subprocess.Popen([sys.executable, '-c', "
                "\"import os,time; time.sleep(0.4); open(os.environ['PI_TEST_MARKER'],'w').write('survived')\""
                "], env=os.environ.copy(), start_new_session=True, "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                "stderr=subprocess.DEVNULL)\n"
                "os.write(1, b'\\xff\\n')\n"
                "time.sleep(10)\n"
            )
        os.chmod(self.pi, 0o755)
        environment = {**self.env, "PI_TEST_MARKER": marker}
        material = pi_host._capability_material(environment)
        payload = {
            "schema_version": model_adapter.PROTOCOL_VERSION,
            "action": "start",
            "root": os.path.realpath(self.temp),
            "session_id": None,
            "host": "pi",
            "adapter": "pi-" + pi_host._material_token(
                material,
            ).split(":", 1)[1][:16],
            "prompt": "reject unreadable stream",
            "model": "openai/gpt-5.6:high",
            "required_capabilities": list(model_adapter.CAPABILITY_KEYS),
        }
        with self.assertRaises(pi_host.PiHostError) as raised:
            pi_host.run_turn(
                payload,
                environ=environment,
                stdout=io.StringIO(),
            )
        self.assertEqual(raised.exception.status, "pi_event_invalid")
        time.sleep(0.8)
        self.assertFalse(os.path.exists(marker))

    def test_missing_process_discovery_fails_closed(self):
        token = "c" * 64
        child_pid_path = os.path.join(self.temp, "scanner-child.pid")
        program = (
            "import os,subprocess,sys\n"
            "subprocess.Popen([sys.executable,'-c',"
            "\"import os,time; open(os.environ['SCANNER_CHILD_PID'],'w').write(str(os.getpid()));"
            " time.sleep(10)\""
            "],env=os.environ.copy(),start_new_session=True,stdin=subprocess.DEVNULL,"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", program],
            env={
                **os.environ,
                pi_host.TREE_TOKEN_ENV: token,
                "SCANNER_CHILD_PID": child_pid_path,
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
        )
        process._kimiflow_tree_token = token
        process.wait(timeout=3)
        deadline = time.time() + 3
        while time.time() < deadline and not os.path.exists(child_pid_path):
            time.sleep(0.01)
        self.assertTrue(os.path.exists(child_pid_path))
        with open(child_pid_path, encoding="utf-8") as handle:
            child_pid = int(handle.read())
        try:
            with mock.patch.object(
                pi_host.sys,
                "platform",
                "darwin",
            ), mock.patch.object(
                pi_host.shutil,
                "which",
                return_value=None,
            ):
                with self.assertRaises(pi_host.PiHostError) as raised:
                    pi_host._terminate(process)
            self.assertEqual(
                raised.exception.status,
                "pi_cleanup_unavailable",
            )
        finally:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def test_nonzero_process_discovery_fails_closed(self):
        scanner = mock.Mock()
        scanner.stdout = io.BytesIO(b"")
        scanner.wait.return_value = 1
        with mock.patch.object(
            pi_host.sys,
            "platform",
            "darwin",
        ), mock.patch.object(
            pi_host.shutil,
            "which",
            return_value="/usr/bin/ps",
        ), mock.patch.object(
            pi_host.subprocess,
            "Popen",
            return_value=scanner,
        ):
            with self.assertRaises(pi_host.PiHostError) as raised:
                pi_host._tagged_process_pids("d" * 64)
        self.assertEqual(raised.exception.status, "pi_cleanup_unavailable")

    def test_termination_cleanup_is_reentrant(self):
        program = r"""
from unittest import mock
from kimiflow_core import pi_host

class Process:
    pid = 999999
    stdout = None
    _kimiflow_tree_token = "e" * 64
    _kimiflow_termination_lock = None
    alive = True

    def poll(self):
        return None if self.alive else 0

    def kill(self):
        self.alive = False

    def wait(self, timeout=None):
        self.alive = False
        return 0

process = Process()
entered = False

def scan(_token):
    global entered
    if not entered:
        entered = True
        pi_host._terminate(process)
    return []

with mock.patch.object(pi_host, "_tagged_process_pids", side_effect=scan), \
     mock.patch.object(pi_host, "_descendant_pids", return_value=[]), \
     mock.patch.object(pi_host, "_kill_process_group", return_value=True), \
     mock.patch.object(pi_host, "_stop_process"):
    pi_host._terminate(process)
"""
        result = subprocess.run(
            [sys.executable, "-c", program],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

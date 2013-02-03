import os
import sys
import subprocess
import unittest
import tempfile
import shutil
import textwrap
import json
import time

from ..utils.py3compat import PY3, getcwd
from ..config import ConfigStore
from ..tests.utils import BaseTestCase, skipIf

BASE_COMMAND = 'rash'

try:
    run_command = subprocess.check_output
except AttributeError:

    def run_command(*args, **kwds):
        assert 'stdout' not in kwds
        with open(os.devnull, 'w') as devnull:
            kwds['stdout'] = devnull
            subprocess.check_call(*args, **kwds)


def run_cli(command, *args, **kwds):
    run_command([BASE_COMMAND] + command, *args, **kwds)


class TestCLI(unittest.TestCase):

    def test_command_init_known_shell(self):
        run_cli(['init', '--shell', 'zsh'])

    def test_command_init_unknown_shell(self):
        self.assertRaises(
            subprocess.CalledProcessError,
            run_cli,
            ['init', '--shell', 'UNKNOWN_SHELL'], stderr=subprocess.PIPE)


class FunctionalTestMixIn(object):

    def setUp(self):
        self.home_dir = tempfile.mkdtemp(prefix='rash-test-')
        self.config_dir = os.path.join(self.home_dir, '.config')
        self.conf_base_path = os.path.join(self.config_dir, 'rash')
        self.__orig_cwd = getcwd()
        os.chdir(self.home_dir)

        self.environ = os.environ.copy()
        self.environ['HOME'] = self.home_dir
        self.conf = ConfigStore(self.conf_base_path)

    def tearDown(self):
        # Kill daemon if exists
        try:
            if os.path.exists(self.conf.daemon_pid_path):
                with open(self.conf.daemon_pid_path) as f:
                    pid = f.read().strip()
                print("Daemon (PID={0}) may be left alive.  Killing it..."
                      .format(pid))
                subprocess.call(['kill', pid])
        except Exception as e:
            print("Got error while trying to kill daemon: {0}"
                  .format(e))

        try:
            os.chdir(self.__orig_cwd)
        finally:
            shutil.rmtree(self.home_dir)

    def popen(self, *args, **kwds):
        if 'env' in kwds:
            raise RuntimeError('Do not use env!')
        kwds['env'] = self.environ
        return subprocess.Popen(*args, **kwds)


class TestIsolation(FunctionalTestMixIn, BaseTestCase):

    """
    Make sure that test environment is isolated from the real one.
    """

    def test_config_isolation(self):
        proc = self.popen(
            [os.path.abspath(sys.executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        (stdout, stderr) = proc.communicate(textwrap.dedent("""
        from rash.config import ConfigStore
        conf = ConfigStore()
        print(repr(conf.base_path))
        """).encode())
        base_path = eval(stdout)
        self.assertEqual(base_path, self.conf_base_path)
        self.assertFalse(stderr)
        self.assertNotEqual(base_path, ConfigStore().base_path)


class ShellTestMixIn(FunctionalTestMixIn):

    shell = 'sh'
    source_command = '.'

    def run_shell(self, script):
        proc = self.popen(
            [self.shell],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        return proc.communicate(script)

    def get_record_data(self, record_type):
        top = os.path.join(self.conf.record_path, record_type)
        for (root, _, files) in os.walk(top):
            for f in files:
                path = os.path.join(root, f)
                with open(path) as f:
                    data = json.load(f)
                yield dict(path=path, data=data)

    def get_all_record_data(self):
        return dict(
            init=list(self.get_record_data('init')),
            exit=list(self.get_record_data('exit')),
            command=list(self.get_record_data('command')),
        )

    def test_init(self):
        script = textwrap.dedent("""
        {0} $({1} init --shell {2})
        test -n "$_RASH_SESSION_ID" && echo "_RASH_SESSION_ID is defined"
        """).format(
            self.source_command, BASE_COMMAND, self.shell).encode()
        (stdout, stderr) = self.run_shell(script)
        self.assertFalse(stderr)
        self.assertIn('_RASH_SESSION_ID is defined', stdout.decode())

        assert os.path.isdir(self.conf.record_path)
        records = self.get_all_record_data()
        self.assertEqual(len(records['init']), 1)
        self.assertEqual(len(records['exit']), 1)
        self.assertEqual(len(records['command']), 0)

        from ..record import get_environ
        subenv = get_environ(['HOST'])

        data = records['init'][0]['data']
        assert 'start' in data
        assert 'stop' not in data
        self.assertEqual(data['environ']['HOST'], subenv['HOST'])
        init_id = data['session_id']

        data = records['exit'][0]['data']
        assert 'start' not in data
        assert 'stop' in data
        assert not data['environ']
        exit_id = data['session_id']

        self.assertEqual(init_id, exit_id)

    def test_postexec(self):
        script = textwrap.dedent("""
        {0} $({1} init --shell {2})
        {3}
        """).format(
            self.source_command, BASE_COMMAND, self.shell,
            self.test_postexec_script).encode()
        (stdout, stderr) = self.run_shell(script)

        # stderr may have some errors in it
        if stderr:
            print("Got STDERR from {0} (but it's OK to ignore it)"
                  .format(self.shell))
            print(stderr)

        records = self.get_all_record_data()
        self.assertEqual(len(records['init']), 1)
        self.assertEqual(len(records['exit']), 1)
        self.assertEqual(len(records['command']), 1)

        init_data = records['init'][0]['data']
        command_data = records['command'][0]['data']
        assert command_data['session_id'] == init_data['session_id']
        assert command_data['environ']['PATH']
        assert isinstance(command_data['stop'], int)
        if self.shell.endswith('zsh'):
            assert isinstance(command_data['start'], int)
        else:
            assert 'start' not in command_data

    test_postexec_script = None
    """Set this to a shell script for :meth:`test_postexc`."""

    def test_exit_code(self):
        script = textwrap.dedent("""
        {0} $({1} init --shell {2})
        {3}
        """).format(
            self.source_command, BASE_COMMAND, self.shell,
            self.test_exit_code_script).encode()
        (stdout, stderr) = self.run_shell(script)

        # stderr may have some errors in it
        if stderr:
            print("Got STDERR from {0} (but it's OK to ignore it)"
                  .format(self.shell))
            print(stderr)

        records = self.get_all_record_data()
        self.assertEqual(len(records['init']), 1)
        self.assertEqual(len(records['exit']), 1)
        self.assertEqual(len(records['command']), 1)

        command_data = [d['data'] for d in records['command']]
        self.assertEqual(command_data[0]['exit_code'], 1)

    test_exit_code_script = None
    """Set this to a shell script for :meth:`test_exit_code`."""

    def test_pipe_status(self):
        script = textwrap.dedent("""
        {0} $({1} init --shell {2})
        {3}
        """).format(
            self.source_command, BASE_COMMAND, self.shell,
            self.test_pipe_status_script).encode()
        (stdout, stderr) = self.run_shell(script)

        # stderr may have some errors in it
        if stderr:
            print("Got STDERR from {0} (but it's OK to ignore it)"
                  .format(self.shell))
            print(stderr)

        records = self.get_all_record_data()
        self.assertEqual(len(records['init']), 1)
        self.assertEqual(len(records['exit']), 1)
        self.assertEqual(len(records['command']), 1)

        command_data = [d['data'] for d in records['command']]
        self.assertEqual(command_data[0]['pipestatus'], [1, 0])

    test_pipe_status_script = None
    """Set this to a shell script for :meth:`test_pipe_status`."""

    def test_non_existing_directory(self):
        script = textwrap.dedent("""
        {0} $({1} init --shell {2})

        rash-precmd
        mkdir non_existing_directory

        rash-precmd
        cd non_existing_directory

        rash-precmd
        rmdir ../non_existing_directory

        rash-precmd
        :

        rash-precmd
        cd ..
        """).format(
            self.source_command, BASE_COMMAND, self.shell).encode()
        (stdout, stderr) = self.run_shell(script)
        self.assertNotIn('Traceback', stderr.decode())

    @skipIf(PY3, "watchdog does not support Python 3")
    def test_daemon(self):
        script = textwrap.dedent("""
        RASH_INIT_DAEMON_OPTIONS="--keep-json --log-level=DEBUG"
        RASH_INIT_DAEMON_OUT=$HOME/.config/rash/daemon.out
        {0} $({1} init --shell {2})
        echo RASH_DAEMON_PID="$RASH_DAEMON_PID"
        """).format(
            self.source_command, BASE_COMMAND, self.shell).encode()
        (stdout, stderr) = self.run_shell(script)
        stderr = stderr.decode()
        stdout = stdout.decode()

        # These are useful when debugging, so let's leave them:
        print(stderr)
        print(stdout)
        print(self.conf.daemon_pid_path)

        # Parse `stdout` to get $RASH_DAEMON_PID
        for line in stdout.splitlines():
            if line.startswith('RASH_DAEMON_PID'):
                pid = line.split('=', 1)[1].strip()
                pid = int(pid)
                break
        else:
            raise AssertionError(
                "RASH_DAEMON_PID cannot be parsed from STDOUT")

        # The daemon process should be alive
        ps_pid_cmd = ['ps', '--pid', str(pid)]
        try:
            run_command(ps_pid_cmd)
        except subprocess.CalledProcessError:
            raise AssertionError(
                'At this point, daemon process should be live '
                '("ps --pid {0}" failed).'.format(pid))

        # The daemon process should create the PID file
        self.assert_poll(lambda: os.path.exists(self.conf.daemon_pid_path),
                         "daemon_pid_path={0!r} is not created on time"
                         .format(self.conf.daemon_pid_path))

        # The PID file should contain a number
        with open(self.conf.daemon_pid_path) as f:
            assert int(f.read().strip()) == pid

        # The daemon should create a log file
        self.assert_poll(lambda: os.path.exists(self.conf.daemon_log_path),
                         "daemon_log_path={0!r} is not created on time"
                         .format(self.conf.daemon_log_path))

        # The daemon should write some debug message to the log file
        # (Note: --log-level=DEBUG is given by $RASH_INIT_DAEMON_OPTIONS)
        with open(self.conf.daemon_log_path) as f:
            @self.assert_poll_do("Nothing written in log file.")
            def log_file_written():
                return f.read().strip()

        # Kill command should succeeds
        run_command(['kill', '-TERM', str(pid)])

        # The daemon should be killed by the TERM signal
        @self.assert_poll_do(
            "Daemon process {0} failed to exit.".format(pid))
        def terminated():
            try:
                run_command(ps_pid_cmd)
                return False
            except subprocess.CalledProcessError:
                return True

        # The daemon should remove the PID file on exit
        assert not os.path.exists(self.conf.daemon_pid_path)

    @staticmethod
    def assert_poll(assertion, message, num=100, tick=0.1):
        """
        Run `assersion` every `tick` second `num` times.

        If none of `assersion` call returns true, it raise
        an assertion error with `message`.

        """
        for i in range(num):
            if assertion():
                break
            time.sleep(tick)
        else:
            raise AssertionError(message)

    @classmethod
    def assert_poll_do(cls, message, *args, **kwds):
        """
        Decorator to run :meth:`assert_poll` right after the definition.
        """
        def decorator(assertion):
            cls.assert_poll(assertion, message, *args, **kwds)
            return assertion
        return decorator


class TestZsh(ShellTestMixIn, BaseTestCase):
    shell = 'zsh'
    test_postexec_script = textwrap.dedent("""\
    rash-precmd
    """)
    test_exit_code_script = textwrap.dedent("""\
    false
    rash-precmd
    """)
    test_pipe_status_script = textwrap.dedent("""\
    false | true
    rash-precmd
    """)

    def test_zsh_executes_preexec(self):
        script = textwrap.dedent("""
        {0} $({1} init --shell {2})
        echo _RASH_EXECUTING=$_RASH_EXECUTING
        """).format(
            self.source_command, BASE_COMMAND, self.shell).encode()
        (stdout, stderr) = self.run_shell(script)
        self.assertFalse(stderr)
        self.assertIn('_RASH_EXECUTING=t', stdout.decode())

    def test_hook_installation(self):
        script = textwrap.dedent("""
        {0} $({1} init --shell {2})
        echo $precmd_functions
        echo $preexec_functions
        """).format(
            self.source_command, BASE_COMMAND, self.shell).encode()
        (stdout, stderr) = self.run_shell(script)
        self.assertIn('rash-precmd', stdout.decode())
        self.assertIn('rash-preexec', stdout.decode())


class TestBash(ShellTestMixIn, BaseTestCase):
    shell = 'bash'
    test_postexec_script = textwrap.dedent("""\
    eval "$PROMPT_COMMAND"
    eval "$PROMPT_COMMAND"
    """)
    test_exit_code_script = textwrap.dedent("""\
    eval "$PROMPT_COMMAND"
    false
    eval "$PROMPT_COMMAND"
    """)
    test_pipe_status_script = textwrap.dedent("""\
    eval "$PROMPT_COMMAND"
    false | true
    eval "$PROMPT_COMMAND"
    """)

    def test_hook_installation(self):
        script = textwrap.dedent("""
        {0} $({1} init --shell {2})
        echo $PROMPT_COMMAND
        """).format(
            self.source_command, BASE_COMMAND, self.shell).encode()
        (stdout, stderr) = self.run_shell(script)
        self.assertIn('rash-precmd', stdout.decode())
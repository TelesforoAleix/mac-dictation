"""Installer checks use disposable Git repositories and an empty synthetic home."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys
import time
import urllib.request
import unittest


REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / 'install-tools.sh'
EXPECTED_FILES = ('recheck.py', 'review.py', 'review.html', 'review.css', 'review.js', 'review-proof.py')


class InstallToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.repo = root / 'checkout'
        self.home = root / 'home'
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home),
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                        PYTHONDONTWRITEBYTECODE='1', PYTHONPATH='')
        self.target = self.home / 'Library/Application Support/mac-dictation/bin'
        self.git('init', '--bare', str(root / 'origin.git'), cwd=root)
        self.git('init', '-b', 'main', str(self.repo), cwd=root)
        self.git('config', 'user.name', 'Synthetic Test')
        self.git('config', 'user.email', 'synthetic')
        shutil.copyfile(INSTALLER, self.repo / 'install-tools.sh')
        for name in EXPECTED_FILES:
            shutil.copyfile(REPO / name, self.repo / name)
        self.git('add', '.')
        self.git('commit', '-m', 'Synthetic base')
        self.git('remote', 'add', 'origin', str(root / 'origin.git'))
        self.git('push', '-u', 'origin', 'main')
        self.commit = self.git('rev-parse', 'HEAD').strip()

    def git(self, *args, cwd=None):
        return subprocess.check_output(['git', *args], cwd=cwd or self.repo,
                                       env=self.env, stderr=subprocess.DEVNULL,
                                       text=True)

    def install(self):
        return subprocess.run(['bash', str(self.repo / 'install-tools.sh')],
                              cwd=self.home, env=self.env, capture_output=True, text=True)

    def test_install_stamp_permissions_and_branch_independence(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.target / 'install.json').read_text()),
                         {'commit': self.commit})
        expected = (self.repo / 'recheck.py').read_bytes()
        self.assertEqual((self.target / 'recheck.py').read_bytes(), expected)
        self.assertEqual({p.name for p in self.target.iterdir()}, {*EXPECTED_FILES, 'install.json'})
        for name in EXPECTED_FILES:
            self.assertEqual((self.target / name).read_bytes(), (self.repo / name).read_bytes())
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.target.parent.stat().st_mode & 0o777, 0o700)
        for name in (*EXPECTED_FILES, 'install.json'):
            self.assertEqual((self.target / name).stat().st_mode & 0o777, 0o600)
        self.git('switch', '-c', 'synthetic-branch')
        (self.repo / 'recheck.py').write_text('# different branch\n')
        self.assertEqual((self.target / 'recheck.py').read_bytes(), expected)

    def test_installed_review_serves_assets_and_notification_targets_installed_code(self):
        self.assertEqual(self.install().returncode, 0)
        review_path = self.home / 'synthetic-review.json'
        review_path.write_text(json.dumps({'version': 1, 'run_id': 'synthetic', 'items': []}))
        session = self.home / 'session.json'
        process = subprocess.Popen([sys.executable, '-B', str(self.target / 'review.py'),
                                    '--review', str(review_path), '--no-open', '--no-copy',
                                    '--session', str(session), '--idle', '5'], cwd=self.home,
                                   env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for _ in range(100):
                if session.exists() or process.poll() is not None:
                    break
                time.sleep(.02)
            self.assertTrue(session.exists(), 'Installed review must start outside the checkout')
            info = json.loads(session.read_text())
            self.assertTrue(info['copy_disabled'])
            for route, name in [('', 'review.html'), ('review.css', 'review.css'), ('review.js', 'review.js')]:
                with urllib.request.urlopen(info['url'] + route, timeout=2) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), (self.target / name).read_bytes())
            origin = info['url'].rsplit('/', 2)[0]
            request = urllib.request.Request(info['url'] + 'close', b'{}',
                                              {'Content-Type': 'application/json', 'Origin': origin})
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 200)
            stdout, stderr = process.communicate(timeout=3)
            self.assertEqual((process.returncode, stderr), (0, b''))
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
        # Load only installed modules in a separate interpreter outside the checkout.
        # Mock Mac commands; generated callback must point to the installed review.
        code = """import sys
from pathlib import Path
import plistlib
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import recheck, review
installed = Path(sys.argv[1])
assert Path(recheck.__file__).parent == installed
assert Path(review.__file__).parent == installed
root = Path(sys.argv[2]) / 'synthetic-notifier'
app = root / 'Weekly Dictation Review.app'
(app / 'Contents').mkdir(parents=True)
(app / 'Contents/Info.plist').write_bytes(plistlib.dumps({}))
with patch.object(review.recheck, 'run_quiet'):
    review.notify(Path(sys.argv[2]) / 'synthetic-review.json', 1, root, no_copy=True)
source = (root / 'review-notification.applescript').read_text()
assert str(installed / 'review.py') in source
assert sys.argv[3] not in source
assert '--no-copy' in source
print('Installed callback verified.')
"""
        result = subprocess.run([sys.executable, '-B', '-c', code, str(self.target),
                                 str(self.home), str(self.repo)], cwd=self.home, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'Installed callback verified.\n')

    def test_refuses_dirty_tracked_staged_and_untracked_files(self):
        for kind in ('tracked', 'staged', 'untracked'):
            with self.subTest(kind=kind):
                path = self.repo / ('extra.txt' if kind == 'untracked' else 'recheck.py')
                original = path.read_bytes() if path.exists() else None
                path.write_text('# synthetic edit\n')
                if kind == 'staged':
                    self.git('add', path.name)
                result = self.install()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Checkout must be clean', result.stderr)
                self.assertFalse(self.target.exists())
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)
                    self.git('add', path.name)

    def test_refuses_unmerged_commit_and_preserves_install(self):
        self.assertEqual(self.install().returncode, 0)
        before = {p.name: p.read_bytes() for p in self.target.iterdir()}
        self.git('switch', '-c', 'unmerged')
        (self.repo / 'recheck.py').write_text('# unmerged code\n')
        self.git('commit', '-am', 'Synthetic unmerged change')
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not contained in origin/main', result.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in self.target.iterdir()}, before)

    def test_accepts_ancestor_of_main(self):
        (self.repo / 'extra.txt').write_text('synthetic\n')
        self.git('add', '.')
        self.git('commit', '-m', 'Synthetic later main')
        self.git('push', 'origin', 'main')
        self.git('checkout', '--detach', self.commit)
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(json.loads((self.target / 'install.json').read_text())['commit'],
                         self.commit)

    def test_update_replaces_code_and_stamp(self):
        self.assertEqual(self.install().returncode, 0)
        (self.repo / 'recheck.py').write_text('# synthetic update\n')
        self.git('commit', '-am', 'Synthetic update')
        self.git('push', 'origin', 'main')
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual((self.target / 'recheck.py').read_text(), '# synthetic update\n')
        self.assertEqual(json.loads((self.target / 'install.json').read_text())['commit'],
                         self.git('rev-parse', 'HEAD').strip())

    def test_fetch_failure_refuses_install(self):
        self.git('remote', 'set-url', 'origin', str(self.home / 'missing.git'))
        self.assertNotEqual(self.install().returncode, 0)
        self.assertFalse(self.target.exists())

    def test_refuses_symlink_destination(self):
        self.target.parent.mkdir(parents=True)
        elsewhere = self.home / 'elsewhere'
        elsewhere.mkdir()
        self.target.symlink_to(elsewhere, target_is_directory=True)
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(list(elsewhere.iterdir()), [])

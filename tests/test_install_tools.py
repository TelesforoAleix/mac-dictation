"""Installer checks use disposable Git repositories and an empty synthetic home."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / 'install-tools.sh'


class InstallToolsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.repo = root / 'checkout'
        self.home = root / 'home'
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home),
                        GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.target = self.home / 'Library/Application Support/mac-dictation/bin'
        self.git('init', '--bare', str(root / 'origin.git'), cwd=root)
        self.git('init', '-b', 'main', str(self.repo), cwd=root)
        self.git('config', 'user.name', 'Synthetic Test')
        self.git('config', 'user.email', 'synthetic')
        shutil.copyfile(INSTALLER, self.repo / 'install-tools.sh')
        (self.repo / 'recheck.py').write_text('# synthetic job code\n')
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
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.target.parent.stat().st_mode & 0o777, 0o700)
        for name in ('recheck.py', 'install.json'):
            self.assertEqual((self.target / name).stat().st_mode & 0o777, 0o600)
        self.git('switch', '-c', 'synthetic-branch')
        (self.repo / 'recheck.py').write_text('# different branch\n')
        self.assertEqual((self.target / 'recheck.py').read_bytes(), expected)

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

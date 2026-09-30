"""Exercise the installation checker with isolated current and stale policies."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CodexRoutingTests(unittest.TestCase):
    def test_current_install_and_stale_role_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            skill = home / 'skills' / 'effort-router'
            (skill / 'agents').mkdir(parents=True)
            shutil.copy(ROOT / 'SKILL.md', skill)
            shutil.copy(ROOT / 'agents/openai.yaml', skill / 'agents')
            shutil.copytree(ROOT / 'platforms/codex-agents', home / 'agents')
            (home / 'config.toml').write_text(
                'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "medium"\n'
                '[agents]\nenabled = true\n')
            adapter = (ROOT / 'platforms/codex.md').read_text()
            guide = adapter.split('## AGENTS.md 삽입 단편')[1].split('```markdown\n')[1].split('```')[0]
            (home / 'AGENTS.md').write_text(guide)

            def verify():
                return subprocess.run(
                    [sys.executable, str(ROOT / 'scripts/verify_global_install.py'), '--plan', 'pro'],
                    env=dict(os.environ, CODEX_HOME=str(home)),
                    capture_output=True, text=True)

            result = verify()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('custom agents: 10/10', result.stdout)
            for role in ('plan-high', 'plan-xhigh', 'plan-adversary-xhigh',
                         'review-pr-high', 'review-pr-xhigh', 'security-audit',
                         'implement-xhigh', 'core-xhigh'):
                with self.subTest(role=role):
                    path = home / 'agents' / (role + '.toml')
                    original = path.read_text()
                    stale = (original.replace('gpt-6.1-sol', 'gpt-5.6-sol')
                             .replace('gpt-6-astra', 'gpt-5.6-sol')
                             .replace('gpt-6-luna', 'gpt-5.6-sol'))
                    path.write_text(stale)
                    result = verify()
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn('agent ' + role + ' expected', result.stdout)
                    path.write_text(original)

            apply = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/configure_codex_plan.py'),
                 '--plan', 'plus', '--agents-dir', str(home / 'agents'), '--apply'],
                capture_output=True, text=True)
            self.assertEqual(apply.returncode, 0, apply.stderr)
            result = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/verify_global_install.py'), '--plan', 'plus'],
                env=dict(os.environ, CODEX_HOME=str(home)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('plan: plus; Sol review effort: xhigh; Astra effort: medium', result.stdout)
            self.assertEqual(verify().returncode, 1)  # Plus files must fail Pro validation.

    def test_expected_main_model_pair_controls_main_config(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            skill = home / 'skills' / 'effort-router'
            (skill / 'agents').mkdir(parents=True)
            shutil.copy(ROOT / 'SKILL.md', skill)
            shutil.copy(ROOT / 'agents/openai.yaml', skill / 'agents')
            shutil.copytree(ROOT / 'platforms/codex-agents', home / 'agents')
            config = home / 'config.toml'
            config.write_text(
                'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "medium"\n'
                '[agents]\nenabled = true\n')
            adapter = (ROOT / 'platforms/codex.md').read_text()
            guide = adapter.split('## AGENTS.md 삽입 단편')[1].split('```markdown\n')[1].split('```')[0]
            (home / 'AGENTS.md').write_text(guide)

            def verify(*args):
                return subprocess.run(
                    [sys.executable, str(ROOT / 'scripts/verify_global_install.py'),
                     '--plan', 'pro', *args],
                    env=dict(os.environ, CODEX_HOME=str(home)),
                    capture_output=True, text=True)

            result = verify()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('supported main preset: gpt-6.1-sol/(low|medium|high|xhigh|max)', result.stdout)
            for effort in ('low', 'medium', 'high', 'xhigh', 'max'):
                config.write_text(
                    f'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "{effort}"\n'
                    '[agents]\nenabled = true\n')
                result = verify()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f'configured main: gpt-6.1-sol/{effort}', result.stdout)
            result = verify('--expected-main-model', 'gpt-6-astra',
                            '--expected-main-effort', 'high')
            self.assertEqual(result.returncode, 1)
            self.assertIn('config model must be gpt-6-astra', result.stdout)

            config.write_text(
                'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "medium"\n'
                '[agents]\nenabled = true\n')
            result = verify('--expected-main-model', 'gpt-6.1-sol',
                            '--expected-main-effort', 'low')
            self.assertEqual(result.returncode, 1)
            self.assertIn('config model_reasoning_effort must be low', result.stdout)
            config.write_text(
                'model = "gpt-6.1-sol"\nmodel_reasoning_effort = "low"\n'
                '[agents]\nenabled = true\n')
            self.assertEqual(verify().returncode, 0)
            self.assertEqual(verify('--expected-main-model', 'gpt-6.1-sol',
                                    '--expected-main-effort', 'low').returncode, 0)

            config.write_text(
                'model = "gpt-6-astra"\nmodel_reasoning_effort = "high"\n'
                '[agents]\nenabled = true\n')
            result = verify('--expected-main-model', 'gpt-6-astra',
                            '--expected-main-effort', 'high')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(verify().returncode, 1)

            config.write_text(
                'model = "gpt-5.6-luna"\nmodel_reasoning_effort = "max"\n'
                '[agents]\nenabled = true\n')
            result = verify()
            self.assertEqual(result.returncode, 1)
            self.assertIn('config model must be gpt-6.1-sol', result.stdout)


if __name__ == '__main__':
    unittest.main()

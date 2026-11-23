"""auction_features: pinned input, immutable publication, reuse, report isolation, failure handling, CLI."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auction_data.storage import digest
from auction_features import build as fbuild
from auction_features.__main__ import main
from auction_features.config import INPUT_FILES
from tests.feature_helpers import synth_panel, write_processed


def snapshot(directory):
    return {str(p.relative_to(directory)): (digest(p), p.stat().st_mtime_ns)
            for p in sorted(Path(directory).rglob('*')) if p.is_file()}


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panel = synth_panel()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vdir = write_processed(self.tmp, self.panel)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_build_verify_reuse_and_processed_unchanged(self):
        before = snapshot(self.tmp / 'processed')
        m = fbuild.build(self.tmp, workers=1)
        self.assertFalse(m['reused'])
        self.assertEqual(m['processed']['dir'], str(self.vdir.resolve()))
        self.assertEqual(m['processed']['inputs'][INPUT_FILES[0]], digest(self.vdir / INPUT_FILES[0]))
        self.assertEqual(snapshot(self.tmp / 'processed'), before)
        version = Path(m['version_dir'])
        self.assertEqual((self.tmp / 'features' / 'current').resolve(), version.resolve())
        self.assertTrue(fbuild.verify(version)['ok'])
        self.assertEqual(m['tables']['daily']['rows'], len(self.panel))
        self.assertEqual(m['tables']['intraday']['rows'], 6 * int(self.panel.cp_row.sum()))
        frozen = snapshot(version)
        again = fbuild.build(self.tmp, workers=1)
        self.assertTrue(again['reused'])
        self.assertEqual(snapshot(version), frozen)
        self.assertFalse(list((self.tmp / 'features' / '.versions').glob('.tmp-*')))

    def test_rebuild_from_scratch_same_content(self):
        a = fbuild.build(self.tmp, workers=1)
        other = Path(tempfile.mkdtemp())
        try:
            write_processed(other, self.panel)
            b = fbuild.build(other, workers=2)
            self.assertEqual({k: v['content_hash'] for k, v in a['tables'].items()},
                             {k: v['content_hash'] for k, v in b['tables'].items()})
            self.assertEqual(a['fingerprint'], b['fingerprint'])
        finally:
            shutil.rmtree(other)

    def test_report_and_verify_do_not_touch_version(self):
        m = fbuild.build(self.tmp, workers=1)
        version = Path(m['version_dir'])
        frozen = snapshot(version)
        with patch('sys.stdout'):
            self.assertEqual(main(['report', '--data-dir', str(self.tmp)]), 0)
            self.assertEqual(main(['verify', '--data-dir', str(self.tmp)]), 0)
        self.assertEqual(snapshot(version), frozen)
        reports = list((self.tmp / 'features' / 'reports' / version.name).glob('*/summary.json'))
        self.assertEqual(len(reports), 1)

    def test_failed_build_keeps_current(self):
        m = fbuild.build(self.tmp, workers=1)
        with patch.object(fbuild, 'fingerprint', return_value='e' * 64), \
                patch.object(fbuild.reporting, 'write_report', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                fbuild.build(self.tmp, workers=1)
        self.assertEqual((self.tmp / 'features' / 'current').resolve(), Path(m['version_dir']).resolve())
        self.assertEqual(len(list((self.tmp / 'features' / '.versions').glob('.tmp-*'))), 1)

    def test_input_digest_mismatch_fails(self):
        with open(self.vdir / INPUT_FILES[1], 'ab') as f:
            f.write(b'x')
        with self.assertRaises(ValueError):
            fbuild.build(self.tmp, workers=1)
        self.assertFalse((self.tmp / 'features' / 'current').exists())

    def test_pinned_directory_survives_pointer_switch(self):
        other = self.tmp / 'processed' / '.wrds_versions' / 'v2'
        shutil.copytree(self.vdir, other)
        real = fbuild.input_identity

        def switching(path):
            ident = real(path)
            link = self.tmp / 'processed' / 'wrds_current'
            link.unlink()
            link.symlink_to(Path('.wrds_versions') / 'v2', target_is_directory=True)
            return ident

        with patch.object(fbuild, 'input_identity', side_effect=switching):
            m = fbuild.build(self.tmp, workers=1)
        self.assertEqual(m['processed']['dir'], str(self.vdir.resolve()))

    def test_tampered_version_fails_verify(self):
        m = fbuild.build(self.tmp, workers=1)
        version = Path(m['version_dir'])
        manifest = json.loads((version / 'manifest.json').read_text())
        first = sorted(manifest['files'])[0]
        target = version / first
        os.chmod(target, 0o644)
        import pandas as pd
        df = pd.read_parquet(target)
        df.iloc[0, df.columns.get_loc('security_id')] = 'X'
        df.to_parquet(target, index=False)
        self.assertFalse(fbuild.verify(version)['ok'])


if __name__ == '__main__':
    unittest.main()

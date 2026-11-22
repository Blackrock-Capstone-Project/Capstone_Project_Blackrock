"""Snapshot comparison: raw WRDS revisions and processed-version differences, read-only."""
from pathlib import Path
import json
import shutil
import subprocess
import sys

import pandas as pd

from scripts.data_pipeline import update_data
from scripts.data_pipeline.common import digest, sessions
from scripts.data_pipeline.compare import compare
import tempfile

from scripts.data_pipeline.tests import test_pipeline as tp
from scripts.data_pipeline.tests.test_pipeline import taq_frame


def tree_digest(root):
    return {str(p.relative_to(root)): digest(p) for p in sorted(Path(root).rglob('*')) if p.is_file() and not p.is_symlink()}


class CompareTests(tp.PipelineTests):
    def test_revision_detected_and_inputs_untouched(self):
        self.assertTrue(self.update()['complete'])
        work = tempfile.TemporaryDirectory(); self.addCleanup(work.cleanup)
        old = Path(work.name) / 'old'
        shutil.copytree(self.root, old, symlinks=True)
        revised_day = sessions(self.start, self.end)[-1]
        # WRDS revises the latest session's cross volume; a refresh re-downloads it into the new snapshot.
        self.mocks[2].side_effect = lambda db, d: taq_frame(d).assign(xvol_n=0. if d == revised_day else 100.)
        r = update_data(self.start, self.end, data_dir=self.root, connection=object(), refresh_sessions=1)
        self.assertTrue(r['complete'], r)
        before_old, before_new = tree_digest(old), tree_digest(self.root)
        out = Path(work.name) / 'cmp_raw'
        raw = compare(old, self.root, out)
        rel = 'raw/taq/days/' + revised_day.replace('-', '') + '.csv.gz'
        self.assertEqual(raw['partitions'][rel]['status'], 'changed')
        self.assertEqual(raw['partitions'][rel]['diff']['changed_by_column'], {'xvol_n': 1})
        self.assertEqual(raw['counts'].get('changed'), 1)  # history is re-fetched but identical
        self.assertTrue((out / 'summary.json').exists())
        # Processed versions: the revised day is now an invalid label -> research sample shrinks.
        v_old = (old / 'processed/wrds_current').resolve()
        v_new = (self.root / 'processed/wrds_current').resolve()
        proc = compare(v_old, v_new, Path(work.name) / 'cmp_proc')
        self.assertFalse(proc['fingerprint_equal'])
        self.assertEqual(proc['input_partitions']['changed'], [rel])
        self.assertEqual(proc['files']['us_wrds_quality/research_sample.csv.gz'], 'different')
        self.assertEqual(proc['research_sample']['removed'], 1)
        self.assertEqual(tree_digest(old), before_old)
        self.assertEqual(tree_digest(self.root), before_new)
        # Identical snapshot compares clean; CLI wrapper returns 0.
        same = compare(old, old)
        self.assertEqual(set(same['counts']), {'identical'})
        cli = subprocess.run([sys.executable, '-m', 'scripts.data_pipeline', 'compare', '--old', str(v_old), '--new', str(v_old)],
                             capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertTrue(json.loads(cli.stdout)['fingerprint_equal'])


for _name in [n for n in dir(tp.PipelineTests) if n.startswith('test_')]:
    setattr(CompareTests, _name, None)

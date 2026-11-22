"""Versioned, all-or-nothing publication under Data/processed/.

Every build goes to .wrds_versions/<id>/, is read back and fingerprinted, and only then becomes current by
atomically switching the `wrds_current` symlink; public directories are aliases through that pointer.
"""
from pathlib import Path
import os
import uuid

import pandas as pd

from .settings import PipelineError
from .storage import deterministic_gzip, digest, output_valid, read_json, save_json, verify_table

ALIASES = ['us_wrds_daily', 'us_wrds_taq', 'us_wrds_quality', 'reference', 'report']


def reuse_existing(root, fingerprint):
    """Current version's manifest if it already matches the fingerprint, else None."""
    processed = Path(root)/'processed'
    processed.mkdir(parents=True, exist_ok=True)
    pointer = processed/'wrds_current'
    if pointer.exists() and not pointer.is_symlink():
        raise PipelineError('wrds_current must be a managed symlink')
    if output_valid(pointer, fingerprint):
        return read_json(pointer/'manifest.json') | {'reused': True}
    return None


class Generation:
    """One build directory; becomes current only through switch(). A failed build is kept for diagnosis."""

    def __init__(self, root, manifest):
        self.processed = Path(root)/'processed'
        self.pointer = self.processed/'wrds_current'
        self.previous_sample = (self.pointer/'us_wrds_quality'/'research_sample.csv.gz'
                                if self.pointer.exists() else None)
        self.dir = self.processed/'.wrds_versions'/uuid.uuid4().hex
        self.dir.mkdir(parents=True)
        self.manifest = manifest

    def table(self, name, frame, keys):
        p = self.dir/name
        p.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(p, index=False, compression={'method': 'gzip', 'mtime': 0} if name.endswith('.gz') else None)
        verify_table(p, keys, len(frame))
        self.manifest['files'][name] = dict(rows=len(frame), sha256=digest(p), keys=keys,
                                            missing_fields={c: int(frame[c].isna().sum()) for c in frame})

    def record(self, name):
        self.manifest['files'][name] = dict(sha256=digest(self.dir/name))

    def streams(self, outputs):
        return Streams(self, outputs)

    def switch(self):
        save_json(self.dir/'manifest.json', self.manifest)
        for name in ALIASES:
            alias = self.processed/name
            if alias.exists() and not alias.is_symlink():
                # Retain an existing independent output directory as a previous local version.
                os.replace(alias, self.processed/'.wrds_versions'/('pre_pipeline_'+name+'_'+uuid.uuid4().hex))
            tmp = self.processed/('.alias_'+uuid.uuid4().hex)
            tmp.symlink_to(Path('wrds_current')/name, target_is_directory=True)
            os.replace(tmp, alias)
        tmp = self.processed/('.current_'+uuid.uuid4().hex)
        tmp.symlink_to(self.dir.relative_to(self.processed), target_is_directory=True)
        os.replace(tmp, self.pointer)
        return self.manifest | {'reused': False}


class Streams:
    """Large tables written incrementally (one security at a time) as deterministic gzip CSV."""

    def __init__(self, generation, outputs):
        self.g, self.outputs = generation, outputs
        for name in outputs:
            (generation.dir/name).parent.mkdir(parents=True, exist_ok=True)
        self.counts = {n: 0 for n in outputs}
        self.handles = {n: deterministic_gzip(generation.dir/n) for n in outputs}
        self.written = {n: False for n in outputs}

    def emit(self, name, frame):
        frame.to_csv(self.handles[name], index=False, header=not self.written[name])
        self.written[name] = True
        self.counts[name] += len(frame)

    def write_missing_headers(self):
        for name, keys in self.outputs.items():
            if not self.written[name]:
                pd.DataFrame(columns=keys+['eligible', 'pit_eligible']).to_csv(self.handles[name], index=False)

    def close(self):
        for h in self.handles.values():
            h.close()

    def verify(self, skip_duplicate_check=()):
        for name, keys in self.outputs.items():
            p = self.g.dir/name
            verify_table(p, keys, self.counts[name], check_duplicates=name not in skip_duplicate_check)
            self.g.manifest['files'][name] = dict(rows=self.counts[name], sha256=digest(p), keys=keys)

"""Check installed wheel resources without private data or network access."""
import hashlib
import json
from pathlib import Path
import sys
from ai_tender_intelligence.assets import root


def check():
    assets = root()
    expected = Path(sys.prefix) / 'share/ai-tender-intelligence'
    if assets.resolve() != expected.resolve():
        raise ValueError('Expected installed wheel resources, not source checkout')
    provenance = json.loads((assets/'reference-data/cpv/2008/PROVENANCE.json').read_text())
    catalogue = assets/'reference-data/cpv/2008/cpv_2008.xml'
    digest = hashlib.sha256(catalogue.read_bytes()).hexdigest()
    if digest not in json.dumps(provenance):
        raise ValueError('Bundled catalogue checksum mismatch')
    for folder in ('migrations', 'discovery_migrations', 'document_migrations', 'extraction_migrations'):
        if not (assets/folder/'env.py').is_file() or not list((assets/folder/'versions').glob('*.py')):
            raise ValueError('Missing installed migration resources')
    if len(list((assets/'prompts').glob('*.md'))) != 15:
        raise ValueError('Missing installed prompts')
    from ai_tender_intelligence.beta67.__main__ import main
    from ai_tender_intelligence.bootstrap import setup
    assert callable(main) and callable(setup)
    print('Installed entrypoints, migrations, prompts and verified CPV data are available')


if __name__ == '__main__':
    check()

"""Publication and CI fail closed without printing suspected secret values."""
import importlib.util
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]


def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('name',['source_records/test.json','AGENTS.md','PROJECT.md',
    'docs/IMPLEMENTATION_99_REPORT.md','.env','credentials.json','tokens.json','backup.dump'])
def test_private_files_cannot_be_allowlisted(tmp_path,name):
    path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('private')
    (tmp_path/'PUBLIC_FILES.txt').write_text('PUBLIC_FILES.txt\n'+name+'\n')
    with pytest.raises(ValueError,match='private/internal'):load('check_public_tree').check(tmp_path)


@pytest.mark.parametrize('secret',[
    'sk-'+'a'*35,'123456789:'+('b'*36),'ghp_'+('c'*36),
    json.dumps(dict(refresh_token='d'*35)),
])
def test_credential_scan_rejects_without_echoing_value(tmp_path,secret):
    (tmp_path/'README.md').write_text(secret)
    (tmp_path/'PUBLIC_FILES.txt').write_text('PUBLIC_FILES.txt\nREADME.md\n')
    with pytest.raises(ValueError) as failure:load('check_public_tree').check(tmp_path)
    assert secret not in str(failure.value)


def test_unlisted_file_is_rejected(tmp_path):
    (tmp_path/'PUBLIC_FILES.txt').write_text('PUBLIC_FILES.txt\n')
    (tmp_path/'extra.txt').write_text('unexpected')
    with pytest.raises(ValueError,match='mismatch'):load('check_public_tree').check(tmp_path)


@pytest.mark.parametrize('status',['skipped','failure','error','missing'])
def test_ci_cannot_pass_with_skipped_or_missing_postgres_tests(tmp_path,status):
    xml=tmp_path/'results.xml'
    case='' if status=='missing' else f'<testcase><{status}/></testcase>'
    xml.write_text('<testsuite>'+case+'</testsuite>')
    with pytest.raises(ValueError):load('check_test_results').check(xml,1)


def test_ci_accepts_executed_postgres_tests(tmp_path):
    xml=tmp_path/'results.xml';xml.write_text('<testsuite><testcase/></testsuite>')
    assert load('check_test_results').check(xml,1)==1


@pytest.mark.parametrize('missing', [
    'acquisition.validate_worker', 'acquisition.validation',
    'extraction.worker', 'extraction.engine', 'extraction.formats',
])
def test_ci_fails_with_any_missing_document_runtime_module(missing):
    checker = load('check_runtime_dependencies')
    package = ROOT / 'src' / checker.PACKAGE
    sources = checker.modules(package)
    module = checker.PACKAGE + '.' + missing
    del sources[module]
    with pytest.raises(ValueError, match='Missing internal runtime dependencies') as error:
        checker.check_imports(sources)
    assert module in str(error.value)

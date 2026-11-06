"""Fail closed against the explicit reviewed publication file list."""
from pathlib import Path
import re
import sys


def check(root):
    allowed=set((root/'PUBLIC_FILES.txt').read_text(encoding='utf-8').splitlines())
    if not allowed or any(not p or '..' in Path(p).parts or Path(p).is_absolute() for p in allowed):
        raise ValueError('Invalid publication allowlist')
    actual={p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()
            and not any(part in {'.git','.venv','__pycache__','.pytest_cache','build','dist'}
                        or part.endswith('.egg-info') for part in p.relative_to(root).parts)}
    if actual!=allowed:raise ValueError('Publication file mismatch: '+str(sorted(actual^allowed)))
    problems=[]
    patterns=[re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
              re.compile(r'(?<![\w-])sk-(?:proj-)?[A-Za-z0-9_-]{20,}'),
              re.compile(r'\b[0-9]{8,12}:[A-Za-z0-9_-]{30,}\b'),
              re.compile(r'(?i)[A-Z]:[\\/]Users[\\/]'),
              re.compile(r'ya29\.[A-Za-z0-9_-]{20,}')]
    for name in sorted(allowed):
        path=root/name
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():problems.append(name+': link')
        text=path.read_text(encoding='utf-8',errors='replace')
        if any(p.search(text) for p in patterns):problems.append(name+': sensitive pattern')
    if problems:raise ValueError('Publication safety check failed: '+str(problems))
    return len(allowed)


if __name__=='__main__':
    try:print('Public allowlist and credential/path checks passed:',check(Path(__file__).resolve().parents[1]),'files')
    except Exception as error:print(str(error));raise SystemExit(1)

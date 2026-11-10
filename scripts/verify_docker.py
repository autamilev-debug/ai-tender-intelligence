"""Disposable, network-isolated image verification; never uses host credentials/data."""
import argparse
import json
import subprocess
import time
from uuid import uuid4


def docker(*args,success=True):
    result=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=180)
    if success and result.returncode:
        raise RuntimeError('Docker verification command failed: '+result.stderr[-2000:])
    return result


def wait_for(check,seconds=60):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if check():return
        time.sleep(1)
    raise RuntimeError('Disposable container did not become ready')


def verify(image):
    prefix='tender-check-'+uuid4().hex[:12]
    network=prefix+'-net';pg=prefix+'-pg';web=prefix+'-web'
    containers=[];network_created=False
    try:
        result=docker('run','--rm','--network','none','--read-only','--tmpfs','/tmp',image,'--offline-check')
        assert 'Installed entrypoints' in result.stdout
        assert 'Document workers verified:' in result.stdout
        assert docker('run','--rm','--network','none','--entrypoint','python',image,
                      '-c','import os; assert os.getuid() != 0; print("non-root")').stdout.strip()=='non-root'
        missing=docker('run','--rm','--network','none',image,success=False)
        assert missing.returncode==2 and 'DATABASE_URL, TELEGRAM_BOT_TOKEN' in missing.stderr
        print('PASS: installed assets, isolated document workers, non-root user and missing-configuration failure',flush=True)

        containers.append(web)
        docker('run','-d','--name',web,'--network','none','--read-only','--tmpfs','/tmp',
               '--stop-timeout','35','--env','PORT=8123',image,'--health-only')
        wait_for(lambda:docker('exec',web,'python','-m','ai_tender_intelligence.container_runtime','--probe',success=False).returncode==0)
        wait_for(lambda:json.loads(docker('inspect',web).stdout)[0]['State']['Health']['Status']=='healthy')
        actual=docker('exec',web,'python','-c',
            'import json,urllib.request; assert json.load(urllib.request.urlopen("http://127.0.0.1:8123/health/live")) == {"status":"ok"}')
        docker('stop','--time','35',web)
        state=json.loads(docker('inspect',web).stdout)[0]['State']
        assert state['ExitCode']==0 and not state['OOMKilled']
        logs=docker('logs',web)
        assert 'Container stopped; no automatic replay.' in logs.stdout
        print('PASS: HTTP liveness, Docker health status, visible logs and graceful stop',flush=True)

        docker('network','create','--internal',network);network_created=True
        containers.append(pg)
        docker('run','-d','--name',pg,'--network',network,'--network-alias','postgres',
            '--tmpfs','/var/lib/postgresql/data:rw,size=256m',
            '--env','POSTGRES_PASSWORD=disposable-test-only','--env','POSTGRES_DB=p5_test_container',
            'postgres:17-alpine')
        wait_for(lambda:docker('exec',pg,'pg_isready','-U','postgres','-d','p5_test_container',success=False).returncode==0)
        docker('exec',pg,'psql','-U','postgres','-d','p5_test_container','-v','ON_ERROR_STOP=1','-c',
            "CREATE ROLE p5_search_runtime LOGIN PASSWORD 'disposable-runtime-only' NOSUPERUSER NOCREATEDB NOCREATEROLE;")
        def run_db(user,password,mode,success=True):
            url='postgresql+psycopg://' + user + ':' + password + '@postgres/p5_test_container'
            return docker('run','--rm','--network',network,'--read-only','--tmpfs','/tmp',
                '--env','DATABASE_URL='+url,'--env','P5_DATABASE_HOST=postgres','--env','P5_DB_SCOPE=docker-test',
                image,mode,success=success)
        before=run_db('p5_search_runtime','disposable-runtime-only','--check',success=False)
        assert before.returncode!=0  # Startup/check never implicitly creates tables.
        run_db('postgres','disposable-test-only','--setup')
        ready=run_db('p5_search_runtime','disposable-runtime-only','--check')
        assert 'readiness passed' in ready.stdout
        version=docker('exec',pg,'psql','-U','postgres','-d','p5_test_container','-Atc',
                       "SELECT current_database(),current_setting('server_version_num')::int/10000")
        assert version.stdout.strip()=='p5_test_container|17'
        print('PASS: isolated PostgreSQL 17, explicit setup and restricted-role readiness',flush=True)
        print('PASS: no host credentials, bind mounts, external APIs or existing databases used',flush=True)
    finally:
        for name in reversed(containers):docker('rm','-f','-v',name,success=False)
        if network_created:docker('network','rm',network,success=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='ai-tender-intelligence:check')
    args=parser.parse_args()
    verify(args.image)

# coding: utf-8
"""Fail-closed snapshot overlay installer; no network, Docker or database usage."""
from __future__ import annotations
import datetime,hashlib,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PACK = json.loads((HERE/'manifest.json').read_text(encoding='utf-8'))
BACKUPS = ROOT.parent/'Tandem_Local'/PACK['backup_id']/'backups'


def digest(data): return hashlib.sha256(data).hexdigest()

def target_path(relative):
    p=Path(relative)
    if p.is_absolute() or any(x in ('..','') for x in p.parts): raise ValueError('unsafe path')
    path=ROOT/p
    # symlink in parent or file is not an admissible install target
    if path.is_symlink() or any(x.is_symlink() for x in path.parents if x!=ROOT.parent):
        raise ValueError('symlink in installation target')
    return path


def inspect():
    if not all((ROOT/p).is_file() for p in PACK['root_markers']):
        raise RuntimeError('wrong repository root, missing expected markers')
    result={}
    for relative,entry in PACK['files'].items():
        p=target_path(relative)
        actual=digest(p.read_bytes()) if p.exists() and p.is_file() else None
        wanted=entry['sha256']
        before=entry['base_sha256']
        state='ALREADY_UPDATED' if actual==wanted else 'READY' if actual==before else 'CONFLICT'
        result[relative]=state
        print(f'[{state}] {relative}')
    if 'CONFLICT' in result.values(): raise RuntimeError('SHA-256 conflict; no file written')
    return result


def test_sources(source):
    suite=PACK['suite']
    if suite=='panel':
        cmd=[sys.executable,'-m','unittest','tests.test_core','-q']
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
    elif suite=='spaces':
        exe=shutil.which('node')
        if not exe: raise RuntimeError('Node.js unavailable: source qualification cannot be verified')
        cmd=[exe,'--test','tests-runtime/source-qualification.test.mjs']
        env=dict(os.environ)
    elif suite=='koa':
        # Bounded static source invariant tests, never a production activation claim.
        a=(source/'components/koa-node-agent/src/bin/koa-node-agent.rs').read_text(encoding='utf-8')
        b=(source/'components/koa-node-agent/src/application/validate_request.rs').read_text(encoding='utf-8')
        c=(source/'components/koa-node-agent/README.md').read_text(encoding='utf-8')
        assertions=[
            ('serve fails closed', 'authenticated local transport and fixed system adapters are not configured' in a and 'ExitCode::from(78)' in a),
            ('default operations disabled', 'enabled_operations: BTreeSet::new()' in b),
            ('Capsule Agent separate owner', 'Konnaxion' in c and 'Capsule Agent' in c),
            ('No readiness by declaration', 'not a drop-in Capsule Agent' in c and 'refuses to start' in c),
        ]
        for title,ok in assertions: print('[PASS]' if ok else '[FAIL]',title)
        if not all(ok for _,ok in assertions): raise RuntimeError('Node Agent source contract invariant failure')
        return
    else: raise RuntimeError('unrecognized test suite')
    print('[TEST]', ' '.join(cmd))
    run=subprocess.run(cmd,cwd=source,env=env,timeout=180,check=False)
    if run.returncode!=0: raise RuntimeError('targeted tests failed with code '+str(run.returncode))


def test_in_copy():
    with tempfile.TemporaryDirectory(prefix='tandem-tour8-') as folder:
        stage=Path(folder)/'repo'
        stage.mkdir()
        suite=PACK['suite']
        if suite=='panel':
            shutil.copytree(ROOT/'koali_control',stage/'koali_control',ignore=shutil.ignore_patterns('__pycache__','.venv'))
            (stage/'tests').mkdir()
            shutil.copy2(ROOT/'tests/test_core.py',stage/'tests/test_core.py')
            (stage/'tests/__init__.py').write_text('',encoding='utf-8')
        elif suite=='spaces':
            (stage/'server/ecosystem').mkdir(parents=True)
            (stage/'tests-runtime').mkdir()
            shutil.copy2(ROOT/'server/ecosystem/source-qualification.mjs',stage/'server/ecosystem/source-qualification.mjs')
            shutil.copy2(ROOT/'tests-runtime/source-qualification.test.mjs',stage/'tests-runtime/source-qualification.test.mjs')
        else:
            (stage/'components/koa-node-agent/src/bin').mkdir(parents=True)
            (stage/'components/koa-node-agent/src/application').mkdir(parents=True)
            shutil.copy2(ROOT/'components/koa-node-agent/src/bin/koa-node-agent.rs',stage/'components/koa-node-agent/src/bin/koa-node-agent.rs')
            shutil.copy2(ROOT/'components/koa-node-agent/src/application/validate_request.rs',stage/'components/koa-node-agent/src/application/validate_request.rs')
        for relative in PACK['files']:
            destination=stage/relative
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(HERE/'payload'/relative,destination)
        test_sources(stage)


def install():
    states=inspect()
    if all(x=='ALREADY_UPDATED' for x in states.values()):
        print('[OK] Already installed, idempotent');test_in_copy();return
    print('[PRECHECK] Testing patched payload on isolated copy ...')
    test_in_copy()
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=BACKUPS/stamp
    backup.mkdir(parents=True,exist_ok=False)
    records={}
    try:
        for relative in PACK['files']:
            p=target_path(relative)
            previous=p.read_bytes() if p.exists() else None
            records[relative]={'old_sha256':digest(previous) if previous is not None else None,'new_sha256':PACK['files'][relative]['sha256']}
            if previous is not None:
                bp=backup/'files'/relative;bp.parent.mkdir(parents=True,exist_ok=True);bp.write_bytes(previous)
        (backup/'record.json').write_text(json.dumps({'files':records},indent=2),encoding='utf-8')
        for relative in PACK['files']:
            dst=target_path(relative);src=HERE/'payload'/relative
            dst.parent.mkdir(parents=True,exist_ok=True)
            if digest(src.read_bytes())!=PACK['files'][relative]['sha256']: raise RuntimeError('payload checksum mismatch')
            tmp=dst.with_name(dst.name+'.tandem-tour8-tmp')
            tmp.write_bytes(src.read_bytes());os.replace(tmp,dst)
        if not all(v=='ALREADY_UPDATED' for v in inspect().values()): raise RuntimeError('post-install SHA-256 mismatch')
        test_in_copy()
        (backup/'APPLIED').write_text('installed',encoding='ascii')
    except Exception:
        print('[FAIL] Installation did not validate, restoring backup ...')
        for relative,item in records.items():
            dst=target_path(relative);bp=backup/'files'/relative
            if item['old_sha256'] is None: dst.unlink(missing_ok=True)
            elif bp.is_file():
                tmp=dst.with_name(dst.name+'.tandem-tour8-restore');tmp.write_bytes(bp.read_bytes());os.replace(tmp,dst)
        raise
    print('[OK] Installed; backup =',backup)


def rollback():
    if not BACKUPS.is_dir(): raise RuntimeError('no backups found')
    backups=sorted((p for p in BACKUPS.iterdir() if (p/'APPLIED').is_file()),reverse=True)
    if not backups: raise RuntimeError('no completed backup to restore')
    backup=backups[0]
    records=json.loads((backup/'record.json').read_text(encoding='utf-8'))['files']
    for rel,entry in records.items():
        dest=target_path(rel)
        current=digest(dest.read_bytes()) if dest.is_file() else None
        if current!=entry['new_sha256']: raise RuntimeError('rollback conflict; modified since install: '+rel)
    for rel,entry in records.items():
        dest=target_path(rel)
        if entry['old_sha256'] is None: dest.unlink(missing_ok=True)
        else:
            bp=backup/'files'/rel
            if digest(bp.read_bytes())!=entry['old_sha256']: raise RuntimeError('corrupt backup')
            tmp=dest.with_name(dest.name+'.tandem-tour8-restore');tmp.write_bytes(bp.read_bytes());os.replace(tmp,dest)
    (backup/'APPLIED').rename(backup/'ROLLED_BACK')
    print('[OK] Rolled back from',backup)


def main():
    if len(sys.argv)!=2 or sys.argv[1] not in ('preview','install','verify','rollback'):
        print('usage: installer.py preview|install|verify|rollback');return 2
    cmd=sys.argv[1]
    print('=== TANDEM TOUR 8 / '+PACK['label']+' / '+cmd.upper()+' ===')
    print('[SCOPE] Local source only: NO VPS, Docker, DB, service or production activation.')
    try:
        if cmd=='preview': inspect()
        elif cmd=='install': install()
        elif cmd=='verify':
            states=inspect()
            if not all(s=='ALREADY_UPDATED' for s in states.values()): raise RuntimeError('incomplete update')
            test_in_copy()
            print('[OK] Local sources and targeted tests pass')
        else: rollback()
        return 0
    except Exception as e:
        print('[FAIL]',str(e));return 1

if __name__=='__main__': sys.exit(main())

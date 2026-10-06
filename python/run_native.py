"""Serialize and bound a complete mesh -> Palace -> postprocess pipeline."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


def run(directory):
    runtime = json.loads((directory / 'runtime.json').read_text())
    root = Path(__file__).resolve().parent
    lock, token = Path(runtime['lock_directory']), str(uuid.uuid4())
    started = time.monotonic()
    receipt = {'native_status':'never_run','solver_status':'never_run','issues':[], 'commands':[], 'mpi_ranks':1,'threads':1,
               'started_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'peak_aggregate_rss_bytes':0,'budget':{k:runtime[k] for k in ['seconds','memory_bytes','disk_bytes']}}
    receipt['adapter_source_sha256'] = {name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                                       for name in ['run_native.py','mesh.py','cad_identity.py','results.py']}
    acquired, process = False, None
    def stop(_signal, _frame):
        raise InterruptedError('Native pipeline interrupted')
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGTERM,signal.SIGINT})
        try:
            lock.mkdir()
            acquired = True
            (lock/'owner.json').write_text(json.dumps({'owner':'circuit-json-crosstalk-simulation','token':token,'pid':os.getpid(),'job':directory.parent.name})+'\n')
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK,previous_mask)
        receipt['palace_binary_sha256'] = hashlib.sha256(Path(runtime['palace']).read_bytes()).hexdigest()
        for stage, command in [('mesh',[runtime['python'],str(root/'mesh.py'),str(directory)]),
                               ('palace',[runtime['palace'],'palace.json']),
                               ('results',[runtime['python'],str(root/'results.py'),str(directory)])]:
            if stage == 'palace':
                receipt['native_status'] = 'failed'
                receipt['solver_status'] = 'failed'
            command_receipt = {'stage':stage,'argv':command}
            receipt['commands'].append(command_receipt)
            with (directory/(stage+'.log')).open('w') as output:
                process = subprocess.Popen(command,cwd=directory,stdout=output,stderr=subprocess.STDOUT,start_new_session=True,
                    env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1'})
                last_disk = -10
                while process.poll() is None:
                    elapsed = time.monotonic()-started
                    stats = subprocess.run(['ps','-axo','pgid=,rss='],capture_output=True,text=True,check=True)
                    rss = sum(int(row.split()[1])*1024 for row in stats.stdout.splitlines()
                              if len(row.split()) == 2 and int(row.split()[0]) == process.pid)
                    receipt['peak_aggregate_rss_bytes'] = max(receipt['peak_aggregate_rss_bytes'],rss)
                    if elapsed > runtime['seconds']: raise TimeoutError('Pipeline wall budget exceeded')
                    if rss > runtime['memory_bytes']: raise MemoryError('Pipeline aggregate RSS budget exceeded')
                    if elapsed-last_disk > 5:
                        disk = sum(p.stat().st_size for p in directory.rglob('*') if p.is_file() and not p.is_symlink())
                        if disk > runtime['disk_bytes']: raise OSError('Pipeline output disk budget exceeded')
                        receipt['working_disk_bytes'] = disk
                        last_disk = elapsed
                    time.sleep(0.25)
                command_receipt['exit_code'] = process.wait()
                process = None
                if command_receipt['exit_code'] != 0: raise RuntimeError(f'{stage} exited {command_receipt["exit_code"]}; see {stage}.log')
                if stage == 'palace': receipt['solver_status'] = 'passed'
        summary = json.loads((directory/'summary.json').read_text())
        if summary['checks_status'] != 'passed': raise RuntimeError('Native matrix checks failed; see summary.json')
        receipt['working_disk_bytes'] = sum(p.stat().st_size for p in directory.rglob('*') if p.is_file() and not p.is_symlink())
        if receipt['working_disk_bytes'] > runtime['disk_bytes']: raise OSError('Pipeline output disk budget exceeded at completion')
        receipt['native_status'] = 'passed'
    except Exception as error:
        receipt['issues'].append(str(error))
    finally:
        if process is not None and process.poll() is None:
            try: os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait()
        receipt['elapsed_seconds'] = time.monotonic()-started
        receipt['finished_at_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            (directory/'native-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        finally:
            if acquired:
                try:
                    if json.loads((lock/'owner.json').read_text()).get('token') == token:
                        (lock/'owner.json').unlink()
                        lock.rmdir()
                except FileNotFoundError: pass
    return 0 if receipt['native_status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(run(Path(sys.argv[1]).resolve()))

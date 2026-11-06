"""Explicit read-only source bootstrap for offline tests; never runs the image.

Uses the already-authorized gcloud account in memory. Writes only ignored
scratch/e45_image_runtime. No deployments, orders, secrets, or Sheet writes.
"""
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'scratch/e45_image_runtime'
IMAGE='sha256:f864415fec2d2b2ef7f9e6a4d2b88834e99770ac547bd3db447d9cc6fff420a8'
REGISTRY='https://asia-northeast3-docker.pkg.dev/v2/stock-monitoring-prod/stockbot-repo/youtube-signal-service/'


def main():
    gcloud=shutil.which('gcloud.cmd') or shutil.which('gcloud')
    if not gcloud:
        raise SystemExit('gcloud is required for an explicit read-only source download')
    token=subprocess.check_output([gcloud,'auth','print-access-token'],text=True).strip()
    def get(path):
        req=urllib.request.Request(REGISTRY+path,headers={'Authorization':'Bearer '+token,
            'Accept':'application/vnd.docker.distribution.manifest.v2+json'})
        with urllib.request.urlopen(req,timeout=60) as response:
            return response.read()
    data=get('manifests/'+IMAGE)
    if 'sha256:'+hashlib.sha256(data).hexdigest()!=IMAGE:
        raise ValueError('MANIFEST_DIGEST_MISMATCH')
    manifest=json.loads(data)
    seen=set();files=[]
    for layer in reversed(manifest['layers']):
        if layer['size']>12000000:
            continue  # OS / dependency binaries are never downloaded or executed.
        payload=get('blobs/'+layer['digest'])
        if 'sha256:'+hashlib.sha256(payload).hexdigest()!=layer['digest']:
            raise ValueError('LAYER_DIGEST_MISMATCH')
        with tarfile.open(fileobj=io.BytesIO(payload),mode='r:*') as archive:
            for member in archive.getmembers():
                name=member.name.lstrip('./');parts=PurePosixPath(name).parts
                if not member.isfile() or not name.endswith('.py') or '..' in parts or name in seen:
                    continue
                if not any(name.startswith('app/'+folder+'/') for folder in ['src','services','config']):
                    continue
                seen.add(name)
                target=OUT/PurePosixPath(name).relative_to('app')
                target.resolve().relative_to(OUT.resolve())
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.extractfile(member).read());files.append(name)
    OUT.joinpath('provenance.json').write_text(json.dumps({'image':IMAGE,'files':files},indent=2),encoding='utf-8')
    print('Read-only production Python files:',len(files))


if __name__=='__main__':
    main()

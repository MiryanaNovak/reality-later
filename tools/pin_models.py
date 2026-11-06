"""Record the exact version and files of vector search's models (rpapp/embedding_models.json).

    python tools/pin_models.py              every model: their latest version on Hugging Face
    python tools/pin_models.py KEY...       those models only (keys as in the file: all-minilm-l6-v2...)
    python tools/pin_models.py --install    and install them for the app too (no second download)

For each model, it asks Hugging Face for the latest version (commit) of its repository, downloads its
files at that version, computes their size and SHA-256, checks that the model loads and makes vectors,
then writes all that into embedding_models.json. The app then downloads exactly those files, from any
source, and refuses any other (see rpapp/embeddings.py). Run it again to move to newer versions, and
commit the file.

--endpoint URL: another server with Hugging Face's layout (a mirror; the tests' stand-in).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rpapp.embeddings import CATALOG, INSTALLED, Embedder, EmbeddingModel, model_folder  # noqa: E402

SAMPLE = 'The old ferryman owes her a favour since she set his broken wrist.'


async def latest_commit(http: httpx.AsyncClient, endpoint: str, repo: str) -> str:
    for url in (f'{endpoint}/api/models/{repo}/revision/main', f'{endpoint}/api/models/{repo}'):
        answer = await http.get(url)
        if answer.status_code == 200 and answer.json().get('sha'):
            return answer.json()['sha']
    raise SystemExit(f'{repo}: no version found at {endpoint} (answer {answer.status_code})')


async def fetch(http: httpx.AsyncClient, url: str, target: Path) -> tuple[int, str]:
    """Download to `target`: its size and SHA-256."""
    target.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    async with http.stream('GET', url) as answer:
        if answer.status_code != 200:
            raise SystemExit(f'{url}: the server answered {answer.status_code}')
        with open(target, 'wb') as out:
            async for chunk in answer.aiter_bytes(1 << 16):
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
    return size, digest.hexdigest()


async def pin(model: dict, endpoint: str, folder: Path) -> dict:
    """The model's entry, with its version and files recorded (the files are left in `folder`)."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=300), follow_redirects=True) as http:
        commit = await latest_commit(http, endpoint, model['repo'])
        files = []
        for f in model['files']:
            url = f'{endpoint}/{model["repo"]}/resolve/{commit}/{f["path"]}'
            size, sha256 = await fetch(http, url, folder / f['path'])
            files.append({'path': f['path'], 'size': size, 'sha256': sha256})
            print(f'    {f["path"]}: {size / 1e6:.1f} MB, {sha256}')
    pinned = {**model, 'revision': commit, 'files': files}
    vectors = Embedder(folder, EmbeddingModel.model_validate(pinned)).embed([SAMPLE])  # it loads, and works
    if vectors.shape[0] != 1 or vectors.shape[1] < 64:
        raise SystemExit(f'{model["key"]}: the model doesn\'t make vectors as expected ({vectors.shape})')
    print(f'    version {commit}: loads, and makes vectors of {vectors.shape[1]} numbers')
    return pinned


def write(catalog: dict, path: Path) -> None:
    """As the file is laid out: a line per file of a model."""
    text = json.dumps(catalog, indent=2, ensure_ascii=False)
    for model in catalog['models']:
        for f in model['files']:
            expanded = json.dumps(f, indent=2, ensure_ascii=False).replace('\n', '\n        ')
            text = text.replace(expanded, json.dumps(f, ensure_ascii=False))
    path.write_text(text + '\n', encoding='utf-8')


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('keys', nargs='*', help='the models to pin (default: all)')
    parser.add_argument('--endpoint', default='https://huggingface.co')
    parser.add_argument('--install', action='store_true', help='install them for the app as well')
    parser.add_argument('--catalog', type=Path, default=CATALOG, help=argparse.SUPPRESS)
    parser.add_argument('--models-dir', type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding='utf-8'))
    known = [m['key'] for m in catalog['models']]
    if unknown := [k for k in args.keys if k not in known]:
        raise SystemExit(f'Unknown model {unknown[0]}: they are {", ".join(known)}')
    if args.install:
        from rpapp.paths import places
        models_dir = args.models_dir or places().models
    for i, model in enumerate(catalog['models']):
        if args.keys and model['key'] not in args.keys:
            continue
        print(f'{model["name"]} ({model["repo"]})')
        with tempfile.TemporaryDirectory() as tmp:
            pinned = catalog['models'][i] = await pin(model, args.endpoint.rstrip('/'), Path(tmp))
            if args.install:
                target = model_folder(models_dir, EmbeddingModel.model_validate(pinned))
                shutil.rmtree(target, ignore_errors=True)
                shutil.copytree(tmp, target)
                (target / INSTALLED).write_text(json.dumps({f['path']: f['sha256'] for f in pinned['files']},
                                                           indent=2), encoding='utf-8')
                print(f'    installed in {target}')
    write(catalog, args.catalog)
    print(f'Written: {args.catalog}')


if __name__ == '__main__':
    asyncio.run(main())

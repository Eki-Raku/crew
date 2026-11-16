#!/usr/bin/env python3
"""Build the native Codex CLI compatibility bundle from tracked source files.

The portable plugin.json remains the primary App package. Codex CLI versions
that skip AgentPlugin hooks need the documented legacy manifest discovery path.
"""
import argparse
import json
import os
import tempfile
from pathlib import Path
import subprocess
import zipfile


def build(root, output):
    root = Path(root).resolve(); output = Path(output).absolute()
    if output.exists(): raise ValueError('Output already exists; choose a new path')
    portable = json.loads((root / 'plugin.json').read_text())
    extension = portable['extensions']['com.openai']
    manifest = {k: portable[k] for k in ('name', 'version', 'description')}
    manifest.update(hooks=extension['hooks'], skills='./skills')
    names = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z'], text=True).split('\0')
    members = []
    for name in filter(None, names):
        path = root / name
        if path.is_symlink() or '..' in Path(name).parts or not path.resolve().is_relative_to(root): raise ValueError('Symlinks and traversal paths cannot be bundled')
        if not path.is_file(): raise ValueError('Tracked bundle source is not a regular file: ' + name)
        if name.startswith('.git/') or '__pycache__' in Path(name).parts: continue
        if name == '.codex-plugin/plugin.json': continue
        members.append(('plugin.portable.json' if name == 'plugin.json' else name, path))
    # Build privately, then publish atomically without overwriting a concurrent output.
    # Keeping the temporary file in the output directory makes the link same-filesystem.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.crew-bundle-', delete=False) as stream:
            temporary = Path(stream.name)
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, path in members: archive.write(path, 'crew/' + name)
                archive.writestr('crew/.codex-plugin/plugin.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)  # fails if output exists, including a dangling symlink
        return output
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    print(build(Path(__file__).resolve().parents[1], args.output))


if __name__ == '__main__': main()

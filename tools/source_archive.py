"""Build source from an explicit file list, without AppData, Git history or media."""
import argparse
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def build(destination):
    names=json.loads((ROOT/'publish-files.json').read_text(encoding='utf-8'))
    if not isinstance(names,list) or len(names)!=len({name.casefold() for name in names}):
        raise ValueError('Invalid or duplicate public file list')
    files=[]
    for name in names:
        part=PurePosixPath(name)
        if part.is_absolute() or '..' in part.parts or ':' in name or '\\' in name:
            raise ValueError('Invalid relative publication path')
        source=ROOT/part
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT) or not source.is_file():
            raise ValueError('Public file missing or linked outside repository: '+name)
        files.append((name,source))
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,source in files:
            entry=zipfile.ZipInfo('Crazy/'+name)
            entry.create_system=3
            entry.external_attr=0o100644<<16
            entry.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(entry,source.read_bytes())
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:raise RuntimeError('Source archive CRC failed')
    print('SOURCE ARCHIVE:',len(files),'explicit entries; code/media release gates must also pass.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'build'/'Crazy-source.zip')
    build(parser.parse_args().output.resolve())

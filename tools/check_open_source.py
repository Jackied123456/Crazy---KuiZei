"""Inspect a proposed source ZIP without importing app code or reading AppData.

This is a release gate, not a general guarantee that arbitrary code is safe.
Only relative filenames and issue categories are written to the report.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

PRIVATE_FILES = {'settings.json', 'statistics.json', 'owner-template.bin',
                 'owner-template.tmp', 'active-media.json', 'active-media.tmp', '.env', '.env.local'}
PRIVATE_DIRS = {'recordings', 'media-packs', '__pycache__', '.git', '.venv', 'node_modules'}
PRIVATE_EXTENSIONS = {'.avi', '.mp4', '.sqlite', '.sqlite3', '.db', '.pkl', '.pickle', '.npz', '.npy', '.log', '.pyc'}
MEDIA_EXTENSIONS = {'.gif', '.jpg', '.jpeg', '.png', '.webp', '.mov'}
PROFILE_PATH = re.compile(r'(?:[a-z]:[\\/]' + 'Users' + r'[\\/][^\\/\s]+|' + '/' + 'Users' + r'/[^/\s]+)', re.I)
TOKEN = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
TEXT = {'.py', '.md', '.txt', '.json', '.yaml', '.yml', '.toml'}


def inspect(path):
    problems=[];media=[];licenses=[];entries=[]
    def issue(name, reason):
        problems.append({'file':name,'reason':reason})
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist()
        if sum(info.file_size for info in infos)>512*1024*1024:
            raise ValueError('Source archive exceeds the release inspection limit')
        if len(infos)>2000:
            raise ValueError('Too many source archive entries')
        for info in infos:
            name=info.filename.replace('\\','/')
            item=PurePosixPath(name)
            entries.append(name)
            if item.is_absolute() or '..' in item.parts or ':' in name:
                issue(name,'unsafe_archive_path')
            if info.comment or info.extra:
                issue(name,'unreviewed_zip_metadata')
            if item.name.casefold() in PRIVATE_FILES or any(part.casefold() in PRIVATE_DIRS for part in item.parts):
                issue(name,'runtime_or_private_data')
            if item.suffix.casefold() in PRIVATE_EXTENSIONS:
                issue(name,'runtime_or_private_data')
            if item.suffix.casefold() in MEDIA_EXTENSIONS:
                media.append(name)
            if item.name.upper() in {'LICENSE','LICENSE.MD','LICENSE.TXT','COPYING'} and len(item.parts)<=2:
                licenses.append(name)
            if item.suffix.casefold() in TEXT:
                if info.file_size>4*1024*1024:
                    issue(name,'oversized_text_input');continue
                data=archive.read(info).decode('utf-8-sig')
                if PROFILE_PATH.search(data):issue(name,'absolute_user_profile_path')
                if TOKEN.search(data):issue(name,'credential_pattern')
        if len(set(entries))!=len(entries):issue('(archive)','duplicate_entry')
        corrupt=archive.testzip()
        if corrupt:issue(corrupt,'crc_error')
    if not licenses:issue('(repository)','code_license_missing')
    # No media is silently declared licensed merely because a source URL exists.
    if media:issue('(media)','explicit_media_license_review_required')
    return {'artifact':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'entry_count':len(entries),'code_license_files':licenses,'media_files':media,
            'issues':problems,'ready_for_publication':not problems,
            'scope':'Archive paths, runtime files, common credential patterns, personal paths, license/media release gates. No app import, camera, network or real profile access.',
            'limitations':'Passing this gate does not replace code review, dependency review or confirmation of rights to all included material.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    result=inspect(args.archive)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('READY' if result['ready_for_publication'] else 'NOT READY',result['entry_count'],'entries;',len(result['issues']),'release issues')
    return 0 if result['ready_for_publication'] else 2


if __name__=='__main__':
    raise SystemExit(main())

"""Release gates must reject seeded data without displaying its values."""
from pathlib import Path
import shutil
import unittest
import uuid
import zipfile

from check_open_source import inspect


class PublicationGateTests(unittest.TestCase):
    def check(self, entries, metadata=False):
        directory=Path(__file__).parent/('gate-test-'+uuid.uuid4().hex)
        directory.mkdir()
        try:
            path=directory/'candidate.zip'
            with zipfile.ZipFile(path,'w') as archive:
                for name,data in entries.items():
                    entry=zipfile.ZipInfo(name)
                    if metadata:entry.comment=b'unreviewed metadata'
                    archive.writestr(entry,data)
            return inspect(path)
        finally:
            shutil.rmtree(directory)

    def test_clean_archive_has_no_detected_gate_issue(self):
        result=self.check({'Crazy/LICENSE':'MIT License\n','Crazy/app.py':'print("hello")\n'})
        self.assertTrue(result['ready_for_publication'])
        self.assertEqual(result['issues'],[])

    def test_license_and_third_party_media_are_independent_gates(self):
        result=self.check({'Crazy/assets/actor.gif':b'not decoded by the archive gate'})
        reasons={item['reason'] for item in result['issues']}
        self.assertIn('code_license_missing',reasons)
        self.assertIn('explicit_media_license_review_required',reasons)

    def test_runtime_files_traversal_private_paths_and_credentials_are_rejected(self):
        artificial_path='C:'+chr(92)+'Users'+chr(92)+'sample-account'+chr(92)+'file'
        artificial_token='gh'+'p_'+'x'*36
        result=self.check({'Crazy/LICENSE':'MIT License',
            'Crazy/owner-template.bin':b'synthetic',
            '../escape.py':'pass',
            'Crazy/.env':'synthetic=true',
            'Crazy/recordings/demo.avi':b'synthetic',
            'Crazy/app.py':artificial_path+'\n'+artificial_token})
        reasons={item['reason'] for item in result['issues']}
        self.assertFalse(result['ready_for_publication'])
        self.assertTrue({'unsafe_archive_path','runtime_or_private_data','absolute_user_profile_path','credential_pattern'}<=reasons)
        self.assertNotIn(artificial_token,str(result))
        self.assertNotIn(artificial_path,str(result))

    def test_unreviewed_zip_metadata_is_reported(self):
        result=self.check({'Crazy/LICENSE':'MIT License'},metadata=True)
        self.assertIn('unreviewed_zip_metadata',{item['reason'] for item in result['issues']})

    def test_scanner_pattern_is_not_mistaken_for_an_actual_user_path(self):
        source=Path(__file__).with_name('check_open_source.py').read_text(encoding='utf-8')
        result=self.check({'Crazy/LICENSE':'MIT License','Crazy/tools/check_open_source.py':source})
        self.assertEqual(result['issues'],[])


if __name__=='__main__':
    unittest.main()

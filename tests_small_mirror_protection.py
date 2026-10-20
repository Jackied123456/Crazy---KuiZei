"""Cover lifecycle regressions using synthetic signals; no camera or profile."""
import unittest
from small_mirror_protection import ProtectionPolicy


class CoverTimingTests(unittest.TestCase):
    def feed(self,p,start,signal,active=False,automatic=True):
        result=None
        for i in range(6):
            action=p.update(start+i*.4,round((start+i*.4)*100)+1,signal,active,automatic)
            result=action or result
        return result

    def test_sustained_look_covers_then_away_uncover_and_return_reacquires(self):
        p=ProtectionPolicy()
        self.assertEqual(self.feed(p,0,True),'cover')
        self.assertIsNone(p.update(2.1,211,False,True,True))
        self.assertEqual(self.feed(p,2.2,False,True),'uncover')
        self.assertEqual(self.feed(p,4.5,True),'cover')

    def test_manual_release_snoozes_and_same_visitor_reacquires_without_leaving(self):
        p=ProtectionPolicy();self.feed(p,0,True);p.release(2)
        self.assertEqual(p.remaining(2),30)
        for t in (3,15,31.99):self.assertIsNone(p.update(t,int(t*100),True,False,True))
        self.assertIsNone(p.update(32,3200,True,False,True))
        self.assertIsNone(p.update(32.5,3250,True,False,True))
        self.assertEqual(p.update(33.01,3301,True,False,True),'cover')

    def test_duplicate_or_out_of_order_frames_never_uncover(self):
        p=ProtectionPolicy()
        for t in (0,.5,1,2,5):self.assertIsNone(p.update(t,1,False,True,True))
        self.assertIsNone(p.update(6,0,False,True,True))

    def test_fault_ambiguity_and_stalls_break_absence_evidence(self):
        for signal in (None,True):
            p=ProtectionPolicy()
            for i,t in enumerate((0,.8,1.6)):self.assertIsNone(p.update(t,i,False,True,True))
            self.assertIsNone(p.update(1.8,3,signal,True,True))
            self.assertIsNone(p.update(2.1,4,False,True,True))
            self.assertIsNone(p.update(4.5,5,False,True,True))

    def test_manual_cover_persists_until_explicit_restore(self):
        p=ProtectionPolicy();self.assertIsNone(self.feed(p,0,False,True,False))

    def test_resume_and_duration_changes_require_new_acquisition(self):
        p=ProtectionPolicy();p.release(0,300);p.release(10,0)
        self.assertEqual(p.remaining(10),0)
        self.assertIsNone(p.update(10,1,True,False,True))
        self.assertIsNone(p.update(10.5,2,True,False,True))
        self.assertEqual(p.update(11.1,3,True,False,True),'cover')

    def test_config_reset_keeps_manual_snooze(self):
        p=ProtectionPolicy();p.release(0,60);p.reset();self.assertEqual(p.remaining(10),50)


if __name__=='__main__':unittest.main()

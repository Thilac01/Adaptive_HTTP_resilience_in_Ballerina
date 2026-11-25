import unittest
from aegis_v6 import Context, decide, STATES
P={s:(.8 if s=='flaky' else .05) for s in STATES}
class PolicyTests(unittest.TestCase):
    def test_retry(self):
        d=decide(Context(True,2000,0,2,'http_error',503),P,'flaky'); self.assertEqual(d.action,'retry'); self.assertLessEqual(d.backoff_ms+d.attempt_ms+50,2000)
    def test_unsafe(self): self.assertEqual(decide(Context(False,5000,0,2,'transport_error'),P,'flaky').action,'stop')
    def test_budget(self): self.assertEqual(decide(Context(True,5000,2,2,'transport_error'),P,'flaky').action,'stop')
    def test_deadline(self): self.assertEqual(decide(Context(True,250,0,2,'transport_error'),P,'flaky').action,'stop')
    def test_status(self): self.assertEqual(decide(Context(True,5000,0,2,'http_error',400),P,'flaky').action,'stop')
    def test_disagreement(self): self.assertEqual(decide(Context(True,5000,0,2,'transport_error'),P,'slow').state,'uncertain')
    def test_no_history(self): self.assertEqual(decide(Context(True,5000,1,2,'transport_error')).action,'stop')
    def test_retry_after(self): self.assertEqual(decide(Context(True,1000,0,2,'http_error',429,2000),P,'flaky').action,'stop')
    def test_success(self): self.assertEqual(decide(Context(False,0,0,0,'success')).action,'complete')
    def test_low_confidence(self): self.assertEqual(decide(Context(True,5000,0,2,'transport_error'),dict.fromkeys(STATES,.2),'flaky').state,'uncertain')
    def test_grid(self):
        for safe in (True,False):
            for remaining in range(0,3001,25):
                for used in range(4):
                    for state in STATES:
                        p={s:float(s==state) for s in STATES}; d=decide(Context(safe,remaining,used,2,'transport_error'),p,state)
                        if d.action=='retry':
                            self.assertTrue(safe); self.assertLess(used,2); self.assertLessEqual(d.backoff_ms+d.attempt_ms+50,remaining)
if __name__=='__main__': unittest.main()

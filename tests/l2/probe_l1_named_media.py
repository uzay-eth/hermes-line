"""Replay the existing L1 media/bridge test with round 6's direct-human expectation.

Only the required presence of a live-person question is inverted to absence.
All conversation/tool/bridge/cleanup assertions remain the original L1 test.
The source in tests/l1 is read, never edited. Existing fake providers/loopback only.
"""
from pathlib import Path
import inspect
import sys
import textwrap
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'l1'))
import test_r5_voice as original

method='test_R5_fake_call_over_media_streams_through_the_real_ingress'
source=textwrap.dedent(inspect.getsource(getattr(original.EndToEndMediaStreamsTests,method)))
old='self.assertIn(line.cfg.confirm_question(), said)'
assert source.count(old)==1
source=source.replace(old,'self.assertNotIn(line.cfg.confirm_question(), said)')
namespace={}
exec(compile(source,__file__,'exec'),original.__dict__,namespace)
Adapted=type('DirectNamedAgentMediaProbe',(original.EndToEndMediaStreamsTests,),{method:namespace[method]})

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([Adapted(method)]))
    raise SystemExit(not result.wasSuccessful())

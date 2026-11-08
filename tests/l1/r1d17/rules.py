import sys,unittest
sys.path[:0]=['.','tests/l1']
from test_r19_small_gate import SmallGateRuleTests,CaseMatrixTests
names=[n for n in unittest.defaultTestLoader.getTestCaseNames(SmallGateRuleTests) if n not in ('test_R19_one_key_per_menu','test_R19_an_unrestricted_task_is_not_gated')]
s=unittest.TestSuite(SmallGateRuleTests(n) for n in names)
s.addTest(CaseMatrixTests('test_R19_matrix_differs_from_the_reviewer_only_where_decided'))
r=unittest.TextTestRunner(verbosity=2).run(s);sys.exit(not r.wasSuccessful())

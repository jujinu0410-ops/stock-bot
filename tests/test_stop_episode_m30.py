"""Verify STOP_BREACH uses an acknowledged holding-episode gate.

No network, API keys or market orders. This exercises the actual conditional
AST in services/unified_scan_m30.py, not a duplicated reimplementation.
"""
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"services"/"unified_scan_m30.py"


class FirstStopOnceGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse(SOURCE.read_text(encoding="utf-8"))
        assigns=[n for n in ast.walk(tree) if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id=="stop_notified" for t in n.targets)]
        if len(assigns)!=1:
            raise AssertionError("expected exactly one stop_notified assignment")
        cls.flag_code=compile(ast.Expression(assigns[0].value),str(SOURCE),"eval")
        matches=[n for n in ast.walk(tree) if isinstance(n,ast.If)
                 and "not stop_notified" in ast.unparse(n.test)
                 and any("STOP_BREACH" in ast.unparse(child)
                         for statement in n.body for child in ast.walk(statement))]
        if len(matches)!=1:
            raise AssertionError("expected one gated STOP_BREACH conditional")
        cls.stop_code=compile(ast.Expression(matches[0].test),str(SOURCE),"eval")

    def emitted(self, sources, payload, price=180000, stop=189900):
        acknowledged=eval(self.flag_code,{"sources":sources,"payload":payload})
        return bool(eval(self.stop_code,{"price":price,"stop":stop,
                                         "stop_notified":acknowledged}))

    def test_first_holding_breach_warns(self):
        self.assertTrue(self.emitted(["HELD"],{},price=180000))

    def test_acknowledged_holding_breach_suppressed(self):
        self.assertFalse(self.emitted(["HELD"],{"stop_notified":True},price=180000))

    def test_strict_boolean_and_held_required(self):
        self.assertTrue(self.emitted(["HELD"],{"stop_notified":"true"}))
        self.assertTrue(self.emitted(["YOUTUBE"],{"stop_notified":True}))

    def test_no_stop_crossing_no_event(self):
        self.assertFalse(self.emitted(["HELD"],{},price=190000))

    def test_missing_stop_does_not_emit(self):
        self.assertFalse(self.emitted(["HELD"],{},stop=None))


if __name__=="__main__":
    unittest.main()

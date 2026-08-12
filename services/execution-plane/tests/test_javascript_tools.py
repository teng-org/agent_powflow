from __future__ import annotations
import sys, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from runtime.javascript_tools import JavaScriptToolError, execute_javascript_tool
from runtime.node_adapters import JavaScriptToolAdapter, NodeExecutionContext

class JavaScriptToolsTest(unittest.TestCase):
 def definition(self): return {"code":"return {total: input.values.reduce((a,b)=>a+b,0)};","input_schema":{"type":"object","properties":{"values":{"type":"array","items":{"type":"number"}}},"required":["values"]},"output_schema":{"type":"object","properties":{"total":{"type":"number"}},"required":["total"]},"allow_workflow":True,"timeout_ms":100,"memory_mb":8}
 def test_schema_bounded_execution(self): self.assertEqual({"total":6},execute_javascript_tool(self.definition(),{"values":[1,2,3]})["output"])
 def test_invalid_input_and_infinite_loop_fail(self):
  with self.assertRaises(JavaScriptToolError): execute_javascript_tool(self.definition(),{})
  with self.assertRaises(JavaScriptToolError): execute_javascript_tool({"code":"while(true){}","timeout_ms":10},{})
 def test_workflow_adapter_uses_published_snapshot(self):
  result=JavaScriptToolAdapter().execute({"id":"js-1","type":"tool.javascript","config":{"tool_id":"t1","version":2,"definition":self.definition(),"arguments":{"values":[2,4]},"output_field":"calculated"}},NodeExecutionContext(payload={},state={},provider_mode="mock"))
  self.assertEqual({"total":6},result.state_updates["calculated"]); self.assertTrue(result.tool_called)
 def test_workflow_scope_is_enforced(self):
  definition=self.definition();definition["allow_workflow"]=False
  with self.assertRaises(RuntimeError): JavaScriptToolAdapter().execute({"id":"js-1","type":"tool.javascript","config":{"tool_id":"t1","version":1,"definition":definition}},NodeExecutionContext(payload={},state={},provider_mode="mock"))

if __name__=='__main__': unittest.main()

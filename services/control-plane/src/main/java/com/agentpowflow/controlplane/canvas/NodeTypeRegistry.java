package com.agentpowflow.controlplane.canvas;

import org.springframework.stereotype.Component;
import java.util.*;

@Component
public class NodeTypeRegistry {
    private final Map<String, NodeTypeDefinition> definitions = new LinkedHashMap<>();
    public NodeTypeRegistry(){
        add("start","基础","开始","◆","流程输入",Map.of(),Map.of("text","string","intent","string"));
        add("condition.if","逻辑","条件判断","◇","按条件选择分支",Map.of("text","string"),Map.of("text","string","intent","string"));
        add("parallel.fork","逻辑","并行分发","⑂","同时执行多个分支",Map.of("text","string"),Map.of("text","string","answer","string"));
        add("parallel.join","逻辑","并行汇合","⑃","合并并行结果",Map.of(),Map.of("text","string","answer","string","branches","object"));
        add("llm.generate","AI 能力","大模型生成","✦","调用模型生成内容",Map.of("text","string"),Map.of("answer","string"));
        add("retrieval.search","AI 能力","知识检索","⌕","检索知识库证据",Map.of("text","string"),Map.of("text","string","evidence","array","citations","array"));
        add("skill.call","AI 能力","Skill 调用","S","调用已发布 Skill",Map.of("text","string"),Map.of("text","string","answer","string"));
        add("tool.http","工具","HTTP 请求","↗","调用外部 API",Map.of("text","string"),Map.of("text","string","tool_response","object"));
        add("tool.mcp","工具","MCP 工具","M","调用已接入的 MCP 工具",Map.of("text","string"),Map.of("text","string","tool_response","object"));
        add("human.approval","协作","人工审批","✓","等待人工决策",Map.of(),Map.of("text","string","answer","string","approval","object"));
        add("end","基础","结束","■","返回流程结果",Map.of("answer","string"),Map.of());
    }
    private void add(String type,String category,String label,String icon,String description,Map<String,String> in,Map<String,String> out){definitions.put(type,new NodeTypeDefinition(type,new CanvasModels.Schema(in),new CanvasModels.Schema(out),category,label,icon,description,"1.0"));}
    public Optional<NodeTypeDefinition> find(String nodeType){return Optional.ofNullable(definitions.get(nodeType));}
    public List<NodeTypeDefinition> list(){return List.copyOf(definitions.values());}
    public record NodeTypeDefinition(String type,CanvasModels.Schema inputSchema,CanvasModels.Schema outputSchema,String category,String label,String icon,String description,String manifestVersion){}
}

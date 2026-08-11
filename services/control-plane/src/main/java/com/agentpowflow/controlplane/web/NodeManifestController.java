package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.canvas.NodeTypeRegistry;
import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController @RequestMapping("/api/v1/workflow-node-types")
public class NodeManifestController {
    private final NodeTypeRegistry registry; public NodeManifestController(NodeTypeRegistry r){registry=r;}
    @GetMapping public Response list(){return new Response("ok","1.0",registry.list());}
    public record Response(String status,String catalog_version,List<NodeTypeRegistry.NodeTypeDefinition> items){}
}

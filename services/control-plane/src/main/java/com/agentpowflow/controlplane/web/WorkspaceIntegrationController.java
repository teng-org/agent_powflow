package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.auth.TokenClaims;
import com.agentpowflow.controlplane.integrations.IntegrationService;
import com.fasterxml.jackson.databind.JsonNode;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import java.util.List;

@RestController @RequestMapping("/api/v1")
public class WorkspaceIntegrationController {
    private final IntegrationService service; public WorkspaceIntegrationController(IntegrationService s){service=s;}
    @GetMapping("/skills") public Items<?> skills(Authentication a){var c=claims(a);return new Items<>(service.listSkills(c.tenantId(),c.sub()));}
    @PostMapping("/skills") public Item<?> createSkill(@RequestBody IntegrationService.SkillInput r,Authentication a){var c=claims(a);return new Item<>(service.saveSkill(c.tenantId(),c.sub(),null,r));}
    @PutMapping("/skills/{id}") public Item<?> saveSkill(@PathVariable String id,@RequestBody IntegrationService.SkillInput r,Authentication a){var c=claims(a);return new Item<>(service.saveSkill(c.tenantId(),c.sub(),id,r));}
    @PostMapping("/skills/{id}/publish") public Item<?> publishSkill(@PathVariable String id,Authentication a){var c=claims(a);return new Item<>(service.publishSkill(c.tenantId(),c.sub(),id));}
    @GetMapping("/skills/{id}/versions") public Items<?> versions(@PathVariable String id,Authentication a){var c=claims(a);return new Items<>(service.skillVersions(c.tenantId(),c.sub(),id));}
    @DeleteMapping("/skills/{id}") public Status deleteSkill(@PathVariable String id,Authentication a){var c=claims(a);service.deleteSkill(c.tenantId(),c.sub(),id);return new Status("ok");}
    @GetMapping("/mcp-connections") public Items<?> mcps(Authentication a){var c=claims(a);return new Items<>(service.listMcp(c.tenantId(),c.sub()));}
    @PostMapping("/mcp-connections") public Item<?> createMcp(@RequestBody IntegrationService.McpInput r,Authentication a){var c=claims(a);return new Item<>(service.saveMcp(c.tenantId(),c.sub(),null,r));}
    @PutMapping("/mcp-connections/{id}") public Item<?> saveMcp(@PathVariable String id,@RequestBody IntegrationService.McpInput r,Authentication a){var c=claims(a);return new Item<>(service.saveMcp(c.tenantId(),c.sub(),id,r));}
    @PutMapping("/mcp-connections/{id}/tools") public Item<?> tools(@PathVariable String id,@RequestBody Tools r,Authentication a){var c=claims(a);return new Item<>(service.replaceTools(c.tenantId(),c.sub(),id,r.tools()));}
    @PostMapping("/mcp-connections/{id}/discover") public Item<?> discover(@PathVariable String id,Authentication a){var c=claims(a);return new Item<>(service.discoverTools(c.tenantId(),c.sub(),id));}
    @DeleteMapping("/mcp-connections/{id}") public Status deleteMcp(@PathVariable String id,Authentication a){var c=claims(a);service.deleteMcp(c.tenantId(),c.sub(),id);return new Status("ok");}
    private TokenClaims claims(Authentication a){return (TokenClaims)a.getPrincipal();}
    public record Item<T>(String status,T item){public Item(T i){this("ok",i);}} public record Items<T>(String status,List<T> items){public Items(List<T> i){this("ok",i);}} public record Status(String status){} public record Tools(List<JsonNode> tools){}
}

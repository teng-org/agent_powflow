package com.agentpowflow.controlplane.integrations;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

@Service
public class IntegrationService {
    private final SkillRepository skills; private final SkillVersionRepository versions; private final McpConnectionRepository mcps; private final ObjectMapper json; private final McpDiscoveryClient discovery;
    public IntegrationService(SkillRepository s, SkillVersionRepository v, McpConnectionRepository m, ObjectMapper j,McpDiscoveryClient d){skills=s;versions=v;mcps=m;json=j;discovery=d;}
    @Transactional(readOnly=true) public List<SkillView> listSkills(String tenant,String actor){return skills.findByTenantIdAndCreatedByOrderByUpdatedAtDesc(tenant,actor).stream().map(this::view).toList();}
    @Transactional public SkillView saveSkill(String tenant,String actor,String id,SkillInput in){
        validateSkill(in); SkillEntity e=id==null?new SkillEntity():skill(id,tenant,actor); e.setTenantId(tenant);e.setName(in.name().trim());e.setDescription(safe(in.description()));e.setUpdatedBy(actor);if(id==null)e.setCreatedBy(actor);
        e.setDraftDefinitionJson(write(Map.of("instructions",in.instructions().trim(),"input_schema",schema(in.input_schema()),"output_schema",schema(in.output_schema()),"model",safe(in.model()),"temperature",in.temperature()==null?0.2:in.temperature(),"max_tokens",in.max_tokens()==null?800:in.max_tokens())));return view(skills.save(e));
    }
    @Transactional public SkillView publishSkill(String tenant,String actor,String id){SkillEntity e=skill(id,tenant,actor);int no=e.getLatestVersionNo()+1;SkillVersionEntity v=new SkillVersionEntity();v.setSkillId(id);v.setTenantId(tenant);v.setVersionNo(no);v.setDefinitionJson(e.getDraftDefinitionJson());v.setChecksum(sha(e.getDraftDefinitionJson()));v.setPublishedBy(actor);versions.save(v);e.setLatestVersionNo(no);e.setStatus("PUBLISHED");e.setUpdatedBy(actor);return view(skills.save(e));}
    @Transactional public void deleteSkill(String tenant,String actor,String id){skills.delete(skill(id,tenant,actor));}
    @Transactional(readOnly=true) public List<SkillVersionView> skillVersions(String tenant,String actor,String id){skill(id,tenant,actor);return versions.findBySkillIdAndTenantIdOrderByVersionNoDesc(id,tenant).stream().map(v->new SkillVersionView(v.getVersionNo(),read(v.getDefinitionJson()),v.getChecksum(),v.getPublishedAt())).toList();}
    @Transactional(readOnly=true) public List<McpView> listMcp(String tenant,String actor){return mcps.findByTenantIdAndCreatedByOrderByUpdatedAtDesc(tenant,actor).stream().map(this::view).toList();}
    @Transactional public McpView saveMcp(String tenant,String actor,String id,McpInput in){validateMcp(in);McpConnectionEntity e=id==null?new McpConnectionEntity():mcp(id,tenant,actor);e.setTenantId(tenant);e.setName(in.name().trim());e.setDescription(safe(in.description()));e.setEndpointUrl(in.endpoint_url().trim());e.setTransport("STREAMABLE_HTTP");e.setAuthType(normAuth(in.auth_type()));e.setSecretRef(blank(in.secret_ref())?null:in.secret_ref().trim());e.setUpdatedBy(actor);if(id==null)e.setCreatedBy(actor);return view(mcps.save(e));}
    @Transactional public McpView replaceTools(String tenant,String actor,String id,List<JsonNode> tools){McpConnectionEntity e=mcp(id,tenant,actor);List<JsonNode> clean=tools==null?List.of():tools.stream().limit(500).toList();e.setToolsJson(write(clean));e.setStatus(clean.isEmpty()?"DRAFT":"READY");e.setUpdatedBy(actor);return view(mcps.save(e));}
    @Transactional public McpView discoverTools(String tenant,String actor,String id){McpConnectionEntity e=mcp(id,tenant,actor);if(!"NONE".equals(e.getAuthType()))throw new ResponseStatusException(HttpStatus.CONFLICT,"鉴权连接请先手工导入工具目录，OAuth/密钥代理发现将在后续版本提供");List<JsonNode> found=discovery.discover(e.getEndpointUrl());e.setToolsJson(write(found));e.setStatus("READY");e.setUpdatedBy(actor);return view(mcps.save(e));}
    @Transactional public void deleteMcp(String tenant,String actor,String id){mcps.delete(mcp(id,tenant,actor));}
    @Transactional(readOnly=true) public JsonNode resolveSkill(String tenant,String id,int version){SkillVersionEntity v=versions.findBySkillIdAndTenantIdAndVersionNo(id,tenant,version).orElseThrow(()->notFound("skill version"));return read(v.getDefinitionJson());}
    @Transactional(readOnly=true) public McpView resolveMcp(String tenant,String id){return view(mcps.findByConnectionIdAndTenantId(id,tenant).orElseThrow(()->notFound("MCP connection")));}
    private SkillEntity skill(String id,String t,String a){return skills.findBySkillIdAndTenantIdAndCreatedBy(id,t,a).orElseThrow(()->notFound("skill"));} private McpConnectionEntity mcp(String id,String t,String a){return mcps.findByConnectionIdAndTenantIdAndCreatedBy(id,t,a).orElseThrow(()->notFound("MCP connection"));}
    private SkillView view(SkillEntity e){JsonNode latest=e.getLatestVersionNo()>0?versions.findBySkillIdAndTenantIdAndVersionNo(e.getSkillId(),e.getTenantId(),e.getLatestVersionNo()).map(v->read(v.getDefinitionJson())).orElse(null):null;return new SkillView(e.getSkillId(),e.getName(),e.getDescription(),e.getStatus(),e.getLatestVersionNo(),read(e.getDraftDefinitionJson()),latest,e.getRowVersion(),e.getUpdatedAt());}
    private McpView view(McpConnectionEntity e){return new McpView(e.getConnectionId(),e.getName(),e.getDescription(),e.getTransport(),e.getEndpointUrl(),e.getAuthType(),e.getSecretRef(),e.getStatus(),read(e.getToolsJson()),e.getRowVersion(),e.getUpdatedAt());}
    private void validateSkill(SkillInput in){if(in==null||blank(in.name())||blank(in.instructions()))throw bad("名称和指令不能为空");if(in.name().trim().length()>128||in.instructions().length()>30000)throw bad("Skill 内容超出限制");}
    private void validateMcp(McpInput in){if(in==null||blank(in.name())||blank(in.endpoint_url()))throw bad("名称和端点不能为空");try{var u=java.net.URI.create(in.endpoint_url().trim());if(!List.of("http","https").contains(u.getScheme())||u.getHost()==null)throw new Exception();}catch(Exception e){throw bad("MCP 端点必须是有效的 HTTP(S) 地址");}if(!normAuth(in.auth_type()).equals("NONE")&&blank(in.secret_ref()))throw bad("鉴权连接必须填写密钥引用");}
    private JsonNode schema(JsonNode n){return n==null||n.isNull()?json.createObjectNode().put("type","object"):n;} private String normAuth(String v){String n=safe(v).toUpperCase(Locale.ROOT);return Set.of("NONE","BEARER","API_KEY").contains(n)?n:"NONE";}
    private String write(Object o){try{return json.writeValueAsString(o);}catch(Exception e){throw new IllegalStateException(e);}} private JsonNode read(String s){try{return json.readTree(s);}catch(Exception e){return json.createObjectNode();}}
    private String sha(String v){try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(v.getBytes(StandardCharsets.UTF_8)));}catch(Exception e){throw new IllegalStateException(e);}}
    private static String safe(String v){return v==null?"":v.trim();} private static boolean blank(String v){return v==null||v.isBlank();} private static ResponseStatusException bad(String m){return new ResponseStatusException(HttpStatus.BAD_REQUEST,m);} private static ResponseStatusException notFound(String n){return new ResponseStatusException(HttpStatus.NOT_FOUND,n+" not found");}
    public record SkillInput(String name,String description,String instructions,JsonNode input_schema,JsonNode output_schema,String model,Double temperature,Integer max_tokens){}
    public record SkillView(String skill_id,String name,String description,String status,int latest_version_no,JsonNode draft_definition,JsonNode latest_definition,long revision,java.time.Instant updated_at){}
    public record SkillVersionView(int version_no,JsonNode definition,String checksum,java.time.Instant published_at){}
    public record McpInput(String name,String description,String endpoint_url,String auth_type,String secret_ref){}
    public record McpView(String connection_id,String name,String description,String transport,String endpoint_url,String auth_type,String secret_ref,String status,JsonNode tools,long revision,java.time.Instant updated_at){}
}

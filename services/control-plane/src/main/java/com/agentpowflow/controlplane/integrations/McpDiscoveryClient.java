package com.agentpowflow.controlplane.integrations;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import java.net.URI;
import java.net.InetAddress;
import java.net.http.*;
import java.time.Duration;
import java.util.*;

@Component
public class McpDiscoveryClient {
    private final ObjectMapper json; private final HttpClient http=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(8)).followRedirects(HttpClient.Redirect.NEVER).build();
    public McpDiscoveryClient(ObjectMapper json){this.json=json;}
    public List<JsonNode> discover(String endpoint){
        try{
            URI uri=URI.create(endpoint); validateDestination(uri); Map<String,String> headers=new LinkedHashMap<>();
            JsonNode init=post(uri,Map.of("jsonrpc","2.0","id","init","method","initialize","params",Map.of("protocolVersion","2025-11-25","capabilities",Map.of(),"clientInfo",Map.of("name","Agent Powflow Control","version","1.0"))),headers);
            String session=init.path("_session_id").asText(""); if(!session.isBlank())headers.put("MCP-Session-Id",session);
            post(uri,Map.of("jsonrpc","2.0","method","notifications/initialized"),headers);
            List<JsonNode> tools=new ArrayList<>();String cursor=null;
            for(int page=0;page<10;page++){Map<String,Object> params=cursor==null?Map.of():Map.of("cursor",cursor);JsonNode response=post(uri,Map.of("jsonrpc","2.0","id","tools-"+page,"method","tools/list","params",params),headers);JsonNode result=response.path("result");if(response.has("error"))throw new IllegalStateException(response.path("error").toString());result.path("tools").forEach(tools::add);cursor=result.path("nextCursor").asText("");if(cursor.isBlank())break;}
            return tools.stream().limit(500).toList();
        }catch(ResponseStatusException e){throw e;}catch(Exception e){throw new ResponseStatusException(HttpStatus.BAD_GATEWAY,"MCP 连接或工具发现失败",e);}
    }
    private JsonNode post(URI uri,Object body,Map<String,String> extra)throws Exception{
        HttpRequest.Builder b=HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(20)).header("Content-Type","application/json").header("Accept","application/json, text/event-stream").header("MCP-Protocol-Version","2025-11-25").POST(HttpRequest.BodyPublishers.ofString(json.writeValueAsString(body)));extra.forEach(b::header);
        HttpResponse<String> r=http.send(b.build(),HttpResponse.BodyHandlers.ofString());if(r.statusCode()==202&&r.body().isBlank())return json.createObjectNode();if(r.statusCode()<200||r.statusCode()>=300)throw new IllegalStateException("HTTP "+r.statusCode());JsonNode parsed=parse(r.body());String session=r.headers().firstValue("MCP-Session-Id").orElse("");if(!session.isBlank()&&parsed.isObject())((com.fasterxml.jackson.databind.node.ObjectNode)parsed).put("_session_id",session);return parsed;
    }
    private JsonNode parse(String body)throws Exception{if(body.stripLeading().startsWith("{"))return json.readTree(body);String data=body.lines().filter(x->x.startsWith("data:")).reduce((a,b)->b).orElseThrow();return json.readTree(data.substring(5).trim());}
    private void validateDestination(URI uri)throws Exception{
        if(!"https".equalsIgnoreCase(uri.getScheme())||uri.getHost()==null||uri.getUserInfo()!=null||uri.getFragment()!=null)throw new IllegalArgumentException("MCP discovery requires a public HTTPS endpoint");
        for(InetAddress address:InetAddress.getAllByName(uri.getHost())){
            byte[] raw=address.getAddress();boolean uniqueLocal=raw.length==16&&(raw[0]&0xfe)==0xfc;
            if(address.isAnyLocalAddress()||address.isLoopbackAddress()||address.isLinkLocalAddress()||address.isSiteLocalAddress()||address.isMulticastAddress()||uniqueLocal)throw new IllegalArgumentException("MCP endpoint resolves to a private or local address");
        }
    }
}

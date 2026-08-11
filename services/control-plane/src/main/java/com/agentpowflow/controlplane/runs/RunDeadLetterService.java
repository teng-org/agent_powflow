package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.web.dto.RunDto;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import java.time.Instant;
import java.util.*;

@Service
public class RunDeadLetterService {
    private static final Set<String> STATUSES=Set.of("OPEN","REPLAYING","REPLAYED","DISMISSED");
    private final RunDeadLetterRepository repository;
    private final RunDeadLetterAuditRepository auditRepository;
    private final RunRepository runRepository;
    private final RunDispatchOutboxService outboxService;
    private final ObjectMapper mapper;
    public RunDeadLetterService(RunDeadLetterRepository repository,RunDeadLetterAuditRepository auditRepository,RunRepository runRepository,RunDispatchOutboxService outboxService,ObjectMapper mapper){this.repository=repository;this.auditRepository=auditRepository;this.runRepository=runRepository;this.outboxService=outboxService;this.mapper=mapper;}

    @Transactional
    public IngestResult ingest(RunDto.DeadLetterIngestRequest request){
        RunDeadLetterEntity existing=repository.findByFailedTaskId(request.failed_task_id()).orElse(null);
        if(existing!=null){
            if(!existing.getRunId().equals(request.run_id())||!existing.getTenantId().equals(request.tenant_id())) throw new ResponseStatusException(HttpStatus.CONFLICT,"failed task id belongs to another run");
            return new IngestResult(view(existing),true);
        }
        runRepository.findByRunIdAndTenantId(request.run_id(),request.tenant_id()).orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"run not found"));
        RunDeadLetterEntity item=new RunDeadLetterEntity(); item.setTenantId(request.tenant_id()); item.setRunId(request.run_id()); item.setFailedTaskId(request.failed_task_id()); item.setSourceQueue(request.source_queue()); item.setError(truncate(request.error())); item.setPayloadJson(write(request.payload()));
        item=repository.saveAndFlush(item); audit(item,"INGESTED","execution-worker",Map.of("failed_task_id",item.getFailedTaskId(),"source_queue",item.getSourceQueue()));
        return new IngestResult(view(item),false);
    }

    @Transactional(readOnly=true)
    public List<RunDto.DeadLetterView> list(String tenantId,String status){
        List<RunDeadLetterEntity> items=(status==null||status.isBlank())?repository.findTop100ByTenantIdOrderByCreatedAtDesc(tenantId):repository.findTop100ByTenantIdAndStatusOrderByCreatedAtDesc(tenantId,normalize(status));
        return items.stream().map(this::view).toList();
    }
    @Transactional(readOnly=true)
    public RunDto.DeadLetterView get(String tenantId,String id){return view(find(tenantId,id));}
    @Transactional(readOnly=true)
    public List<RunDto.DeadLetterAuditView> audit(String tenantId,String id){find(tenantId,id);return auditRepository.findByDeadLetterIdOrderByCreatedAtAsc(id).stream().map(this::auditView).toList();}

    @Transactional
    public RunDto.DeadLetterView replay(String tenantId,String id,String actorId){
        RunDeadLetterEntity item=repository.lockById(id).filter(d->tenantId.equals(d.getTenantId())).orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"dead letter not found"));
        if(!"OPEN".equals(item.getStatus())) throw new ResponseStatusException(HttpStatus.CONFLICT,"only open dead letters can be replayed");
        outboxService.requeueExecutionDeadLetter(tenantId,item.getRunId());
        item.setStatus("REPLAYING"); item.setReplayCount(item.getReplayCount()+1); item.setReplayRequestedAt(Instant.now()); item.setReplayedBy(actorId); item.setLastError(""); item=repository.saveAndFlush(item);
        audit(item,"REPLAY_REQUESTED",actorId,Map.of("replay_count",item.getReplayCount())); return view(item);
    }

    @Transactional
    public RunDto.DeadLetterView dismiss(String tenantId,String id,String actorId){
        RunDeadLetterEntity item=repository.lockById(id).filter(d->tenantId.equals(d.getTenantId())).orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"dead letter not found"));
        if(!"OPEN".equals(item.getStatus())) throw new ResponseStatusException(HttpStatus.CONFLICT,"only open dead letters can be dismissed");
        item.setStatus("DISMISSED"); item.setReplayedBy(actorId); item=repository.saveAndFlush(item);
        audit(item,"DISMISSED",actorId,Map.of("reason","operator dismissed")); return view(item);
    }

    @Transactional
    public void completeForRun(String runId,String terminalStatus){
        for(RunDeadLetterEntity item:repository.findByRunIdAndStatus(runId,"REPLAYING")){item.setStatus("REPLAYED");item.setReplayedAt(Instant.now());repository.save(item);audit(item,"REPLAY_COMPLETED","execution-worker",Map.of("terminal_status",terminalStatus));}
    }
    private RunDeadLetterEntity find(String tenantId,String id){return repository.findByDeadLetterIdAndTenantId(id,tenantId).orElseThrow(()->new ResponseStatusException(HttpStatus.NOT_FOUND,"dead letter not found"));}
    private void audit(RunDeadLetterEntity item,String action,String actor,Object detail){RunDeadLetterAuditEntity a=new RunDeadLetterAuditEntity();a.setDeadLetterId(item.getDeadLetterId());a.setTenantId(item.getTenantId());a.setRunId(item.getRunId());a.setAction(action);a.setActorId(actor);a.setDetailJson(write(mapper.valueToTree(detail)));auditRepository.save(a);}
    private String normalize(String value){String v=value.trim().toUpperCase();if(!STATUSES.contains(v))throw new ResponseStatusException(HttpStatus.BAD_REQUEST,"invalid dead letter status");return v;}
    private String write(JsonNode value){try{return mapper.writeValueAsString(value==null?JsonNodeFactory.instance.objectNode():value);}catch(Exception e){throw new IllegalStateException(e);}}
    private JsonNode read(String value){try{return mapper.readTree(value);}catch(Exception e){return JsonNodeFactory.instance.objectNode();}}
    private String truncate(String v){String s=v==null?"":v;return s.length()<=2000?s:s.substring(0,2000);}
    private RunDto.DeadLetterView view(RunDeadLetterEntity d){return new RunDto.DeadLetterView(d.getDeadLetterId(),d.getTenantId(),d.getRunId(),d.getFailedTaskId(),d.getSourceQueue(),d.getError(),read(d.getPayloadJson()),d.getStatus(),d.getReplayCount(),d.getLastError(),d.getReplayRequestedAt(),d.getReplayedAt(),d.getReplayedBy(),d.getCreatedAt(),d.getUpdatedAt(),d.getRowVersion());}
    private RunDto.DeadLetterAuditView auditView(RunDeadLetterAuditEntity a){return new RunDto.DeadLetterAuditView(a.getAuditId(),a.getDeadLetterId(),a.getTenantId(),a.getRunId(),a.getAction(),a.getActorId(),read(a.getDetailJson()),a.getCreatedAt());}
    public record IngestResult(RunDto.DeadLetterView item,boolean duplicate){}
}

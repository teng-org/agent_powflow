package com.agentpowflow.controlplane.web;

import com.agentpowflow.controlplane.runs.*;
import com.agentpowflow.controlplane.web.dto.RunDto;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@RestController
@RequestMapping("/api/v1/internal/run-dead-letters")
public class InternalRunDeadLetterController {
    private final RunDeadLetterService service; private final InternalApiProperties properties;
    public InternalRunDeadLetterController(RunDeadLetterService service,InternalApiProperties properties){this.service=service;this.properties=properties;}
    @PostMapping
    public RunDto.DeadLetterResponse ingest(@RequestHeader(value="X-Internal-Token",required=false) String token,@Valid @RequestBody RunDto.DeadLetterIngestRequest request){requireToken(token);RunDeadLetterService.IngestResult result=service.ingest(request);return new RunDto.DeadLetterResponse("ok",result.item(),result.duplicate());}
    private void requireToken(String supplied){String expected=properties.getToken();if(supplied==null||expected==null||expected.isBlank()||!MessageDigest.isEqual(supplied.getBytes(StandardCharsets.UTF_8),expected.getBytes(StandardCharsets.UTF_8)))throw new ResponseStatusException(HttpStatus.UNAUTHORIZED,"invalid internal token");}
}

package com.agentpowflow.controlplane.runs;

import com.agentpowflow.controlplane.web.dto.RunDto;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

@Component
public class RunEventStream {
    private final ConcurrentHashMap<String, CopyOnWriteArrayList<SseEmitter>> emitters = new ConcurrentHashMap<>();

    public SseEmitter subscribe(String runId, List<RunDto.EventView> history) {
        SseEmitter emitter = new SseEmitter(30L * 60L * 1000L);
        emitters.computeIfAbsent(runId, ignored -> new CopyOnWriteArrayList<>()).add(emitter);
        emitter.onCompletion(() -> remove(runId, emitter));
        emitter.onTimeout(() -> remove(runId, emitter));
        emitter.onError(ignored -> remove(runId, emitter));
        try {
            for (RunDto.EventView event : history) send(emitter, event);
        } catch (IOException ex) {
            remove(runId, emitter);
            emitter.completeWithError(ex);
        }
        return emitter;
    }

    public void publish(RunDto.EventView event) {
        List<SseEmitter> subscribers = emitters.getOrDefault(event.run_id(), new CopyOnWriteArrayList<>());
        for (SseEmitter emitter : subscribers) {
            try {
                send(emitter, event);
                if (isTerminal(event.status())) emitter.complete();
            } catch (IOException | IllegalStateException ex) {
                remove(event.run_id(), emitter);
            }
        }
    }

    private void send(SseEmitter emitter, RunDto.EventView event) throws IOException {
        emitter.send(SseEmitter.event()
                .id(String.valueOf(event.sequence_no()))
                .name(event.event_type())
                .data(event));
    }

    private void remove(String runId, SseEmitter emitter) {
        CopyOnWriteArrayList<SseEmitter> list = emitters.get(runId);
        if (list == null) return;
        list.remove(emitter);
        if (list.isEmpty()) emitters.remove(runId, list);
    }

    private boolean isTerminal(String status) {
        return "SUCCEEDED".equals(status) || "FAILED".equals(status) || "CANCELLED".equals(status);
    }
}

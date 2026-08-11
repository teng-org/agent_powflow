package com.agentpowflow.controlplane.runs;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "control-plane.execution-lease")
public class ExecutionLeaseProperties {
    private long durationSeconds = 30;
    private long heartbeatSeconds = 10;

    public long getDurationSeconds() { return durationSeconds; }
    public void setDurationSeconds(long value) { durationSeconds = value; }
    public long getHeartbeatSeconds() { return heartbeatSeconds; }
    public void setHeartbeatSeconds(long value) { heartbeatSeconds = value; }
}

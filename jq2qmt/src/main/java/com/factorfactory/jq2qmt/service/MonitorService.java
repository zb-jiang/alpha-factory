package com.factorfactory.jq2qmt.service;

import com.factorfactory.jq2qmt.model.StreamInfo;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.*;

@Service
public class MonitorService {

    private static final Logger log = LoggerFactory.getLogger(MonitorService.class);

    private final RedisStreamService redisStreamService;

    public MonitorService(RedisStreamService redisStreamService) {
        this.redisStreamService = redisStreamService;
    }

    public List<StreamInfo> getAllStreamInfo() {
        Set<String> streams = redisStreamService.findAllStreams();
        List<StreamInfo> result = new ArrayList<>();

        for (String streamKey : streams) {
            StreamInfo info = redisStreamService.getStreamInfo(streamKey);
            if (streamKey.endsWith(":result")) {
                info.setType("result");
            } else if (streamKey.endsWith(":dead")) {
                info.setType("dead");
            } else {
                info.setType("signal");
            }
            result.add(info);
        }

        result.sort(Comparator.comparing(StreamInfo::getStreamName));
        return result;
    }

    public Map<String, Object> getDashboardSummary() {
        Map<String, Object> summary = new LinkedHashMap<>();

        Set<String> signalStreams = redisStreamService.findAllSignalStreams();
        Set<String> allStreams = redisStreamService.findAllStreams();

        long totalSignalCount = 0;
        long totalResultCount = 0;
        long totalDeadCount = 0;
        List<String> strategies = new ArrayList<>();

        for (String streamKey : allStreams) {
            StreamInfo info = redisStreamService.getStreamInfo(streamKey);
            if (streamKey.endsWith(":result")) {
                totalResultCount += info.getLength();
            } else if (streamKey.endsWith(":dead")) {
                totalDeadCount += info.getLength();
            } else {
                totalSignalCount += info.getLength();
                String strategy = extractStrategy(streamKey);
                strategies.add(strategy);
            }
        }

        summary.put("totalSignalStreams", signalStreams.size());
        summary.put("totalSignalCount", totalSignalCount);
        summary.put("totalResultCount", totalResultCount);
        summary.put("totalDeadCount", totalDeadCount);
        summary.put("strategies", strategies);
        summary.put("timestamp", new Date().toString());

        return summary;
    }

    public List<Map<String, String>> getSignalHistory(String strategy, int count) {
        return redisStreamService.getSignalHistory(strategy, Math.min(count, 100));
    }

    public List<Map<String, String>> getResultHistory(String strategy, int count) {
        return redisStreamService.getResultHistory(strategy, Math.min(count, 100));
    }

    public List<Map<String, String>> getPendingSignals(String strategy) {
        return redisStreamService.getPendingSignals(strategy);
    }

    private String extractStrategy(String streamKey) {
        String prefix = streamKey.contains(":") ?
                streamKey.substring(0, streamKey.indexOf(':') + 1) : "";
        return streamKey.substring(prefix.length());
    }
}

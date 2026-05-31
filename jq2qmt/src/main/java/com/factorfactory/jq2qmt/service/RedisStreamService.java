package com.factorfactory.jq2qmt.service;

import com.factorfactory.jq2qmt.config.AppProperties;
import com.factorfactory.jq2qmt.model.ExecutionResult;
import com.factorfactory.jq2qmt.model.StreamInfo;
import com.factorfactory.jq2qmt.model.TradeSignal;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Range;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.connection.stream.*;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.*;
import java.util.stream.Collectors;

@Service
public class RedisStreamService {

    private static final Logger log = LoggerFactory.getLogger(RedisStreamService.class);

    private final RedisTemplate<String, String> redisTemplate;
    private final RedisConnectionFactory connectionFactory;
    private final AppProperties properties;

    public RedisStreamService(RedisTemplate<String, String> redisTemplate,
                              RedisConnectionFactory connectionFactory,
                              AppProperties properties) {
        this.redisTemplate = redisTemplate;
        this.connectionFactory = connectionFactory;
        this.properties = properties;
    }

    public String addSignal(TradeSignal signal) {
        String streamKey = properties.getStream().signalStreamKey(signal.getStrategy());
        Map<String, String> messageBody = signalToMap(signal);

        StringRecord record = StringRecord.of(messageBody);
        RecordId recordId = redisTemplate.opsForStream().add(record.withStreamKey(streamKey));

        if (recordId == null) {
            throw new RuntimeException("Failed to add signal to Redis Stream: " + streamKey);
        }

        trimStream(streamKey, properties.getStream().getMaxLength());

        ensureConsumerGroup(streamKey);

        log.info("Signal added to stream [{}], recordId={}, signalId={}, action={}, code={}",
                streamKey, recordId.getValue(), signal.getSignalId(), signal.getAction(), signal.getCode());

        return recordId.getValue();
    }

    @SuppressWarnings("unchecked")
    public List<Map<String, String>> consumeSignals(String strategy, String consumerName, int count) {
        String streamKey = properties.getStream().signalStreamKey(strategy);
        String group = properties.getStream().getConsumerGroup();

        ensureConsumerGroup(streamKey);

        Consumer consumer = Consumer.from(group, consumerName);
        StreamReadOptions options = StreamReadOptions.empty().count(count);
        StreamOffset<String> offset = StreamOffset.create(streamKey, ReadOffset.lastConsumed());

        List<MapRecord<String, Object, Object>> rawRecords =
                redisTemplate.opsForStream().read(consumer, options, offset);

        if (rawRecords == null || rawRecords.isEmpty()) {
            return Collections.emptyList();
        }

        return rawRecords.stream()
                .map(this::recordToMap)
                .peek(msg -> msg.put("_stream_key", streamKey))
                .collect(Collectors.toList());
    }

    @SuppressWarnings("unchecked")
    public List<Map<String, String>> consumeSignalsBlocking(String strategy, String consumerName,
                                                            int count, Duration timeout) {
        String streamKey = properties.getStream().signalStreamKey(strategy);
        String group = properties.getStream().getConsumerGroup();

        ensureConsumerGroup(streamKey);

        Consumer consumer = Consumer.from(group, consumerName);
        StreamReadOptions options = StreamReadOptions.empty().count(count).block(timeout);
        StreamOffset<String> offset = StreamOffset.create(streamKey, ReadOffset.lastConsumed());

        List<MapRecord<String, Object, Object>> rawRecords =
                redisTemplate.opsForStream().read(consumer, options, offset);

        if (rawRecords == null || rawRecords.isEmpty()) {
            return Collections.emptyList();
        }

        return rawRecords.stream()
                .map(this::recordToMap)
                .peek(msg -> msg.put("_stream_key", streamKey))
                .collect(Collectors.toList());
    }

    public void ackSignal(String strategy, String recordId) {
        String streamKey = properties.getStream().signalStreamKey(strategy);
        String group = properties.getStream().getConsumerGroup();

        Long acknowledged = redisTemplate.opsForStream().acknowledge(streamKey, group, recordId);

        log.info("ACK signal: stream={}, recordId={}, acknowledged={}", streamKey, recordId, acknowledged);
    }

    public String addResult(ExecutionResult result) {
        String strategy = result.getStrategy();
        String streamKey = properties.getStream().resultStreamKey(strategy);
        Map<String, String> messageBody = resultToMap(result);

        StringRecord record = StringRecord.of(messageBody);
        RecordId recordId = redisTemplate.opsForStream().add(record.withStreamKey(streamKey));

        if (recordId == null) {
            throw new RuntimeException("Failed to add result to Redis Stream: " + streamKey);
        }

        trimStream(streamKey, properties.getStream().getMaxLength());

        log.info("Result added to stream [{}], recordId={}, signalId={}, status={}",
                streamKey, recordId.getValue(), result.getSignalId(), result.getStatus());

        return recordId.getValue();
    }

    public void moveToDeadLetter(String strategy, String recordId, Map<String, String> messageBody, String reason) {
        String deadKey = properties.getStream().deadStreamKey(strategy);
        messageBody.put("_dead_reason", reason);
        messageBody.put("_original_record_id", recordId);
        messageBody.put("_dead_time", new Date().toString());

        StringRecord record = StringRecord.of(messageBody);
        redisTemplate.opsForStream().add(record.withStreamKey(deadKey));

        trimStream(deadKey, properties.getStream().getMaxLength());

        log.warn("Signal moved to dead letter: stream={}, recordId={}, reason={}", deadKey, recordId, reason);
    }

    public List<Map<String, String>> getPendingSignals(String strategy) {
        String streamKey = properties.getStream().signalStreamKey(strategy);
        String group = properties.getStream().getConsumerGroup();

        PendingMessages pending = redisTemplate.opsForStream()
                .pending(streamKey, Consumer.from(group, "unused"));

        if (pending == null || pending.isEmpty()) {
            return Collections.emptyList();
        }

        List<Map<String, String>> result = new ArrayList<>();
        for (PendingMessage pm : pending) {
            Map<String, String> info = new HashMap<>();
            info.put("recordId", pm.getId().getValue());
            info.put("consumerName", pm.getConsumerName());
            info.put("totalDeliveryCount", String.valueOf(pm.getTotalDeliveryCount()));
            result.add(info);
        }
        return result;
    }

    @SuppressWarnings("unchecked")
    public List<Map<String, String>> getSignalHistory(String strategy, int count) {
        String streamKey = properties.getStream().signalStreamKey(strategy);

        List<MapRecord<String, Object, Object>> rawRecords =
                redisTemplate.opsForStream().range(streamKey, Range.unbounded());

        if (rawRecords == null) {
            return Collections.emptyList();
        }

        return rawRecords.stream()
                .limit(count)
                .map(this::recordToMap)
                .collect(Collectors.toList());
    }

    @SuppressWarnings("unchecked")
    public List<Map<String, String>> getResultHistory(String strategy, int count) {
        String streamKey = properties.getStream().resultStreamKey(strategy);

        List<MapRecord<String, Object, Object>> rawRecords =
                redisTemplate.opsForStream().range(streamKey, Range.unbounded());

        if (rawRecords == null) {
            return Collections.emptyList();
        }

        return rawRecords.stream()
                .limit(count)
                .map(this::recordToMap)
                .collect(Collectors.toList());
    }

    public StreamInfo getStreamInfo(String streamKey) {
        StreamInfo si = new StreamInfo();
        si.setStreamName(streamKey);
        si.setType("stream");
        try {
            Long streamLength = redisTemplate.opsForStream().size(streamKey);
            si.setLength(streamLength != null ? streamLength : 0L);
        } catch (Exception e) {
            si.setLength(0L);
        }
        si.setRadixTreeKeys(0L);
        si.setRadixTreeNodes(0L);
        si.setConsumerGroupCount(0L);
        return si;
    }

    public Set<String> findAllSignalStreams() {
        String pattern = properties.getStream().getPrefix() + "*";
        Set<String> keys = redisTemplate.keys(pattern);
        if (keys == null) {
            return Collections.emptySet();
        }
        return keys.stream()
                .filter(k -> !k.endsWith(":result") && !k.endsWith(":dead"))
                .collect(Collectors.toSet());
    }

    public Set<String> findAllStreams() {
        String pattern = properties.getStream().getPrefix() + "*";
        Set<String> keys = redisTemplate.keys(pattern);
        return keys != null ? keys : Collections.emptySet();
    }

    private void ensureConsumerGroup(String streamKey) {
        String group = properties.getStream().getConsumerGroup();
        try {
            redisTemplate.opsForStream().createGroup(streamKey, ReadOffset.from("0"), group);
            log.info("Created consumer group '{}' for stream '{}'", group, streamKey);
        } catch (Exception e) {
            if (!e.getMessage().contains("BUSYGROUP")) {
                log.debug("Consumer group '{}' already exists for stream '{}'", group, streamKey);
            }
        }
    }

    private void trimStream(String streamKey, int maxLength) {
        redisTemplate.opsForStream().trim(streamKey, maxLength, true);
    }

    private Map<String, String> recordToMap(MapRecord<String, Object, Object> record) {
        Map<String, String> msg = new LinkedHashMap<>();
        record.getValue().forEach((k, v) -> msg.put(k.toString(), v != null ? v.toString() : ""));
        msg.put("_redis_record_id", record.getId().getValue());
        return msg;
    }

    private long toLong(Object value) {
        if (value == null) return 0L;
        if (value instanceof Number) return ((Number) value).longValue();
        try { return Long.parseLong(value.toString()); } catch (Exception e) { return 0L; }
    }

    private Map<String, String> signalToMap(TradeSignal signal) {
        Map<String, String> map = new LinkedHashMap<>();
        map.put("action", signal.getAction());
        map.put("code", signal.getCode());
        map.put("pct", String.valueOf(signal.getPct()));
        map.put("price", String.valueOf(signal.getPrice() != null ? signal.getPrice() : 0.0));
        map.put("strategy", signal.getStrategy());
        map.put("signal_time", signal.getSignalTime() != null ? signal.getSignalTime() : "");
        map.put("signal_id", signal.getSignalId() != null ? signal.getSignalId() : "");
        return map;
    }

    private Map<String, String> resultToMap(ExecutionResult result) {
        Map<String, String> map = new LinkedHashMap<>();
        map.put("signal_id", result.getSignalId());
        map.put("status", result.getStatus());
        map.put("order_id", result.getOrderId() != null ? String.valueOf(result.getOrderId()) : "");
        map.put("filled_price", result.getFilledPrice() != null ? String.valueOf(result.getFilledPrice()) : "");
        map.put("filled_volume", result.getFilledVolume() != null ? String.valueOf(result.getFilledVolume()) : "");
        map.put("filled_amount", result.getFilledAmount() != null ? String.valueOf(result.getFilledAmount()) : "");
        map.put("execute_time", result.getExecuteTime() != null ? result.getExecuteTime() : "");
        map.put("remark", result.getRemark() != null ? result.getRemark() : "");
        map.put("strategy", result.getStrategy() != null ? result.getStrategy() : "");
        return map;
    }
}

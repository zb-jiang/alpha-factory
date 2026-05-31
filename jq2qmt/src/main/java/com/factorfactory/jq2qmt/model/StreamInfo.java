package com.factorfactory.jq2qmt.model;

import java.io.Serializable;
import java.util.Map;

public class StreamInfo implements Serializable {

    private String streamName;
    private Long length;
    private Long radixTreeKeys;
    private Long radixTreeNodes;
    private Map<String, String> firstEntry;
    private Map<String, String> lastEntry;
    private Long consumerGroupCount;
    private String type;

    public StreamInfo() {}

    public String getStreamName() { return streamName; }
    public void setStreamName(String streamName) { this.streamName = streamName; }

    public Long getLength() { return length; }
    public void setLength(Long length) { this.length = length; }

    public Long getRadixTreeKeys() { return radixTreeKeys; }
    public void setRadixTreeKeys(Long radixTreeKeys) { this.radixTreeKeys = radixTreeKeys; }

    public Long getRadixTreeNodes() { return radixTreeNodes; }
    public void setRadixTreeNodes(Long radixTreeNodes) { this.radixTreeNodes = radixTreeNodes; }

    public Map<String, String> getFirstEntry() { return firstEntry; }
    public void setFirstEntry(Map<String, String> firstEntry) { this.firstEntry = firstEntry; }

    public Map<String, String> getLastEntry() { return lastEntry; }
    public void setLastEntry(Map<String, String> lastEntry) { this.lastEntry = lastEntry; }

    public Long getConsumerGroupCount() { return consumerGroupCount; }
    public void setConsumerGroupCount(Long consumerGroupCount) { this.consumerGroupCount = consumerGroupCount; }

    public String getType() { return type; }
    public void setType(String type) { this.type = type; }
}

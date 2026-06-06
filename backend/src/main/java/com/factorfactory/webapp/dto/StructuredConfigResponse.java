package com.factorfactory.webapp.dto;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;
import java.util.Map;

/**
 * 结构化配置响应
 * 将YAML配置解析为分组的表单字段，供前端渲染专业配置界面
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class StructuredConfigResponse {

    /** 配置分区名称 */
    private String section;

    /** 分区显示名称 */
    private String sectionLabel;

    /** 分区描述 */
    private String sectionDescription;

    /** 分区图标（Element Plus 图标名） */
    private String sectionIcon;

    /** 分组列表，每个组包含多个配置字段 */
    private List<ConfigGroup> groups;

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class ConfigGroup {
        /** 组名称 */
        private String name;
        /** 组显示名称 */
        private String label;
        /** 组描述 */
        private String description;
        /** 组图标 */
        private String icon;
        /** 组内字段列表 */
        private List<ConfigField> fields;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class ConfigField {
        /** 字段key（对应YAML路径，用.分隔） */
        private String key;
        /** 字段显示名称 */
        private String label;
        /** 字段描述/提示 */
        private String description;
        /** 字段类型：text, number, select, switch, slider, date, password, tag_select, provider_list */
        private String type;
        /** 当前值 */
        private Object value;
        /** 默认值 */
        private Object defaultValue;
        /** 占位文本 */
        private String placeholder;
        /** 可选值列表（select/tag_select类型） */
        private List<SelectOption> options;
        /** 数值最小值（number/slider类型） */
        private Number min;
        /** 数值最大值（number/slider类型） */
        private Number max;
        /** 步长（number/slider类型） */
        private Number step;
        /** 精度（number类型） */
        private Integer precision;
        /** 是否必填 */
        private Boolean required;
        /** 是否只读 */
        private Boolean readonly;
        /** 配置来源层级：global / user / task */
        private String source;
        /** 条件显示：当指定key的值等于指定值时才显示 */
        private String showWhenKey;
        private Object showWhenValue;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    public static class SelectOption {
        private String label;
        private String value;
        private String description;
    }
}

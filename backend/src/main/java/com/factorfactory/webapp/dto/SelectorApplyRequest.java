package com.factorfactory.webapp.dto;

import lombok.Data;

@Data
public class SelectorApplyRequest {
    private String trainStartDate;
    private String trainEndDate;
    private Integer recommendSpanMonths;
    private String mode;
}

ENTITY_LABELS = [
    "Device",
    "Process",
    "Material",
    "Defect",
    "Cause",
    "Countermeasure",
]

REL_TYPES = [
    "USED_IN",
    "USES_MATERIAL",
    "OCCURS_AT",
    "CAUSED_BY",
    "ADDRESSED_BY",
    "TARGETS",
    "INDUCED_BY_MATERIAL",
    "SUBCLASS_OF",
    "PRECEDES",
]

LABEL_CN = {
    "Device": "设备",
    "Process": "工序",
    "Material": "物料",
    "Defect": "不良现象",
    "Cause": "原因",
    "Countermeasure": "对策",
}

REL_CN = {
    "USED_IN": "用于工序",
    "USES_MATERIAL": "耗用物料",
    "OCCURS_AT": "发生于工序",
    "CAUSED_BY": "归因于",
    "ADDRESSED_BY": "对策为",
    "TARGETS": "作用于",
    "INDUCED_BY_MATERIAL": "物料诱发",
    "SUBCLASS_OF": "细分为",
    "PRECEDES": "前序工序",
}

CAUSAL_RELS = {"CAUSED_BY", "ADDRESSED_BY", "OCCURS_AT", "INDUCED_BY_MATERIAL"}

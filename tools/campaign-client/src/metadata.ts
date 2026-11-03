// Backend metadata snapshot; tests compare every schema and hint with Python.
export const TOOL_METADATA = {
  "ncp_domain_read_describe": {
    "annotations": {
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": false,
      "readOnlyHint": true
    },
    "inputSchema": {
      "additionalProperties": false,
      "properties": {
        "domain": {
          "enum": [
            "metrology",
            "gis",
            "finance",
            "trade"
          ],
          "type": "string"
        }
      },
      "required": [
        "domain"
      ],
      "type": "object"
    },
    "outputSchema": {
      "type": "object"
    }
  },
  "ncp_domain_read_list": {
    "annotations": {
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": false,
      "readOnlyHint": true
    },
    "inputSchema": {
      "additionalProperties": false,
      "properties": {
        "domain": {
          "enum": [
            "metrology",
            "gis",
            "finance",
            "trade"
          ],
          "type": "string"
        },
        "limit": {
          "maximum": 32,
          "minimum": 1,
          "type": "integer"
        },
        "offset": {
          "maximum": 64,
          "minimum": 0,
          "type": "integer"
        }
      },
      "required": [
        "domain"
      ],
      "type": "object"
    },
    "outputSchema": {
      "type": "object"
    }
  },
  "ncp_domain_read_record": {
    "annotations": {
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": false,
      "readOnlyHint": true
    },
    "inputSchema": {
      "additionalProperties": false,
      "properties": {
        "domain": {
          "enum": [
            "metrology",
            "gis",
            "finance",
            "trade"
          ],
          "type": "string"
        },
        "record_id": {
          "maxLength": 160,
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "domain",
        "record_id"
      ],
      "type": "object"
    },
    "outputSchema": {
      "type": "object"
    }
  },
  "ncp_domain_read_verify": {
    "annotations": {
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": false,
      "readOnlyHint": true
    },
    "inputSchema": {
      "additionalProperties": false,
      "properties": {
        "domain": {
          "enum": [
            "metrology",
            "gis",
            "finance",
            "trade"
          ],
          "type": "string"
        },
        "receipt_json": {
          "maxLength": 65536,
          "minLength": 1,
          "type": "string"
        },
        "record_id": {
          "maxLength": 160,
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "domain",
        "record_id",
        "receipt_json"
      ],
      "type": "object"
    },
    "outputSchema": {
      "type": "object"
    }
  },
  "ncp_intake_describe": {
    "annotations": {
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": false,
      "readOnlyHint": true
    },
    "inputSchema": {
      "additionalProperties": false,
      "properties": {},
      "required": [],
      "type": "object"
    },
    "outputSchema": {
      "type": "object"
    }
  },
  "ncp_intake_record": {
    "annotations": {
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": false,
      "readOnlyHint": true
    },
    "inputSchema": {
      "additionalProperties": false,
      "properties": {
        "record_id": {
          "maxLength": 128,
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "record_id"
      ],
      "type": "object"
    },
    "outputSchema": {
      "type": "object"
    }
  },
  "ncp_intake_records": {
    "annotations": {
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": false,
      "readOnlyHint": true
    },
    "inputSchema": {
      "additionalProperties": false,
      "properties": {
        "limit": {
          "maximum": 32,
          "minimum": 1,
          "type": "integer"
        },
        "offset": {
          "maximum": 256,
          "minimum": 0,
          "type": "integer"
        },
        "protocol": {
          "enum": [
            "ros2.joint-state.v1",
            "opcua.scalar.v1",
            "mqtt.scalar-observation.v1"
          ],
          "type": "string"
        }
      },
      "required": [],
      "type": "object"
    },
    "outputSchema": {
      "type": "object"
    }
  },
  "ncp_intake_verify": {
    "annotations": {
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": false,
      "readOnlyHint": true
    },
    "inputSchema": {
      "additionalProperties": false,
      "properties": {
        "receipt_json": {
          "maxLength": 32768,
          "minLength": 1,
          "type": "string"
        },
        "record_id": {
          "maxLength": 128,
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "record_id",
        "receipt_json"
      ],
      "type": "object"
    },
    "outputSchema": {
      "type": "object"
    }
  },
  "ncp_proof_describe": {
    "annotations": {
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": false,
      "readOnlyHint": true
    },
    "inputSchema": {
      "additionalProperties": false,
      "properties": {},
      "type": "object"
    },
    "outputSchema": {
      "type": "object"
    }
  },
  "ncp_proof_verify": {
    "annotations": {
      "destructiveHint": false,
      "idempotentHint": true,
      "openWorldHint": false,
      "readOnlyHint": true
    },
    "inputSchema": {
      "additionalProperties": false,
      "properties": {
        "receipt_json": {
          "maxLength": 49152,
          "minLength": 1,
          "type": "string"
        },
        "request_json": {
          "maxLength": 8192,
          "minLength": 1,
          "type": "string"
        }
      },
      "required": [
        "request_json",
        "receipt_json"
      ],
      "type": "object"
    },
    "outputSchema": {
      "type": "object"
    }
  }
} as const;

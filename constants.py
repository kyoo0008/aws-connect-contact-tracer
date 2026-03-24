import json

# Error 로 인식하는 Results Keyword
ERROR_KEYWORDS = [
    'Error', 'Failed', 'Timeout', 'Exception', 'No prompt provided',
    'Instance has reached concurrent Lambda thread access limit',
    'Unsupported', 'Invalid', 'not found', 'NotDone', 'MultipleFound',
    'The Lambda Function Returned An Error.'
]

# 반복되는 Flow Block 중복 제거
DUP_CONTACT_FLOW_MODULE_TYPE = ['SetAttributes', 'SetFlowAttributes']

# 생략 Flow Block
OMIT_CONTACT_FLOW_MODULE_TYPE = ['InvokeFlowModule']

# Associated Contact 조회 여부
# True : 여러 관련된 Contact 조회, False : 입력된 하나의 Contact만 조회
ASSOCIATED_CONTACTS_FLAG = True


def _load_flow_translation(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        flow_translation = json.load(f)
    return {item["en_name"]: item["ko_name"] for item in flow_translation}


flow_translation_map = _load_flow_translation("./mnt/flow_ko_en.json")

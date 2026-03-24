import os
import json

from utils import sanitize_label, wrap_text, replace_generic_arn, valid_uuid
from describe_flow import get_comparison_value, get_comparison_second_value
from constants import DUP_CONTACT_FLOW_MODULE_TYPE, flow_translation_map


def add_edges(dot, nodes):
    """노드 리스트를 기반으로 에지를 추가하는 함수"""
    added_edges = set()
    for i in range(len(nodes) - 1):
        if (nodes[i], nodes[i + 1]) not in added_edges:
            dot.edge(nodes[i], nodes[i + 1], label=str(i))
            added_edges.add((nodes[i], nodes[i + 1]))
    return dot


def get_module_name_ko(module_type, log):
    """한글 모듈 이름 가져오기"""
    module_name_ko = flow_translation_map.get(module_type, module_type)
    if module_type in DUP_CONTACT_FLOW_MODULE_TYPE:
        module_name_ko = f"{module_name_ko} x {len(log.get('Parameters', {}))}"
    return module_name_ko


def define_module_type(module_type, param_json):
    """모듈 타입 정의"""
    if module_type == "SetContactFlow":
        flow_type = param_json.get("Type")
        if flow_type in ("CustomerHold", "AgentHold"):
            return "SetHoldFlow"
        elif flow_type in ("CustomerWhisper", "AgentWhisper"):
            return "SetWhisperFlow"
        elif flow_type == "CustomerQueue":
            return "SetCustomerQueueFlow"
        elif flow_type == "DefaultAgentUI":
            return "SetEventHook"
    return module_type


def get_node_text_by_module_type(module_type, log, block_id):
    """모듈 타입에 따른 node text 정의"""
    replaced_arn_log = replace_generic_arn(log)
    node_text = ""
    node_footer = ""
    param_json = replaced_arn_log.get("Parameters", {})

    if module_type == "CheckAttribute":
        op = param_json.get("ComparisonMethod")
        value = param_json.get("Value")
        second_value = param_json.get("SecondValue")

        value = wrap_text(value, is_just_cut=False, max_length=50)

        comparison_value = None
        comparison_second_value = None
        if log.get("ContactFlowId") and block_id:
            comparison_value = get_comparison_value(log.get("ContactFlowId"), block_id, "ComparisonValue")
            comparison_second_value = get_comparison_second_value(log.get("ContactFlowId"), block_id)

        operand_map = {
            "Contains": "⊃",
            "Equals": "=",
            "GreaterThan": ">",
            "GreaterThanOrEqualTo": "≧",
            "LessThan": "<",
            "LessThanOrEqualTo": "≦",
            "StartsWith": "StartsWith",
        }
        operand = operand_map.get(op, "")
        if not operand and op:
            node_text = "Invalid Operator"

        value = f"{value}" + (f"({comparison_value})" if comparison_value else "")
        second_value = f"{second_value}" + (f"({comparison_second_value})" if comparison_second_value else "")

        is_too_long = len(str(value) + str(second_value)) > 30
        if is_too_long:
            node_text += f"{value} {operand} \n{second_value} ? "
        else:
            node_text += f"{value} {operand} {second_value} ? "

        node_footer = "Results : " + (replaced_arn_log.get('Results') or '')

    elif module_type in ("InvokeExternalResource", "InvokeLambdaFunction"):
        if param_json.get("Parameters"):
            parameters = param_json.get("Parameters")
            for key in parameters:
                parameter_attr = get_comparison_value(log.get("ContactFlowId"), block_id, "LambdaInvocationAttributes")
                attr_value = parameter_attr.get(key) if parameter_attr else None
                attr_suffix = f"({attr_value})" if attr_value and "$" in attr_value else ""
                kv = f"{key} = {parameters[key]}"
                node_text += f"{wrap_text(kv, is_just_cut=True, max_length=25)} {attr_suffix}\n"

        if replaced_arn_log.get("ExternalResults"):
            node_footer = "ExternalResults : " + json.dumps(
                replaced_arn_log.get("ExternalResults", ""), indent=2, ensure_ascii=False
            )
        else:
            node_footer += replaced_arn_log.get("Results", "")

    elif module_type in ("PlayPrompt", "GetUserInput", "StoreUserInput"):
        param_str = param_json.get("Text")
        if param_str:
            param_str = param_str.replace(",", ",\n").replace(".", ".\n")
            for line in param_str.split("\n"):
                if len(line) > 30:
                    l_arr = line.split(" ")
                    l_arr[int(len(l_arr) / 2)] = l_arr[int(len(l_arr) / 2)] + "\n"
                    node_text += " ".join(l_arr) + "\n"
                else:
                    node_text += line + "\n"
        elif param_json.get("PromptSource"):
            prompt_wav = param_json.get("PromptLocation")
            if len(prompt_wav.split("/")) > 2:
                node_text += f"음원재생 : \n {prompt_wav.split('/')[-2]}/{prompt_wav.split('/')[-1]}"

        if replaced_arn_log.get('Results'):
            node_footer = "Results : " + wrap_text(replaced_arn_log.get('Results'), is_just_cut=True, max_length=20)

    elif module_type == "TagContact":
        if param_json.get("Tags"):
            tags = param_json.get("Tags")
            for key in tags:
                node_text += f"{key} : {tags[key]} \n"

    elif module_type in ("SetAttributes", "SetFlowAttributes"):
        for param in param_json:
            kv = f"{param['Key']} = {param['Value']}"
            node_text += wrap_text(kv, is_just_cut=True, max_length=30) + " \n"

    elif module_type == "SetLoggingBehavior":
        node_text += f"LoggingBehavior = {param_json['LoggingBehavior']}"

    elif module_type in ("SetContactFlow", "SetContactData"):
        for key in param_json:
            node_text += f"{key} : {param_json[key]} \n"

    elif module_type == "GetCustomerProfile":
        data = replaced_arn_log.get("ResultData")
        if data:
            node_text += "ProfileId: " + data['ProfileId']
        if replaced_arn_log.get('Results'):
            node_footer = "Results : " + replaced_arn_log.get('Results')

    elif module_type == "AssociateContactToCustomerProfile":
        node_text += f"{param_json['ProfileRequestData'][0]}\n{param_json['ProfileRequestData'][1]}"

    elif module_type in ("Dial", "Resume", "ReturnFromFlowModule"):
        node_text = ""

    else:
        for key in param_json:
            kv = f"{key} = {param_json[key]}"
            node_text += wrap_text(kv, is_just_cut=True, max_length=25) + " \n"
        if replaced_arn_log.get('Results'):
            node_footer = "Results : " + replaced_arn_log.get('Results')

    node_text = wrap_text(node_text, is_just_cut=True, max_length=100)
    return node_text, node_footer


def get_image_label(icon_path, text, size):
    """image label 가져오기"""
    label = f"""<<table border="0" cellborder="0" cellspacing="0">
        <tr>
            <td bgcolor="white" width="{size}" height="{size}" fixedsize="true"><img scale="true" src="{icon_path}"/></td>
        </tr>
        <tr>
            <td bgcolor="white" width="100">{text}</td>
        </tr></table>>"""
    return label


def get_node_label(module_type, node_title, node_text, node_footer, block_id):
    """node label 가져오기"""
    icon_path = f"{os.getcwd()}/mnt/img/{module_type}.png"
    has_icon = os.path.isfile(icon_path)

    node_text = str(node_text).replace(">", "＞").replace("<", "＜").replace("\n", "<br/>")
    node_footer = str(node_footer).replace(">", "＞").replace("<", "＜").replace("\n", "<br/>")

    if node_footer.startswith("ExternalResults"):
        if '"isSuccess": "true"' in node_footer:
            node_footer = "isSuccess: true ✅"
        elif '"isSuccess": "false"' in node_footer:
            node_footer = "isSuccess: false ❌"
        else:
            node_footer = wrap_text(node_footer, is_just_cut=True, max_length=30)
    else:
        if "false" in node_footer or "Fail" in node_footer:
            node_footer += " ❌"
        elif "true" in node_footer or "Success" in node_footer:
            node_footer += " ✅"

    if has_icon:
        top_label = f"""<<table border="0" cellborder="0" cellspacing="0">
        <tr>
            <td bgcolor="lightgray" width="30" height="30" fixedsize="true"><img scale="true" src="{icon_path}"/></td>
            <td bgcolor="lightgray" width="150">{node_title}</td>
        </tr>"""
    else:
        top_label = f"""<<table border="0" cellborder="0" cellspacing="0">
        <tr>
            <td bgcolor="lightgray">{node_title}</td>
        </tr>"""

    if block_id is None or valid_uuid(block_id):
        block_id_label = ""
    elif has_icon:
        block_id_label = f"""<tr><td colspan="2">{sanitize_label(block_id)}</td></tr>"""
    else:
        block_id_label = f"""<tr><td>{sanitize_label(block_id)}</td></tr>"""

    if has_icon:
        bottom_label = f"""<tr>
            <td colspan="2" bgcolor="white">{sanitize_label(node_text)}</td>
        </tr>"""
    else:
        bottom_label = f"""<tr>
            <td bgcolor="white">{sanitize_label(node_text)}</td>
        </tr>"""

    if node_footer is None or node_footer == "None":
        result_label = "</table>>"
    elif has_icon:
        result_label = f"""<tr><td colspan="2">{node_footer}</td></tr></table>>"""
    else:
        result_label = f"""<tr><td>{node_footer}</td></tr></table>>"""

    return top_label + block_id_label + bottom_label + result_label

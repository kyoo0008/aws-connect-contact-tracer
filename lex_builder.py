import os
import json
import uuid

from graphviz import Digraph
from utils import wrap_transcript, apply_rank, find_lex_xray_timestamp
from graph_labels import get_image_label, get_node_label, add_edges
from xray_builder import build_xray_dot
from fetch_data_from_s3 import get_analysis_object


def build_lex_dot(contact_id, region):
    """Lex 대화 내용을 시각화합니다."""
    file_path = f"./virtual_env/lex_{contact_id}.json"
    if not os.path.isfile(file_path):
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        lex_transcript = json.loads(f.read())

    function_logs = []
    lex_hook_path = f"./virtual_env/lex_hook_{contact_id}.json"
    if os.path.isfile(lex_hook_path):
        with open(lex_hook_path, "r", encoding="utf-8") as f:
            function_logs = json.loads(f.read())

    if not lex_transcript:
        return []

    lex_dot = Digraph(comment="Transcript")
    lex_dot.attr(rankdir="LR")
    lex_nodes = []

    for script in lex_transcript:
        customer_node_id = script.get("requestId", str(uuid.uuid4())) + "-customer"
        lex_nodes.append(customer_node_id)

        intent_footer = ""
        for intent in script.get("interpretations", []):
            intent_name = intent.get("intent", {})["name"]
            confidence = intent.get("nluConfidence", "0.0")
            current_intent = script.get("sessionState", {}).get("intent", {}).get("name", "")
            if current_intent == intent_name:
                intent_footer += f"* {intent_name} : {confidence}\n"
            else:
                intent_footer += f"{intent_name} : {confidence}\n"

        lex_dot.node(
            customer_node_id,
            label=get_node_label(
                "customer", "customer",
                wrap_transcript(script.get("inputTranscript", "")),
                intent_footer, None
            ),
            shape='box',
            style='rounded,filled',
            color='lightgray',
            URL=str(json.dumps(script, indent=4, ensure_ascii=False))
        )

        if function_logs:
            xray_trace_id = find_lex_xray_timestamp(script, function_logs)
            is_transcript_found = any(
                l.get("xray_trace_id") == xray_trace_id
                and l.get("event", {}).get("inputTranscript") == script.get("inputTranscript")
                for l in function_logs
            )
            if xray_trace_id and is_transcript_found:
                lex_dot, lex_nodes, _ = build_xray_dot(
                    lex_dot, lex_nodes, 0, xray_trace_id, region, function_logs, {}, None, contact_id
                )

        agent_node_id = script.get("requestId", "") + "-agent"
        lex_nodes.append(agent_node_id)

        agent_transcript = "".join(m.get("content", "") for m in script.get("messages", []))

        tool = script.get("sessionState", {}).get("sessionAttributes", {}).get("Tool", "")
        if tool:
            intent_footer = f"Tool : {tool}"

        lex_dot.node(
            agent_node_id,
            label=get_node_label(
                "agent", "agent",
                wrap_transcript(agent_transcript),
                intent_footer, None
            ),
            shape='box',
            style='rounded,filled',
            color='lightgray',
            URL=str(json.dumps(script, indent=4, ensure_ascii=False))
        )

    lex_dot = add_edges(lex_dot, lex_nodes)
    apply_rank(lex_dot, lex_nodes)
    lex_dot.render(f"./virtual_env/lex_{contact_id}", format="dot", cleanup=True)

    return lex_nodes


def build_lex_hook_dot(contact_id, region):
    """Lex Hook Lambda 실행 내용을 시각화합니다."""
    lex_hook_path = f"./virtual_env/lex_hook_{contact_id}.json"
    if not os.path.isfile(lex_hook_path):
        return [], 0

    nodes = []
    error_count = 0

    lex_hook_dot = Digraph(comment="Lex Hook")
    lex_hook_dot.attr(rankdir="LR")

    with open(lex_hook_path, "r", encoding="utf-8") as f:
        function_logs = json.loads(f.read())

    xray_trace_ids = {log.get("xray_trace_id") for log in function_logs}

    for xray_trace_id in xray_trace_ids:
        lex_hook_dot, nodes, error_count = build_xray_dot(
            lex_hook_dot, nodes, error_count, xray_trace_id, region, function_logs, {}, None, contact_id
        )

    lex_hook_dot = add_edges(lex_hook_dot, nodes)
    apply_rank(lex_hook_dot, nodes)
    lex_hook_dot.render(f"./virtual_env/lex_hook_{contact_id}", format="dot", cleanup=True)

    return nodes, error_count


def build_transcript_dot(env, contact_id, region, instance_id):
    """통화 Transcript를 시각화합니다."""
    contact_transcript = get_analysis_object(env, contact_id, region, instance_id)
    if not contact_transcript:
        return []

    transcript_dot = Digraph(comment="Transcript")
    transcript_dot.attr(rankdir="LR")

    transcript_nodes = []
    temp_dup_set = set()

    for index, script in enumerate(contact_transcript):
        is_last = index + 1 == len(contact_transcript)
        next_same_participant = (
            not is_last
            and script.get("ParticipantId") == contact_transcript[index + 1].get("ParticipantId")
        )

        if next_same_participant:
            temp_dup_set.add(script.get("Id"))
            temp_dup_set.add(contact_transcript[index + 1].get("Id"))
        else:
            if temp_dup_set:
                temp_nodes = sorted(
                    [l for l in contact_transcript if l.get("Id") in temp_dup_set],
                    key=lambda x: x['BeginOffsetMillis']
                )
                script_contents = "/".join(n.get("Content") for n in temp_nodes)
                temp_dup_set = set()

                node_id = temp_nodes[0].get("Id")
                label = get_node_label(
                    temp_nodes[0].get("ParticipantId").lower(),
                    temp_nodes[0].get("ParticipantId").lower(),
                    wrap_transcript(script_contents), None, None
                )
                detail = str(json.dumps(temp_nodes, indent=4, ensure_ascii=False))
            else:
                node_id = script.get("Id")
                label = get_node_label(
                    script.get("ParticipantId").lower(),
                    script.get("ParticipantId").lower(),
                    wrap_transcript(script.get("Content")), None, None
                )
                detail = str(json.dumps(script, indent=4, ensure_ascii=False))

            transcript_nodes.append(node_id)
            transcript_dot.node(
                node_id,
                label=label,
                shape='box',
                style='rounded,filled',
                color='lightgray',
                URL=detail
            )

    transcript_dot = add_edges(transcript_dot, transcript_nodes)
    apply_rank(transcript_dot, transcript_nodes)
    transcript_dot.render(f"./virtual_env/transcript_{contact_id}", format="dot", cleanup=True)

    return transcript_nodes

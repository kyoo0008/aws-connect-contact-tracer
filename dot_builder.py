import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from graphviz import Digraph

from utils import fetch_logs
from flow_builder import build_main_flow
from lex_builder import build_lex_dot, build_lex_hook_dot
from graph_labels import get_image_label
from constants import ASSOCIATED_CONTACTS_FLAG


def build_main_contacts(selected_contact_id, associated_contacts, initiation_timestamp, region, log_group, env, instance_id):
    """여러 Associated Contact에 대한 메인 시각화 그래프를 생성합니다."""
    search_contacts = (
        associated_contacts["ContactSummaryList"]
        if ASSOCIATED_CONTACTS_FLAG
        else [l for l in associated_contacts["ContactSummaryList"] if l.get("ContactId") == selected_contact_id]
    )

    dot = Digraph("Amazon Connect Contact Flow", engine="neato", filename="contact_flow.gv")
    dot.attr(rankdir="LR")
    dot.node("start", label="Start", shape="Mdiamond")

    subgraphs = {}
    subgraph_nodes = {}
    subcontact_logs = {}
    subcontact_lambda_logs = {}
    subcontact_attr = {}
    root_contact_ids = {}

    def _fetch_contact_data(contact):
        """단일 contact에 대한 로그 및 속성을 가져오는 헬퍼 함수"""
        contact_id = contact.get("ContactId")
        if not contact_id:
            return None

        logs, lambda_logs, _ = fetch_logs(contact_id, initiation_timestamp, region, log_group, env, instance_id)

        connect_client = boto3.client("connect", region_name=region)
        response = connect_client.get_contact_attributes(
            InstanceId=instance_id,
            InitialContactId=contact_id
        )
        contact_attrs = response["Attributes"]

        data = []
        for k, v in contact_attrs.items():
            matched_log = None
            for log in logs:
                if log.get("ContactFlowModuleType") == "SetAttributes" and "Parameters" in log:
                    if log["Parameters"].get("Key") == k:
                        matched_log = log
                        break

            entry = {
                "k": k,
                "v": json.dumps(v, ensure_ascii=False),
                "c": matched_log.get("ContactFlowName") if matched_log else "",
                "i": matched_log.get("Identifier") if matched_log else ""
            }
            data.append(entry)

        return contact_id, logs, lambda_logs, data

    # Associated contact 로그를 병렬로 가져오기
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_fetch_contact_data, contact): contact
            for contact in search_contacts
        }
        for future in as_completed(futures):
            contact = futures[future]
            contact_id = contact.get("ContactId")
            try:
                result = future.result()
                if result is None:
                    continue
                cid, logs, lambda_logs, data = result
                subcontact_logs[cid] = logs
                subcontact_lambda_logs[cid] = lambda_logs
                subcontact_attr[cid] = data
            except Exception as e:
                print(f"Error fetching contact {contact_id}: {e}")

    for contact in search_contacts:
        contact_id = contact.get("ContactId")
        if not contact_id or contact_id not in subcontact_logs:
            continue

        channel = contact.get("Channel")
        label = (
            f"Contact Id : {contact_id} ✅ \nChannel : {channel}"
            if selected_contact_id == contact_id
            else f"Contact Id : {contact_id} \nChannel : {channel}"
        )
        subgraphs[contact_id] = Digraph(f"cluster_{contact_id}")
        subgraphs[contact_id].attr(label=label)

    # 다른 Contact에서 누락된 속성 값 보완
    for my_id, my_data in subcontact_attr.items():
        for entry in my_data:
            if entry["v"] and entry["c"] and entry["i"]:
                continue
            for other_id, other_data in subcontact_attr.items():
                if other_id == my_id:
                    continue
                for other_entry in other_data:
                    if other_entry["k"] == entry["k"] and other_entry["v"] == entry["v"]:
                        entry["v"] = other_entry["v"]
                        entry["c"] = other_entry["c"]
                        entry["i"] = other_entry["i"]
                        break

    def _build_contact_graph(contact):
        """단일 contact에 대해 그래프를 빌드하는 헬퍼 함수"""
        contact_id = contact.get("ContactId")
        if not contact_id or contact_id not in subcontact_logs:
            return None

        logs = subcontact_logs[contact_id]
        lambda_logs = subcontact_lambda_logs[contact_id]

        contact_graph, nodes = build_main_flow(logs, lambda_logs, contact_id, env, region)

        lex_nodes = build_lex_dot(contact_id, region)
        if lex_nodes:
            contact_graph.node(
                contact_id + "_lex_script",
                label=get_image_label(f"{os.getcwd()}/mnt/aws/Lex.png", "Lex", 30),
                shape="plaintext",
                URL=f"./virtual_env/lex_{contact_id}.dot"
            )

        lex_hook_nodes, _ = build_lex_hook_dot(contact_id, region)
        if lex_hook_nodes:
            contact_graph.node(
                contact_id + "_lex_hook",
                label=get_image_label(f"{os.getcwd()}/mnt/aws/Lambda.png", "Lex Hook", 30),
                shape="plaintext",
                URL=f"./virtual_env/lex_hook_{contact_id}.dot"
            )

        contact_graph.node(
            contact_id + "_attributes",
            label=get_image_label(f"{os.getcwd()}/mnt/img/SetAttributes.png", "Attributes", 30),
            shape="plaintext",
            URL=f'{subcontact_attr[contact_id]}'
        )

        return contact_id, contact_graph, nodes

    # Associated contact별 그래프 빌드 병렬 처리
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_build_contact_graph, contact): contact
            for contact in search_contacts
        }
        for future in as_completed(futures):
            try:
                result = future.result()
                if result is None:
                    continue
                cid, contact_graph, nodes = result
                subgraphs[cid].subgraph(contact_graph)
                subgraph_nodes[cid] = nodes
            except Exception as e:
                contact = futures[future]
                print(f"Error building graph for {contact.get('ContactId')}: {e}")

    for contact in search_contacts:
        contact_id = contact.get("ContactId")
        if not contact_id:
            continue

        prev_id = contact.get("PreviousContactId")
        related_id = contact.get("RelatedContactId")

        if not prev_id:
            root_contact_ids[contact_id] = contact.get("InitiationMethod")
        if related_id:
            root_contact_ids[contact_id] = contact.get("InitiationMethod")

        dot.subgraph(subgraphs[contact_id])

        try:
            if related_id and subgraph_nodes.get(related_id) and subgraph_nodes.get(contact_id):
                dot.edge(subgraph_nodes[related_id][-1], subgraph_nodes[contact_id][0], label="Related", dir="none")
            elif prev_id and prev_id in subgraphs and subgraph_nodes.get(prev_id) and subgraph_nodes.get(contact_id):
                dot.edge(subgraph_nodes[prev_id][-1], subgraph_nodes[contact_id][0], label=contact.get("InitiationMethod"))
        except Exception:
            print(traceback.format_exc())

    for contact_id, initiation_method in root_contact_ids.items():
        if subgraph_nodes.get(contact_id):
            dot.edge("start", subgraph_nodes[contact_id][0], label=initiation_method)

    return dot

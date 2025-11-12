#!/usr/bin/env python3
"""
Web-based UI for AWS Connect Contact Tracer
Flask 웹 애플리케이션으로 사용자 입력을 받고 결과를 표시
"""

from flask import Flask, render_template, request, jsonify, send_file, session
from flask_cors import CORS
import subprocess
import json
import os
import threading
import time
from pathlib import Path

app = Flask(__name__)
app.secret_key = os.urandom(24)
# JSON 응답에서 한글을 유니코드 이스케이프 없이 출력
app.config['JSON_AS_ASCII'] = False
CORS(app)

# 전역 변수로 상태 저장
app_state = {
    'status': 'idle',
    'message': '',
    'options': [],
    'result': None,
    'graph_file': None
}

@app.route('/')
def index():
    """메인 페이지"""
    return render_template('index.html')

@app.route('/api/init', methods=['POST'])
def initialize():
    """초기화 및 검색 옵션 제공"""
    app_state['status'] = 'selecting_search_option'
    app_state['options'] = [
        'ContactId',
        'Customer',
        'Agent',
        'History',
        'LambdaError',
        'ContactFlow',
        'DNIS'
    ]
    return jsonify({
        'status': 'success',
        'options': app_state['options'],
        'message': '검색할 기준을 선택하세요'
    })

@app.route('/api/search', methods=['POST'])
def search():
    """검색 실행"""
    data = request.json
    search_type = data.get('search_type')
    search_value = data.get('search_value', '')

    try:
        # 검색 실행
        if search_type == 'ContactId':
            result = execute_contact_search(search_value)
        elif search_type == 'Customer':
            result = execute_customer_search(search_value)
        elif search_type == 'Agent':
            result = execute_agent_search(search_value)
        elif search_type == 'History':
            result = execute_history_search()
        elif search_type == 'LambdaError':
            error_type = data.get('error_type', 'Lambda Error')
            result = execute_lambda_error_search(error_type)
        elif search_type == 'ContactFlow':
            result = execute_contactflow_search(search_value)
        elif search_type == 'DNIS':
            result = execute_dnis_search(search_value)
        else:
            return jsonify({'status': 'error', 'message': '잘못된 검색 타입입니다.'})

        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/select_contact', methods=['POST'])
def select_contact():
    """Contact ID 선택 및 상세 정보 조회"""
    data = request.json
    contact_id = data.get('contact_id')

    try:
        # Python 스크립트 실행
        result = execute_main_script(contact_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/graph/<path:filename>')
def serve_graph(filename):
    """생성된 그래프 파일 제공 (SVG, DOT, PNG 등)"""
    print(f"[DEBUG] Requested file: {filename}")

    # filename이 이미 virtual_env를 포함하고 있는지 확인
    if filename.startswith('virtual_env/'):
        file_path = filename
    else:
        file_path = os.path.join('virtual_env', filename)

    print(f"[DEBUG] Resolved file path: {file_path}")

    if os.path.exists(file_path):
        # 파일 확장자에 따라 MIME 타입 결정
        if filename.endswith('.svg'):
            mimetype = 'image/svg+xml'
        elif filename.endswith('.png'):
            mimetype = 'image/png'
        elif filename.endswith('.dot'):
            mimetype = 'text/plain'
        else:
            mimetype = 'application/octet-stream'

        print(f"[DEBUG] Serving file: {file_path} with mimetype: {mimetype}")
        return send_file(file_path, mimetype=mimetype)

    print(f"[DEBUG] File not found: {file_path}")
    return jsonify({'status': 'error', 'message': '파일을 찾을 수 없습니다.'}), 404

def execute_contact_search(contact_id):
    """ContactId로 직접 검색"""
    # UUID 검증
    import re
    uuid_regex = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    if not re.match(uuid_regex, contact_id):
        return {'status': 'error', 'message': '유효한 UUID 형식이 아닙니다.'}

    return {
        'status': 'success',
        'contact_id': contact_id,
        'message': 'Contact ID가 선택되었습니다.'
    }

def execute_customer_search(customer_info):
    """Customer 정보로 검색"""
    # 실제 검색 로직은 bash 스크립트의 함수를 Python으로 포팅 필요
    # 여기서는 간단히 처리
    return {
        'status': 'success',
        'message': f'Customer 검색: {customer_info}',
        'contacts': []  # DynamoDB 쿼리 결과
    }

def execute_agent_search(agent_info):
    """Agent 정보로 검색"""
    return {
        'status': 'success',
        'message': f'Agent 검색: {agent_info}',
        'contacts': []  # DynamoDB 쿼리 결과
    }

def execute_history_search():
    """History 검색"""
    venv_dir = 'virtual_env'
    history_files = []

    # .dot 파일 찾기
    for file in Path(venv_dir).rglob('*-main_flow_*.dot'):
        history_files.append({
            'contact_id': file.stem.split('main_flow_')[1] if 'main_flow_' in file.stem else '',
            'created_time': time.ctime(file.stat().st_mtime)
        })

    return {
        'status': 'success',
        'message': '기록된 Contact 목록',
        'contacts': history_files
    }

def execute_lambda_error_search(error_type):
    """Lambda Error 검색"""
    return {
        'status': 'success',
        'message': f'{error_type} 검색 중...',
        'contacts': []  # CloudWatch Logs 쿼리 결과
    }

def execute_contactflow_search(flow_name):
    """ContactFlow로 검색"""
    return {
        'status': 'success',
        'message': f'ContactFlow 검색: {flow_name}',
        'contacts': []  # CloudWatch Logs 쿼리 결과
    }

def execute_dnis_search(dnis):
    """DNIS로 검색"""
    return {
        'status': 'success',
        'message': f'DNIS 검색: {dnis}',
        'contacts': []  # CloudWatch Logs 쿼리 결과
    }

def execute_main_script(contact_id):
    """메인 Python 스크립트 실행"""
    try:
        # virtual_env 디렉토리 확인 및 생성
        venv_dir = Path('virtual_env')
        if not venv_dir.exists():
            venv_dir.mkdir(parents=True, exist_ok=True)
            print(f"[DEBUG] Created directory: {venv_dir}")

        # 환경 변수 가져오기
        instance_alias = os.getenv('INSTANCE_ALIAS', '')
        instance_id = os.getenv('INSTANCE_ID', '')
        region = os.getenv('AWS_REGION', 'ap-northeast-2')
        env = os.getenv('ENV', 'dev')

        # main.py 실행 (헤드리스 모드)
        # associated_contacts는 ContactSummaryList 키를 포함해야 함
        associated_contacts = json.dumps({"ContactSummaryList": []})

        cmd = [
            'python3', 'main.py',
            '--headless',  # GUI 없이 실행
            instance_alias,
            instance_id,
            contact_id,
            region,
            '',  # initiation_timestamp
            associated_contacts,  # {"ContactSummaryList": []}
            'ContactId',  # search_option
            env
        ]

        # 디버깅 정보 출력
        print(f"[DEBUG] Executing command: {' '.join(cmd)}")
        print(f"[DEBUG] Environment: INSTANCE_ALIAS={instance_alias}, INSTANCE_ID={instance_id}, REGION={region}, ENV={env}")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        # 결과 로깅
        print(f"[DEBUG] Return code: {result.returncode}")
        print(f"[DEBUG] STDOUT:\n{result.stdout}")
        print(f"[DEBUG] STDERR:\n{result.stderr}")

        if result.returncode == 0:
            # 생성된 그래프 파일 찾기 (DOT 및 SVG)
            dot_file = find_graph_file(contact_id, env, 'dot')
            svg_file = find_graph_file(contact_id, env, 'svg')

            return {
                'status': 'success',
                'message': '처리 완료',
                'output': result.stdout,
                'graph_file': dot_file,
                'svg_file': svg_file
            }
        else:
            # 더 자세한 오류 메시지 제공
            error_msg = f"스크립트 실행 실패 (Exit Code: {result.returncode})\n\n"
            if result.stderr:
                error_msg += f"오류 내용:\n{result.stderr}\n\n"
            if result.stdout:
                error_msg += f"출력 내용:\n{result.stdout}"

            return {
                'status': 'error',
                'message': error_msg,
                'stdout': result.stdout,
                'stderr': result.stderr,
                'returncode': result.returncode
            }
    except subprocess.TimeoutExpired:
        return {'status': 'error', 'message': '실행 시간 초과 (300초)'}
    except Exception as e:
        import traceback
        return {
            'status': 'error',
            'message': f'예외 발생: {str(e)}\n\n상세 정보:\n{traceback.format_exc()}'
        }

def find_graph_file(contact_id, env, extension='dot'):
    """생성된 그래프 파일 찾기"""
    venv_dir = Path('virtual_env')
    expected_filename = f'{env}-main_flow_{contact_id}.{extension}'
    expected_path = venv_dir / expected_filename

    print(f"[DEBUG] Looking for {extension.upper()} file: {expected_path}")

    # 정확한 경로로 파일 확인
    if expected_path.exists():
        print(f"[DEBUG] Found {extension.upper()} file: {expected_path}")
        return str(expected_path)

    # 디렉토리 내 모든 파일 나열 (디버깅용)
    if venv_dir.exists():
        print(f"[DEBUG] Files in {venv_dir}:")
        for file in venv_dir.iterdir():
            print(f"[DEBUG]   - {file.name}")
    else:
        print(f"[DEBUG] Directory {venv_dir} does not exist!")

    return None

if __name__ == '__main__':
    # 환경 변수에서 포트 가져오기 (기본값: 5000)
    port = int(os.getenv('FLASK_PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

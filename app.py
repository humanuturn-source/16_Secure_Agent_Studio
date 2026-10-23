import os
import io
import re
import ast
import time
import shutil
import tempfile
import docker
import httpx
from flask import Flask, render_template, request, jsonify, session

# Initialize Flask application and configure secret key for sessions
app = Flask(__name__)
app.secret_key = "secure-agent-studio-secret-key"

# Configuration for Ollama local LLM and file storage
OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "gemma4"
UPLOAD_FOLDER = tempfile.mkdtemp()
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


def query_ollama(prompt: str, history: list) -> str:
    """
    Sends a user prompt and conversation history to the local Ollama instance.

    Args:
        prompt (str): The new prompt or follow-up instruction.
        history (list): Conversation history containing previous system, user, and assistant messages.

    Returns:
        str: The content of the assistant response from Gemma 4.
    """
    # Step 1: Append user prompt to current chat history
    messages = history + [{"role": "user", "content": prompt}]
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.2}  # Low temperature for deterministic code generation
    }
    
    # Step 2: Issue HTTP POST request to Ollama chat API
    response = httpx.post(OLLAMA_URL, json=payload, timeout=120.0)
    response.raise_for_status()
    
    # Step 3: Extract and return response message text
    return response.json()["message"]["content"]


def extract_imports(code: str) -> list[str]:
    """
    Parses Python code using AST to identify required non-standard library import modules.

    Args:
        code (str): The Python source code string to analyze.

    Returns:
        list[str]: A list of third-party package names detected in the code.
    """
    # Step 1: Parse code into an Abstract Syntax Tree
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    
    imports = set()
    # List of standard Python modules to filter out
    stdlib_modules = {
        'os', 'sys', 'json', 're', 'math', 'datetime', 'collections', 
        'itertools', 'functools', 'pathlib', 'tempfile', 'shutil', 
        'subprocess', 'ast', 'logging', 'typing', 'http', 'urllib', 'io', 'csv'
    }
    
    # Step 2: Traverse tree nodes to collect 'import' and 'from ... import' modules
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name.split('.')[0]
                if mod not in stdlib_modules:
                    imports.add(mod)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mod = node.module.split('.')[0]
                if mod not in stdlib_modules:
                    imports.add(mod)
                    
    return list(imports)


def execute_code_in_docker(code: str, input_file_paths: list[str] = None) -> tuple[str, str, int, str, dict]:
    """
    Executes Python code safely inside an isolated, network-disabled Docker container.

    Args:
        code (str): Python script code to execute.
        input_file_paths (list[str], optional): Local file paths to mount into the container.

    Returns:
        tuple[str, str, int, str, dict]: Execution logs, output file content, exit status code, 
                                        output filename, and performance metrics (runtime, peak RAM).
    """
    client = docker.from_env()
    
    # Step 1: Detect third-party imports in the code
    required_packages = extract_imports(code)
    base_image = "secure-python-sandbox:latest"
    image_to_use = base_image

    # Step 2: Create a temporary directory for script and output files
    sandbox_dir = tempfile.mkdtemp()
    script_path = os.path.join(sandbox_dir, "script.py")
    
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code)

    try:
        # Step 3: Dynamically build an ephemeral Docker image if third-party packages are needed
        if required_packages:
            package_mapping = {'bs4': 'beautifulsoup4', 'cv2': 'opencv-python', 'sklearn': 'scikit-learn'}
            install_pkgs = [package_mapping.get(pkg, pkg) for pkg in required_packages]
            dockerfile_content = f"FROM {base_image}\nRUN pip install --no-cache-dir {' '.join(install_pkgs)}\n"
            file_obj = io.BytesIO(dockerfile_content.encode('utf-8'))
            client.images.build(fileobj=file_obj, rm=True, tag="ephemeral-sandbox:latest")
            image_to_use = "ephemeral-sandbox:latest"

        # Step 4: Map script and input files to container volumes
        volumes = {os.path.abspath(script_path): {'bind': '/app/script.py', 'mode': 'ro'}}
        
        if input_file_paths:
            for idx, path in enumerate(input_file_paths):
                if os.path.exists(path):
                    ext = os.path.splitext(path)[1]
                    container_path = f"/app/input_{idx+1}{ext}"
                    volumes[os.path.abspath(path)] = {'bind': container_path, 'mode': 'ro'}
            
        # Step 5: Start isolated Docker container (offline mode, 512MB RAM cap)
        start_time = time.time()
        container = client.containers.run(
            image=image_to_use,
            command="python /app/script.py",
            volumes={**volumes, os.path.abspath(sandbox_dir): {'bind': '/app/data', 'mode': 'rw'}},
            working_dir="/app",
            network_mode="none",
            mem_limit="512m",
            detach=True,
            stdout=True,
            stderr=True
        )
        
        # Step 6: Collect container resource statistics
        stats_data = {}
        try:
            stats_data = container.stats(stream=False)
        except Exception:
            pass

        # Step 7: Wait for container completion and extract timing/execution logs
        result = container.wait(timeout=30)
        end_time = time.time()
        runtime_duration = end_time - start_time
        
        logs = container.logs(stdout=True, stderr=True).decode('utf-8')
        exit_code = result.get("StatusCode", -1)
        
        # Step 8: Calculate peak memory consumption
        peak_memory_mb = 0.0
        try:
            usage = stats_data.get("memory_stats", {}).get("usage", 0)
            peak_memory_mb = usage / (1024 * 1024)
        except Exception:
            pass

        # Clean up container instance
        container.remove()
        
        metrics = {
            "exit_code": exit_code,
            "runtime": f"{runtime_duration:.3f}s",
            "memory": f"{peak_memory_mb:.2f} MB / 512 MB"
        }

        # Step 9: Read generated output file contents from sandbox directory
        final_local_filename = ""
        output_content = ""
        for filename in ["result.json", "result.csv", "output.txt"]:
            sandbox_output_path = os.path.join(sandbox_dir, filename)
            if os.path.exists(sandbox_output_path):
                final_local_filename = filename
                with open(sandbox_output_path, "r", encoding="utf-8") as out_f:
                    output_content = out_f.read()
                break
            
        return logs, output_content, exit_code, final_local_filename, metrics
        
    except Exception as e:
        return "", str(e), -1, "", {"exit_code": -1, "runtime": "0s", "memory": "0 MB"}
    finally:
        # Step 10: Clean up local temporary sandbox directory
        if os.path.exists(sandbox_dir):
            shutil.rmtree(sandbox_dir, ignore_errors=True)


def extract_python_code(content: str) -> str:
    """
    Extracts executable Python code wrapped inside markdown code blocks.

    Args:
        content (str): Text response returned by the LLM.

    Returns:
        str: Extracted Python code or an empty string if no block is found.
    """
    match = re.search(r"```python(.*?)```", content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""


@app.route('/')
def index():
    """Renders the main dashboard page and resets session state."""
    session.clear()
    return render_template('index.html')


@app.route('/api/run', methods=['POST'])
def run_agent():
    """
    API endpoint to handle uploaded files, initialize conversation history,
    and execute the agent task with an iterative self-correction loop.
    """
    # Step 1: Parse uploaded files and initial prompt
    uploaded_files = request.files.getlist('files')
    prompt = request.form.get('prompt', '').strip()
    
    if not uploaded_files or not prompt:
        return jsonify({"error": "Please provide files and a prompt instruction."}), 400

    # Step 2: Save uploaded files locally to temporary upload folder
    file_paths = []
    for file in uploaded_files:
        if file.filename:
            path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(path)
            file_paths.append(path)

    session['file_paths'] = file_paths

    # Step 3: Format mounted file references for prompt system instructions
    file_list_str = "\n".join([f"- /app/input_{idx+1}{os.path.splitext(p)[1]} (source: {os.path.basename(p)})" for idx, p in enumerate(file_paths)])
    
    system_instruction = (
        "You are a secure coding assistant. Write clean Python code wrapped strictly inside ```python ... ``` blocks to solve the task. "
        f"The following input files are mounted inside the container:\n{file_list_str}\n"
        "Note: Use the 'pypdf' library to extract text from each mounted file path (e.g., '/app/input_1.pdf', '/app/input_2.pdf'). "
        "OUTPUT FORMAT RULE: If the prompt requests a structured output format like JSON or CSV, your Python script must write the structured data directly to `/app/data/result.json` or `/app/data/result.csv`. "
        "Otherwise, write the summary report to `/app/data/output.txt`. "
        "CRITICAL PARSING LOGIC:\n"
        "```python\n"
        "from pypdf import PdfReader\n"
        "import re\n\n"
        "def get_total_comp(pdf_path):\n"
        "    reader = PdfReader(pdf_path)\n"
        "    full_text = ''\n"
        "    for page in reader.pages:\n"
        "        full_text += page.extract_text() + '\\n'\n"
        "    matches = re.findall(r'Total Compensation.*?\\$([0-9,]+\\.\\d{2})', full_text, re.IGNORECASE)\n"
        "    if not matches:\n"
        "        all_amounts = re.findall(r'\\$([0-9,]+\\.\\d{2})', full_text)\n"
        "        return all_amounts[-1] if all_amounts else '0.00'\n"
        "    return matches[0]\n"
        "```\n"
        "Perform the analysis, ensure safe execution within the sandbox, and write the final output to the correct path in `/app/data/`."
    )

    history = [{"role": "system", "content": system_instruction}]
    session['history'] = history

    current_prompt = prompt
    max_iterations = 3
    output_content = ""
    logs = ""
    metrics = {}
    filename = ""

    # Step 4: Execute agent self-correction loop (up to max_iterations)
    for _ in range(max_iterations):
        response_text = query_ollama(current_prompt, history)
        history.append({"role": "assistant", "content": response_text})
        
        code = extract_python_code(response_text)
        if not code:
            current_prompt = "Please format your code properly inside a ```python ``` block and ensure you write output to the correct file in '/app/data/'."
            continue
            
        logs, output_content, exit_code, filename, metrics = execute_code_in_docker(code, file_paths)
        
        # Step 5: Break on successful execution, or feed error logs back to the LLM
        if exit_code == 0 and filename:
            break
        else:
            current_prompt = f"The code failed with exit code {exit_code}.\nOutput/Error logs:\n{logs}\nPlease analyze the error, fix the bug, and provide updated Python code."

    session['history'] = history
    return jsonify({
        "status": "success" if filename else "error",
        "output_filename": filename,
        "output_content": output_content,
        "logs": logs,
        "metrics": metrics,
        "user_prompt": prompt
    })


@app.route('/api/chat', methods=['POST'])
def chat_agent():
    """
    API endpoint to handle conversational follow-up instructions, retaining
    session conversation context and input file associations.
    """
    # Step 1: Parse user chat message and retrieve active session history
    data = request.get_json()
    message = data.get('message', '').strip()
    file_paths = session.get('file_paths', [])
    history = session.get('history', [])

    if not message:
        return jsonify({"error": "Empty message."}), 400

    max_iterations = 3
    output_content = ""
    logs = ""
    metrics = {}
    filename = ""

    # Step 2: Execute follow-up agent self-correction loop
    for _ in range(max_iterations):
        response_text = query_ollama(message, history)
        history.append({"role": "assistant", "content": response_text})
        
        code = extract_python_code(response_text)
        if not code:
            message = "Please format your code properly inside a ```python ``` block and write output to '/app/data/result.json', '/app/data/result.csv', or '/app/data/output.txt'."
            continue
            
        logs, output_content, exit_code, filename, metrics = execute_code_in_docker(code, file_paths)
        
        # Step 3: Check execution results and retry if errors occur
        if exit_code == 0 and filename:
            break
        else:
            message = f"The follow-up code failed with exit code {exit_code}.\nOutput/Error logs:\n{logs}\nPlease fix the bug and provide updated Python code."

    # Step 4: Update session history and return JSON payload
    session['history'] = history
    return jsonify({
        "status": "success" if filename else "error",
        "output_filename": filename,
        "output_content": output_content,
        "logs": logs,
        "metrics": metrics
    })


if __name__ == '__main__':
    # Step 1: Launch local Flask development web server on port 5000
    app.run(debug=True, port=5000)

import os
import json
import subprocess
import re
import time
import shutil

# -------------------- CONFIG --------------------
WORKSPACE = "/Users/haze/meshy-gen3"
SPEC_FILE = "/Users/haze/meshy-gen3/spec.json"
MODEL = "qwen3-coder:30b"
EXECUTE_SCRIPTS = True
MAX_RETRIES = 3
# ------------------------------------------------

os.makedirs(WORKSPACE, exist_ok=True)

# -------------------- LOAD JSON SPEC --------------------
if not os.path.exists(SPEC_FILE):
    raise FileNotFoundError(f"spec.json not found at: {SPEC_FILE}")

with open(SPEC_FILE, "r", encoding="utf-8") as f:
    spec_data = json.load(f)

# Extract phases from the spec
phases = None
for approach in spec_data.get("development_approach", {}).get("approach", []):
    if approach.get("name") == "Phased_Implementation":
        phases = approach.get("phases", {}).get("phase", [])
        break

if not phases:
    raise RuntimeError("No phases found in spec.json under development_approach/Phased_Implementation")

print(f"[OK] Loaded spec.json — {len(phases)} phases found")

# -------------------- UTILS --------------------

def ollama_generate(prompt: str, attempt: int):
    """Call Ollama using 'ollama run', return raw text output."""
    print(f"[ollama] Running attempt {attempt}...")

    try:
        result = subprocess.run(
            ["ollama", "run", MODEL],
            input=prompt,
            text=True,
            capture_output=True,
            timeout=300  # 5 minute timeout
        )
        return result.stdout.strip()

    except subprocess.TimeoutExpired:
        print(f"[ollama] Timeout on attempt {attempt}")
        return None
    except subprocess.CalledProcessError as e:
        print(f"[ollama] Error on attempt {attempt}")
        print(e.stderr)
        return None


def extract_json_block(text: str):
    """
    Extract the FIRST valid JSON array from messy model output.
    Returns Python object or None.
    """
    # Find first '[' and last ']'
    start = text.find("[")
    end = text.rfind("]")

    if start == -1 or end == -1:
        return None

    candidate = text[start : end + 1]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def write_files(file_list):
    written_files = []
    for file_info in file_list:
        path = os.path.join(WORKSPACE, file_info["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            f.write(file_info["content"])

        print(f"[written] {path}")
        written_files.append(path)

    return written_files


def extract_python_dependencies(file_path):
    """Extract dependencies from Python file, filtering out empty/invalid module names."""
    deps = set()
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                # Match import statements
                match = re.match(r'^\s*(?:import|from)\s+([\w\.]+)', line)
                if match:
                    module_name = match.group(1).split(".")[0].strip()
                    # Only add non-empty module names
                    if module_name and not module_name.startswith('_'):
                        deps.add(module_name)
    except Exception as e:
        print(f"[warn] Could not parse dependencies from {file_path}: {e}")
    
    return deps


def is_stdlib_module(module_name):
    """Check if module is part of Python standard library."""
    stdlib_modules = {
        'os', 'sys', 'json', 're', 'time', 'datetime', 'math', 'random',
        'collections', 'itertools', 'functools', 'pathlib', 'subprocess',
        'threading', 'multiprocessing', 'logging', 'argparse', 'io',
        'shutil', 'glob', 'pickle', 'copy', 'dataclasses', 'typing',
        'abc', 'struct', 'array', 'queue', 'enum', 'traceback'
    }
    return module_name in stdlib_modules


def safe_execute(file_path):
    """Safely execute a file with proper dependency handling."""
    ext = os.path.splitext(file_path)[1]

    try:
        if ext == ".py":
            # Skip __init__.py files from execution (they're just imports)
            if os.path.basename(file_path) == "__init__.py":
                print(f"[skip] Skipping __init__.py: {file_path}")
                return

            # Extract and install dependencies
            deps = extract_python_dependencies(file_path)
            for dep in deps:
                # Skip empty strings and stdlib modules
                if not dep or is_stdlib_module(dep):
                    continue
                
                try:
                    __import__(dep)
                except ModuleNotFoundError:
                    print(f"[pip] Installing missing dependency: {dep}")
                    try:
                        subprocess.run(
                            ["pip3", "install", dep],
                            check=True,
                            capture_output=True
                        )
                        print(f"[pip] Successfully installed {dep}")
                    except subprocess.CalledProcessError as e:
                        print(f"[pip] Failed to install {dep}: {e}")
                        # Continue anyway - might not be a real package
                except Exception as e:
                    print(f"[warn] Error checking module {dep}: {e}")

            # Try to execute the Python file
            print(f"[exec] Attempting to execute: {file_path}")
            result = subprocess.run(
                ["python3", file_path],
                cwd=WORKSPACE,
                capture_output=True,
                text=True,
                timeout=60  # 1 minute timeout per script
            )
            
            if result.returncode == 0:
                print(f"[exec] ✓ {file_path} executed successfully")
                if result.stdout:
                    print(f"[stdout] {result.stdout}")
            else:
                print(f"[exec] ✗ {file_path} failed with return code {result.returncode}")
                if result.stderr:
                    print(f"[stderr] {result.stderr}")

        elif ext == ".sh":
            os.chmod(file_path, 0o755)
            result = subprocess.run(
                ["bash", file_path],
                cwd=WORKSPACE,
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode == 0:
                print(f"[exec] ✓ {file_path} executed successfully")
            else:
                print(f"[exec] ✗ {file_path} failed")
                print(result.stderr)

        elif ext == ".js":
            result = subprocess.run(
                ["node", file_path],
                cwd=WORKSPACE,
                capture_output=True,
                text=True,
                timeout=60
            )
            if result.returncode == 0:
                print(f"[exec] ✓ {file_path} executed successfully")
            else:
                print(f"[exec] ✗ {file_path} failed")
                print(result.stderr)

    except subprocess.TimeoutExpired:
        print(f"[exec] ✗ {file_path} timed out")
    except Exception as e:
        print(f"[exec] ✗ Error executing {file_path}: {e}")


# -------------------- MAIN LOOP --------------------

all_written_files = set()  # Collect all written files for final execution

for idx, phase in enumerate(phases, start=1):
    print(f"\n{'='*80}")
    print(f"=== Processing Phase {idx}/{len(phases)}: {phase} ===")
    print(f"{'='*80}\n")

    # Collect existing files to include in prompt (for continuity)
    existing_files_str = ""
    file_count = 0
    for root_dir, dirs, files in os.walk(WORKSPACE):
        # Skip __pycache__ and other cache directories
        dirs[:] = [d for d in dirs if not d.startswith('__') and d not in ['.git', 'node_modules']]
        
        for fname in files:
            if fname.endswith(('.py', '.json', '.sh', '.js')) and not fname.startswith('.'):
                full_path = os.path.join(root_dir, fname)
                rel_path = os.path.relpath(full_path, WORKSPACE)
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    existing_files_str += f"\n[{rel_path}]\n{content}\n[/{rel_path}]\n"
                    file_count += 1
                except Exception as e:
                    print(f"[warn] Could not read {rel_path}: {e}")

    if file_count > 0:
        print(f"[info] Found {file_count} existing files")

    # Build instruction with phase, full spec, and existing files
    spec_str = json.dumps(spec_data, indent=2)
    instruction = f"""Implement the '{phase}' phase of the project as described in the following JSON specification.

IMPORTANT INSTRUCTIONS:
- Generate ONLY the files needed for THIS phase
- If updating existing files, provide the COMPLETE new content
- Ensure all imports use correct relative paths
- Add proper error handling and type hints
- Include docstrings for all functions and classes

JSON Specification:
{spec_str}"""

    if existing_files_str:
        instruction += f"\n\nExisting project files (update or reference as needed):\n{existing_files_str}"

    print(f"[info] Instruction length: {len(instruction)} characters\n")

    # -------- Build strict prompt --------
    model_prompt = f"""You are an autonomous code generator implementing a phase-based development project.

{instruction}

You MUST output ONLY valid JSON in this EXACT format:
[
  {{
    "path": "relative/path/to/file.ext",
    "content": "complete file contents here"
  }}
]

CRITICAL RULES:
1. Output MUST be a valid JSON array
2. NO text before the opening [
3. NO text after the closing ]
4. NO markdown code blocks
5. NO explanations or comments outside the JSON
6. Properly escape all quotes and special characters in content
7. For file updates, include the COMPLETE new file content
8. Only generate files relevant to the current phase

Begin JSON output now:"""

    # -------- LLM with retries --------
    files_json = None

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"\n[ollama] Attempt {attempt}/{MAX_RETRIES}")
        raw_output = ollama_generate(model_prompt, attempt)

        if not raw_output:
            print("[error] No output from model")
            if attempt < MAX_RETRIES:
                print(f"[retry] Waiting 3 seconds before retry...")
                time.sleep(3)
            continue

        # Save raw output for debugging
        debug_file = os.path.join(WORKSPACE, f"_debug_phase{idx}_attempt{attempt}.txt")
        with open(debug_file, "w") as f:
            f.write(raw_output)
        print(f"[debug] Raw output saved to {debug_file}")

        json_obj = extract_json_block(raw_output)

        if json_obj is not None and isinstance(json_obj, list):
            print(f"[OK] Valid JSON array extracted with {len(json_obj)} files")
            files_json = json_obj
            break
        else:
            print(f"[warn] Could not extract valid JSON array")
            if attempt < MAX_RETRIES:
                print(f"[retry] Waiting 3 seconds before retry...")
                time.sleep(3)

    if files_json is None:
        print(f"\n[FATAL] Failed to get valid JSON after {MAX_RETRIES} attempts")
        print(f"[FATAL] Check debug files in {WORKSPACE}/_debug_phase{idx}_*.txt")
        print(f"[FATAL] Skipping phase {idx}\n")
        continue

    # -------- Write files --------
    print(f"\n[info] Writing {len(files_json)} files...")
    written = write_files(files_json)
    all_written_files.update(written)
    print(f"[info] Phase {idx} complete - {len(written)} files written\n")

# -------- Optional execution (after all phases) --------
if EXECUTE_SCRIPTS and all_written_files:
    print("\n" + "="*80)
    print("=== Executing all generated scripts ===")
    print("="*80 + "\n")
    
    # Execute in order: .py, then .sh, then .js
    for ext in [".py", ".sh", ".js"]:
        matching_files = sorted([f for f in all_written_files if f.endswith(ext)])
        if matching_files:
            print(f"\n[exec] Executing {len(matching_files)} {ext} files...\n")
            for fpath in matching_files:
                safe_execute(fpath)
else:
    print("\n[info] Script execution disabled or no files to execute")

print("\n" + "="*80)
print("=== All phases completed ===")
print("="*80)
print(f"\nTotal files written: {len(all_written_files)}")
print(f"Workspace: {WORKSPACE}")
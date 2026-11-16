# AI Model Coding Performance Test Prompt (Simplified)

## Instructions for the Model

**You are being evaluated on your coding ability. Please follow these instructions carefully:**

1. **Solve the problem below** by writing complete, working Python code **to a file in the repository**.
2. **Track your performance**: Record the **start time** when you begin, and the **end time** when you submit your final solution.
3. **Count your turns**: Each message you send counts as one turn. Report the total number of turns used.
4. **Output format**: At the end of your solution, include a summary block with:
   - `Time taken: [X minutes Y seconds]`
   - `Turns used: [N]`
   - `Final solution: [brief description]`

---

## Problem: Parse and Validate Semantic Version Strings

Implement a function `parse_semver(version: str) -> dict` that parses a semantic version string and returns a dictionary with the components.

### Requirements

**Input format**: `MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]`

Examples:
- `"1.2.3"` → `{"major": 1, "minor": 2, "patch": 3, "prerelease": None, "build": None}`
- `"2.0.0-alpha.1"` → `{"major": 2, "minor": 0, "patch": 0, "prerelease": "alpha.1", "build": None}`
- `"1.0.0+build.123"` → `{"major": 1, "minor": 0, "patch": 0, "prerelease": None, "build": "build.123"}`
- `"3.1.4-rc.2+sha.abc123"` → `{"major": 3, "minor": 1, "patch": 4, "prerelease": "rc.2", "build": "sha.abc123"}`

**Rules**:
- MAJOR, MINOR, PATCH must be non-negative integers (no leading zeros unless the number is 0)
- PRERELEASE: optional, alphanumeric and hyphens/dots, no leading/trailing dots/hyphens
- BUILD: optional, alphanumeric and hyphens/dots, no leading/trailing dots/hyphens
- Return `{"error": "description"}` for invalid versions

### Test Cases to Handle

```python
test_cases = [
    "1.0.0",
    "2.10.3",
    "0.0.1",
    "1.2.3-alpha",
    "1.2.3-alpha.1",
    "1.2.3-0.3.7",
    "1.2.3-x.7.z.92",
    "1.2.3+build",
    "1.2.3+build.123",
    "1.2.3-alpha+build",
    "1.2.3-alpha.1+build.456",
    "1.0.0-rc.1+sha.abc",
    # Invalid cases:
    "1",              # missing minor.patch
    "1.2",            # missing patch
    "1.2.3.4",        # too many parts
    "1.2.x",          # non-numeric
    "01.2.3",         # leading zero
    "1.02.3",         # leading zero
    "1.2.03",         # leading zero
    "1.2.3-",         # empty prerelease
    "1.2.3+",         # empty build
    "1.2.3-.1",       # leading dot in prerelease
    "1.2.3-1.",       # trailing dot in prerelease
    "",               # empty string
]
```

### Function Signature

```python
def parse_semver(version: str) -> dict:
    """
    Parse a semantic version string.
    
    Args:
        version: Version string in semver format
        
    Returns:
        Dict with keys: major, minor, patch, prerelease, build
        Or dict with key: error
    """
    pass
```

---

## REQUIRED EXECUTION WORKFLOW

**You MUST follow this exact workflow - do not just output code to chat:**

### Step 1: Create the Solution File
Write your complete Python solution to: `/mnt/c/Users/russe/code/llama.cpp-model-manager/semver_solution.py`

The file must contain:
1. The `parse_semver()` function implementation
2. A `main()` function that runs all test cases and prints results
3. The `if __name__ == "__main__": main()` guard

### Step 2: Validate Python Syntax
Run this command to verify syntax is valid:
```bash
python3 -m py_compile /mnt/c/Users/russe/code/llama.cpp-model-manager/semver_solution.py
```
If this fails, fix the syntax error and re-run until it passes.

### Step 3: Execute and Validate
Run the solution and capture output:
```bash
cd /mnt/c/Users/russe/code/llama.cpp-model-manager && python3 semver_solution.py
```

### Step 4: Verify All Test Cases Pass
Compare your output against expected results. Each test case must produce the correct dictionary. If any test case fails, fix your implementation and re-run Steps 2-3.

### Step 5: Report Results
Only after ALL test cases pass, include the performance summary block in your final response.

---

## Expected Output Format

Your `semver_solution.py` should produce output like:

```
'1.0.0'                       → {'major': 1, 'minor': 0, 'patch': 0, 'prerelease': None, 'build': None}
'2.10.3'                      → {'major': 2, 'minor': 10, 'patch': 3, 'prerelease': None, 'build': None}
...
'1'                           → {'error': 'missing minor and patch version'}
...
```

---

## Evaluation Criteria

| Criterion | Weight |
|-----------|--------|
| Correctness (all test cases pass) | 50% |
| Code quality (clean, readable, handles edge cases) | 30% |
| Proper error handling | 20% |

---

## Submission Requirements

Your final response MUST include:

1. **Confirmation that you created `/mnt/c/Users/russe/code/llama.cpp-model-manager/semver_solution.py`**
2. **Proof that `python3 -m py_compile semver_solution.py` passed** (no syntax errors)
3. **Proof that `python3 semver_solution.py` executed and all test cases passed**
4. **The performance summary block:**

```
=== PERFORMANCE SUMMARY ===
Time taken: [X minutes Y seconds]
Turns used: [N]
Final solution: [brief description of your approach]
```

---

## Notes

- Python standard library only (`re` module allowed)
- Target Python 3.10+
- Focus on correctness and clarity
- Should complete in **5-10 minutes** for a 9B model
- **FAILURE TO CREATE AND EXECUTE THE FILE WILL RESULT IN AUTOMATIC FAILURE**
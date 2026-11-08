import json
from src.memory.wm_formatting import format_context_summary
from src.memory.working_memory import LogEntry, EntryType


def entry(type: EntryType, content: str, thread_id: str = "t1") -> LogEntry:
    return LogEntry(thread_id=thread_id, type=type, content=content)


def tool_call(tool: str, input_str: str = "") -> LogEntry:
    return entry(EntryType.TOOL_CALL, json.dumps({"tool": tool, "input": input_str}))


def tool_result(tool: str, result: str, success: bool = True, duration_ms: int = 100) -> LogEntry:
    return entry(
        EntryType.TOOL_RESULT,
        json.dumps(
            {
                "tool": tool,
                "result": result,
                "success": success,
                "duration_ms": duration_ms,
            }
        ),
    )


def checkpoint(label: str, status: str, output: str = "") -> LogEntry:
    return entry(
        EntryType.CHECKPOINT,
        json.dumps(
            {
                "node_id": "n1",
                "label": label,
                "status": status,
                "output": output,
            }
        ),
    )


def system_event(data: dict) -> LogEntry:
    return entry(EntryType.SYSTEM_EVENT, json.dumps(data))


class TestEmptyInput:
    def test_empty_list(self):
        assert format_context_summary([]) == ""

    def test_none_items_treated_as_empty(self):
        assert format_context_summary([], max_items=5) == ""


class TestSectionStructure:
    def test_has_section_headers(self):
        items = [
            entry(EntryType.MESSAGE_USER, "build a REST API"),
            tool_call("bash", "ls -la"),
            tool_result("bash", "file1.py file2.py", success=True),
            entry(EntryType.MESSAGE_ASSISTANT, "I've listed the files"),
        ]
        result = format_context_summary(items, max_items=10)
        assert "# Current State" in result
        assert "# Task Activity" in result
        assert "# Tool Operations" in result

    def test_empty_sections_omitted(self):
        items = [entry(EntryType.MESSAGE_USER, "hello")]
        result = format_context_summary(items, max_items=10)
        assert "# Errors" not in result
        assert "# Session Events" not in result


class TestToolPairing:
    def test_adjacent_call_result_paired(self):
        items = [
            tool_call("file_read", "/src/main.py"),
            tool_result("file_read", "def main(): pass", success=True, duration_ms=50),
        ]
        result = format_context_summary(items, max_items=10)
        assert "file_read" in result
        assert "50ms" in result
        assert "[ok]" in result

    def test_failed_tool_appears_in_errors(self):
        items = [
            tool_call("bash", "rm -rf /"),
            tool_result("bash", "Permission denied", success=False, duration_ms=10),
        ]
        result = format_context_summary(items, max_items=10)
        assert "[FAILED]" in result
        assert "# Errors & Corrections" in result
        assert "Permission denied" in result

    def test_unpaired_result_still_shown(self):
        items = [
            tool_result("bash", "output text", success=True, duration_ms=200),
        ]
        result = format_context_summary(items, max_items=10)
        assert "bash" in result
        assert "200ms" in result


class TestFileExtraction:
    def test_file_paths_from_tool_input(self):
        items = [
            tool_call("file_read", "/src/models/user.py"),
            tool_result("file_read", "class User: pass", success=True),
        ]
        result = format_context_summary(items, max_items=10)
        assert "# Files Touched" in result
        assert "/src/models/user.py" in result

    def test_deduplicates_file_paths(self):
        items = [
            tool_call("file_read", "/src/main.py"),
            tool_result("file_read", "code", success=True),
            tool_call("file_read", "/src/main.py"),
            tool_result("file_read", "code again", success=True),
        ]
        result = format_context_summary(items, max_items=10)
        files_section = (
            result.split("# Files Touched")[1].split("\n#")[0]
            if "# Files Touched" in result
            else ""
        )
        assert files_section.count("/src/main.py") == 1


class TestCheckpointsAndUserInput:
    def test_checkpoint_in_decisions(self):
        items = [checkpoint("API routes built", "completed", "3 endpoints created")]
        result = format_context_summary(items, max_items=10)
        assert "# Decisions & Checkpoints" in result
        assert "API routes built" in result
        assert "completed" in result

    def test_user_input_request_response(self):
        items = [
            entry(
                EntryType.USER_INPUT_REQUEST,
                json.dumps({"question": "Should I use REST or GraphQL?", "node_id": "n1"}),
            ),
            entry(EntryType.USER_INPUT_RESPONSE, json.dumps({"response": "Use REST"})),
        ]
        result = format_context_summary(items, max_items=10)
        assert "Asked user" in result
        assert "REST or GraphQL" in result
        assert "User answered" in result
        assert "Use REST" in result


class TestCurrentState:
    def test_last_messages_in_current_state(self):
        items = [
            entry(EntryType.MESSAGE_USER, "first request"),
            entry(EntryType.MESSAGE_ASSISTANT, "first response"),
            entry(EntryType.MESSAGE_USER, "second request"),
            entry(EntryType.MESSAGE_ASSISTANT, "second response"),
        ]
        result = format_context_summary(items, max_items=10)
        assert "Last user request: second request" in result
        assert "Last assistant response: second response" in result


class TestSystemEvents:
    def test_noise_events_filtered(self):
        items = [
            system_event({"event": "thread_state_change", "state": "running"}),
            system_event({"event": "thread_state_change", "state": "idle"}),
        ]
        result = format_context_summary(items, max_items=10)
        assert "thread_state_change" not in result

    def test_user_input_event_in_activity(self):
        items = [system_event({"user_input": "build a login page"})]
        result = format_context_summary(items, max_items=10)
        assert "# Task Activity" in result
        assert "login page" in result

    def test_charter_event_shown(self):
        items = [system_event({"charter_id": "c1", "reason": "replan requested"})]
        result = format_context_summary(items, max_items=10)
        assert "Charter update" in result


class TestTruncation:
    def test_per_section_truncation(self):
        items = [entry(EntryType.MESSAGE_USER, f"request {'z' * 1500} #{i}") for i in range(30)]
        result = format_context_summary(items, max_items=30)
        assert "truncated" in result.lower()

    def test_max_tokens_cap(self):
        items = [entry(EntryType.MESSAGE_USER, f"request {'z' * 3000}") for _ in range(10)]
        result = format_context_summary(items, max_items=20, max_tokens=500)
        assert "exceeded token budget" in result.lower()

    def test_max_items_respected(self):
        items = [entry(EntryType.MESSAGE_USER, f"msg {i}") for i in range(20)]
        result = format_context_summary(items, max_items=5)
        assert "msg 10" not in result
        assert "msg 19" in result


class TestMalformedContent:
    def test_non_json_content_handled(self):
        items = [
            entry(EntryType.TOOL_CALL, "not json at all"),
            entry(EntryType.TOOL_RESULT, "also not json"),
        ]
        result = format_context_summary(items, max_items=10)
        assert "Previous context" in result

    def test_mixed_json_and_plain(self):
        items = [
            entry(EntryType.MESSAGE_USER, "plain text"),
            tool_call("bash", "ls"),
            entry(EntryType.TOOL_RESULT, "not json output"),
        ]
        result = format_context_summary(items, max_items=10)
        assert "plain text" in result


class TestRealisticSession:
    def test_full_session_flow(self):
        items = [
            system_event({"user_input": "Build a user authentication module"}),
            entry(EntryType.MESSAGE_USER, "Build a user authentication module with JWT tokens"),
            tool_call("file_read", "/src/config.py"),
            tool_result("file_read", "SECRET_KEY = 'abc123'", success=True, duration_ms=30),
            tool_call("bash", "pip install pyjwt"),
            tool_result(
                "bash", "Successfully installed PyJWT-2.8.0", success=True, duration_ms=5000
            ),
            tool_call("file_write", "/src/auth.py"),
            tool_result("file_write", "File written", success=True, duration_ms=20),
            checkpoint("Auth module", "completed", "JWT auth with login/logout endpoints"),
            entry(
                EntryType.USER_INPUT_REQUEST,
                json.dumps({"question": "Add refresh token support?", "node_id": "n1"}),
            ),
            entry(
                EntryType.USER_INPUT_RESPONSE, json.dumps({"response": "Yes, add refresh tokens"})
            ),
            tool_call("bash", "python -m pytest tests/test_auth.py"),
            tool_result(
                "bash", "FAILED: test_login_invalid_password", success=False, duration_ms=3000
            ),
            entry(
                EntryType.MESSAGE_ASSISTANT,
                "The test failed because password validation was missing. Let me fix that.",
            ),
        ]
        result = format_context_summary(items, max_items=20)

        assert "# Current State" in result
        assert "# Task Activity" in result
        assert "authentication" in result.lower()
        assert "# Tool Operations" in result
        assert "# Files Touched" in result
        assert "/src/auth.py" in result or "/src/config.py" in result
        assert "# Errors & Corrections" in result
        assert "FAILED" in result
        assert "# Decisions & Checkpoints" in result
        assert "Auth module" in result
        assert "refresh token" in result.lower()

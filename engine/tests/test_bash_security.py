"""Tests for AST-based bash security analysis validators."""

from src.sandbox.bash_security import (
    analyze_bash_security,
    extract_quoted_content,
    has_unescaped_char,
    is_escaped_at_position,
    has_backslash_escaped_operator,
    find_brace_expansion,
    has_mismatched_brace_count,
    extract_heredoc_bodies,
    validate_empty,
    validate_incomplete_commands,
    validate_safe_heredoc,
    validate_git_commit,
    validate_jq_command,
    validate_obfuscated_flags,
    validate_shell_metacharacters,
    validate_dangerous_variables,
    validate_newlines,
    validate_dangerous_patterns,
    validate_redirections,
    validate_ifs_injection,
    validate_git_commit_substitution,
    validate_proc_environ,
    validate_malformed_tokens,
    validate_backslash_escaped_whitespace,
    validate_brace_expansion,
    validate_control_characters,
    validate_unicode_whitespace,
    validate_mid_word_hash,
    validate_zsh_dangerous_commands,
    validate_backslash_escaped_operators,
    validate_comment_quote_desync,
    validate_carriage_return,
    validate_shell_quote_bug,
    validate_quoted_newline,
    build_context,
)

import asyncio
from src.sandbox.permissions import check_permission, ToolSource


class TestExtractQuotedContent:
    def test_no_quotes(self):
        wd, fu, kq = extract_quoted_content("ls -la /tmp")
        assert wd == "ls -la /tmp"
        assert fu == "ls -la /tmp"

    def test_single_quotes_stripped(self):
        wd, fu, kq = extract_quoted_content("echo 'hello world'")
        assert "hello world" in wd
        assert "hello" not in fu

    def test_double_quotes_stripped_in_fully(self):
        wd, fu, kq = extract_quoted_content('echo "hello world"')
        assert "hello world" in wd
        assert "hello" not in fu

    def test_backslash_escape_preserved(self):
        wd, fu, kq = extract_quoted_content("echo \\$HOME")
        assert "\\$" in wd
        assert "\\$" in fu

    def test_nested_escape_in_double_quotes(self):
        wd, fu, kq = extract_quoted_content('echo "path\\"quoted"')
        assert '\\"' in wd

    def test_keep_quotes_preserves_delimiters(self):
        _, _, kq = extract_quoted_content("echo 'hello' world")
        assert "'" in kq
        assert "hello" not in kq


class TestHasUnescapedChar:
    def test_finds_unescaped(self):
        assert has_unescaped_char("hello|world", "|") is True

    def test_skips_escaped(self):
        assert has_unescaped_char("hello\\|world", "|") is False

    def test_finds_after_escaped(self):
        assert has_unescaped_char("a\\|b|c", "|") is True

    def test_not_found(self):
        assert has_unescaped_char("hello world", "|") is False

    def test_empty_string(self):
        assert has_unescaped_char("", "|") is False


class TestIsEscapedAtPosition:
    def test_not_escaped(self):
        assert is_escaped_at_position("abc", 1) is False

    def test_single_backslash(self):
        assert is_escaped_at_position("a\\bc", 2) is True

    def test_double_backslash(self):
        assert is_escaped_at_position("a\\\\c", 3) is False

    def test_triple_backslash(self):
        assert is_escaped_at_position("a\\\\\\c", 4) is True


class TestHasBackslashEscapedOperator:
    def test_escaped_semicolon(self):
        found, op = has_backslash_escaped_operator("find . \\; cat")
        assert found is True
        assert op == ";"

    def test_escaped_pipe(self):
        found, op = has_backslash_escaped_operator("echo \\| sh")
        assert found is True
        assert op == "|"

    def test_escaped_ampersand(self):
        found, op = has_backslash_escaped_operator("echo \\& bg")
        assert found is True
        assert op == "&"

    def test_escaped_less_than(self):
        found, op = has_backslash_escaped_operator("cmd \\< file")
        assert found is True
        assert op == "<"

    def test_escaped_greater_than(self):
        found, op = has_backslash_escaped_operator("cmd \\> file")
        assert found is True
        assert op == ">"

    def test_inside_single_quotes_passthrough(self):
        found, _ = has_backslash_escaped_operator("echo '\\;' safe")
        assert found is False

    def test_inside_double_quotes_passthrough(self):
        found, _ = has_backslash_escaped_operator('echo "\\;" safe')
        assert found is False

    def test_normal_operator_passthrough(self):
        found, _ = has_backslash_escaped_operator("echo ok && ls")
        assert found is False


class TestExtractHeredocBodies:
    def test_quoted_heredoc_body_removed(self):
        cmd = "cat <<'EOF'\nsensitive content\nEOF\n"
        result = extract_heredoc_bodies(cmd)
        assert "sensitive content" not in result
        assert "__HEREDOC_BODY__" in result

    def test_no_heredoc_unchanged(self):
        cmd = "echo hello"
        assert extract_heredoc_bodies(cmd) == cmd

    def test_double_quoted_delimiter(self):
        cmd = 'cat <<"END"\nsome body\nEND\n'
        result = extract_heredoc_bodies(cmd)
        assert "some body" not in result


class TestBuildContext:
    def test_has_all_fields(self):
        ctx = build_context("echo hello")
        assert ctx.original_command == "echo hello"
        assert ctx.base_command == "echo"
        assert ctx.fully_unquoted_pre_strip != ""
        assert ctx.unquoted_keep_quotes != ""

    def test_pre_strip_differs_from_post(self):
        ctx = build_context("echo test 2>/dev/null")
        assert ctx.fully_unquoted_pre_strip != ctx.fully_unquoted


class TestValidateEmpty:
    def test_empty_string_allows(self):
        ctx = build_context("")
        result = validate_empty(ctx)
        assert result is not None
        assert result.blocked is False
        assert result.behavior == "allow"

    def test_whitespace_only_allows(self):
        ctx = build_context("   ")
        result = validate_empty(ctx)
        assert result is not None
        assert result.blocked is False

    def test_non_empty_passthrough(self):
        ctx = build_context("ls")
        result = validate_empty(ctx)
        assert result is None


class TestValidateIncompleteCommands:
    def test_starts_with_pipe(self):
        ctx = build_context("| cat /etc/passwd")
        result = validate_incomplete_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_starts_with_semicolon(self):
        ctx = build_context("; rm -rf /")
        result = validate_incomplete_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_starts_with_ampersand(self):
        ctx = build_context("&& cat /etc/shadow")
        result = validate_incomplete_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_starts_with_tab(self):
        ctx = build_context("\trm -rf /")
        result = validate_incomplete_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_normal_command_passthrough(self):
        ctx = build_context("ls -la")
        result = validate_incomplete_commands(ctx)
        assert result is None

    def test_pipe_in_middle_passthrough(self):
        ctx = build_context("ls | grep foo")
        result = validate_incomplete_commands(ctx)
        assert result is None


class TestValidateSafeHeredoc:
    def test_safe_heredoc_allows(self):
        cmd = "$(cat <<'EOF'\nhello world\nEOF\n)"
        ctx = build_context(cmd)
        result = validate_safe_heredoc(ctx)
        assert result is not None
        assert result.blocked is False
        assert result.behavior == "allow"

    def test_nested_heredoc_blocked(self):
        cmd = "$(cat <<'EOF'\n<<'INNER' nested\nEOF\n)"
        ctx = build_context(cmd)
        result = validate_safe_heredoc(ctx)
        assert result is not None
        assert result.blocked is True

    def test_cmd_substitution_in_body_blocked(self):
        cmd = "$(cat <<'EOF'\n$(rm -rf /)\nEOF\n)"
        ctx = build_context(cmd)
        result = validate_safe_heredoc(ctx)
        assert result is not None
        assert result.blocked is True

    def test_backtick_in_body_blocked(self):
        cmd = "$(cat <<'EOF'\n`whoami`\nEOF\n)"
        ctx = build_context(cmd)
        result = validate_safe_heredoc(ctx)
        assert result is not None
        assert result.blocked is True

    def test_non_heredoc_passthrough(self):
        ctx = build_context("echo hello")
        result = validate_safe_heredoc(ctx)
        assert result is None

    def test_unquoted_delimiter_no_match(self):
        cmd = "$(cat <<EOF\nhello\nEOF\n)"
        ctx = build_context(cmd)
        result = validate_safe_heredoc(ctx)
        assert result is None

    def test_double_quoted_delimiter_allows(self):
        cmd = '$(cat <<"EOF"\nhello world\nEOF\n)'
        ctx = build_context(cmd)
        result = validate_safe_heredoc(ctx)
        assert result is not None
        assert result.blocked is False

    def test_backslash_escaped_delimiter_allows(self):
        cmd = "$(cat <<\\EOF\nhello world\nEOF\n)"
        ctx = build_context(cmd)
        result = validate_safe_heredoc(ctx)
        assert result is not None
        assert result.blocked is False

    def test_strip_form_allows(self):
        cmd = "$(cat <<-'EOF'\n\thello world\nEOF\n)"
        ctx = build_context(cmd)
        result = validate_safe_heredoc(ctx)
        assert result is not None
        assert result.blocked is False

    def test_unsafe_remainder_blocked(self):
        cmd = "$(cat <<'EOF'\nhello\nEOF\n); rm -rf /"
        ctx = build_context(cmd)
        result = validate_safe_heredoc(ctx)
        assert result is not None
        assert result.blocked is True


class TestValidateGitCommit:
    def test_simple_commit_allows(self):
        ctx = build_context("git commit -m 'fix bug'")
        result = validate_git_commit(ctx)
        assert result is not None
        assert result.blocked is False
        assert result.behavior == "allow"

    def test_cmd_substitution_blocked(self):
        ctx = build_context('git commit -m "$(whoami)"')
        result = validate_git_commit(ctx)
        assert result is not None
        assert result.blocked is True

    def test_backtick_substitution_blocked(self):
        ctx = build_context('git commit -m "`whoami`"')
        result = validate_git_commit(ctx)
        assert result is not None
        assert result.blocked is True

    def test_variable_expansion_blocked(self):
        ctx = build_context('git commit -m "${HOME}"')
        result = validate_git_commit(ctx)
        assert result is not None
        assert result.blocked is True

    def test_redirect_attack_blocked(self):
        ctx = build_context("git commit -m 'safe' > ~/.bashrc")
        result = validate_git_commit(ctx)
        assert result is not None
        assert result.blocked is True

    def test_backslash_in_message_blocked(self):
        ctx = build_context("git commit -m 'test\\ninjection'")
        result = validate_git_commit(ctx)
        assert result is not None
        assert result.blocked is True

    def test_dash_starting_message_blocked(self):
        ctx = build_context("git commit -m '-amend'")
        result = validate_git_commit(ctx)
        assert result is not None
        assert result.blocked is True

    def test_metachar_in_remainder_blocked(self):
        ctx = build_context("git commit -m 'safe' ; echo pwned")
        result = validate_git_commit(ctx)
        assert result is not None
        assert result.blocked is True

    def test_non_git_passthrough(self):
        ctx = build_context("echo hello")
        result = validate_git_commit(ctx)
        assert result is None


class TestValidateJqCommand:
    def test_jq_system_blocked(self):
        ctx = build_context('echo "{}" | jq "system("whoami")"')
        result = validate_jq_command(ctx)
        assert result is not None
        assert result.blocked is True

    def test_jq_from_file_blocked(self):
        ctx = build_context("jq --from-file /etc/passwd")
        result = validate_jq_command(ctx)
        assert result is not None
        assert result.blocked is True

    def test_jq_rawfile_blocked(self):
        ctx = build_context("jq --rawfile key /etc/shadow .")
        result = validate_jq_command(ctx)
        assert result is not None
        assert result.blocked is True

    def test_jq_slurpfile_blocked(self):
        ctx = build_context("jq --slurpfile data secrets.json .")
        result = validate_jq_command(ctx)
        assert result is not None
        assert result.blocked is True

    def test_jq_L_flag_blocked(self):
        ctx = build_context("jq -L /malicious/path .")
        result = validate_jq_command(ctx)
        assert result is not None
        assert result.blocked is True

    def test_jq_f_flag_blocked(self):
        ctx = build_context("jq -f /malicious/filter.jq .")
        result = validate_jq_command(ctx)
        assert result is not None
        assert result.blocked is True

    def test_jq_library_path_blocked(self):
        ctx = build_context("jq --library-path /bad/path .")
        result = validate_jq_command(ctx)
        assert result is not None
        assert result.blocked is True

    def test_safe_jq_passthrough(self):
        ctx = build_context("echo '{}' | jq '.name'")
        result = validate_jq_command(ctx)
        assert result is None

    def test_non_jq_passthrough(self):
        ctx = build_context("echo hello")
        result = validate_jq_command(ctx)
        assert result is None


class TestValidateObfuscatedFlags:
    def test_quoted_char_in_flag(self):
        ctx = build_context("rm -r'f' /tmp/data")
        result = validate_obfuscated_flags(ctx)
        assert result is not None
        assert result.blocked is True

    def test_ansi_c_quoting_blocked(self):
        ctx = build_context("cmd $'\\x2d\\x65\\x78\\x65\\x63'")
        result = validate_obfuscated_flags(ctx)
        assert result is not None
        assert result.blocked is True
        assert "ANSI-C" in result.message

    def test_locale_quoting_blocked(self):
        ctx = build_context('cmd $"flag"')
        result = validate_obfuscated_flags(ctx)
        assert result is not None
        assert result.blocked is True

    def test_empty_special_dash_blocked(self):
        ctx = build_context("cmd $''-exec")
        result = validate_obfuscated_flags(ctx)
        assert result is not None
        assert result.blocked is True

    def test_empty_pair_dash_blocked(self):
        ctx = build_context("cmd ''-exec")
        result = validate_obfuscated_flags(ctx)
        assert result is not None
        assert result.blocked is True

    def test_triple_quotes_blocked(self):
        ctx = build_context("cmd '''arg")
        result = validate_obfuscated_flags(ctx)
        assert result is not None
        assert result.blocked is True

    def test_flag_continuation_blocked(self):
        ctx = build_context('cmd -""exec')
        result = validate_obfuscated_flags(ctx)
        assert result is not None
        assert result.blocked is True

    def test_quoted_dash_flag_blocked(self):
        ctx = build_context("cmd '-rf' /tmp")
        result = validate_obfuscated_flags(ctx)
        assert result is not None
        assert result.blocked is True

    def test_cut_d_allowed(self):
        ctx = build_context("cut -d',' -f1 file.csv")
        result = validate_obfuscated_flags(ctx)
        assert result is None

    def test_normal_flag_passthrough(self):
        ctx = build_context("ls -la /tmp")
        result = validate_obfuscated_flags(ctx)
        assert result is None

    def test_normal_quoted_arg_passthrough(self):
        ctx = build_context("echo 'hello world'")
        result = validate_obfuscated_flags(ctx)
        assert result is None


class TestValidateShellMetacharacters:
    def test_semicolon_in_name_flag(self):
        ctx = build_context("find . -name 'foo;rm -rf /'")
        result = validate_shell_metacharacters(ctx)
        assert result is not None
        assert result.blocked is True

    def test_pipe_in_path_flag(self):
        ctx = build_context("find . -path '*.txt|*.sh'")
        result = validate_shell_metacharacters(ctx)
        assert result is not None
        assert result.blocked is True

    def test_normal_find_passthrough(self):
        ctx = build_context("find . -name '*.py'")
        result = validate_shell_metacharacters(ctx)
        assert result is None

    def test_non_find_passthrough(self):
        ctx = build_context("grep 'pattern' file.txt")
        result = validate_shell_metacharacters(ctx)
        assert result is None


class TestValidateDangerousVariables:
    def test_var_to_redirect(self):
        ctx = build_context("echo $DATA > /etc/passwd")
        result = validate_dangerous_variables(ctx)
        assert result is not None
        assert result.blocked is True

    def test_redirect_from_var(self):
        ctx = build_context("cat < $FILENAME")
        result = validate_dangerous_variables(ctx)
        assert result is not None
        assert result.blocked is True

    def test_var_to_pipe(self):
        ctx = build_context("echo $CMD | sh")
        result = validate_dangerous_variables(ctx)
        assert result is not None
        assert result.blocked is True

    def test_safe_echo_passthrough(self):
        ctx = build_context("echo hello world")
        result = validate_dangerous_variables(ctx)
        assert result is None


class TestValidateNewlines:
    def test_embedded_newline_blocked(self):
        ctx = build_context("echo safe\nrm -rf /")
        result = validate_newlines(ctx)
        assert result is not None
        assert result.blocked is True

    def test_heredoc_with_newlines_passthrough(self):
        ctx = build_context("cat <<EOF\nhello\nEOF")
        result = validate_newlines(ctx)
        assert result is None

    def test_single_line_passthrough(self):
        ctx = build_context("ls -la")
        result = validate_newlines(ctx)
        assert result is None

    def test_backslash_newline_continuation_passthrough(self):
        ctx = build_context("echo hello \\\n--flag")
        result = validate_newlines(ctx)
        assert result is None


class TestValidateDangerousPatterns:
    def test_dollar_paren_blocked(self):
        ctx = build_context("echo $(whoami)")
        result = validate_dangerous_patterns(ctx)
        assert result is not None
        assert result.blocked is True

    def test_dollar_brace_blocked(self):
        ctx = build_context("echo ${PATH}")
        result = validate_dangerous_patterns(ctx)
        assert result is not None
        assert result.blocked is True

    def test_dollar_bracket_blocked(self):
        ctx = build_context("echo $[1+1]")
        result = validate_dangerous_patterns(ctx)
        assert result is not None
        assert result.blocked is True

    def test_process_substitution_blocked(self):
        ctx = build_context("diff <(ls dir1) >(cat)")
        result = validate_dangerous_patterns(ctx)
        assert result is not None
        assert result.blocked is True

    def test_unescaped_backtick_blocked(self):
        ctx = build_context("echo `whoami`")
        result = validate_dangerous_patterns(ctx)
        assert result is not None
        assert result.blocked is True

    def test_escaped_backtick_passthrough(self):
        ctx = build_context("mysql -e 'SELECT \\`name\\` FROM users'")
        result = validate_dangerous_patterns(ctx)
        assert result is None

    def test_safe_echo_passthrough(self):
        ctx = build_context("echo hello world")
        result = validate_dangerous_patterns(ctx)
        assert result is None

    def test_zsh_equals_expansion_blocked(self):
        ctx = build_context("cat =ls")
        result = validate_dangerous_patterns(ctx)
        assert result is not None
        assert result.blocked is True

    def test_zsh_always_block_blocked(self):
        ctx = build_context("{ cmd } always { cleanup }")
        result = validate_dangerous_patterns(ctx)
        assert result is not None
        assert result.blocked is True

    def test_zsh_glob_qualifier_blocked(self):
        ctx = build_context("ls *(e:'cmd':)")
        result = validate_dangerous_patterns(ctx)
        assert result is not None
        assert result.blocked is True

    def test_tilde_bracket_blocked(self):
        ctx = build_context("echo ~[param]")
        result = validate_dangerous_patterns(ctx)
        assert result is not None
        assert result.blocked is True

    def test_powershell_comment_blocked(self):
        ctx = build_context("cmd <# comment #>")
        result = validate_dangerous_patterns(ctx)
        assert result is not None
        assert result.blocked is True


class TestValidateRedirections:
    def test_output_redirect_blocked(self):
        ctx = build_context("echo pwned > /etc/passwd")
        result = validate_redirections(ctx)
        assert result is not None
        assert result.blocked is True

    def test_input_redirect_blocked(self):
        ctx = build_context("cmd < /etc/shadow")
        result = validate_redirections(ctx)
        assert result is not None
        assert result.blocked is True

    def test_safe_redirect_stripped_passthrough(self):
        ctx = build_context("cmd 2>/dev/null")
        result = validate_redirections(ctx)
        assert result is None

    def test_no_redirect_passthrough(self):
        ctx = build_context("ls -la")
        result = validate_redirections(ctx)
        assert result is None


class TestValidateIfsInjection:
    def test_ifs_assign_blocked(self):
        ctx = build_context("IFS=/ cmd arg")
        result = validate_ifs_injection(ctx)
        assert result is not None
        assert result.blocked is True

    def test_ifs_expansion_blocked(self):
        ctx = build_context("echo ${IFS}")
        result = validate_ifs_injection(ctx)
        assert result is not None
        assert result.blocked is True

    def test_dollar_ifs_blocked(self):
        ctx = build_context("echo word${IFS}evil")
        result = validate_ifs_injection(ctx)
        assert result is not None
        assert result.blocked is True

    def test_bare_dollar_ifs_blocked(self):
        ctx = build_context("echo $IFS")
        result = validate_ifs_injection(ctx)
        assert result is not None
        assert result.blocked is True

    def test_no_ifs_passthrough(self):
        ctx = build_context("echo hello")
        result = validate_ifs_injection(ctx)
        assert result is None


class TestValidateGitCommitSubstitution:
    def test_dollar_paren_in_commit_blocked(self):
        ctx = build_context("git commit --author=$(whoami)")
        result = validate_git_commit_substitution(ctx)
        assert result is not None
        assert result.blocked is True

    def test_backtick_in_commit_blocked(self):
        ctx = build_context("git commit --author=`id`")
        result = validate_git_commit_substitution(ctx)
        assert result is not None
        assert result.blocked is True

    def test_dash_C_with_variable_blocked(self):
        ctx = build_context("git commit -C $COMMIT_SHA")
        result = validate_git_commit_substitution(ctx)
        assert result is not None
        assert result.blocked is True

    def test_simple_commit_passthrough(self):
        ctx = build_context("git commit -m 'safe message'")
        result = validate_git_commit_substitution(ctx)
        assert result is None

    def test_non_git_passthrough(self):
        ctx = build_context("echo hello")
        result = validate_git_commit_substitution(ctx)
        assert result is None


class TestValidateProcEnviron:
    def test_proc_self_environ_blocked(self):
        ctx = build_context("cat /proc/self/environ")
        result = validate_proc_environ(ctx)
        assert result is not None
        assert result.blocked is True

    def test_proc_pid_environ_blocked(self):
        ctx = build_context("cat /proc/1234/environ")
        result = validate_proc_environ(ctx)
        assert result is not None
        assert result.blocked is True

    def test_proc_status_passthrough(self):
        ctx = build_context("cat /proc/self/status")
        result = validate_proc_environ(ctx)
        assert result is None

    def test_non_proc_passthrough(self):
        ctx = build_context("cat /etc/hosts")
        result = validate_proc_environ(ctx)
        assert result is None


class TestValidateMalformedTokens:
    def test_unbalanced_single_quote_blocked(self):
        ctx = build_context("echo 'hello")
        result = validate_malformed_tokens(ctx)
        assert result is not None
        assert result.blocked is True
        assert result.is_misparsing is True

    def test_unbalanced_double_quote_blocked(self):
        ctx = build_context('echo "hello')
        result = validate_malformed_tokens(ctx)
        assert result is not None
        assert result.blocked is True

    def test_balanced_quotes_passthrough(self):
        ctx = build_context("echo 'hello' \"world\"")
        result = validate_malformed_tokens(ctx)
        assert result is None

    def test_escaped_quotes_passthrough(self):
        ctx = build_context("echo \\'hello\\'")
        result = validate_malformed_tokens(ctx)
        assert result is None

    def test_no_quotes_passthrough(self):
        ctx = build_context("ls -la")
        result = validate_malformed_tokens(ctx)
        assert result is None

    def test_unbalanced_braces_with_separator_blocked(self):
        ctx = build_context("echo {a; evil")
        result = validate_malformed_tokens(ctx)
        assert result is not None
        assert result.blocked is True

    def test_unbalanced_parens_with_separator_blocked(self):
        ctx = build_context("echo (a; evil")
        result = validate_malformed_tokens(ctx)
        assert result is not None
        assert result.blocked is True

    def test_balanced_braces_no_separator_passthrough(self):
        ctx = build_context("echo {a}")
        result = validate_malformed_tokens(ctx)
        assert result is None


class TestValidateBackslashEscapedWhitespace:
    def test_backslash_space_blocked(self):
        ctx = build_context("echo\\ test")
        result = validate_backslash_escaped_whitespace(ctx)
        assert result is not None
        assert result.blocked is True

    def test_backslash_tab_blocked(self):
        ctx = build_context("echo\\\tworld")
        result = validate_backslash_escaped_whitespace(ctx)
        assert result is not None
        assert result.blocked is True

    def test_escaped_n_literal_blocked(self):
        ctx = build_context("echo hello\\nrm -rf /")
        result = validate_backslash_escaped_whitespace(ctx)
        assert result is not None
        assert result.blocked is True

    def test_escaped_t_literal_blocked(self):
        ctx = build_context("echo hello\\tworld")
        result = validate_backslash_escaped_whitespace(ctx)
        assert result is not None
        assert result.blocked is True

    def test_inside_single_quotes_passthrough(self):
        ctx = build_context("echo 'hello\\ world'")
        result = validate_backslash_escaped_whitespace(ctx)
        assert result is None

    def test_no_escapes_passthrough(self):
        ctx = build_context("echo hello world")
        result = validate_backslash_escaped_whitespace(ctx)
        assert result is None


class TestValidateBraceExpansion:
    def test_sensitive_path_expansion_blocked(self):
        ctx = build_context("cat {/etc/passwd,/dev/null}")
        result = validate_brace_expansion(ctx)
        assert result is not None
        assert result.blocked is True

    def test_general_brace_expansion_blocked(self):
        ctx = build_context("echo {a,b,c}")
        result = validate_brace_expansion(ctx)
        assert result is not None
        assert result.blocked is True

    def test_range_expansion_blocked(self):
        ctx = build_context("echo {1..100}")
        result = validate_brace_expansion(ctx)
        assert result is not None
        assert result.blocked is True

    def test_no_brace_passthrough(self):
        ctx = build_context("echo hello")
        result = validate_brace_expansion(ctx)
        assert result is None

    def test_single_brace_not_expansion(self):
        ctx = build_context("echo {hello}")
        result = validate_brace_expansion(ctx)
        assert result is None

    def test_escaped_brace_passthrough(self):
        ctx = build_context("echo \\{a,b\\}")
        result = validate_brace_expansion(ctx)
        assert result is None

    def test_mismatched_brace_count_blocked(self):
        result = has_mismatched_brace_count("echo '{' }", "echo  }")
        assert result is True

    def test_nested_braces_detected(self):
        assert find_brace_expansion("{a,{b,c}}") is True

    def test_quoted_brace_in_unquoted_context_blocked(self):
        ctx = build_context("echo '{' {a,b}")
        result = validate_brace_expansion(ctx)
        assert result is not None
        assert result.blocked is True


class TestValidateControlCharacters:
    def test_null_byte_blocked(self):
        ctx = build_context("echo \x00hidden")
        result = validate_control_characters(ctx)
        assert result is not None
        assert result.blocked is True
        assert result.is_misparsing is True

    def test_bell_char_blocked(self):
        ctx = build_context("echo \x07beep")
        result = validate_control_characters(ctx)
        assert result is not None
        assert result.blocked is True

    def test_escape_char_blocked(self):
        ctx = build_context("echo \x1bhidden")
        result = validate_control_characters(ctx)
        assert result is not None
        assert result.blocked is True

    def test_del_char_blocked(self):
        ctx = build_context("echo \x7fhidden")
        result = validate_control_characters(ctx)
        assert result is not None
        assert result.blocked is True

    def test_tab_allowed(self):
        ctx = build_context("echo\thello")
        result = validate_control_characters(ctx)
        assert result is None

    def test_normal_text_passthrough(self):
        ctx = build_context("echo hello world")
        result = validate_control_characters(ctx)
        assert result is None


class TestValidateUnicodeWhitespace:
    def test_nbsp_blocked(self):
        ctx = build_context("echo\u00a0hidden")
        result = validate_unicode_whitespace(ctx)
        assert result is not None
        assert result.blocked is True

    def test_zero_width_space_blocked(self):
        ctx = build_context("cmd\u200barg")
        result = validate_unicode_whitespace(ctx)
        assert result is not None
        assert result.blocked is True

    def test_bidi_override_blocked(self):
        ctx = build_context("echo \u202atext\u202c")
        result = validate_unicode_whitespace(ctx)
        assert result is not None
        assert result.blocked is True

    def test_ogham_space_blocked(self):
        ctx = build_context("echo\u1680hidden")
        result = validate_unicode_whitespace(ctx)
        assert result is not None
        assert result.blocked is True

    def test_narrow_nbsp_blocked(self):
        ctx = build_context("echo\u202fhidden")
        result = validate_unicode_whitespace(ctx)
        assert result is not None
        assert result.blocked is True

    def test_ideographic_space_blocked(self):
        ctx = build_context("echo\u3000hidden")
        result = validate_unicode_whitespace(ctx)
        assert result is not None
        assert result.blocked is True

    def test_normal_spaces_passthrough(self):
        ctx = build_context("echo hello world")
        result = validate_unicode_whitespace(ctx)
        assert result is None


class TestValidateMidWordHash:
    def test_hash_after_quote_blocked(self):
        ctx = build_context("echo 'x'#hidden")
        result = validate_mid_word_hash(ctx)
        assert result is not None
        assert result.blocked is True

    def test_word_hash_blocked(self):
        ctx = build_context("echo word#comment")
        result = validate_mid_word_hash(ctx)
        assert result is not None
        assert result.blocked is True

    def test_hash_in_comment_passthrough(self):
        ctx = build_context("echo hello # comment")
        result = validate_mid_word_hash(ctx)
        assert result is None

    def test_no_hash_passthrough(self):
        ctx = build_context("echo hello")
        result = validate_mid_word_hash(ctx)
        assert result is None

    def test_string_length_syntax_passthrough(self):
        ctx = build_context("echo ${#var}")
        result = validate_mid_word_hash(ctx)
        assert result is None


class TestValidateZshDangerousCommands:
    def test_zmodload_blocked(self):
        ctx = build_context("zmodload zsh/system")
        result = validate_zsh_dangerous_commands(ctx)
        assert result is not None
        assert result.blocked is True
        assert "zmodload" in result.message

    def test_sysread_blocked(self):
        ctx = build_context("sysread -i 0 line")
        result = validate_zsh_dangerous_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_zf_rm_blocked(self):
        ctx = build_context("zf_rm /tmp/file")
        result = validate_zsh_dangerous_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_ztcp_blocked(self):
        ctx = build_context("ztcp example.com 80")
        result = validate_zsh_dangerous_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_emulate_blocked(self):
        ctx = build_context("emulate -L sh")
        result = validate_zsh_dangerous_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_mapfile_blocked(self):
        ctx = build_context("mapfile -t lines")
        result = validate_zsh_dangerous_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_fc_e_blocked(self):
        ctx = build_context("fc -e vim")
        result = validate_zsh_dangerous_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_builtin_prefix_zmodload_blocked(self):
        ctx = build_context("builtin zmodload zsh/system")
        result = validate_zsh_dangerous_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_env_prefix_zmodload_blocked(self):
        ctx = build_context("FOO=bar zmodload zsh/system")
        result = validate_zsh_dangerous_commands(ctx)
        assert result is not None
        assert result.blocked is True

    def test_normal_command_passthrough(self):
        ctx = build_context("ls -la")
        result = validate_zsh_dangerous_commands(ctx)
        assert result is None


class TestValidateBackslashEscapedOperators:
    def test_escaped_semicolon_blocked(self):
        ctx = build_context("find . -type f \\; cat /etc/shadow")
        result = validate_backslash_escaped_operators(ctx)
        assert result is not None
        assert result.blocked is True
        assert result.is_misparsing is True

    def test_escaped_pipe_blocked(self):
        ctx = build_context("echo safe \\| sh")
        result = validate_backslash_escaped_operators(ctx)
        assert result is not None
        assert result.blocked is True

    def test_escaped_ampersand_blocked(self):
        ctx = build_context("echo safe \\& bg")
        result = validate_backslash_escaped_operators(ctx)
        assert result is not None
        assert result.blocked is True

    def test_escaped_less_than_blocked(self):
        ctx = build_context("cmd \\< file")
        result = validate_backslash_escaped_operators(ctx)
        assert result is not None
        assert result.blocked is True

    def test_escaped_greater_than_blocked(self):
        ctx = build_context("cmd \\> file")
        result = validate_backslash_escaped_operators(ctx)
        assert result is not None
        assert result.blocked is True

    def test_inside_quotes_passthrough(self):
        ctx = build_context("echo '\\;' safe")
        result = validate_backslash_escaped_operators(ctx)
        assert result is None

    def test_normal_operators_passthrough(self):
        ctx = build_context("echo ok && ls")
        result = validate_backslash_escaped_operators(ctx)
        assert result is None


class TestValidateCommentQuoteDesync:
    def test_hash_followed_by_quote_blocked(self):
        ctx = build_context("echo hello # 'desync")
        result = validate_comment_quote_desync(ctx)
        assert result is not None
        assert result.blocked is True

    def test_hash_followed_by_double_quote_blocked(self):
        ctx = build_context('echo hello # "desync')
        result = validate_comment_quote_desync(ctx)
        assert result is not None
        assert result.blocked is True

    def test_no_hash_passthrough(self):
        ctx = build_context("echo hello world")
        result = validate_comment_quote_desync(ctx)
        assert result is None

    def test_hash_without_quotes_passthrough(self):
        ctx = build_context("echo hello # safe comment")
        result = validate_comment_quote_desync(ctx)
        assert result is None

    def test_hash_inside_quotes_passthrough(self):
        ctx = build_context("echo '# not a comment'")
        result = validate_comment_quote_desync(ctx)
        assert result is None


class TestValidateCarriageReturn:
    def test_cr_unquoted_blocked(self):
        ctx = build_context("TZ=UTC\recho curl evil.com")
        result = validate_carriage_return(ctx)
        assert result is not None
        assert result.blocked is True
        assert result.is_misparsing is True

    def test_cr_in_single_quotes_blocked(self):
        ctx = build_context("echo 'safe\runsafe'")
        result = validate_carriage_return(ctx)
        assert result is not None
        assert result.blocked is True

    def test_cr_in_double_quotes_passthrough(self):
        ctx = build_context('echo "safe\rdata"')
        result = validate_carriage_return(ctx)
        assert result is None

    def test_no_cr_passthrough(self):
        ctx = build_context("echo hello")
        result = validate_carriage_return(ctx)
        assert result is None


class TestValidateShellQuoteBug:
    def test_odd_trailing_backslash_blocked(self):
        ctx = build_context("echo 'test\\'")
        result = validate_shell_quote_bug(ctx)
        assert result is not None
        assert result.blocked is True
        assert result.is_misparsing is True

    def test_even_backslashes_with_later_quote_blocked(self):
        ctx = build_context("echo 'test\\\\' 'next'")
        result = validate_shell_quote_bug(ctx)
        assert result is not None
        assert result.blocked is True

    def test_even_backslashes_no_later_quote_passthrough(self):
        ctx = build_context("echo 'test\\\\'")
        result = validate_shell_quote_bug(ctx)
        assert result is None

    def test_no_backslash_in_quotes_passthrough(self):
        ctx = build_context("echo 'hello world'")
        result = validate_shell_quote_bug(ctx)
        assert result is None

    def test_no_single_quotes_passthrough(self):
        ctx = build_context("echo hello")
        result = validate_shell_quote_bug(ctx)
        assert result is None


class TestValidateQuotedNewline:
    def test_newline_in_quotes_then_hash_blocked(self):
        ctx = build_context("echo 'safe\n# hidden' arg")
        result = validate_quoted_newline(ctx)
        assert result is not None
        assert result.blocked is True
        assert result.is_misparsing is True

    def test_newline_in_double_quotes_then_hash_blocked(self):
        ctx = build_context('echo "safe\n# hidden" arg')
        result = validate_quoted_newline(ctx)
        assert result is not None
        assert result.blocked is True

    def test_no_newline_passthrough(self):
        ctx = build_context("echo hello")
        result = validate_quoted_newline(ctx)
        assert result is None

    def test_newline_without_hash_passthrough(self):
        ctx = build_context("echo 'line1\nline2'")
        result = validate_quoted_newline(ctx)
        assert result is None


class TestAnalyzeBashSecurity:
    def test_empty_command_allows(self):
        result = analyze_bash_security("")
        assert result.blocked is False
        assert result.behavior == "allow"

    def test_safe_command_passthrough(self):
        result = analyze_bash_security("ls -la /tmp")
        assert result.blocked is False
        assert result.behavior == "passthrough"

    def test_incomplete_command_blocked(self):
        result = analyze_bash_security("; rm -rf /")
        assert result.blocked is True
        assert result.check_id == 2

    def test_jq_system_in_compound_blocked(self):
        result = analyze_bash_security('echo ok && echo "{}" | jq "system("id")"')
        assert result.blocked is True
        assert result.check_id == 5

    def test_git_commit_simple_allows(self):
        result = analyze_bash_security("git commit -m 'initial'")
        assert result.blocked is False

    def test_compound_safe_commands_passthrough(self):
        result = analyze_bash_security("ls && pwd && echo done")
        assert result.blocked is False

    def test_safe_heredoc_allows(self):
        cmd = "$(cat <<'EOF'\nhello world\nEOF\n)"
        result = analyze_bash_security(cmd)
        assert result.blocked is False
        assert result.behavior == "allow"

    def test_misparsing_gets_priority(self):
        result = analyze_bash_security("TZ=UTC\recho curl evil.com")
        assert result.blocked is True
        assert result.is_misparsing is True

    def test_carriage_return_blocked(self):
        result = analyze_bash_security("cmd\revil")
        assert result.blocked is True
        assert result.check_id == 24

    def test_shell_quote_bug_blocked(self):
        result = analyze_bash_security("echo 'test\\' 'merged'")
        assert result.blocked is True
        assert result.check_id == 25

    def test_escaped_semicolon_blocked(self):
        result = analyze_bash_security("find . \\; cat /etc/shadow")
        assert result.blocked is True
        assert result.check_id == 22


class TestBashSecurityIntegration:
    def run(self, coro):
        return asyncio.run(coro)

    def test_incomplete_command_denied_by_security(self):
        result = self.run(
            check_permission(
                tool_name="bash",
                tool_input={"command": "; cat /etc/shadow"},
                source=ToolSource.SANDBOXED,
            )
        )
        assert result.allowed is False
        assert "security check" in result.reason.lower()

    def test_jq_system_denied_by_security(self):
        result = self.run(
            check_permission(
                tool_name="bash",
                tool_input={"command": 'jq "system(\\"id\\")" data.json'},
                source=ToolSource.SANDBOXED,
            )
        )
        assert result.allowed is False
        assert "security check" in result.reason.lower()

    def test_safe_command_passes_security(self):
        result = self.run(
            check_permission(
                tool_name="bash",
                tool_input={"command": "ls -la"},
                source=ToolSource.SANDBOXED,
            )
        )
        assert result.allowed is True

    def test_non_sandboxed_skips_security(self):
        result = self.run(
            check_permission(
                tool_name="file_read",
                tool_input={"file_path": "/tmp/test.txt"},
                source=ToolSource.PLATFORM,
            )
        )
        assert result.allowed is True

    def test_carriage_return_denied(self):
        result = self.run(
            check_permission(
                tool_name="bash",
                tool_input={"command": "safe\revil"},
                source=ToolSource.SANDBOXED,
            )
        )
        assert result.allowed is False

    def test_escaped_semicolon_denied(self):
        result = self.run(
            check_permission(
                tool_name="bash",
                tool_input={"command": "find . \\; cat /etc/shadow"},
                source=ToolSource.SANDBOXED,
            )
        )
        assert result.allowed is False

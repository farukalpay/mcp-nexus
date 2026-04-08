"""Explicit tool catalog used by intelligence and documentation helpers."""

from __future__ import annotations

from dataclasses import dataclass

TOOL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "filesystem": (
        "read_file",
        "write_file",
        "edit_file",
        "list_directory",
        "search_files",
        "search_content",
        "compare_paths",
        "file_info",
        "move_file",
        "delete_file",
        "create_directory",
        "tree",
        "tail_file",
        "head_file",
        "chmod_file",
        "chown_file",
        "file_exists",
        "batch_read",
        "replace_in_file",
        "count_lines",
    ),
    "terminal": (
        "execute_command",
        "execute_script",
        "execute_batch",
        "execute_python",
        "execute_python_file",
        "environment_info",
        "which_command",
        "server_capabilities",
        "create_python_sandbox",
        "list_python_sandboxes",
        "remove_python_sandbox",
    ),
    "git": (
        "git_status",
        "git_diagnose",
        "git_diff",
        "git_log",
        "git_commit",
        "git_branch",
        "git_pull",
        "git_push",
        "git_stash",
        "git_stage",
        "git_show",
        "git_fetch",
        "git_remotes",
        "git_blame",
        "git_tags",
    ),
    "debug": (
        "lint_python",
        "typecheck",
        "syntax_check",
        "find_todos",
        "code_symbols",
        "compare_files",
        "find_errors",
        "python_trace",
        "find_references",
        "run_tests",
        "format_code",
        "stack_traces",
    ),
    "process": (
        "list_services",
        "service_status",
        "restart_service",
        "start_service",
        "stop_service",
        "view_logs",
        "list_processes",
        "kill_process",
        "cron_list",
        "cron_add",
        "enable_service",
        "disable_service",
        "service_dependencies",
        "process_tree",
        "process_open_files",
        "process_status",
        "run_background_command",
        "background_job_status",
        "background_job_logs",
        "background_job_wait",
        "background_job_stop",
        "list_background_jobs",
        "docker_compose_ps",
        "docker_compose_logs",
    ),
    "database": (
        "db_client_status",
        "db_client_bootstrap",
        "inspect_database",
        "db_profiles",
        "db_use",
        "db_query",
        "db_safe_query",
        "db_tables",
        "db_schema",
        "db_table_inspect",
        "db_sample",
        "db_profile",
        "db_export_csv",
        "db_execute",
        "db_size",
        "db_explain",
        "db_query_explain",
        "db_indexes",
        "db_connections",
        "db_table_stats",
        "db_extensions",
        "db_join_suggest",
    ),
    "monitoring": (
        "server_health",
        "disk_usage",
        "memory_usage",
        "cpu_usage",
        "network_stats",
        "active_connections",
        "nginx_status",
        "docker_status",
        "server_resources",
        "io_activity",
    ),
    "deployment": (
        "deploy_sync",
        "deploy_service",
        "create_backup",
        "list_backups",
        "restore_backup",
        "pip_install",
        "deploy_release",
        "deploy_activate_release",
        "deploy_rollback_release",
        "deploy_compose",
        "deploy_health_check",
    ),
    "network": (
        "check_port",
        "dns_lookup",
        "ssl_info",
        "firewall_rules",
        "curl_test",
        "listening_ports",
        "port_forward",
        "list_forwards",
        "remove_forward",
        "iptables_forward",
        "port_scan",
        "network_route",
        "trace_route",
        "ssh_tunnel",
    ),
    "packages": (
        "pip_list",
        "pip_show",
        "apt_list",
        "apt_install",
        "npm_list",
        "package_managers",
        "package_search",
        "package_info",
        "package_install",
        "package_outdated",
        "npm_install",
        "python_virtualenvs",
    ),
    "intelligence": (
        "nexus_recall",
        "nexus_insights",
        "nexus_suggest",
        "nexus_preferences",
        "nexus_workflows",
        "nexus_tool_catalog",
    ),
    "analysis": (
        "tabular_dataset_profile",
        "train_tabular_classifier",
    ),
    "logs": (
        "nexus_audit_recent",
        "nexus_audit_summary",
        "nexus_audit_failures",
        "nexus_slowest_tools",
    ),
}


TOOL_TO_CATEGORY = {tool: category for category, tools in TOOL_CATEGORIES.items() for tool in tools}


@dataclass(frozen=True)
class CatalogSummary:
    total_tools: int
    category_counts: dict[str, int]


def category_for_tool(tool_name: str) -> str | None:
    """Return the explicit category for a tool when known."""
    return TOOL_TO_CATEGORY.get(tool_name)


def category_counts() -> dict[str, int]:
    return {category: len(tools) for category, tools in TOOL_CATEGORIES.items()}


def catalog_summary() -> CatalogSummary:
    counts = category_counts()
    return CatalogSummary(total_tools=sum(counts.values()), category_counts=counts)

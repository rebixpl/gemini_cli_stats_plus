import os
import json
import glob
import argparse
import hashlib
from datetime import datetime
from collections import defaultdict

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich import box
except ImportError:
    print("Please install 'rich' via 'pip install rich'")
    exit(1)

SCHEMA = {
    "total_sessions": "int",
    "total_messages": "int",
    "total_cost": "float",
    "model_usage": "dict (model_name -> {input: int, output: int, cached: int, cost: float, messages: int})",
    "project_usage": "dict (project_hash -> {messages: int, cost: float, sessions: int, id: str, name: str})",
    "tool_usage": "dict (tool_name -> int)",
    "session_durations": "list of dict ({file: str, duration: float, date: str})",
    "active_days": "dict (date_str -> int)"
}

def get_agent_guide():
    return """AGENT GUIDE:
This module analyzes Gemini CLI session history.
To use programmatically:
    import gemini_stats
    stats = gemini_stats.analyze(base_dir="path/to/.gemini", silent=True)
    
The 'analyze' function returns a dictionary with the following structure:
%s
""" % json.dumps(SCHEMA, indent=4)

# Cost per 1M tokens (Input, Output, Cached)
COSTS = {
    # Gemini 3 Preview
    "gemini-3-pro-preview": {"input": 2.00, "output": 12.00, "cached": 0.20},
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00, "cached": 0.20},
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.00, "cached": 0.05},
    
    # Gemini 2.5
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "cached": 0.125},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "cached": 0.03},
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40, "cached": 0.01},
    
    # Gemini 1.5
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00, "cached": 0.125},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30, "cached": 0.0075},
}

def calculate_cost(model, input_tokens, output_tokens, cached_tokens=0):
    rates = COSTS.get(model)
    if not rates:
        # Sort keys by length descending to match most specific model first
        for key in sorted(COSTS.keys(), key=len, reverse=True):
            if key in model:
                rates = COSTS[key]
                break
        else:
            return 0.0
    
    actual_input = max(0, input_tokens - cached_tokens)
    return (actual_input / 1_000_000 * rates["input"]) + \
           (output_tokens / 1_000_000 * rates["output"]) + \
           (cached_tokens / 1_000_000 * rates.get("cached", 0.0))

def parse_date(date_str):
    if not date_str: return None
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except ValueError:
        return None

def format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"

def get_project_map(base_dir):
    project_map = {}
    history_dir = os.path.join(base_dir, "history")
    if not os.path.exists(history_dir):
        return project_map
        
    for project_name in os.listdir(history_dir):
        proj_dir = os.path.join(history_dir, project_name)
        if os.path.isdir(proj_dir):
            root_file = os.path.join(proj_dir, ".project_root")
            if os.path.exists(root_file):
                try:
                    with open(root_file, "r") as f:
                        path = f.read().strip()
                        
                        # Try standard CLI normalization (normcase + normpath)
                        # This is what the CLI typically uses for consistent hashing
                        norm_path = os.path.normcase(os.path.normpath(path))
                        h = hashlib.sha256(norm_path.encode()).hexdigest()
                        project_map[h] = project_name
                        
                        # Fallback: Literal path hash
                        h_literal = hashlib.sha256(path.encode()).hexdigest()
                        if h_literal not in project_map:
                            project_map[h_literal] = project_name
                            
                        # Fallback: Just normpath without normcase
                        h_norm = hashlib.sha256(os.path.normpath(path).encode()).hexdigest()
                        if h_norm not in project_map:
                            project_map[h_norm] = project_name
                except:
                    continue
    return project_map

def analyze(base_dir=None, silent=False):
    """Primary entry point for programmatic use."""
    if base_dir is None:
        base_dir = os.path.expanduser("~/.gemini")
        
    if not os.path.exists(base_dir):
        if not silent:
            print(f"Error: Directory '{base_dir}' not found.")
        return None

    if not silent:
        print(f"Analyzing sessions in {base_dir}...")
        
    stats = analyze_sessions(base_dir)
    
    if not silent:
        if stats["total_sessions"] == 0:
            print("No valid session files found in the specified directory.")
        else:
            display_stats(stats)
            
    return stats

def analyze_sessions(base_dir):
    project_map = get_project_map(base_dir)
    search_pattern = os.path.join(base_dir, "**", "*.json")
    files = glob.glob(search_pattern, recursive=True)
    
    stats = {
        "total_sessions": 0,
        "total_messages": 0,
        "total_cost": 0.0,
        "model_usage": defaultdict(lambda: {"input": 0, "output": 0, "cached": 0, "cost": 0.0, "messages": 0}),
        "project_usage": defaultdict(lambda: {"messages": 0, "cost": 0.0, "sessions": 0, "id": "", "name": ""}),
        "tool_usage": defaultdict(int),
        "session_durations": [],
        "active_days": defaultdict(int)
    }

    for file_path in files:
        if not os.path.basename(file_path).startswith("session-"):
            continue
            
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        if "messages" not in data:
            continue

        stats["total_sessions"] += 1
        
        p_hash = data.get("projectHash", "unknown")
        p_id = p_hash[:8] if p_hash != "unknown" else "Global"
        
        # Try to infer project name from the directory structure
        # Typical path: .../.gemini/history/PROJECT_NAME/chats/session-*.json
        # or .../.gemini/tmp/PROJECT_NAME/chats/session-*.json
        path_parts = os.path.normpath(file_path).split(os.sep)
        p_name = "Unknown"
        if "chats" in path_parts:
            chats_idx = path_parts.index("chats")
            if chats_idx > 0:
                p_name = path_parts[chats_idx - 1]
        
        # Fallback to project_map if path inference failed
        if p_name == "Unknown" or p_name == "tmp" or p_name == "history":
            p_name = project_map.get(p_hash, "Global" if p_hash == "unknown" else "Unknown")
        
        usage = stats["project_usage"][p_hash]
        usage["sessions"] += 1
        usage["id"] = p_id
        usage["name"] = p_name
        
        start_time = parse_date(data.get("startTime"))
        end_time = parse_date(data.get("lastUpdated"))
        
        if start_time:
            day_str = start_time.strftime("%Y-%m-%d")
            stats["active_days"][day_str] += 1
            if end_time:
                duration = (end_time - start_time).total_seconds()
                stats["session_durations"].append({"file": os.path.basename(file_path), "duration": duration, "date": day_str})

        for msg in data["messages"]:
            stats["total_messages"] += 1
            
            model = msg.get("model", "unknown")
            tokens = msg.get("tokens", {})
            input_tokens = tokens.get("input", 0)
            output_tokens = tokens.get("output", 0)
            cached_tokens = tokens.get("cached", 0)
            
            if model != "unknown" or input_tokens > 0 or output_tokens > 0:
                cost = calculate_cost(model, input_tokens, output_tokens, cached_tokens)
                
                stats["model_usage"][model]["input"] += input_tokens
                stats["model_usage"][model]["output"] += output_tokens
                stats["model_usage"][model]["cached"] += cached_tokens
                stats["model_usage"][model]["cost"] += cost
                stats["model_usage"][model]["messages"] += 1
                
                usage["messages"] += 1
                usage["cost"] += cost
                
                stats["total_cost"] += cost

            for tool_call in msg.get("toolCalls", []):
                tool_name = tool_call.get("name", "unknown")
                stats["tool_usage"][tool_name] += 1

    return stats

def display_stats(stats):
    console = Console()
    console.print("\n[bold cyan]✨ Gemini CLI Stats Plus ✨[/bold cyan]\n")

    summary_table = Table(title="Overall Summary", box=box.ROUNDED)
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="magenta")
    
    summary_table.add_row("Total Sessions", str(stats["total_sessions"]))
    summary_table.add_row("Total Messages", str(stats["total_messages"]))
    summary_table.add_row("Total Estimated Cost", f"${stats['total_cost']:.4f}")
    
    console.print(summary_table)

    if stats["project_usage"]:
        proj_table = Table(title="Usage by Project", box=box.ROUNDED)
        proj_table.add_column("ID", style="blue")
        proj_table.add_column("Project Name", style="cyan")
        proj_table.add_column("Sessions", justify="right")
        proj_table.add_column("Messages", justify="right")
        proj_table.add_column("Est. Cost", justify="right", style="bold yellow")
        
        sorted_projs = sorted(stats["project_usage"].values(), key=lambda x: x["cost"], reverse=True)
        for usage in sorted_projs:
            proj_table.add_row(
                usage["id"],
                usage["name"],
                str(usage["sessions"]),
                str(usage["messages"]),
                f"${usage['cost']:.4f}"
            )
        console.print(proj_table)

    if stats["model_usage"]:
        model_table = Table(title="Usage by Model", box=box.ROUNDED)
        model_table.add_column("Model", style="green")
        model_table.add_column("Messages", justify="right")
        model_table.add_column("Input", justify="right")
        model_table.add_column("Cached", justify="right", style="dim")
        model_table.add_column("Output", justify="right")
        model_table.add_column("Est. Cost", justify="right", style="bold yellow")
        
        sorted_models = sorted(stats["model_usage"].items(), key=lambda x: x[1]["cost"], reverse=True)
        
        for model, usage in sorted_models:
            model_table.add_row(
                model,
                str(usage["messages"]),
                f"{usage['input']:,}",
                f"{usage['cached']:,}",
                f"{usage['output']:,}",
                f"${usage['cost']:.4f}"
            )
        console.print(model_table)

    if stats["tool_usage"]:
        tool_table = Table(title="Top Tools Used", box=box.ROUNDED)
        tool_table.add_column("Tool", style="blue")
        tool_table.add_column("Calls", justify="right")
        
        sorted_tools = sorted(stats["tool_usage"].items(), key=lambda x: x[1], reverse=True)[:10]
        for tool, count in sorted_tools:
            tool_table.add_row(tool, str(count))
            
        console.print(tool_table)

    if stats["active_days"] or stats["session_durations"]:
        stats_table = Table(title="Activity Highlights", box=box.ROUNDED)
        stats_table.add_column("Category", style="cyan")
        stats_table.add_column("Details", style="white")
        
        if stats["active_days"]:
            top_day = max(stats["active_days"].items(), key=lambda x: x[1])
            stats_table.add_row("Most Active Day", f"{top_day[0]} ({top_day[1]} sessions)")
            
        if stats["session_durations"]:
            longest_session = max(stats["session_durations"], key=lambda x: x["duration"])
            stats_table.add_row("Longest Session", f"{longest_session['date']} - {format_duration(longest_session['duration'])}")
            
        console.print(stats_table)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze Gemini CLI usage statistics.")
    parser.add_argument("--path", "-p", type=str, help="Path to Gemini sessions directory. Defaults to ~/.gemini")
    parser.add_argument("--silent", "-s", action="store_true", help="Run in silent mode (only for programmatic check, though CLI usually wants output)")
    
    args = parser.parse_args()
    path = args.path if args.path else os.path.expanduser("~/.gemini")
    
    analyze(base_dir=path, silent=args.silent)

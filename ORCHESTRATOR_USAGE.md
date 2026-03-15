# Using Gemini CLI Stats in an Orchestrator

This guide explains how to integrate `gemini_stats.py` into an **orchestrator script** (a script that manages multiple AI sessions, tasks, or agents) for monitoring usage and costs.

## Prerequisites

Ensure `rich` is installed in your environment:
```bash
pip install rich
```

Place `gemini_stats.py` in your project root or add its directory to your `PYTHONPATH`.

---

## Programmatic Entry Point

The primary function for orchestrators is `gemini_stats.analyze()`.

```python
import gemini_stats

# silent=True prevents the script from printing tables to stdout
stats = gemini_stats.analyze(base_dir="~/.gemini", silent=True)
```

### Return Value (`stats` object)

The `stats` dictionary contains the following metrics:

| Field | Type | Description |
| :--- | :--- | :--- |
| `total_sessions` | `int` | Total number of session files found. |
| `total_messages` | `int` | Count of all messages in history. |
| `total_cost` | `float` | Sum of estimated costs for all tokens. |
| `model_usage` | `dict` | Usage stats per model (input, output, cost, etc). |
| `project_usage` | `dict` | Usage stats per project (name, cost, etc). |
| `tool_usage` | `dict` | Usage count for each CLI tool. |

---

## Sample Orchestrator Integration

Below is an example of how an orchestrator might use `gemini_stats` to log usage after a task.

```python
import gemini_stats
import json
import os

class TaskOrchestrator:
    def __init__(self, gemini_path="~/.gemini"):
        self.gemini_path = os.path.expanduser(gemini_path)

    def run_task(self, task_name):
        print(f"Running task: {task_name}...")
        # ... logic to run Gemini CLI or other agents ...
        pass

    def get_summary_report(self):
        """Generates a summary for the current environment."""
        stats = gemini_stats.analyze(base_dir=self.gemini_path, silent=True)
        
        if not stats:
            return "No usage data found."

        report = [
            f"--- Gemini Usage Report ---",
            f"Total sessions tracked: {stats['total_sessions']}",
            f"Total estimated cost:  ${stats['total_cost']:.4f}",
            f"Top model used:        {self._get_top_model(stats)}"
        ]
        return "\n".join(report)

    def _get_top_model(self, stats):
        if not stats["model_usage"]: return "N/A"
        return max(stats["model_usage"].items(), key=lambda x: x[1]["messages"])[0]

# Usage in your orchestrator
orchestrator = TaskOrchestrator()
orchestrator.run_task("Feature Implementation")

# Print the report
print(orchestrator.get_summary_report())
```

---

## Agent Handshake (Internal Documentation)

If your orchestrator needs to "teach" an AI agent how to use this tool, you can retrieve its schema programmatically:

```python
guide = gemini_stats.get_agent_guide()
print(guide)
```

This will output a structured explanation of the `stats` object that agents can parse to understand what data is available.

---

## Cost Calculation Details

`gemini_stats.py` uses an internal `COSTS` table to estimate spend. If you are using custom pricing or need to update rates, you can modify the `COSTS` dictionary in `gemini_stats.py` or extend the `calculate_cost` logic.
